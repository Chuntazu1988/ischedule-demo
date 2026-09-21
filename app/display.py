import io
import re
from time import time
import pandas as pd
import streamlit as st

from utils.helpers import (
    clean_text, safe_html, normalize_role_label, gender_role_label,
    role_label_for, MALE_VALUES, TEAM_LEAD_LABEL,
    is_time_text, to_datetime_time, time_to_minutes, minutes_between,
    short_flight_number, flight_key, name_key, name_key_reversed, is_cancelled_flight,
    classify_shift, shift_length, break_label_for_employee, required_break, required_refresh,
    return_text_by_shift, break_deadline_before_flight,
    employee_shift_text, gap_minutes_to_next, next_task_plain_text,
    preferred_break_window_by_shift,
)
from app.scheduler import (
    requirements_text, get_qualified_candidates_for_swap,
    get_extendable_candidates_for_swap, do_swap,
    is_within_shift, is_night_shift_for_return_rule, get_body_type, get_terminal,
    is_leased_aircraft, is_ferry_flight, get_aircraft_model, is_remote_gate,
)


# =========================
# LABEL SCHEDULE (adds text annotations to result_df)
# =========================

def _full_shift_span_text(emp_row, task_start=None):
    """Return "HH:MM-HH:MM" for the worker's true full shift span.

    A terminal-transfer worker's "תחילת משמרת"/"סוף משמרת" only reflect the
    window of the PRIMARY (night-priority) shift entry picked for scheduling
    purposes — e.g. a worker who starts at Terminal 1 in the evening and
    moves to Terminal 3 at night shows only the T3-side portion here
    (21:30-01:30) even though her real shift starts at 19:00 at T1.

    But a worker can also have TWO ENTIRELY UNRELATED shifts the same day
    (e.g. an early-morning 02:00-09:30 shift AND a separate night shift
    21:00-07:00) — "תחילת משמרת"/"סוף משמרת" is a single employee-level pair
    that can end up holding whichever of the two happened to win the
    primary-window tie-break, regardless of which shift the CURRENT task
    actually belongs to (found via real data 2026-07-25: agent#46, assigned
    to a NIGHT flight 21.07→22.07, showed "02:00-09:30" — her separate,
    unrelated early-morning shift that day). When `task_start` is given, it
    first picks whichever window in "זמינות מלאה" actually CONTAINS that
    time, so the right one of several unrelated shifts is used.

    Only after that does it widen across a genuine TRANSFER split: a window
    counts as "the other side of the same transfer" only if it's tagged a
    different terminal ("זמינות טרמינל", index-aligned with "זמינות מלאה")
    than the selected window's own, AND its start/end falls within a few
    hours of the transfer's "at" time (break+shuttle proximity) — this is
    what stops an unrelated shift from being merged in just because it also
    differs in terminal.

    Shared by build_next_task_labels and build_output_table.
    """
    _ss = clean_text(str(emp_row.get("תחילת משמרת", "")))
    _se = clean_text(str(emp_row.get("סוף משמרת", "")))
    _own_term = clean_text(str(emp_row.get("טרמינל", "")))
    _full_avail = clean_text(str(emp_row.get("זמינות מלאה", "")))
    _terms = clean_text(str(emp_row.get("זמינות טרמינל", ""))).split(",")
    _ranges = _full_avail.split(",") if _full_avail else []

    def _contains(s, e, t):
        sm, em, tm = time_to_minutes(s), time_to_minutes(e), time_to_minutes(t)
        if None in (sm, em, tm):
            return False
        span = (em - sm) % 1440 or 1440
        return 0 <= (tm - sm) % 1440 <= span

    # Only switch to a "זמינות מלאה" window when the PRIMARY window doesn't
    # already cover this task — "זמינות מלאה" can itself be a CLAMPED/
    # restricted availability range (e.g. an "upcoming_night" worker's
    # scheduling-eligibility window gets cut to "start-01:30" even though her
    # real shift is the full "22:00-08:30" already correctly held in
    # "תחילת משמרת"/"סוף משמרת") — trusting it whenever it happens to contain
    # the task would silently NARROW an already-correct span (found via real
    # data 2026-07-25: restricted-agent#1, 22:00-08:30 shift, shown as truncated
    # "22:00-01:30" because her "זמינות מלאה" was clamped to that same
    # upcoming-night restriction). Only switch entirely to a different window
    # when the primary one doesn't contain the task at all — the genuine
    # "worker has two unrelated shifts" case (see agent#46 above).
    if (task_start and is_time_text(str(task_start)) and _ranges
            and not (is_time_text(_ss) and is_time_text(_se) and _contains(_ss, _se, str(task_start)))):
        for _i, _rng in enumerate(_ranges):
            _parts = _rng.split("-")
            if len(_parts) != 2:
                continue
            _rs, _re = clean_text(_parts[0]), clean_text(_parts[1])
            if not (is_time_text(_rs) and is_time_text(_re)):
                continue
            if _contains(_rs, _re, str(task_start)):
                _ss, _se = _rs, _re
                _own_term = _terms[_i].strip() if _i < len(_terms) else _own_term
                break

    _tr = clean_text(str(emp_row.get("מעבר טרמינל", "")))
    _tr_m = re.match(r"^(\d)@(\d{1,2}:\d{2})$", _tr)
    if _ranges and _tr_m:
        _at_m = time_to_minutes(_tr_m.group(2))

        def _pivot_m(_t):
            _m = time_to_minutes(_t)
            return None if _m is None else (_m + 1440 if _m < 12 * 60 else _m)

        _best_s, _best_s_piv = _ss, _pivot_m(_ss) if is_time_text(_ss) else None
        _best_e, _best_e_piv = _se, _pivot_m(_se) if is_time_text(_se) else None
        _PROXIMITY_MIN = 4 * 60
        for _i, _rng in enumerate(_ranges):
            _parts = _rng.split("-")
            if len(_parts) != 2:
                continue
            _rs, _re = clean_text(_parts[0]), clean_text(_parts[1])
            if not (is_time_text(_rs) and is_time_text(_re)):
                continue
            _term = _terms[_i].strip() if _i < len(_terms) else ""
            if not _term or _term == _own_term:
                continue  # not the OTHER side of a transfer — a same-terminal
                          # window is either this row's own or an unrelated shift
            _rs_m, _re_m = time_to_minutes(_rs), time_to_minutes(_re)
            if _at_m is None or _rs_m is None or _re_m is None:
                continue
            _near_at = (
                min((_rs_m - _at_m) % 1440, (_at_m - _rs_m) % 1440) <= _PROXIMITY_MIN
                or min((_re_m - _at_m) % 1440, (_at_m - _re_m) % 1440) <= _PROXIMITY_MIN
            )
            if not _near_at:
                continue
            _rs_piv, _re_piv = _pivot_m(_rs), _pivot_m(_re)
            if _best_s_piv is None or (_rs_piv is not None and _rs_piv < _best_s_piv):
                _best_s, _best_s_piv = _rs, _rs_piv
            if _best_e_piv is None or (_re_piv is not None and _re_piv > _best_e_piv):
                _best_e, _best_e_piv = _re, _re_piv
        _ss, _se = _best_s, _best_e
    return f"{_ss}-{_se}" if _ss and _se else ""


# Shortest possible follow-on task: a narrow-body דייל is on station 50 minutes
# before departure, plus the 15-minute walk down to the gate.
_MIN_ROOM_FOR_NEXT_TASK = 65


def build_next_task_labels(result_df, employees_df, built_until_minutes=None):
    """
    built_until_minutes: when the day was built in segments (נייט/דיי/אפטר),
    the departure time of the last flight built so far. A worker whose shift
    runs past it has a next task that simply is not decided yet — the next
    team's build will decide it — so their last task shows "המשך יבוא" instead
    of claiming they are done for the day.
    """
    df = result_df.copy()

    if df.empty:
        df["טקסט עובד"] = []
        df["תפקיד נוכחי"] = []
        df["המשך אזורי"] = []
        return df

    df["טקסט עובד"] = df["עובד"].astype(str)
    df["תפקיד נוכחי"] = df["תפקיד"].apply(normalize_role_label)
    df["המשך אזורי"] = ""

    timed_df = df[df["התחלה"].astype(str).str.strip() != ""].copy()
    timed_df["_start_dt"] = pd.to_datetime(timed_df["התחלה"], format="%H:%M", errors="coerce")

    # Pre-build employee dict once — eliminates O(n_employees) per-row DataFrame scans.
    _emp_dict: dict = {}   # name -> row-as-dict
    for _, _er in employees_df.iterrows():
        _en = clean_text(str(_er.get("שם", "")))
        if _en:
            _emp_dict[_en] = _er

    _gender_col_main = next(
        (c for c in employees_df.columns
         if clean_text(c) in {"מין", "gender", "זכר/נקבה", "מגדר"}), None)

    def _is_male_emp(_nm):
        _r = _emp_dict.get(clean_text(str(_nm)))
        if _r is None or not _gender_col_main:
            return False
        return clean_text(str(_r.get(_gender_col_main, ""))).upper() in MALE_VALUES

    # A dual-shift worker is told apart from a single continuous night shift
    # by her זמינות windows: a plain night shift's windows chain into ONE
    # continuous span (agent#60: "01:30-08:30,22:00-01:30" → 22:00→08:30),
    # while genuinely separate shifts overlap or leave gaps (ג'ולי:
    # "21:00-07:00,19:00-01:30" — yesterday's night shift + tonight's
    # evening shift). is_night_shift_for_return_rule alone can't tell them
    # apart — both carry night-looking primary shift fields.
    def _avail_is_single_span(_er3):
        _av = clean_text(str(_er3.get("זמינות", "") or ""))
        _wins = []
        for _piece in _av.split(","):
            _piece = _piece.strip()
            if "-" not in _piece:
                continue
            _a, _b = _piece.split("-", 1)
            if is_time_text(_a.strip()) and is_time_text(_b.strip()):
                _wins.append((time_to_minutes(_a.strip()), time_to_minutes(_b.strip())))
        if len(_wins) <= 1:
            return True
        # try to chain the windows into one continuous timeline: repeatedly
        # attach the window whose start equals the current chain's end.
        _starts = {_s: _e for _s, _e in _wins}
        if len(_starts) != len(_wins):
            return False  # duplicate starts — cannot be one chain
        for _s0, _e0 in _wins:
            _cur = _e0
            _used = 1
            while _cur in _starts and _used < len(_wins):
                _cur = _starts[_cur]
                _used += 1
            if _used == len(_wins):
                return True

        # Windows that OVERLAP also form one span — they just don't chain
        # end-to-start. agent#11 holds 21:00-07:00 and 22:00-08:30, which is
        # a single 21:00-08:30 stretch, but the chain test above rejected it and
        # she lost the night ordering: her tasks were then walked from the
        # 03:00 pivot, so the 05:15 flight came BEFORE the 23:50 one and took
        # her break with it (fixed 2026-08-21).
        _norm = sorted(((_s, _s + ((_e - _s) % 1440 or 1440)) for _s, _e in _wins),
                       key=lambda w: (w[0] - 180) % 1440)
        _cur_end = _norm[0][1]
        for _s, _e in _norm[1:]:
            _s_adj = _s if _s >= _norm[0][0] else _s + 1440
            if _s_adj > _cur_end:
                return False        # a real hole between the windows
            _cur_end = max(_cur_end, _e if _e >= _s_adj else _e + 1440)
        return True

    # Pre-build O(1) next-task lookup — replaces per-row O(n) scans in next_task_plain_text
    # and gap_minutes_to_next (was O(n²) total, now O(n log n)).
    _next_map: dict = {}   # idx -> (next_plain_text, gap_minutes_or_None)
    _emp_sorted: dict = {}  # emp -> [(idx, start_dt, row), ...]
    for _nidx, _nrow in timed_df.iterrows():
        _ne = str(_nrow["עובד"]).strip()
        if _ne not in _emp_sorted:
            _emp_sorted[_ne] = []
        _emp_sorted[_ne].append((_nidx, _nrow["_start_dt"], _nrow))

    # Genuine single-span night-shift workers get SHIFT-RELATIVE task ordering
    # ((task_start − shift_start) % 1440) everywhere below — both in
    # _emp_sorted/_next_map here and in the main per-task loop's processing
    # order. The old "+1440 if start<06:00" heuristic split a morning block
    # straddling 06:00 (found via real 30.07 data: restricted-agent#2, 22:00-08:30
    # — LY573 03:55 shifted but LY541 06:15/LY347 07:10 not, producing a
    # garbage order where her chronologically-LAST task pointed "המשך ל־001",
    # the previous evening's flight). Dual-shift workers (non-contiguous
    # זמינות, e.g. TL#7) are excluded — their morning block really
    # does come first, so plain clock order is correct for them.
    _night_rule_emps: set = set()
    _night_shift_start_m: dict = {}
    for _ne in _emp_sorted:
        _er2 = _emp_dict.get(clean_text(_ne))
        if _er2 is None or not is_night_shift_for_return_rule(_er2) or not _avail_is_single_span(_er2):
            continue
        _ss2 = clean_text(str(_er2.get("תחילת משמרת", "")))
        if not is_time_text(_ss2):
            continue
        _night_rule_emps.add(_ne)
        _night_shift_start_m[_ne] = time_to_minutes(_ss2)

    for _ne, _tasks in _emp_sorted.items():
        # Build sort keys that handle midnight-spanning shifts correctly.
        # If an employee has tasks both in the evening (≥20:00) and after midnight
        # (<06:00), plain HH:MM order is wrong — a 03:00 task sorts before a 23:00
        # task even though 23:00 is earlier in the shift.
        # Night-rule workers sort shift-relatively (see above); others fall back
        # to the evening/midnight heuristic: add 1440 to post-midnight task
        # times when the schedule spans midnight.
        _mins = [
            (int(_sdt.hour) * 60 + int(_sdt.minute)) if pd.notna(_sdt) else 0
            for _, _sdt, _ in _tasks
        ]
        if _ne in _night_rule_emps:
            _sk = [(m - _night_shift_start_m[_ne]) % 1440 for m in _mins]
        else:
            _has_evening  = any(m >= 20 * 60 for m in _mins)
            _has_midnight = any(m < 6  * 60  for m in _mins)
            if _has_evening and _has_midnight:
                _sk = [m + 1440 if m < 6 * 60 else m for m in _mins]
            else:
                _sk = _mins
        _tasks[:] = [t for _, t in sorted(zip(_sk, _tasks), key=lambda x: x[0])]
        for _ti, (_nidx, _, _nr) in enumerate(_tasks):
            if _ti + 1 < len(_tasks):
                _, _, _nnr = _tasks[_ti + 1]
                # The role is the one THIS worker will fill next, so write it
                # in their own gender — and a team leader is always ר"צ.
                _np = (f"{short_flight_number(_nnr['טיסה'])} "
                       f"{role_label_for(_nnr['תפקיד'], _is_male_emp(_ne))}")
                _cur_end = to_datetime_time(_nr["סיום"])
                _nxt_st  = to_datetime_time(_nnr["התחלה"])
                _gap = minutes_between(_cur_end, _nxt_st)
                if _gap < 0:
                    _gap += 1440
            else:
                _np, _gap = "", None
            _next_map[_nidx] = (_np, _gap)

    # Pre-compute which employees have at least one midnight-crossing flight task
    # (role starts ≥22:00 AND ends <06:00 — straddles midnight).
    _has_night_flight_emps: set = set()
    for _, _nr in timed_df.iterrows():
        _st2 = to_datetime_time(_nr.get("התחלה", ""))
        _en2 = to_datetime_time(_nr.get("סיום",  ""))
        if _st2 and _en2:
            _sm2 = _st2.hour * 60 + _st2.minute
            _em2 = _en2.hour * 60 + _en2.minute
            if _sm2 >= 22 * 60 and _em2 < 6 * 60:
                _has_night_flight_emps.add(str(_nr["עובד"]).strip())

    break_state = {}   # emp -> {"stage": 0/1/2, "last_m": int}
    # Workers whose full הפסקה has already been PRINTED in this pass. "stage" is
    # not a substitute: night workers are pre-seeded at stage 1 (see the night
    # pre-seed below), which does not mean their break was taken.
    _break_shown: set = set()
    _preflight_note = {}   # task idx -> "[הפסקה HH:MM-HH:MM לפני הירידה לטיסות]"
    MIN_BETWEEN_BREAKS = 4 * 60

    # ── Gap-aware break-slot selection ────────────────────────────────────────
    # For each employee pick THE task after which the break chip should appear,
    # based on the actual gaps in their schedule — instead of blindly holding the
    # break until the "preferred window" opens (which is often fully occupied by
    # flights, pushing the break all the way to the end of the shift).
    # Selection cascade (first match wins):
    #   1. earliest gap ≥ break-duration whose start falls INSIDE the preferred window
    #   2. earliest gap ≥ break-duration starting AFTER the window opened
    #   3. the LARGEST gap ≥ break-duration anywhere mid-shift (e.g. a 2-hour gap
    #      at 13:30 for an 11:30-21:00 worker whose 16:00-17:00 window is occupied)
    # If no gap fits, no slot is stored and the legacy behavior applies.
    _break_slot: dict = {}   # emp -> df index of the task right before the chosen gap
    for _bne, _btasks in _emp_sorted.items():
        _ber = _emp_dict.get(clean_text(str(_bne)))
        if _ber is None or len(_btasks) < 2:
            continue
        _bl = break_label_for_employee(_ber)
        if not _bl:
            continue
        _bdur = required_break(_ber) or (45 if "הפסקה" in _bl else 20)
        _bss = clean_text(str(_ber.get("תחילת משמרת", "")))
        _bss_m = time_to_minutes(_bss) if is_time_text(_bss) else None
        _b_long = shift_length(_ber) > 9 * 60
        _b_shift_type = classify_shift(_ber)
        # early-morning peak hold (mirror of the in-loop rule): break may not start
        # before 06:30 (or 04:00 for SHORT 02:xx shifts; a long 02:00-11:00 shift
        # follows the 03:30-11:00 rules — user 2026-07-06) unless the gap ≥ 90 min.
        if _b_shift_type == "early_morning":
            _b_end_m = time_to_minutes(clean_text(str(_ber.get("סוף משמרת", "")))) if is_time_text(clean_text(str(_ber.get("סוף משמרת", "")))) else 0
            _b_short_0200 = _bss_m is not None and _bss_m < 3 * 60 and _b_end_m <= 10 * 60
            _b_hold_m = 4 * 60 if _b_short_0200 else 6 * 60 + 30
        else:
            _b_hold_m = None
        _bps, _bpe = preferred_break_window_by_shift(_ber)
        _bps_m = time_to_minutes(_bps) if _bps and is_time_text(_bps) else None
        _bpe_m = time_to_minutes(_bpe) if _bpe and is_time_text(_bpe) else None

        _cands = []   # (df_idx, break_start_m_raw, gap_minutes, is_tail)
        _early_cands = []  # long-shift gaps earlier than 4h in — fallback only
        for _bidx, _bsdt, _brow in _btasks:
            _bnp, _bgap = _next_map.get(_bidx, ("", None))
            if _bgap is None or _bgap < _bdur:
                continue
            _bend = to_datetime_time(_brow.get("סיום", ""))
            if _bend is None:
                continue
            _bend_m = _bend.hour * 60 + _bend.minute
            # early-morning peak hold
            if _b_hold_m is not None and _bend_m < _b_hold_m and _bgap < 90:
                continue
            # long shifts: PREFER a break ≥ 4h from shift start, but when the only
            # adequate gap is earlier (e.g. a 2h gap at 13:30 in an 11:30-21:00
            # shift whose later hours are packed with flights), use it — a real
            # mid-shift break beats one shoved to the end of the shift.
            if _b_long and _bss_m is not None and ((_bend_m - _bss_m) % 1440) < MIN_BETWEEN_BREAKS:
                _early_cands.append((_bidx, _bend_m, _bgap, False))
                continue
            _cands.append((_bidx, _bend_m, _bgap, False))

        # Tail gap: from the worker's LAST task to shift end. The loop above
        # only sees gaps captured by _next_map (this task → NEXT task), which
        # is (…, None) for the LAST task — so the shift's own tail time was
        # completely invisible as a break candidate, even when it's the only
        # real opportunity. A DIFFERENT, downstream "no adequate gap" fallback
        # (the pre-flight-break-for-late-first-task block, below) then silently
        # assumed the break was taken before the first flight instead — but it
        # only records that assumption in "טקסט עובד", never "המשך אזורי", so
        # the workflow tab showed NO break at all (found via real data
        # 2026-07-14: restricted-agent#2, 10:00-16:00, last task ends 15:00 — a
        # genuine 60-min gap before shift end at 16:00 that was never
        # considered). Appended AFTER the per-task loop so it always sorts
        # last (chronologically latest) — a real EARLIER candidate from the
        # loop above still wins tier-1/tier-2 selection unchanged; this only
        # matters when no earlier candidate exists, i.e. genuine last resort.
        if _btasks:
            _tail_bidx, _tail_bsdt, _tail_brow = _btasks[-1]
            _tail_bend = to_datetime_time(_tail_brow.get("סיום", ""))
            _bse_str = clean_text(str(_ber.get("סוף משמרת", "")))
            if _tail_bend is not None and is_time_text(_bse_str):
                _tail_bend_m = _tail_bend.hour * 60 + _tail_bend.minute
                _bse_m = time_to_minutes(_bse_str)
                _tail_gap = (_bse_m - _tail_bend_m) % 1440
                if _tail_gap >= _bdur and not (
                    _b_hold_m is not None and _tail_bend_m < _b_hold_m and _tail_gap < 90
                ):
                    if _b_long and _bss_m is not None and ((_tail_bend_m - _bss_m) % 1440) < MIN_BETWEEN_BREAKS:
                        _early_cands.append((_tail_bidx, _tail_bend_m, _tail_gap, True))
                    else:
                        _cands.append((_tail_bidx, _tail_bend_m, _tail_gap, True))

        if not _cands and _early_cands:
            _cands = _early_cands
        elif _early_cands and not any(not c[3] for c in _cands):
            # _cands has no genuine between-task gap — only a tail gap survived
            # the "≥4h from shift start" filter below (a tail gap is naturally
            # far from shift start, so it's never diverted to _early_cands even
            # when it lands right before shift end). A real between-flights
            # gap, even an early one, beats a break pushed all the way to the
            # very end of a long shift — found via real data 2026-07-15:
            # TL#22, 11:30-21:00, had a genuine 90-min gap at 14:25-15:55
            # (2h55m into the shift, under the 4h threshold) that lost by
            # default to the 90-min tail gap (19:30-21:00) simply because nothing
            # else was available in _cands. Merge the early candidates back in
            # so the tier-1/tier-2 window logic below can consider them too.
            _cands = _cands + _early_cands
        # NOTE 2026-08-05: a "genuine last resort — use the first gap even
        # under the required break duration" fallback was tried here and
        # reverted. It let TL#17's break land in a real but only-35-min
        # gap; the user confirmed a break must always be the FULL required
        # duration (45 min for הפסקה) — a short gap is not an acceptable
        # placement at all, so the tail gap (the only one actually ≥45 min
        # here) is the genuinely correct "first real opportunity", not a
        # fallback to avoid. Do not reintroduce under-duration placement.
        if not _cands:
            continue
        _slot = None
        if _bps_m is not None and _bpe_m is not None:
            _in_win = [c for c in _cands if _bps_m <= c[1] <= _bpe_m]
            if _in_win:
                _slot = _in_win[0][0]          # earliest gap inside the window
            else:
                _after = [c for c in _cands if c[1] > _bpe_m]
                # A tail gap (last task → shift end) is a genuine last resort:
                # prefer ANY real between-task gap over it, even one that falls
                # BEFORE the window opened, rather than letting "earliest gap
                # after the window" accidentally match only the tail gap and
                # skip past an earlier, perfectly reasonable between-flights
                # gap. Found via real data 2026-07-15: TL#22, 11:30-21:00,
                # window 16:00-17:00 — a real 90-min gap existed at 14:25-15:55
                # (before the window) and the tail gap was ALSO 90 min
                # (19:30-21:00, right before shift end); tier-2's "after
                # window" rule picked the tail gap by coincidence, landing the
                # break needlessly late near shift end instead of mid-shift.
                _after_non_tail = [c for c in _after if not c[3]]
                if _after_non_tail:
                    _slot = _after_non_tail[0][0]   # earliest non-tail gap after the window
                elif _after:
                    _between_any = [c for c in _cands if not c[3]]
                    if _between_any:
                        _slot = _between_any[0][0]  # earliest between-task gap anywhere, beats a tail gap
                    else:
                        _slot = _after[0][0]        # no between-task gap exists at all — tail gap it is
        if _slot is None:
            if _bl == "הפסקה ורענון":
                # 10h+ shifts get a break AND a later רענון (≥4h apart) — pick the
                # EARLIEST adequate gap so the רענון still fits before shift end.
                _slot = _cands[0][0]
            else:
                _slot = max(_cands, key=lambda c: c[2])[0]   # largest mid-shift gap
        _break_slot[str(_bne).strip()] = _slot

    # ── Terminal-transfer chip ────────────────────────────────────────────────
    # A worker with a mid-shift terminal transfer ("מעבר טרמינל" = "1@02:00")
    # takes their break AS PART of the transfer (45 min break + 30 min travel,
    # user rule). The chip goes on their LAST task ending before the transfer:
    # "הפסקה ומעבר לטרמינל 1 (הגעה 02:00)". It replaces the regular break slot.
    _transfer_chip: dict = {}   # emp -> (df_idx, to, at, with_break)
    def _noon_pivot(_m):
        return _m + 1440 if _m < 720 else _m

    for _tne, _ttasks in _emp_sorted.items():
        _ter = _emp_dict.get(clean_text(str(_tne)))
        if _ter is None:
            continue
        _tm = re.match(r"^(\d)@(\d{1,2}:\d{2})$", clean_text(str(_ter.get("מעבר טרמינל", ""))))
        if not _tm:
            continue
        _t_to, _t_at = _tm.group(1), _tm.group(2)
        _t_at_m = time_to_minutes(_t_at)

        # A worker can hold TWO shifts in one operational day — last night's,
        # ending this morning, and tonight's. The transfer belongs to ONE of
        # them (the "שיוך משמרת" column tags each availability window: "0" =
        # the primary shift, "1" = tonight's). Tasks from the OTHER shift must
        # not attract the transfer or its break note — found via real 16.08
        # data: TL#41 transfers to T1 during the night that starts today, yet
        # his EARLY-MORNING tasks, which belong to yesterday's shift, were the
        # ones carrying "[הפסקה ... לפני המעבר לטרמינל 1]".
        #
        # WHICH of the two tags ("0"/"1") owns the transfer is normally "1"
        # (tonight's shift — every case above involves a transfer during the
        # shift that starts today) but is not universal: "מעבר משמרת" (set in
        # data_loader.apply_shift_map_to_employees) names the actual owner —
        # found via real 12.07.2026 data: agent#65 has a page-1
        # (tag "0") night 22:00-07:00 with a T3→T1 transfer, PLUS an unrelated,
        # transfer-less page-2 night that also starts 22:00 — hard-coding "1"
        # put the transfer/break chip on the wrong (transfer-less) shift.
        _sh_tags = [t.strip() for t in clean_text(str(_ter.get("שיוך משמרת", ""))).split(",")]
        _tr_shift_tag = clean_text(str(_ter.get("מעבר משמרת", ""))).strip()
        if _tr_shift_tag not in ("0", "1"):
            _tr_shift_tag = "1"
        if _tr_shift_tag in _sh_tags and "0" in _sh_tags and "1" in _sh_tags:
            _wins = [w.strip() for w in clean_text(str(_ter.get("זמינות", ""))).split(",")]
            _tr_spans = []
            for _wi, _w in enumerate(_wins):
                if "-" not in _w or _wi >= len(_sh_tags) or _sh_tags[_wi] != _tr_shift_tag:
                    continue
                _ws, _we = _w.split("-", 1)
                if is_time_text(_ws.strip()) and is_time_text(_we.strip()):
                    _tr_spans.append((time_to_minutes(_ws.strip()), time_to_minutes(_we.strip())))

            def _in_transfer_shift(_row):
                _st = to_datetime_time(_row.get("התחלה", ""))
                if _st is None:
                    return False
                _stm = _st.hour * 60 + _st.minute
                return any(
                    0 <= (_stm - _a) % 1440 <= ((_b - _a) % 1440 or 1440)
                    for _a, _b in _tr_spans
                )

            _ttasks = [t for t in _ttasks if _in_transfer_shift(t[2])]
            if not _ttasks:
                continue

        # עובד שכל המשימות שלו אחרי שעת המעבר = הגיע מהטרמינל השני כבר לאחר
        # הפסקה (כך כתוב בסידור) — אין לתת לו הפסקה/רענון נוספים.
        _ops_first = None
        for _tidx, _tsdt, _trow in _ttasks:
            _ts2 = to_datetime_time(_trow.get("התחלה", ""))
            if _ts2 is None:
                continue
            _k = _noon_pivot(_ts2.hour * 60 + _ts2.minute)
            if _ops_first is None or _k < _ops_first:
                _ops_first = _k
        if _ops_first is not None and _ops_first >= _noon_pivot(_t_at_m):
            break_state[str(_tne).strip()] = {"stage": 2, "last_m": _t_at_m}
            _break_slot.pop(str(_tne).strip(), None)
            # Still show the break explicitly — user rule 2026-07-09: a worker
            # with a terminal transfer must ALWAYS show the break-before-
            # transfer in the workflow tab, never silently assumed, even when
            # all their visible tasks happen to start after the transfer time
            # (previously this branch set stage=2 and skipped with no note at
            # all — found via real data: agent#21, TL#19 both showed no
            # break chip, while other workers only happened to show one via an
            # unrelated coincidental mechanism).
            _t_rb2 = required_break(_ter) or 45
            _b_start2 = (_t_at_m - _t_rb2) % 1440
            _first_idx2 = _ttasks[0][0] if _ttasks else None
            if _first_idx2 is not None:
                _preflight_note[_first_idx2] = (
                    f" [הפסקה {_b_start2 // 60:02d}:{_b_start2 % 60:02d}-"
                    f"{_t_at_m // 60:02d}:{_t_at_m % 60:02d} "
                    f"לפני המעבר לטרמינל {_t_to}]"
                )
            continue

        _best = None   # (gap_to_transfer_minutes, df_idx)
        for _tidx, _tsdt, _trow in _ttasks:
            _te2 = to_datetime_time(_trow.get("סיום", ""))
            if _te2 is None:
                continue
            _te2_m = _te2.hour * 60 + _te2.minute
            _d = (_t_at_m - _te2_m) % 1440
            if 0 < _d <= 8 * 60 and (_best is None or _d < _best[0]):
                _best = (_d, _tidx)
        if _best is not None:
            _t_rb = required_break(_ter) or 45
            # ההפסקה נוסעת עם המעבר רק אם יש די זמן בין סוף המשימה האחרונה
            # להגעה (הפסקה + 30 דק' נסיעה). אחרת ההפסקה חייבת להילקח מוקדם
            # יותר — עדיפות להפסקה לפני הטיסה הראשונה (הנחיית משתמש 2026-07-06).
            _with_break = _best[0] >= _t_rb + 30
            _transfer_chip[str(_tne).strip()] = (_best[1], _t_to, _t_at, _with_break)
            if _with_break:
                # their break is the transfer — cancel any regular break slot
                _break_slot.pop(str(_tne).strip(), None)
            else:
                # break before the FIRST flight when the pre-flight stretch fits
                # (_ttasks is already sorted chronologically — see _emp_sorted build above)
                _first_idx, _first_sdt, _first_row = _ttasks[0]
                _fs3 = to_datetime_time(_first_row.get("התחלה", ""))
                _wss = clean_text(str(_ter.get("תחילת משמרת", "")))
                if _fs3 is not None and is_time_text(_wss):
                    _fs3_m = _fs3.hour * 60 + _fs3.minute
                    _pre_gap = (_fs3_m - time_to_minutes(_wss)) % 1440
                    # +15 min walk-to-gate after the break (user rule
                    # 2026-08-06: the walk time is added ON TOP of the full
                    # break — a break ending at flight start is only ~30
                    # usable minutes).
                    if _pre_gap >= _t_rb + 15:
                        _b_end = _fs3_m - 15
                        _b_start = _b_end - _t_rb
                        _preflight_note[_first_idx] = (
                            f" [הפסקה {_b_start % 1440 // 60:02d}:{_b_start % 60:02d}-"
                            f"{_b_end % 1440 // 60:02d}:{_b_end % 60:02d} לפני הירידה לטיסות]"
                        )
                        break_state[str(_tne).strip()] = {"stage": 2, "last_m": _b_end}
                        _break_slot.pop(str(_tne).strip(), None)

    # Night shift employees (22:00–08:30 etc.) pre-set to stage=2 when their break
    # is guaranteed to have already been taken.  Two cases:
    #   1. First scheduled task starts at or after 03:00 → break taken at counters
    #      before shift action began.
    #   2. Employee has at least one task that ENDS before 03:00 (overnight flight
    #      or similar) → break was taken after that task or at counter closing.
    #      Example: TL#18 22:00-08:30 with LY353 ending ~01:30 took her break
    #      then; no second break is due until the 4-hour minimum has elapsed again.
    # User rule 2026-07-06: a genuine night-shift worker (is_night_shift_for_return_rule
    # — start >=20:30, crosses midnight, end >=04:00) NECESSARILY already had their
    # break by the time the operational day's flights start: either after a night
    # flight (if scheduled to one), or during counter-closing before their first
    # task. This is unconditional — it must NOT depend on the exact clock position
    # of their first task. The previous "first task >=03:00 OR a task ends <03:00"
    # heuristic missed a worker whose first task starts just before 03:00 but ends
    # after it (e.g. TL#44: LY549 02:55-03:55 — neither case matched, so she wrongly
    # got a visible mid-shift break chip). A night-shift worker's shift starts hours
    # before ANY first task regardless, so there is always ample counter time first.
    if _emp_dict:
        for _emp_name, _grp in timed_df.groupby("עובד"):
            _erow = _emp_dict.get(clean_text(str(_emp_name)))
            if _erow is None:
                continue
            if not is_night_shift_for_return_rule(_erow):
                continue
            # Skip this blanket "already broke" assumption for a worker who
            # HAS a genuine midnight-crossing task of her own (_has_night_
            # flight_emps) — her FIRST scheduled tasks are themselves the
            # night-flight block, often starting within an hour or two of
            # shift start, leaving no real room for a break to have already
            # happened first. Forcing stage=1 here made the dedicated
            # midnight-cross block below (which correctly fires the FULL
            # break, not just a רענון, right after her last night-block
            # task) grant only a רענון instead — she hadn't actually broken
            # yet (user rule 2026-07-25: a night-shift worker assigned a
            # night flight goes on break immediately after her last task,
            # labeled "הפסקה וחזרה", not just "רענון וחזרה" — found via real
            # data: agent#46, LY017/LY015, 21:00-07:00 shift). Leaving
            # stage=0 here lets that block run its normal, unassumed logic.
            if _emp_name in _has_night_flight_emps:
                continue
            # A long night shift (>9:30h, e.g. 22:00-08:30) is entitled to BOTH
            # a הפסקה AND a later רענון (break_label_for_employee returns
            # "הפסקה ורענון"). The blanket "already had their break" assumption
            # only covers the FIRST break — forcing stage=2 here (fully done)
            # would silently swallow the still-owed רענון for the whole shift
            # (found via real data: TL#44 22:00-08:30 had a 55-min gap after
            # LY541 with no רענון shown at all). stage=1 keeps the "already had
            # the initial break" assumption but leaves the רענון mid-shift
            # break-slot mechanism (_break_slot / MIN_BETWEEN_BREAKS below) free
            # to fire later, same as a fresh "הפסקה ורענון" worker after their
            # first break.
            _night_bl = break_label_for_employee(_erow)
            _night_stage = 1 if _night_bl == "הפסקה ורענון" else 2
            break_state[_emp_name] = {"stage": _night_stage, "last_m": -9999}

    # Accumulate text results; batch-write to df at the end (avoids per-row COW overhead)
    _worker_text: dict = {}
    _region_cont: dict = {}

    # Iterate in SHIFT-RELATIVE order for genuine night-shift workers (see
    # _night_rule_emps above), so a midnight-spanning worker's evening tasks
    # are processed before her post-midnight ones — raw clock order inverted
    # her break_state progression (the FULL break fired on the
    # chronologically-LAST morning task, processed first, and the night
    # flight processed later only got the leftover רענון; found via real
    # 30.07 data: agent#60, LY121 23:10-00:15 + LY553 03:25).
    def _loop_key(_lr):
        if pd.isna(_lr["_start_dt"]):
            return 0
        _m3 = int(_lr["_start_dt"].hour) * 60 + int(_lr["_start_dt"].minute)
        _ne3 = str(_lr["עובד"]).strip()
        if _ne3 in _night_rule_emps:
            return (_m3 - _night_shift_start_m[_ne3]) % 1440
        return _m3

    timed_df["_loop_m"] = [_loop_key(_lr) for _, _lr in timed_df.iterrows()]
    for idx, row in timed_df.sort_values("_loop_m", kind="stable").iterrows():
        emp = str(row["עובד"]).strip()

        if "❌" in emp:
            _worker_text[idx] = emp
            _region_cont[idx] = ""
            continue

        _pre_next, _pre_gap = _next_map.get(idx, ("", None))
        emp_row = _emp_dict.get(clean_text(emp))
        if emp_row is None:
            next_text = _pre_next or "חוזר"
            _worker_text[idx] = f"{emp} → {next_text}"
            _region_cont[idx] = next_text
            continue

        # Shift-span text is shown ONLY via the dedicated shift-time badge
        # (build_output_table's "(HH:MM-HH:MM)" pill, rendered as its own
        # floated badge in render_flight_card_with_swap) — NOT duplicated
        # inline here too. Two independent computations of this same value
        # used to exist (this one and build_output_table's), so a row could
        # show two different-looking shift times at once when only one had
        # the terminal-transfer full-span fix applied (user rule 2026-07-25:
        # "תציג את שעות המשמרת רק במה שהקפתי בעיגול ולא איפה שה-X" — show
        # shift hours only in the badge, not inline here).
        shift_suffix = ""
        shift_type  = classify_shift(emp_row)

        next_plain    = _pre_next
        task_end_dt   = to_datetime_time(row["סיום"])
        task_start_dt = to_datetime_time(row["התחלה"])
        task_end_m    = task_end_dt.hour * 60 + task_end_dt.minute
        gap_to_next   = _pre_gap
        break_label   = break_label_for_employee(emp_row)
        base_role     = normalize_role_label(str(row.get("תפקיד בסיס", "")))
        is_agent      = base_role in {"דיילת", "דייל", "שומר TSA"}

        state  = break_state.get(emp, {"stage": 0, "last_m": -9999})
        stage  = state["stage"]
        last_m = state["last_m"]

        te = task_end_m
        if last_m > 0 and te < last_m:
            te += 1440
        time_since_last = (te - last_m) if last_m != -9999 else 9999

        is_last_task = (next_plain == "")

        # Early-morning employees (03:00–04:30 start) whose first scheduled task
        # begins AFTER the preferred break window has closed should have their break
        # BEFORE this task, not after it.  Mark stage=2 so the break text doesn't
        # fire again after the flight.
        # BUT: only do this if the employee was actually FREE during the break window
        # (had a gap >= required_break minutes inside it). If they were assigned to
        # flights throughout the window, they never took a break → keep stage=0.
        # ALSO: skip this silent "already broke before flights" assumption when a
        # real BETWEEN-FLIGHTS gap was found (_break_slot) — user rule 2026-07-06:
        # for the short 02:00 shift, a between-flights break is fine too and should
        # not be silently pre-empted just because the pre-flight window was empty
        # (TL#22 / TL#12: 45-min gap between their two flights was being
        # ignored, leaving them with NO visible break at all).
        task_start_m_now = to_datetime_time(row["התחלה"])
        task_start_m_now = task_start_m_now.hour * 60 + task_start_m_now.minute if task_start_m_now else 0
        # 02:00-09:30-family shift (window "03:00"-"05:00") is excluded here —
        # the newer "Pre-flight break" mechanism below now handles this shift
        # type explicitly and visibly (both the squeeze case and the
        # room-to-spare/proactive case), so this silent "assume already
        # broken" shortcut must not preempt it. It ALSO never got a chance to
        # run for a genuinely single-task worker (the _break_slot cascade
        # requires len(tasks)>=2, so _break_slot.get(emp) is always None for
        # them) — found via real data 2026-07-19: agent#27, 02:00-08:30,
        # single flight LY2371 at 05:20, would otherwise be silently marked
        # "already broke" with NO visible note at all instead of getting the
        # explicit proactive/squeeze decision.
        if (stage == 0 and classify_shift(emp_row) == "early_morning" and _break_slot.get(emp) is None
                and preferred_break_window_by_shift(emp_row) != ("03:00", "05:00")):
            _pref_s, _pref_e = preferred_break_window_by_shift(emp_row)
            if _pref_e and is_time_text(_pref_e):
                _pref_e_m = time_to_minutes(_pref_e)
                if task_start_m_now >= _pref_e_m:
                    # Check if employee had a gap >= required_break inside the break window
                    _pref_s_m = time_to_minutes(_pref_s) if _pref_s and is_time_text(_pref_s) else 0
                    _rb = required_break(emp_row) or 45
                    _had_gap = False
                    _prev_end = _pref_s_m
                    for _chk_row in timed_df[timed_df["עובד"] == emp].sort_values("התחלה").itertuples():
                        _ct_s = time_to_minutes(clean_text(str(getattr(_chk_row, "התחלה", "")))) if is_time_text(clean_text(str(getattr(_chk_row, "התחלה", "")))) else None
                        _ct_e = time_to_minutes(clean_text(str(getattr(_chk_row, "סיום", "")))) if is_time_text(clean_text(str(getattr(_chk_row, "סיום", "")))) else None
                        if _ct_s is None or _ct_e is None:
                            continue
                        if _ct_e <= _pref_s_m or _ct_s >= _pref_e_m:
                            continue  # task outside break window
                        if _ct_s - _prev_end >= _rb:
                            _had_gap = True
                            break
                        _prev_end = max(_prev_end, _ct_e)
                    if _pref_e_m - _prev_end >= _rb:
                        _had_gap = True
                    if _had_gap:
                        break_state[emp] = {"stage": 2, "last_m": _pref_e_m}
                        stage = 2

        # ── Pre-flight break for late-starting flight blocks (all shift types) ──
        # A worker whose FIRST flight starts late in the shift, with the preferred
        # break window free before it and NO adequate mid-shift gap later, takes
        # the break BEFORE going down to the flights (user rule 2026-07-05: "יש
        # לתת בין הטיסות או לפני הירידה לטיסות"). Example: 11:30-21:00 workers
        # whose only flights are 16:45-19:15 back-to-back — break 16:00-16:45,
        # then the flight block; no end-of-shift break afterwards.
        # early_morning exclusion REMOVED (2026-07-17): it existed so this
        # block wouldn't interfere with the SEPARATE "early-morning employees"
        # blanket rule just above (which only fires when _break_slot.get(emp)
        # is None) -- but that separation left a genuine gap for the SHORT
        # 02:00-09:30 shift: when the worker has 2+ tasks, _break_slot's
        # cascade has no HEAD-gap candidate type (same missing-gap-class bug
        # fixed for the tail gap and for scheduler.py's gap-fill earlier this
        # session), so it falls through to whatever between-task gap exists,
        # even when the preferred pre-flight window (03:00-05:00) fits
        # cleanly before the first flight. The `_existing_too_late` check
        # below (now reachable for early_morning shifts too) safely handles
        # this: it only overrides an existing _break_slot placement when that
        # placement is confirmed too late relative to the window, so the
        # LOCKED reference patterns (TL#10, TL#8, TL#7 —
        # all early_morning, all already correctly placed IN-window by the
        # normal cascade) are unaffected — verified by regression test.
        # Found via real data 2026-07-17: restricted-agent#6, agent#3
        # (02:00-09:30, first flight 05:20) — break landed in the 115-min
        # between-task gap (06:20-08:15) instead of the pre-flight window.
        if stage == 0:
            _fp_first = _emp_sorted.get(emp)
            if _fp_first and _fp_first[0][0] == idx:     # this is the worker's first task
                _fp_s, _fp_e = preferred_break_window_by_shift(emp_row)
                if _fp_s and is_time_text(_fp_s):
                    _fp_s_m = time_to_minutes(_fp_s)
                    _fp_e_m = time_to_minutes(_fp_e) if _fp_e and is_time_text(_fp_e) else None
                    _fp_rb = required_break(emp_row) or 45

                    # An existing _break_slot gap (found by the cascade above) may
                    # still land well AFTER the preferred window closed — e.g. wedged
                    # between two late-evening flights in a long shift that has a big
                    # empty stretch beforehand. That is not a real improvement over
                    # taking the break early inside the window, while the worker is
                    # genuinely free (nothing precedes their first task). User rule
                    # 2026-07-14 (agent#24 / agent#34, 12:30-00:30 shift, only
                    # flights at 21:15 & 23:10): a break wedged at ~22:20 is "too
                    # late" for a 12h shift — take it earlier instead.
                    #
                    # _fp_placement_windows: shift types where "prefer the window
                    # over a late gap" PLACEMENT applies. Defined here (moved up
                    # from a length-based ">9h" heuristic) as an explicit per-
                    # shift-type opt-in — the length heuristic missed 08:00-17:00
                    # (exactly 9h, not >9h) by the same kind of off-by-threshold
                    # margin as the is_night_shift_for_return_rule 20:30 bug —
                    # found via real data 2026-07-15: agent#51, agent#1, trainee-agent#3, agent#53, agent#44, agent#28
                    # (08:00-17:00) and סימן agent#10, trainee-agent#9, agent#12,
                    # agent#6, agent#36, agent#48 (09:30-17:30) and restricted-agent#7 (11:00-19:00) all had a real but LATE between-task gap
                    # that _break_slot correctly found and used (so
                    # _existing_slot_idx was never None), which meant the old
                    # length-based guard never even got a chance to reconsider it.
                    # _fp_proactive_windows (defined further below, reused here) is
                    # a SUBSET of this — those windows additionally get "יזומה"
                    # wording; 12:30-00:30 (16:00-17:30) gets the placement fix but
                    # keeps "לפני הירידה לטיסות" wording per the narrower whitelist.
                    # A SHORT shift not in this set (e.g. restricted-agent#2, 10:00-16:00)
                    # keeps a break near the tail end — explicitly wanted, do not
                    # override a legitimate tail-gap slot for shift types the user
                    # hasn't asked for this treatment on.
                    # ("03:00","05:00") added 2026-07-17: 02:00-09:30 short shift —
                    # opposite framing from the others (this one keeps plain "לפני
                    # הירידה לטיסות" wording, matching the user's explicit ask for
                    # genuine pre-flight placement, NOT "יזומה" — see
                    # _fp_proactive_windows below, deliberately NOT extended here).
                    # NOT role-scoped (revised same day): a first attempt limited
                    # this to base_role=="דייל" only (the user's first report named
                    # "הדייל" specifically), to protect TL#7's earlier LOCKED
                    # reference pattern (same shift/window, role "ראש צוות", break
                    # between flights). But the user then showed a SECOND real case
                    # with the SAME between-flights problem on ר"צ-role tasks (agent#50, and TL#7 herself with a different real day's task
                    # mix: LY395/LY357/LY221 as ר"צ, LY285 as דייל) — confirming the
                    # rule is genuinely shift-type-wide, not role-scoped. The earlier
                    # ג'ולי locked pattern is SUPERSEDED by this broader rule; see
                    # optimal_schedule_patterns memory update.
                    _fp_placement_windows = {("10:00", "12:00"), ("11:30", "13:30"), ("13:00", "15:00"), ("18:00", "19:00"), ("16:00", "17:30"), ("03:00", "05:00")}
                    _existing_slot_idx = _break_slot.get(emp)
                    _existing_too_late = False
                    if _existing_slot_idx is not None and _fp_e_m is not None and (_fp_s, _fp_e) in _fp_placement_windows:
                        if (_fp_s, _fp_e) == ("03:00", "05:00"):
                            # 02:00-09:30 short shift: prefer genuine pre-flight
                            # placement over a MARGINAL existing _break_slot
                            # result — one that barely meets the required
                            # duration, e.g. landing right at the window's
                            # closing boundary (found via real data 2026-07-17:
                            # agent#50's break landed exactly at 05:00, right
                            # after her first flight, technically "in window"
                            # but still a between-flights placement, not a
                            # pre-flight one). EXCEPTION (2026-08-05): if the
                            # existing slot is a comfortably-real gap — at
                            # least 20 min more than the bare minimum, e.g. one
                            # deliberately opened up by
                            # protect_early_shift_preflight_breaks's second
                            # pass — that's a genuine break opportunity and
                            # must NOT be thrown away in favor of a squeezed,
                            # invisible pre-flight claim (found via real data:
                            # TL#31, a real 70-min gap created by swapping
                            # her off LY347, got silently discarded by this
                            # override, leaving her with NO visible break at
                            # all instead of the real one).
                            _, _existing_gap_min = _next_map.get(_existing_slot_idx, ("", None))
                            if _existing_gap_min is not None and _existing_gap_min >= _fp_rb + 20:
                                _existing_too_late = False
                            else:
                                _existing_too_late = True
                        else:
                            _ex_row = df.loc[_existing_slot_idx] if _existing_slot_idx in df.index else None
                            _ex_end = to_datetime_time(_ex_row.get("סיום", "")) if _ex_row is not None else None
                            if _ex_end is not None:
                                _ex_end_m = _ex_end.hour * 60 + _ex_end.minute
                                _existing_too_late = _ex_end_m > _fp_e_m

                    if _existing_slot_idx is None or _existing_too_late:
                        # the break must fit between the window opening and this task.
                        # For ("03:00","05:00") specifically, anchor feasibility to the
                        # worker's ACTUAL shift start (typically 02:00), not the
                        # preferred window's own open time (03:00) — a first flight at
                        # e.g. 03:15 leaves only 15 min before 03:00+45=03:45, failing
                        # this check even though 02:00->03:15 is 75 min, plenty for a
                        # pre-flight break squeezed in earlier than the window's
                        # "ideal" open time. Without this, these workers fell straight
                        # through to a tail/end-of-shift break instead (found via real
                        # data 2026-07-31: TL#17/agent#32/agent#41/דז'אנשווילי
                        # לינוי/agent#14, all 02:00-09:30 with a first flight
                        # 03:10-03:25 — 15-25 min into the window, not the full 45).
                        _fp_earliest_m = _fp_s_m
                        # 15-min walk-to-gate buffer AFTER the break, on top of
                        # its own duration (user rule 2026-08-05 for the 02:00
                        # family, generalized 2026-08-06: "בהפסקה לפני טיסה יש
                        # לציין את 15 הדקות של זמן הירידה לאולם ולהוסיף אותו
                        # ל-45 דקות ההפסקה" — a break ending exactly at flight
                        # start really gives only 30 usable minutes, e.g. agent#47 03:25-04:10 before an 04:10 flight).
                        _fp_walk = 15
                        if (_fp_s, _fp_e) == ("03:00", "05:00"):
                            _fp_shift_start_str = clean_text(emp_row.get("תחילת משמרת", ""))
                            if is_time_text(_fp_shift_start_str):
                                _fp_earliest_m = min(_fp_s_m, time_to_minutes(_fp_shift_start_str))
                        if task_start_m_now >= _fp_earliest_m + _fp_rb + _fp_walk:
                            # For ("03:00","05:00") specifically, "room to spare"
                            # isn't measured against the window's own end (05:00) —
                            # that bar is too strict for what should count as "the
                            # first flight isn't close to shift start" (user
                            # 2026-07-19: "רק במקרה והטיסה הראשונה קרובה יחסית
                            # לתחילת המשמרת... ואם לא תינתן הפסקה לפני הטיסה
                            # הראשונה, לא תהיה הזדמנות להוציא את העובד להפסקה").
                            # Instead reuse the same 60-min "genuinely idle beyond
                            # the break" threshold as the scheduler's own
                            # return-to-counters rule: if the flight starts 60+ min
                            # after a break taken at the window's OPENING would
                            # end, there's a real gap to return to counters in
                            # between — not an emergency squeeze. Found via real
                            # data: agent#27, 02:00-08:30, only flight LY2371 at
                            # 05:20 — 95 min past where a 03:00-03:45 break would
                            # end, wrongly squeezed to 04:35-05:20 (jammed right
                            # before the flight) instead of getting a proactive
                            # 03:00-03:45 break with a genuine return in between.
                            if (_fp_s, _fp_e) == ("03:00", "05:00"):
                                _fp_room_to_spare = (task_start_m_now - (_fp_s_m + _fp_rb + _fp_walk)) >= 60
                            else:
                                _fp_room_to_spare = _fp_e_m is not None and _fp_e_m + _fp_rb <= task_start_m_now
                            if _fp_room_to_spare:
                                # window closes with room to spare before the flight —
                                # take the break there instead of jamming it right up
                                # against the flight (needlessly late in a long shift).
                                # This is a genuinely PROACTIVE break (a real gap sits
                                # between the break and the flight, not a break taken
                                # "because" the flight is imminent) — label it as such
                                # rather than "לפני הירידה לטיסות", which implies the
                                # break exists only to clear time right before the
                                # flight (user rule 2026-07-14: shifts like 08:00-17:00,
                                # 09:30-17:30, 11:00-19:00 whose only flights bunch late
                                # in the shift should show "הפסקה יזומה", not "לפני
                                # טיסה" — agent#53, agent#51, agent#44 / agent#52, agent#57, סימן agent#10, trainee-agent#9, agent#37, agent#12, agent#6, agent#48 / agent#49).
                                _fp_b_start = _fp_s_m
                                _fp_b_end = _fp_s_m + _fp_rb
                                # Wording is scoped to exactly the requested window
                                # values, not inferred from "room to spare" alone —
                                # other shifts that also get earlier placement (e.g.
                                # the short 10:00-16:00 shift, or the 12:30-00:30 long
                                # evening shift) keep the "לפני הירידה לטיסות" wording;
                                # only these explicitly-named shift types say "יזומה".
                                # 18:00-19:00 added 2026-07-14: 14:00-01:30-style shift
                                # (only flight ~23:40-00:55, break was landing jammed
                                # right before it — agent#20, לוי לילי לוטם, agent#63, agent#26).
                                _fp_proactive_windows = {("10:00", "12:00"), ("11:30", "13:30"), ("13:00", "15:00"), ("18:00", "19:00"), ("03:00", "05:00")}
                                if (_fp_s, _fp_e) in _fp_proactive_windows:
                                    _fp_note_prefix = "הפסקה יזומה"
                                else:
                                    _fp_note_prefix = "הפסקה"
                            else:
                                # window is still open (or closing right as the flight
                                # starts) — no real room to spare, break is genuinely
                                # taken because the flight is imminent. Keeps the
                                # original wording (e.g. restricted-agent#2, short 10:00-16:00
                                # shift close to shift start — "קרוב יחסית לתחילת
                                # המשמרת" — this branch does not even apply to her since
                                # her break comes from the _break_slot tail-gap fix, not
                                # this mechanism, but other short/early shifts that DO
                                # hit this branch keep the pre-flight framing).
                                _fp_b_end = task_start_m_now - _fp_walk
                                _fp_b_start = _fp_b_end - _fp_rb
                                _fp_note_prefix = "הפסקה"
                            # The break must never be claimed as starting before
                            # 02:45 (relaxed from 03:00 per user rule
                            # 2026-08-05) — e.g. 02:15 is unrealistically early,
                            # barely 15 min into a 02:00-09:30 shift. When the
                            # squeeze placement (already net of the 15-min
                            # walk-to-gate buffer) would need to start earlier
                            # than that, there is NO valid pre-flight slot —
                            # leave stage untouched so this flight's protection
                            # falls entirely to protect_early_shift_preflight_
                            # breaks (the scheduler post-pass that reassigns
                            # the flight to a night-shift/02:00-11:00 worker
                            # instead), not a geometrically-impossible early
                            # break.
                            if (_fp_s, _fp_e) == ("03:00", "05:00") and _fp_b_start < 2 * 60 + 45:
                                pass
                            else:
                                break_state[emp] = {"stage": 2, "last_m": _fp_b_end}
                                stage = 2
                                _break_slot.pop(emp, None)
                                _fp_note_suffix = "" if _fp_note_prefix == "הפסקה יזומה" else " לפני הירידה לטיסות"
                                _preflight_note[idx] = (
                                    f" [{_fp_note_prefix} {_fp_b_start // 60:02d}:{_fp_b_start % 60:02d}-"
                                    f"{_fp_b_end // 60:02d}:{_fp_b_end % 60:02d}{_fp_note_suffix}]"
                                )
        action = None

        def enough_time_from_shift_start():
            ss = clean_text(emp_row.get("תחילת משמרת", ""))
            if not is_time_text(ss):
                return True
            s = time_to_minutes(ss)
            te_local = task_end_m
            if te_local < s: te_local += 1440
            return (te_local - s) >= MIN_BETWEEN_BREAKS

        long_shift    = shift_length(emp_row) > 9 * 60
        can_break_now = (not long_shift) or enough_time_from_shift_start()

        # For non-early_morning shifts: hold the break until the preferred window opens.
        # e.g. noon shift (11:00-19:00) preferred window 13:00-15:00 → no break before 13:00.
        if stage == 0 and classify_shift(emp_row) != "early_morning":
            _pref_s_now, _ = preferred_break_window_by_shift(emp_row)
            if _pref_s_now and is_time_text(_pref_s_now):
                _pref_s_now_m = time_to_minutes(_pref_s_now)
                if task_end_m < _pref_s_now_m:
                    can_break_now = False

        # Early morning shifts (03:00–04:30 start): hold the break until after the
        # morning peak. Boarding activity peaks until ~06:30; breaking before that
        # leaves the counter short-staffed. Only allow a break after task ends ≥ 06:30.
        # Exception 1: large-enough gap (≥ 60 min) → allow break regardless.
        # Exception 2: 02:xx shifts (02:00-08:30/09:30) — break should come BEFORE
        #   flights or right after the first flight; hold only until 04:00, not 06:30.
        if classify_shift(emp_row) == "early_morning":
            _ss_local_disp = clean_text(str(emp_row.get("תחילת משמרת", "")))
            _ss_m_local_disp = time_to_minutes(_ss_local_disp) if is_time_text(_ss_local_disp) else 210
            _se_local_disp = clean_text(str(emp_row.get("סוף משמרת", "")))
            _se_m_local_disp = time_to_minutes(_se_local_disp) if is_time_text(_se_local_disp) else 0
            # SHORT 02:xx shifts (end ≤10:00): allow break from 04:00 onward.
            # A long 02:00-11:00 shift follows the 03:30-11:00 rules (hold 06:30).
            _hold_until_m = 4 * 60 if (_ss_m_local_disp < 3 * 60 and _se_m_local_disp <= 10 * 60) else 6 * 60 + 30
            if task_end_m < _hold_until_m:
                # Require ≥ 90 min gap to allow break before peak ends.
                # 60 min is borderline and lands exactly during peak — not practical.
                _gap_fits_break = gap_to_next is not None and gap_to_next >= 90
                if not _gap_fits_break:
                    can_break_now = False

        # Gap-aware slot: when a concrete break slot was chosen for this employee,
        # the break fires exactly after THAT task — not "as soon as the preferred
        # window opens" (which lands at whatever task happens to end first, or at
        # the end of the shift when the window is occupied).
        _slot_idx = _break_slot.get(emp)
        if _slot_idx is not None:
            fire_break_here = (_slot_idx == idx)
        else:
            fire_break_here = can_break_now

        # Computed BEFORE every break-consuming rule below (the compound-label
        # rule right here, the generic night rule, AND the dedicated
        # midnight-cross block further down) so ALL of them defer the same
        # way: a night-shift worker covering several PARALLEL midnight-
        # crossing tasks (e.g. a TSA supervisor on LY017 23:00-00:15 AND
        # LY015 23:05-01:05) was getting her break consumed by an EARLIER
        # rule on whichever parallel task the iteration reached FIRST (LY017,
        # the earlier-ending one) — setting stage=2 before the dedicated
        # block further down ever got to the TRUE last-ending task (LY015),
        # whose own `stage < 2` guard then silently no-oped, leaving a bare
        # "חזרה לדלפקים" with no break mentioned at all (found via real data
        # 2026-07-25: agent#46, LY015 BOS — a 10h+ shift, so it was actually
        # the "הפסקה ורענון" compound-label rule below, not just the simple
        # "night" rule, that fired first and needed the same deferral).
        _task_start_m_local = task_start_dt.hour * 60 + task_start_dt.minute
        _raw_ss = clean_text(str(emp_row.get("תחילת משמרת", ""))) if emp_row is not None else ""
        _shift_start_m = time_to_minutes(_raw_ss) if is_time_text(_raw_ss) else 0
        _is_late_evening_shift = 20 * 60 <= (_shift_start_m or 0) <= 21 * 60 + 30
        _later_cross_exists = False
        if shift_type == "night" or _is_late_evening_shift:
            for _oidx, _osdt, _orow in _emp_sorted.get(emp, []):
                if _oidx == idx:
                    continue
                _oe = to_datetime_time(_orow.get("סיום", ""))
                _os = to_datetime_time(_orow.get("התחלה", ""))
                if _oe is None or _os is None:
                    continue
                _oem = _oe.hour * 60 + _oe.minute
                _osm = _os.hour * 60 + _os.minute
                if _oem < _osm and _oem > task_end_m:  # another crossing task ends later
                    _later_cross_exists = True
                    break

        if break_label == "הפסקה ורענון":
            if stage == 0 and fire_break_here and not _later_cross_exists:
                action = "break"
            elif stage == 1 and time_since_last >= MIN_BETWEEN_BREAKS and not _later_cross_exists:
                action = "refresh"
        elif break_label in ("הפסקה", "רענون"):
            if stage == 0 and fire_break_here and not _later_cross_exists:
                action = break_label

        # `fire_break_here` falls back to `can_break_now`, which is
        # unconditionally True for any shift ≤9h (see its definition above) —
        # it never actually checks there's ENOUGH TIME left for the break.
        # Combined with the min-duration guard further below being skipped
        # entirely for the LAST task (by design, since "return" wording takes
        # over there), a worker whose last task ends with almost no time
        # before shift end got a fabricated "הפסקה וחזרה הביתה" with zero
        # realistic room for it (user rule 2026-08-05, found via real data:
        # TL#31, 02:00-09:30, last flight ends 09:05 — only 25 min before
        # her 09:30 shift end, nowhere near the 45-min requirement). Verify
        # here specifically for the last task, where nothing else does.
        if is_last_task and action in ("break", "הפסקה", "refresh", "רענון"):
            _last_req_mins = (required_refresh(emp_row) or 20) if action in ("refresh", "רענון") else (required_break(emp_row) or 45)
            _se_str_last = clean_text(str(emp_row.get("סוף משמרת", ""))) if emp_row is not None else ""
            if is_time_text(_se_str_last):
                _se_m_last = time_to_minutes(_se_str_last)
                _remaining_last = (_se_m_last - task_end_m) % 1440
                if _remaining_last < _last_req_mins:
                    action = None

        # Night/late-evening shift employee finishing a midnight-crossing flight
        # (role starts ≥22:00, departs after midnight) → give a break
        # + return-to-counters after the flight, regardless of current break stage.
        # ONE break per night block: a TSA supervisor covers several night flights
        # in PARALLEL (e.g. LY27 22:30-00:30, LY1 22:30-01:00, LY5 22:30-01:05 —
        # same worker, overlapping windows). The chip fires only on the task that
        # ends LAST among the worker's midnight-crossing tasks, so the block gets
        # a single break at its true end instead of one per parallel task.
        _is_midnight_cross_flight = (
            (shift_type == "night" or _is_late_evening_shift)
            and task_end_m < _task_start_m_local  # end < start ↔ crosses midnight
        )

        if (shift_type == "night" and stage == 0 and break_label and action is None
                and fire_break_here and not _later_cross_exists):
            action = "break" if "הפסקה" in break_label else "רענון"

        _force_return_after_break = False
        # ר"צ variant of the above: stay in the hall to help instead of going
        # back to the counters (see the idle-gap trigger further down).
        _hall_help_after_break = False
        # Set only for the night-block trigger below, never for the idle-gap
        # trigger further down — the two need different wording when a real
        # next task exists (see the `continuation` assignment below).
        _night_block_break = False
        # stage < 2 guard: a worker already marked "break already taken" (e.g. a
        # transfer worker who arrives at this terminal already post-break — see
        # the _transfer_chip pre-break logic above) must not get a SECOND break
        # here just because their task happens to cross midnight.
        # action in (None, refresh): a רענון assigned earlier must NOT pre-empt
        # this — after the night-flight block the worker is owed the full break,
        # and the refresh is the weaker claim on the same stretch (user rule
        # 2026-08-21, agent#11: LY083 ended 00:55 with nothing until 03:55
        # and she was shown "רענון והמשך ל־321", while her break sat unused at
        # the tail of the shift, 06:05-06:50).
        if (_is_midnight_cross_flight and break_label
                and (action is None or action in ("refresh", "רענון")) and stage < 2):
            if not _later_cross_exists:
                action = "break"
                # Per the confirmed domain rule: a night worker who staffed the
                # night flights at shift start gets "הפסקה וחזרה לדלפקים" after
                # the block — regardless of whether early-morning tasks follow
                # later or this is their last assigned task. A genuine night
                # shift (e.g. 21:00-07:00) continuing past the night-flight
                # block means the worker is NOT done for the day just because
                # no further task is assigned yet — they return to the
                # counters (or go home, if the shift is genuinely about to
                # end — return_text_by_shift's own ≤30-min-left check already
                # handles that distinction). The `if not is_last_task` guard
                # here used to suppress "וחזרה" specifically when it WAS their
                # last task, producing a bare "חזרה" with no break mention at
                # all — the exact gap the user reported 2026-07-25 (real data:
                # TSA-inspector#3, TSA inspector consolidated onto pier D's night
                # block, showed plain "חזרה לדלפקים" with no הפסקה).
                _force_return_after_break = True
                _night_block_break = True
                # This IS their break; a slot reserved for later in the shift
                # would otherwise show a SECOND one (agent#11 also had
                # 06:05-06:50 pencilled in at the tail).
                _break_slot.pop(str(emp).strip(), None)

        # Late-evening shift (20:xx start) with NO night flight:
        # fire a break at the end of their last task (= counter closing time).
        _is_late_eve_no_night = (
            _is_late_evening_shift
            and emp not in _has_night_flight_emps
            and is_last_task
            and stage < 2
            and break_label  # employee is entitled to a break
        )
        if _is_late_eve_no_night and action is None:
            action = "break"

        # Fallback rules below only apply when no explicit slot was chosen —
        # otherwise they would fire the break at an earlier, unchosen gap.
        if action is None and _slot_idx is None and is_agent and gap_to_next is not None and gap_to_next >= 30:
            if stage == 0 and break_label and can_break_now:
                action = "break" if "הפסקה" in break_label else "רענון"

        # Urgent break: employee worked ≥ 3 h continuously with no break opportunity
        # and there's a gap of ≥ 20 min to the next task — show break even if gap < 45 min.
        # Covers cases like 30-min gap between back-to-back flights where the scheduler
        # couldn't find room for a proper break but a short breather is better than nothing.
        if action is None and _slot_idx is None and not is_last_task and stage == 0 and break_label:
            _rb_urgent = required_break(emp_row) or 45
            if gap_to_next is not None and gap_to_next >= 20:
                # Check if worked ≥ 3 h since shift start with no gap >= rb
                _ss_urg = clean_text(str(emp_row.get("תחילת משמרת", ""))) if emp_row is not None else ""
                if is_time_text(_ss_urg):
                    _ss_urg_m = time_to_minutes(_ss_urg)
                    _tasks_so_far = [
                        t for t in timed_df[timed_df["עובד"] == emp].itertuples()
                        if is_time_text(clean_text(str(getattr(t, "התחלה", ""))))
                        and time_to_minutes(clean_text(str(getattr(t, "התחלה", "")))) <= task_end_m
                    ]
                    _total_worked = sum(
                        (time_to_minutes(clean_text(str(getattr(t, "סיום", "")))) -
                         time_to_minutes(clean_text(str(getattr(t, "התחלה", "")))))
                        for t in _tasks_so_far
                        if is_time_text(clean_text(str(getattr(t, "סיום", ""))))
                    )
                    if _total_worked >= 3 * 60:
                        action = "break" if "הפסקה" in break_label else "רענון"

        # Guard: never show a break between two tasks if there isn't enough
        # time for the break before the next task starts.
        # e.g. task ends 06:30, next task starts 06:45 → 15 min gap < 45 min break
        # A רענון only needs its own (shorter) duration, not the full הפסקה
        # duration — using required_break (45 min) for BOTH action types meant
        # a genuine 20-30 min gap (plenty for a רענון) was being wrongly
        # rejected, silently suppressing the רענון entirely for workers whose
        # only free gap was in that range (found via real data: agent#29,
        # TL#35, agent#45 — all had a real ~30-min gap, enough for the
        # 20-min רענון they're entitled to, but it never showed at all).
        # stage 1 = the הפסקה half of a "הפסקה ורענון" entitlement is already
        # spent and only the רענון is left. Any later action must therefore be a
        # רענון — several branches above pick "break" without consulting stage,
        # which gave agent#11 a SECOND full break at the tail of her shift
        # (00:55-01:40 after the night block, then 06:05-06:50 again).
        if emp in _break_shown and action in ("break", "הפסקה"):
            action = "refresh"

        if action in ("break", "הפסקה", "refresh", "רענון") and not is_last_task:
            if action in ("refresh", "רענון"):
                _req_break_mins = required_refresh(emp_row) or 20
            else:
                _req_break_mins = required_break(emp_row) or 45
            # A mid-shift רענון that eats the ENTIRE gap (zero or near-zero
            # buffer before the next task's gate) is worse than no רענון at
            # all — it shows the worker as "refreshed and ready" at the exact
            # minute they need to already be at a different gate, with no
            # margin for the walk. In a real gap this tight, don't consume
            # the רענון here at all: leave `action` unset so the task falls
            # through to a plain "המשך" continuation, and this worker's
            # entitlement stays open for a later, genuinely slack gap (user
            # rule 2026-09-14, TL#4: LY5193→LY5187, רענון ending
            # the same minute she had to open the next gate — "עדיף לתת רווח
            # בין הטיסות במקום רענון ואת הרענון לתת אחרי הטיסה האחרונה").
            # Not applied to a full הפסקה — its own ≥60-min-slack branch just
            # below already handles that case differently.
            _REFRESH_BUFFER_MIN = 15
            if (action in ("refresh", "רענון") and gap_to_next is not None
                    and 0 <= (gap_to_next - _req_break_mins) < _REFRESH_BUFFER_MIN):
                action = None
            elif gap_to_next is not None and gap_to_next < _req_break_mins:
                action = None
            elif (action in ("break", "הפסקה") and gap_to_next is not None
                    and (gap_to_next - _req_break_mins) >= 60):
                # The break itself only accounts for part of the gap before the
                # next task — a worker whose break ends an hour or more before
                # they're needed again is genuinely idle in between and should
                # return to the counters, not be shown as still "on break" with
                # no indication of what happens for the next ~hour+ (user:
                # "ניתן רווח מאוד גדול בין ההפסקה לטיסה הבאה... להחזיר
                # לדלפקים" — found via real data: agent#41, break 06:25-07:10,
                # next flight not until 09:05, ~2h idle shown as bare "הפסקה"
                # with the next flight only appearing on its OWN task row, no
                # hint of the return in between).
                _force_return_after_break = True
                # A ר"צ is NOT sent back to the counters for that idle stretch
                # — when nothing can be scheduled for them they stay airside
                # and help in the departure hall until their next flight (user
                # rule 2026-08-06, TL#41: "במידה ואין שום ברירה אחרת, יש
                # להשאיר אותו באולם לעזרה — יש לציין את זה ברצף המשימות שלו
                # כ'עזרה באולם'/'הפסקה ועזרה באולם' ואז את המשימה הבאה שלו").
                # NOT for a night shift: a night worker genuinely goes back to
                # the counters between the night block and the pre-dawn
                # flights, and keeps the ordinary "הפסקה וחזרה" wording (user
                # rule 2026-08-06, TL#15).
                # Terminal-1 workers are excluded for now — their gaps are a
                # separate, still-open piece of work (user 2026-08-06: "אל
                # תחיל את כלל ההפסקה ועזרה באולם על עובדי טרמינל 1, שים אותם
                # בצד ונתקן את זה בשלב מאוחר יותר"). They keep the ordinary
                # "הפסקה וחזרה לדלפקים" wording.
                if (emp_row is not None
                        and clean_text(str(emp_row.get("ראש צוות", ""))) == "כן"
                        and clean_text(str(emp_row.get("טרמינל", ""))) != "1"
                        and not is_night_shift_for_return_rule(emp_row)):
                    _hall_help_after_break = True

        def do_action(act, continuation):
            nonlocal stage
            label = "הפסקה" if act == "break" else "רענון" if act == "refresh" else act
            if act in ("break", "הפסקה"):
                break_state[emp] = {"stage": 1 if break_label == "הפסקה ורענון" else 2, "last_m": task_end_m}
                _break_shown.add(emp)
            elif act in ("refresh", "רענון"):
                break_state[emp] = {"stage": 2, "last_m": task_end_m}
            return f"{label} ו{continuation}" if continuation else label

        return_text = return_text_by_shift(emp_row, task_end_dt)

        if is_last_task:
            if break_label == "הפסקה ורענון" and stage == 1 and time_since_last >= MIN_BETWEEN_BREAKS:
                # Only fire the רענון here if real time actually remains AFTER
                # this task before the shift ends — stage==1 with the night-
                # shift blanket rule's last_m=-9999 sentinel makes
                # time_since_last always satisfy MIN_BETWEEN_BREAKS regardless
                # of whether the LAST task runs right up to (or past) shift
                # end, which used to fire a "רענון וחזרה" chip with zero real
                # minutes for it (found via real data: agent#46, shift
                # 21:00-07:00, last flight LY5487 ends exactly at 07:00 —
                # shown "רענון וחזרה הביתה" with no time left for a refresh).
                # MUST explicitly override (not just conditionally set) —
                # the identical break_label/stage/time_since_last condition
                # already fired earlier in this same per-task pass (the
                # generic "הפסקה ורענון" block above) and set action="refresh"
                # there with NO room check at all; leaving that untouched here
                # would silently keep the wrong action when room is missing.
                _se_str_lt = clean_text(str(emp_row.get("סוף משמרת", ""))) if emp_row is not None else ""
                _req_refresh_mins = required_refresh(emp_row) or 20
                _has_room_after = True
                if is_time_text(_se_str_lt):
                    _se_m_lt = time_to_minutes(_se_str_lt)
                    _remaining_m = (_se_m_lt - task_end_m) % 1440
                    _has_room_after = _remaining_m >= _req_refresh_mins
                action = "refresh" if _has_room_after else None
            continuation = return_text
        else:
            # Mid-shift break: never show "חזרה לדלפקים" here.
            # If the employee has another task after the break they are still on the
            # floor, so "חזרה" would be a lie. "חזרה" only belongs on the last task.
            # Exception: after a night-flight block (midnight-cross break) the worker
            # genuinely returns to the counters before their early-morning tasks —
            # UNLESS a real next task already follows the break, in which case that
            # next task (not "חזרה") is what's actually true (found via real data
            # 2026-07-28: TL#5 showed "הפסקה וחזרה לדלפקים" after her night
            # block even though she goes straight on to another flight, not the
            # counters). The idle-gap trigger below (line ~1007) is a DIFFERENT
            # scenario — genuinely idle for an hour+ before the next task — and
            # must keep forcing "חזרה" regardless of _pre_next.
            if _force_return_after_break:
                # ...but only when the break actually runs INTO the next task.
                # When it ends an hour or more before it, the worker really does
                # go back to the counters and comes down again (user rule
                # 2026-08-21, agent#11: LY083 ends 00:55, break to 01:40,
                # LY321 not until 03:55 — "הפסקה וחזרה לדלפקים ורק אז ירידה
                # מחודשת"). TL#5's case, which this exception was
                # written for, has the next task right after the break.
                _nb_idle_after_break = None
                if _night_block_break and gap_to_next is not None:
                    _nb_idle_after_break = gap_to_next - (required_break(emp_row) or 45)
                if (_night_block_break and not is_last_task and _pre_next
                        and (_nb_idle_after_break is None or _nb_idle_after_break < 60)):
                    continuation = f"המשך ל־{_pre_next}"
                elif _hall_help_after_break:
                    # ר"צ — stays airside helping in the hall, not sent back
                    # to the counters (see the flag's comment above).
                    continuation = "עזרה באולם"
                else:
                    continuation = "חזרה לדלפקים"
            else:
                continuation = None

        # Terminal-transfer chip: on the last task before the transfer.
        # When the break travels WITH the transfer, the chip reads "הפסקה ומעבר";
        # when the break was already taken pre-flight (see _preflight_note above),
        # the worker is just moving — no second break implied.
        _tc = _transfer_chip.get(emp)
        if _tc is not None and _tc[0] == idx:
            action = None
            break_state[emp] = {"stage": 2, "last_m": task_end_m}
            stage = 2
            _tc_with_break = _tc[3] if len(_tc) > 3 else True
            _tc_label = "הפסקה ומעבר" if _tc_with_break else "מעבר"
            next_text = f"{_tc_label} לטרמינל {_tc[1]}" + (f" (הגעה {_tc[2]})" if _tc[2] else "")
        elif action:
            # A mid-shift break/רענון with a real next task coming shouldn't
            # show a bare label with no indication of what follows — found via
            # real data 2026-07-17: agent#8, mid-shift רענון with a next
            # flight coming, showed just "רענון" with no hint she wasn't done
            # for the day; and 2026-07-28: TL#30/עלוש שחר on LY347, mid-
            # shift break with a real next task after it, showed bare "הפסקה"
            # the same way — so the same fix now applies to "break"/"הפסקה"
            # too, not just refresh. Only via this dedicated variable (not the
            # shared `continuation` used by the non-action tiers below) — an
            # earlier attempt broadened `continuation` itself and silently
            # changed unrelated locked patterns' wording (e.g. "המשך ישיר
            # ל־X" → "המשך ל־X" for tight <=15-min gaps) by preempting the
            # `elif continuation` / `elif _pre_gap<=15` tiers further down.
            _action_continuation = continuation
            if not _action_continuation and not is_last_task and _pre_next:
                _action_continuation = f"המשך ל־{_pre_next}"
            next_text = do_action(action, _action_continuation)
        elif continuation:
            next_text = continuation
        elif _pre_gap is not None and _pre_gap <= 15 and _pre_next:
            # הטיסה הבאה צמודה (עד 15 דק') — אין באמת חזרה לדלפקים באמצע;
            # להציג את ההמשך הישיר כדי שלא ישלחו את העובד/ת לדלפקים בטעות.
            next_text = f"המשך ישיר ל־{_pre_next}"
        elif not is_last_task and _pre_next:
            # A genuine mid-shift gap too big to count as "direct" (>15 min)
            # but the worker still has a LATER task today — this must NOT
            # fall through to return_text (meant for end-of-shift), or it
            # wrongly claims she's returning to the counters/going home when
            # she isn't (found via real data 2026-07-28: TL#9,
            # LY5465→LY345 a 25-min gap, showed "חזרה לדלפקים" even though
            # she continues on to LY345 and several more flights after that).
            next_text = f"המשך ל־{_pre_next}"
        else:
            next_text = return_text

        task_start_m   = task_start_dt.hour * 60 + task_start_dt.minute
        # Break info on this screen is shown ONLY for the three cases the
        # assignment itself is about: a break between flights, a break before
        # returning to the counters, or a break tied to a terminal transfer —
        # all three are already carried by `action`/`next_text` above. A break
        # taken BEFORE the worker's first flight (the _preflight_note cases)
        # or a generic "must break by HH:MM" advisory unrelated to any actual
        # scheduled transition is not relevant to THIS assignment and must not
        # be shown here (user rule 2026-07-25). _preflight_note's break_state/
        # stage side effects still apply — only its display text is dropped.
        if action in ("break", "הפסקה"):
            # Break happens AFTER this task — show when it ENDS, not a past deadline
            _break_dur = required_break(emp_row) or 45
            _break_end_m = (task_end_m + _break_dur) % 1440
            deadline_suffix = f" [הפסקה עד {_break_end_m // 60:02d}:{_break_end_m % 60:02d}]"
        else:
            deadline_suffix = ""

        # "המשך יבוא" — the segment ends here but this worker's shift doesn't.
        # Only on the genuinely last built task, and never over a real action
        # (a break/רענון still has to happen at its scheduled time).
        if is_last_task and built_until_minutes is not None and not action and emp_row is not None:
            _se_cont = clean_text(str(emp_row.get("סוף משמרת", "")))
            if is_time_text(_se_cont):
                _op = lambda _m: (_m - 180) % 1440      # operational-day ordering
                _se_m_cont = time_to_minutes(_se_cont)
                # Only when there is genuinely room for another flight after
                # this one. The shortest task is a narrow-body דייל, on station
                # 50 min before departure, plus the 15-min walk down to the
                # gate — under that, the worker goes back to the counters and
                # "המשך יבוא" would just be wrong (found via real data
                # 2026-08-09: TL#25 / TL#27 finish LY5467 at 11:50 with
                # a 12:30 shift end — 40 min, no room for anything).
                _room = (_se_m_cont - task_end_m) % 1440
                if _op(_se_m_cont) > _op(built_until_minutes) and _room >= _MIN_ROOM_FOR_NEXT_TASK:
                    next_text = "המשך יבוא"
                    deadline_suffix = ""

        _worker_text[idx] = f"{emp} - {next_text}{shift_suffix}{deadline_suffix}"
        _region_cont[idx] = next_text

    # Batch-write collected results (one Series assignment per column)
    import pandas as _pd
    if _worker_text:
        df.loc[list(_worker_text.keys()), "טקסט עובד"] = list(_worker_text.values())
    if _region_cont:
        df.loc[list(_region_cont.keys()), "המשך אזורי"] = list(_region_cont.values())
    return df


# =========================
# OUTPUT TABLES
# =========================

def build_output_table(flights_df, result_labeled, employees_df):
    rows = []

    # Pre-build employee info dict once — avoids O(n_employees) scan per task
    _out_emp: dict = {}   # name -> {"shift": "HH:MM-HH:MM", "gender": "M/F"}
    _gender_col = None
    for _, _er in employees_df.iterrows():
        _en = clean_text(str(_er.get("שם", "")))
        if not _en:
            continue
        if _gender_col is None:
            _gender_col = next((c for c in employees_df.columns if clean_text(c) in {"מין", "gender", "זכר/נקבה", "מגדר"}), "")
        _gv = clean_text(str(_er.get(_gender_col, ""))) if _gender_col else ""

        def _yn_er(col):
            return clean_text(str(_er.get(col, ""))) == "כן"

        _out_emp[_en] = {
            "row": _er,
            "gender": _gv.upper(),
            # (ט) = דייל בטרייני (מוצמד); (ס) = ילד עובדים / פורש
            "trainee_att": _yn_er("דייל בטרייני"),
            "restricted": _yn_er("ילד עובדים") or _yn_er("ילדי עובדים") or _yn_er("פורשים"),
        }

    def _gender_role(role, worker):
        base = normalize_role_label(role)
        if not worker or "❌" in worker:
            return base
        _info = _out_emp.get(clean_text(worker))
        if not _info:
            return base
        # role_label_for keeps every gendered form in ONE place — and writes a
        # team leader as ר"צ for everyone (there is no "ראשת צוות").
        return role_label_for(base, _info["gender"] in MALE_VALUES)

    def _shift_text(worker, task_start=None):
        if not worker or "❌" in worker:
            return ""
        _info = _out_emp.get(clean_text(worker))
        if not _info:
            return ""
        return _full_shift_span_text(_info["row"], task_start=task_start)

    flights_df = flights_df.copy()
    flights_df["_flight_key"] = flights_df["טיסה"].apply(flight_key)
    flights_df = flights_df.drop_duplicates(subset=["_flight_key"], keep="first").drop(columns=["_flight_key"])

    # מיון לפי שעת המראה עם ציר 02:00 — טיסות לילה (00:xx, 01:xx) בסוף
    _SORT_PIVOT = 2 * 60
    def _dep_sort_key(val):
        dep = clean_text(str(val))
        try:
            h, m = dep.split(":")[:2]
            return (int(h) * 60 + int(m) - _SORT_PIVOT) % 1440
        except Exception:
            return 9999
    flights_df = flights_df.sort_values(
        by="המראה", key=lambda col: col.apply(_dep_sort_key)
    ).reset_index(drop=True)

    for _, flight in flights_df.iterrows():
        fnum     = str(flight["טיסה"]).strip()
        dep      = clean_text(flight.get("המראה", ""))
        boarding = clean_text(flight.get("בורדינג", ""))
        aircraft = clean_text(flight.get("סוג מטוס", ""))
        reg      = clean_text(flight.get("רישוי", ""))
        reqs     = requirements_text(flight)

        tasks = result_labeled[result_labeled["טיסה"].astype(str).str.strip() == fnum]

        management_lines = []
        agents_lines     = []
        management_roles = []
        agent_roles      = []

        for _, task in tasks.iterrows():
            role       = str(task["תפקיד"])
            base_role  = normalize_role_label(role)
            worker     = str(task.get("עובד", ""))
            text_value = str(task["טקסט עובד"])

            display_role = _gender_role(role, worker)

            if "❌" not in worker and worker and " - " in text_value:
                name_part, rest = text_value.split(" - ", 1)
                text_value = f"{name_part} ({display_role}) - {rest}"
            elif "❌" not in worker and worker:
                text_value = f"{worker} ({display_role})"

            shift = _shift_text(worker, task_start=task.get("התחלה"))
            if shift and "❌" not in worker and f"({shift})" not in text_value:
                text_value = f"{text_value} ({shift})"

            # "הגעה באיחור" — a forced (הכרח שיבוץ) assignment where the worker
            # is kept on a prior flight running past this slot's floor time, so
            # they reach this flight late. User rule 2026-07-22.
            if (str(task.get("הגעה באיחור", "")).strip() in ("True", "כן", "1")
                    and "❌" not in worker and worker):
                text_value = f"{text_value} — באיחור ל{fnum} {base_role}"

            # סימוני קטגוריה אחרי השם: (ט) = דייל בטרייני, (ס) = ילד עובדים/פורש
            _winfo = _out_emp.get(clean_text(worker))
            _is_trainee_att_task = False
            if _winfo and "❌" not in worker and worker:
                if _winfo.get("trainee_att"):
                    text_value = text_value.replace(worker, f"{worker} (ט)", 1)
                    _is_trainee_att_task = True
                elif _winfo.get("restricted"):
                    text_value = text_value.replace(worker, f"{worker} (ס)", 1)

            item = {"text": text_value, "role": base_role,
                    "trainee_att": _is_trainee_att_task,
                    "worker": worker}

            if "ראש צוות" in role or "טרייני" in role or "מתאם" in role or "מפקח" in role:
                management_lines.append(item)
                if base_role not in management_roles:
                    management_roles.append(base_role)
            elif "דייל" in role or "שומר" in role or "בדיקת טרייני" in role:
                agents_lines.append(item)
                if base_role not in agent_roles:
                    agent_roles.append(base_role)

        # דייל-בטרייני מוצג צמוד לדייל שאליו הוא מוצמד (מיד אחרי הדייל הוותיק
        # הראשון בטיסה). טרייני בלי דייל ותיק לצידו — מסומן במפורש.
        _t_items = [x for x in agents_lines if x.get("trainee_att") and x["role"] == "דייל"]
        if _t_items:
            _rest = [x for x in agents_lines if x not in _t_items]
            _anchor = next(
                (i for i, x in enumerate(_rest) if x["role"] == "דייל" and not x.get("trainee_att")),
                None,
            )
            if _anchor is not None:
                agents_lines = _rest[:_anchor + 1] + _t_items + _rest[_anchor + 1:]
            else:
                for _ti in _t_items:
                    # "מוצמד/מוצמדת" follows the trainee's own gender; the
                    # veteran they should be attached to is written generically.
                    _tw = clean_text(str(_ti.get("worker", "")))
                    _tm = (_out_emp.get(_tw, {}).get("gender", "") in MALE_VALUES)
                    _ti["text"] = (f"{_ti['text']}  ⚠️ "
                                   + ("לא מוצמד לדייל ותיק" if _tm
                                      else "לא מוצמדת לדיילת ותיקה"))
                agents_lines = _rest + _t_items

        _gate_or_term = clean_text(flight.get("גייט", "")) or clean_text(str(flight.get("שלוחה", "")))
        rows.append({
            "מספר טיסה": fnum,
            "יעד": clean_text(flight["יעד"]),
            "גייט": clean_text(flight.get("גייט", "")),
            "טרמינל": "1" if get_terminal(_gate_or_term) == "1" else "3",
            "זמנים": f"{dep} ({boarding})" if boarding else dep,
            "מטוס/רישוי": f"{aircraft}\n{reg}".strip(),
            "סוג גוף": get_body_type(flight),
            "חכור": "כן" if is_leased_aircraft(flight) else "",
            "פרי": "כן" if is_ferry_flight(flight) else "",
            "תפקידים דרושים": " | ".join(reqs),
            "כותרת ניהול": " / ".join(management_roles) if management_roles else "ניהול",
            "כותרת דיילים": " / ".join(agent_roles) if agent_roles else "דיילים",
            "דיילים / שומר TSA": "\n".join([f"{x['role']}||{x['text']}" for x in agents_lines]),
            "ראש צוות / מתאם תורים / מפקח TSA": "\n".join([f"{x['role']}||{x['text']}" for x in management_lines]),
        })

    return pd.DataFrame(rows)


# =============================================================
# PEAK ANALYSIS — דלפקים ואולם יציאה
# =============================================================

# קיבולת נוסעים לפי סוג מטוס (מספר ישיבות אל-על טיפוסי)
_AIRCRAFT_PAX = {
    "787": 290, "789": 290, "788": 290,
    "737": 180, "738": 189, "73h": 189,
    "777": 350, "77w": 350, "773": 350,
    "320": 180, "321": 220, "319": 145,
    "330": 295, "332": 295, "333": 310,
}

def _aircraft_pax(aircraft):
    """מספר נוסעים משוער לפי סוג מטוס."""
    ac = clean_text(str(aircraft)).replace(" ", "").lower()
    for k, v in _AIRCRAFT_PAX.items():
        if ac.startswith(k) or ac == k:
            return v
    # חיפוש חלקי
    for k, v in _AIRCRAFT_PAX.items():
        if k in ac:
            return v
    return 160  # ברירת מחדל


def build_peak_analysis(flights_df):
    """
    מחשב עומס לאורך היום:
      - דלפקים: עומס בנוסעים — מ-(המראה - 4h) עד שעת הבורדינג
      - אולם:   עומס בטיסות  — מ-שעת הבורדינג עד שעת ההמראה
    מחזיר dict עם ציר זמן, עומסים, רשימת כל הפיקים ומידע נוסף.
    """
    CHECKIN_OPEN_BEFORE = 4 * 60
    COUNTER_OPEN_BEFORE = 3 * 60

    def _parse(val):
        s = clean_text(str(val))
        if not s or s in ("-", "nan"):
            return None
        try:
            parts = s.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        except Exception:
            return None

    def _fmt(m):
        if m is None:
            return "—"
        return f"{(m % 1440) // 60:02d}:{(m % 1440) % 60:02d}"

    flight_windows = []
    for _, row in flights_df.iterrows():
        fnum_raw = clean_text(str(row.get("טיסה", ""))).replace(" ", "").lstrip("LYly")
        if len(fnum_raw) >= 3 and fnum_raw.startswith("8") and fnum_raw[:3].isdigit():
            continue

        dep_m      = _parse(row.get("המראה", ""))
        boarding_m = _parse(row.get("בורדינג", ""))
        if dep_m is None:
            continue

        if boarding_m is None:
            boarding_m = dep_m - 45

        if dep_m < 4 * 60:
            dep_m += 1440
        if boarding_m < dep_m - 8 * 60:
            boarding_m += 1440

        # נוסעים בפועל מ-FIDS; נפול לקיבולת מטוס אם חסר
        pax_raw = clean_text(str(row.get("נוסעים", "")))
        try:
            pax = int(float(pax_raw)) if pax_raw and pax_raw not in ("-", "nan") else None
        except (ValueError, TypeError):
            pax = None
        pax_from_fids = pax is not None and pax > 0
        if not pax_from_fids:
            pax = _aircraft_pax(row.get("סוג מטוס", ""))
        flight_windows.append({
            "טיסה":          str(row.get("טיסה", "")).strip(),
            "יעד":           str(row.get("יעד", "")).strip(),
            "pax":           pax,
            "pax_from_fids": pax_from_fids,
            "dep_m":        dep_m,
            "boarding_m":   boarding_m,
            "checkin_open": dep_m - CHECKIN_OPEN_BEFORE,
            "checkin_close":boarding_m,
        })

    if not flight_windows:
        return None

    first_dep     = min(f["dep_m"]      for f in flight_windows)
    last_board    = max(f["boarding_m"] for f in flight_windows)
    counter_open  = first_dep - COUNTER_OPEN_BEFORE
    counter_close = last_board

    step   = 5
    slots  = list(range(counter_open, counter_close + step, step))

    counter_load = []  # נוסעים
    lounge_load  = []  # טיסות
    for t in slots:
        c_pax = sum(
            f["pax"] for f in flight_windows
            if max(f["checkin_open"], counter_open) <= t < f["checkin_close"]
        )
        l_cnt = sum(
            1 for f in flight_windows
            if f["boarding_m"] <= t < f["dep_m"]
        )
        counter_load.append(c_pax)
        lounge_load.append(l_cnt)

    def _peaks_by_zero_gaps(load_list):
        """פיקים לפי אזורים רצופים עם עומס > 0 — מתאים לאולם שיש בו פערים טבעיים."""
        peaks = []
        in_region = False
        region_start = None
        for i, v in enumerate(load_list):
            if v >= 1 and not in_region:
                in_region = True
                region_start = i
            elif v < 1 and in_region:
                seg = load_list[region_start:i]
                peak_val = max(seg)
                peak_idx = seg.index(peak_val)
                peaks.append({
                    "start":     slots[region_start],
                    "end":       slots[i - 1] + step,
                    "peak_val":  peak_val,
                    "peak_time": slots[region_start + peak_idx],
                })
                in_region = False
        if in_region:
            seg = load_list[region_start:]
            peak_val = max(seg)
            peak_idx = seg.index(peak_val)
            peaks.append({
                "start":     slots[region_start],
                "end":       slots[-1] + step,
                "peak_val":  peak_val,
                "peak_time": slots[region_start + peak_idx],
            })
        return peaks

    def _peaks_by_valleys(load_list, smooth_w=8, min_prominence_pct=0.22, min_gap=10):
        """
        פיקים לפי גילוי עמקים מקומיים — מתאים לדלפקים שהעומס כמעט אף פעם לא 0.
        smooth_w       — רוחב חלון החלקה (slots, כל slot=5 דק')
        min_prominence — עומק מינימלי של עמק ביחס לשיא הסביבתי
        min_gap        — מרחק מינימלי בין עמקים (slots)
        """
        n = len(load_list)
        if n < 3 or max(load_list, default=0) == 0:
            return []

        # החלקה
        half = smooth_w // 2
        smoothed = []
        for i in range(n):
            win = load_list[max(0, i - half): i + half + 1]
            smoothed.append(sum(win) / len(win))

        max_sm = max(smoothed)
        min_prom = max_sm * min_prominence_pct

        # מינימום מקומי
        raw_valleys = []
        for i in range(1, n - 1):
            if smoothed[i] <= smoothed[i - 1] and smoothed[i] <= smoothed[i + 1]:
                look = 18  # ~90 דק' לכל כיוון
                lp = max(smoothed[max(0, i - look): i + 1])
                rp = max(smoothed[i: min(n, i + look)])
                prom = min(lp, rp) - smoothed[i]
                if prom >= min_prom:
                    raw_valleys.append(i)

        # מיזוג עמקים קרובים — שמור את העמוק ביותר
        valleys = [0]
        for v in raw_valleys:
            if v - valleys[-1] < min_gap:
                if smoothed[v] < smoothed[valleys[-1]]:
                    valleys[-1] = v
            else:
                valleys.append(v)
        valleys.append(n)

        # בנה פיק לכל קטע בין עמקים
        peaks = []
        for i in range(len(valleys) - 1):
            s, e = valleys[i], valleys[i + 1]
            seg = load_list[s:e]
            if not seg or max(seg) == 0:
                continue
            peak_val = max(seg)
            peak_idx = seg.index(peak_val)
            peaks.append({
                "start":     slots[s],
                "end":       slots[min(e - 1, n - 1)] + step,
                "peak_val":  peak_val,
                "peak_time": slots[s + peak_idx],
            })
        return peaks

    COUNTER_PEAK_THRESHOLD = 2000   # נוסעים
    LOUNGE_PEAK_THRESHOLD  = 6      # טיסות

    def _peaks_by_threshold(load_list, threshold):
        """כל תקופה רצופה שבה העומס מעל הסף — פיק נפרד."""
        peaks = []
        in_peak = False
        region_start = None
        for i, v in enumerate(load_list):
            if v > threshold and not in_peak:
                in_peak = True
                region_start = i
            elif v <= threshold and in_peak:
                seg = load_list[region_start:i]
                peak_val = max(seg)
                peaks.append({
                    "start":     slots[region_start],
                    "end":       slots[i - 1] + step,
                    "peak_val":  peak_val,
                    "peak_time": slots[region_start + seg.index(peak_val)],
                })
                in_peak = False
        if in_peak:
            seg = load_list[region_start:]
            peak_val = max(seg)
            peaks.append({
                "start":     slots[region_start],
                "end":       slots[-1] + step,
                "peak_val":  peak_val,
                "peak_time": slots[region_start + seg.index(peak_val)],
            })
        return peaks

    counter_peaks = _peaks_by_threshold(counter_load, COUNTER_PEAK_THRESHOLD)
    lounge_peaks  = _peaks_by_threshold(lounge_load,  LOUNGE_PEAK_THRESHOLD)
    fids_pax_count = sum(1 for f in flight_windows if f.get("pax_from_fids"))

    return {
        "slots":           slots,
        "counter":         counter_load,
        "lounge":          lounge_load,
        "counter_open":    counter_open,
        "counter_close":   counter_close,
        "counter_peaks":   counter_peaks,
        "lounge_peaks":    lounge_peaks,
        "flights":         flight_windows,
        "fmt":             _fmt,
        "fids_pax_count":  fids_pax_count,
        "total_flights":   len(flight_windows),
    }


def render_peak_analysis(peak):
    """מציג את תוצאות ניתוח הפיקים ב-Streamlit."""
    if peak is None:
        st.info("אין נתוני טיסות לחישוב פיק.")
        return


    fmt = peak["fmt"]

    # ── גרף ציר זמן ──────────────────────────────────────────────────────
    import json

    labels     = [fmt(s) for s in peak["slots"]]
    tick_every = 6
    tick_labels = [lbl if i % tick_every == 0 else "" for i, lbl in enumerate(labels)]

    # בנה רשימות צבע: פיקים בצבע עמוק
    def _peak_colors(load_list, peaks, peak_color, normal_color):
        colors = [normal_color] * len(load_list)
        for pk in peaks:
            s_idx = next((i for i, s in enumerate(peak["slots"]) if s == pk["start"]), None)
            e_idx = next((i for i, s in enumerate(peak["slots"]) if s >= pk["end"]), len(peak["slots"]))
            if s_idx is not None:
                for j in range(s_idx, e_idx):
                    colors[j] = peak_color
        return colors

    c_colors = _peak_colors(
        peak["counter"], peak["counter_peaks"],
        "rgba(92,152,212,0.9)", "rgba(92,152,212,0.3)"
    )
    l_colors = _peak_colors(
        peak["lounge"],  peak["lounge_peaks"],
        "rgba(80,180,100,0.9)", "rgba(80,180,100,0.3)"
    )

    chart_data = json.dumps({
        "labels":    tick_labels,
        "counter":   peak["counter"],
        "lounge":    peak["lounge"],
        "c_colors":  c_colors,
        "l_colors":  l_colors,
    })

    st.components.v1.html(
        f"""
        <div dir="rtl" style="font-family:Arial,sans-serif;">
          <canvas id="peakChart" style="width:100%;max-height:260px;"></canvas>
        </div>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
        <script>
        const d = {chart_data};
        new Chart(document.getElementById('peakChart'), {{
          type: 'bar',
          data: {{
            labels: d.labels,
            datasets: [
              {{
                label: 'דלפקי צ׳ק-אין (נוסעים)',
                data: d.counter,
                backgroundColor: d.c_colors,
                borderWidth: 0,
                borderRadius: 2,
                yAxisID: 'yPax',
              }},
              {{
                label: 'אולם יציאה (טיסות)',
                data: d.lounge,
                backgroundColor: d.l_colors,
                borderWidth: 0,
                borderRadius: 2,
                yAxisID: 'yFlight',
              }},
            ]
          }},
          options: {{
            responsive: true,
            plugins: {{
              legend: {{ labels: {{ color: '#ccc', font: {{ size: 12 }} }} }},
              tooltip: {{
                callbacks: {{
                  label: ctx => ctx.datasetIndex === 0
                    ? 'דלפקים: ' + ctx.parsed.y + ' נוסעים'
                    : 'אולם: '   + ctx.parsed.y + ' טיסות',
                }}
              }}
            }},
            scales: {{
              x: {{ ticks: {{ color:'#aaa', maxRotation:45, font:{{size:10}} }}, grid:{{color:'rgba(255,255,255,0.05)'}} }},
              yPax: {{
                type: 'linear', position: 'right',
                ticks: {{ color:'#5a9fd4' }},
                grid:  {{ color:'rgba(255,255,255,0.08)' }},
                title: {{ display:true, text:'נוסעים בדלפקים', color:'#5a9fd4', font:{{size:11}} }},
                beginAtZero: true,
              }},
              yFlight: {{
                type: 'linear', position: 'left',
                ticks: {{ color:'#50b464', stepSize:1 }},
                grid:  {{ drawOnChartArea:false }},
                title: {{ display:true, text:'טיסות באולם', color:'#50b464', font:{{size:11}} }},
                beginAtZero: true,
              }},
            }},
            animation: {{ duration:400 }},
          }}
        }});
        </script>
        """,
        height=320,
        scrolling=False,
    )

    # ── פיקי דלפקים (כרטיסים) ────────────────────────────────────────────
    st.markdown(
        f"<div style='direction:rtl;text-align:right;margin-top:20px'>"
        f"<div style='font-weight:700;font-size:1em;color:#5a9fd4;margin-bottom:10px;'>🏢 פיקי דלפקי צ׳ק-אין"
        f"<span style='font-weight:400;font-size:0.75em;color:rgba(140,180,230,0.7);margin-right:8px;'>מעל 2,000 נוסעים</span></div>",
        unsafe_allow_html=True,
    )
    def _render_peak_cards(peaks, val_label, val_fmt, accent, bg_alpha, text_color, sub_color):
        """מציג את רשימת הפיקים בשורות של 4, בסדר כרונולוגי."""
        if not peaks:
            st.info("אין פיקים")
            return
        sorted_peaks = sorted(peaks, key=lambda p: p["start"])
        max_val = max(p["peak_val"] for p in sorted_peaks)
        per_row = 4
        for row_start in range(0, len(sorted_peaks), per_row):
            row_peaks = sorted_peaks[row_start: row_start + per_row]
            cols = st.columns(per_row)
            for col_idx, pk in enumerate(row_peaks):
                is_max = pk["peak_val"] == max_val
                border = accent if is_max else f"rgba({accent[1:][:2]},{accent[1:][2:4]},{accent[1:][4:]},0.45)"
                badge = " 🔺" if is_max else ""
                # earliest on the right: invert column index
                cols[per_row - 1 - col_idx].markdown(
                    f"""<div style='background:rgba({bg_alpha});border-right:3px solid {accent if is_max else "#3a5f8a"};
                    border-radius:8px;padding:10px 14px;text-align:right;margin-bottom:8px;'>
                    <div style='font-size:0.85em;font-weight:700;color:{accent};'>
                      {fmt(pk["start"])} – {fmt(pk["end"])}{badge}
                    </div>
                    <div style='font-size:1.15em;font-weight:800;color:{text_color};margin:4px 0;'>
                      {val_fmt(pk["peak_val"])}
                    </div>
                    <div style='font-size:0.72em;color:{sub_color};'>
                      שיא ב-{fmt(pk["peak_time"])}
                    </div></div>""",
                    unsafe_allow_html=True,
                )

    _render_peak_cards(
        peak["counter_peaks"],
        val_label="נוסעים",
        val_fmt=lambda v: f"{v:,} נוסעים",
        accent="#5a9fd4",
        bg_alpha="60,120,200,0.12",
        text_color="#ffffff",
        sub_color="rgba(180,210,255,0.6)",
    )

    # ── פיקי אולם (כרטיסים) ──────────────────────────────────────────────
    st.markdown(
        "<div style='direction:rtl;text-align:right;font-weight:700;font-size:1em;color:#50b464;margin:18px 0 10px;'>🚪 פיקי אולם יציאה"
        "<span style='font-weight:400;font-size:0.75em;color:rgba(120,210,140,0.7);margin-right:8px;'>מעל 6 טיסות</span></div>",
        unsafe_allow_html=True,
    )
    _render_peak_cards(
        peak["lounge_peaks"],
        val_label="טיסות",
        val_fmt=lambda v: f"{v} טיסות",
        accent="#50b464",
        bg_alpha="80,180,100,0.12",
        text_color="#ffffff",
        sub_color="rgba(160,230,170,0.6)",
    )



def build_workload(result_df, employees_df):
    rows = []

    if result_df.empty:
        return pd.DataFrame(columns=["עובד", "משימות", "דקות עבודה", "הפסקה נדרשת", "סה״כ כולל הפסקות"])

    timed = result_df[result_df["התחלה"].astype(str).str.strip() != ""].copy()

    # Pre-build O(1) employee lookup (also used for required_break)
    _wl_emp_dict: dict = {}
    name_canon: dict = {}
    for _, _er in employees_df.iterrows():
        canon = clean_text(str(_er.get("שם", "")))
        if not canon:
            continue
        _wl_emp_dict[canon] = _er
        name_canon[name_key(canon)] = canon
        name_canon[name_key_reversed(canon)] = canon

    def canonicalize(w):
        w = clean_text(str(w))
        if "❌" in w:
            return w
        return name_canon.get(name_key(w), w)

    timed["עובד"] = timed["עובד"].apply(canonicalize)

    real_workers = sorted([
        worker for worker in timed["עובד"].dropna().unique()
        if "❌" not in str(worker)
    ])

    for emp in real_workers:
        tasks = timed[timed["עובד"] == emp]
        total = sum(
            minutes_between(to_datetime_time(task["התחלה"]), to_datetime_time(task["סיום"]))
            for _, task in tasks.iterrows()
        )
        _wl_er = _wl_emp_dict.get(emp)
        break_min = required_break(_wl_er) if _wl_er is not None else 0
        rows.append({
            "עובד":               emp,
            "משימות":             len(tasks),
            "דקות עבודה":         total,
            "הפסקה נדרשת":       break_min,
            "סה״כ כולל הפסקות":  total + break_min,
        })

    return pd.DataFrame(rows)


def build_counter_continuity_rows(result_labeled, employees_df):
    rows = []

    if result_labeled.empty:
        return pd.DataFrame(columns=["עובד", "משמרת", "טיסות", "טיסות משובצות", "תפקידים", "הערה"])

    for _, emp in employees_df.iterrows():
        name  = str(emp["שם"]) if "שם" in emp.index else ""
        _ss = clean_text(str(emp.get("תחילת משמרת", "")))
        _se = clean_text(str(emp.get("סוף משמרת", "")))
        shift = f"{_ss}-{_se}" if _ss and _se else ""

        emp_tasks = result_labeled[
            (result_labeled["עובד"].astype(str) == name) &
            (result_labeled["התחלה"].astype(str).str.strip() != "")
        ].copy()

        if emp_tasks.empty:
            rows.append({
                "עובד": name, "משמרת": shift, "טיסות": 0,
                "טיסות משובצות": "", "תפקידים": "",
                "הערה": "לא שובץ לטיסות. נשאר בדלפקים או לפי הנחיית אחמ״ש.",
            })
            continue

        rows.append({
            "עובד":          name,
            "משמרת":         shift,
            "טיסות":         len(emp_tasks),
            "טיסות משובצות": " | ".join(emp_tasks["טיסה"].astype(str).tolist()),
            "תפקידים":       " | ".join(emp_tasks["תפקיד"].astype(str).tolist()),
            "הערה":          "רצף טיסות" if len(emp_tasks) >= 2 else "טיסה בודדת ואז חזרה לדלפקים/סיום משמרת",
        })

    return pd.DataFrame(rows)


def build_available_in_hall(schedule_df, employees_df, flights_df):
    timed = schedule_df[
        (schedule_df["התחלה"].astype(str).str.strip() != "") &
        (~schedule_df["עובד"].astype(str).str.contains("❌", na=False))
    ].copy()

    if timed.empty:
        return pd.DataFrame(columns=["עובד", "תפקיד עיקרי", "משמרת", "פנוי מ", "פנוי עד", "פנות (דק׳)", "משימה הבאה", "הערה"])

    timed["_start_dt"] = pd.to_datetime(timed["התחלה"], format="%H:%M", errors="coerce")
    timed["_end_dt"]   = pd.to_datetime(timed["סיום"],   format="%H:%M", errors="coerce")

    hall_workers = timed["עובד"].unique()
    rows = []

    for emp_name in hall_workers:
        emp_tasks  = timed[timed["עובד"] == emp_name].sort_values("_start_dt")
        emp_row_df = employees_df[employees_df["שם"] == emp_name]
        emp_row    = emp_row_df.iloc[0] if not emp_row_df.empty else None

        shift_start_str = clean_text(emp_row.get("תחילת משמרת", "")) if emp_row is not None else ""
        shift_end_str   = clean_text(emp_row.get("סוף משמרת",   "")) if emp_row is not None else ""

        if not is_time_text(shift_start_str) or not is_time_text(shift_end_str):
            continue

        shift_text = f"{shift_start_str}-{shift_end_str}"
        main_role  = emp_tasks["תפקיד בסיס"].mode().iloc[0] if not emp_tasks.empty else ""

        try:
            shift_start_dt = pd.to_datetime(shift_start_str, format="%H:%M")
            shift_end_dt   = pd.to_datetime(shift_end_str,   format="%H:%M")
            if shift_end_dt <= shift_start_dt:
                shift_end_dt += pd.Timedelta(hours=24)
        except Exception:
            continue

        task_list = emp_tasks.reset_index(drop=True)
        gaps = []

        for i in range(len(task_list) - 1):
            end_i      = task_list.loc[i,   "_end_dt"]
            start_next = task_list.loc[i+1, "_start_dt"]
            if start_next < end_i:
                start_next += pd.Timedelta(hours=24)
            gap_min = int((start_next - end_i).total_seconds() / 60)
            if gap_min < 20 or gap_min > 300:
                continue
            next_task_text = f"{task_list.loc[i+1, 'טיסה']} — {normalize_role_label(task_list.loc[i+1, 'תפקיד'])}"
            gaps.append({
                "from": end_i.strftime("%H:%M"),
                "to":   start_next.strftime("%H:%M"),
                "gap":  gap_min,
                "next": next_task_text,
                "note": "פנוי בין טיסות",
            })

        if not task_list.empty:
            last_end = task_list.iloc[-1]["_end_dt"]
            le = last_end
            if le < shift_start_dt:
                le += pd.Timedelta(hours=24)
            se = shift_end_dt
            if se < le:
                se += pd.Timedelta(hours=24)
            remaining = int((se - le).total_seconds() / 60)
            if 30 <= remaining <= 300:
                gaps.append({
                    "from": last_end.strftime("%H:%M"),
                    "to":   shift_end_str,
                    "gap":  remaining,
                    "next": "סיום משמרת",
                    "note": "פנוי לאחר סיום טיסות",
                })

        for gap in gaps:
            rows.append({
                "עובד":         emp_name,
                "תפקיד עיקרי": normalize_role_label(main_role),
                "משמרת":        shift_text,
                "פנוי מ":       gap["from"],
                "פנוי עד":      gap["to"],
                "פנות (דק׳)":  gap["gap"],
                "משימה הבאה":   gap["next"],
                "הערה":         gap["note"],
            })

    if not rows:
        return pd.DataFrame(columns=["עובד", "תפקיד עיקרי", "משמרת", "פנוי מ", "פנוי עד", "פנות (דק׳)", "משימה הבאה", "הערה"])

    return pd.DataFrame(rows).sort_values(["פנוי מ", "עובד"]).reset_index(drop=True)


def build_unassigned_agents(schedule_df, employees_df, shift_map):
    assigned_workers = set(
        schedule_df[~schedule_df["עובד"].astype(str).str.contains("❌", na=False)]["עובד"].tolist()
    )
    rows = []
    for _, emp in employees_df.iterrows():
        name = clean_text(emp.get("שם", ""))
        if not name:
            continue
        shift_start = clean_text(emp.get("תחילת משמרת", ""))
        shift_end   = clean_text(emp.get("סוף משמרת", ""))
        if not is_time_text(shift_start) or not is_time_text(shift_end):
            continue
        if name in assigned_workers:
            continue
        is_agent = str(emp.get("דייל", "")).strip() == "כן"
        is_tl    = str(emp.get("ראש צוות", "")).strip() == "כן"
        if not is_agent and not is_tl:
            continue
        role = "ראש צוות" if is_tl else "דייל"
        rows.append({
            "שם":           name,
            "תפקיד":        role,
            "משמרת":        f"{shift_start}-{shift_end}",
            "תחילת משמרת": shift_start,
            "סוף משמרת":   shift_end,
        })
    if not rows:
        return pd.DataFrame(columns=["שם", "תפקיד", "משמרת", "תחילת משמרת", "סוף משמרת"])
    df = pd.DataFrame(rows)
    return df.sort_values(["תחילת משמרת", "שם"]).reset_index(drop=True)


# =========================
# RENDERING FUNCTIONS
# =========================

def line_style_by_role(current_role, line):
    if "❌" in str(line):
        return "role-missing"
    role = normalize_role_label(current_role)
    if role == "ראש צוות":   return "role-teamlead"
    if role == "מפקח TSA":   return "role-inspector"
    if role == "שומר TSA":   return "role-guard"
    if role == "טרייני ר״צ": return "role-trainee"
    if role == "מתאם תורים": return "role-queue"
    if role == "דיילת":      return "role-agent"
    return "role-agent"


def render_line(line, current_role=""):
    line_str   = str(line)
    style_cls  = line_style_by_role(current_role, line_str)
    shift_badge = ""
    shift_match = re.search(r"\((\d{2}:\d{2}-\d{2}:\d{2})\)\s*$", line_str)
    if shift_match:
        shift_badge = shift_match.group(1)
        line_str = line_str[:shift_match.start()].rstrip()

    badge_html = (
        f'<span style="float:left;background:#e8f0fe;color:#1a3d7a;'
        f'font-size:11px;font-weight:900;border-radius:6px;'
        f'padding:2px 7px;margin-right:6px;white-space:nowrap;">🕐 {safe_html(shift_badge)}</span>'
        if shift_badge else ""
    )
    st.markdown(
        f'<div class="assignment-line {style_cls}">'
        f'{badge_html}'
        f'{safe_html(line_str)}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_flight_card(row):
    aircraft   = str(row["מטוס/רישוי"]).replace("\n", " / ")
    reqs       = str(row["תפקידים דרושים"])
    left_text  = str(row["ראש צוות / מתאם תורים / מפקח TSA"])
    right_text = str(row["דיילים / שומר TSA"])

    required_line = " | ".join([part.strip() for part in reqs.split("|") if part.strip()]) or "לא הוגדרו תפקידים"

    st.markdown(
        f"""
        <div class="flight-card">
          <div class="flight-head">
            <div class="flight-row">
              <div class="flight-name">✈️ {safe_html(row['מספר טיסה'])} ← {safe_html(row['יעד'])}</div>
              <div class="flight-meta">🕒 {safe_html(row['זמנים'])} | 🛩️ {safe_html(aircraft)}</div>
            </div>
            <div class="req-line">תפקידים דרושים: {safe_html(required_line)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    management_title = str(row.get("כותרת ניהול", "ניהול"))
    st.markdown(f'<div class="panel-title">👔 {safe_html(management_title)}</div>', unsafe_allow_html=True)
    if left_text and left_text != "nan":
        for line in left_text.split("\n"):
            if "||" in line:
                role_part, text_part = line.split("||", 1)
                render_line(text_part, role_part)
            else:
                render_line(line)
    else:
        render_line("אין שיבוץ")

    st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)

    agents_title = str(row.get("כותרת דיילים", "דיילים"))
    st.markdown(f'<div class="panel-title">🧍 {safe_html(agents_title)}</div>', unsafe_allow_html=True)
    if right_text and right_text != "nan":
        for line in right_text.split("\n"):
            if "||" in line:
                role_part, text_part = line.split("||", 1)
                render_line(text_part, role_part)
            else:
                render_line(line)
    else:
        render_line("אין שיבוץ")


def render_flight_card_with_swap(row, schedule_df, employees_df, highlighted_tasks=None,
                                  transfer_delay_tasks=None):
    aircraft   = str(row["מטוס/רישוי"]).replace("\n", " / ")
    reqs       = str(row["תפקידים דרושים"])
    left_text  = str(row["ראש צוות / מתאם תורים / מפקח TSA"])
    right_text = str(row["דיילים / שומר TSA"])
    fnum       = str(row["מספר טיסה"]).strip()

    highlighted_tasks = highlighted_tasks or set()
    transfer_delay_tasks = transfer_delay_tasks or set()

    required_line  = " | ".join([p.strip() for p in reqs.split("|") if p.strip()]) or "לא הוגדרו תפקידים"
    aircraft_short = str(row["מטוס/רישוי"]).split("\n")[0].strip()

    # אייקון מוביל + צביעת סימול המטוס (737/787...) לפי סוג גוף — כדי להבדיל במבט אחד.
    # מטוס חכור = צר גוף המסומן בסגול (רישוי LOC/BBN/PEX/EAC/EAI/MGM/PMI).
    _body_type = str(row.get("סוג גוף", "")).strip()
    _is_leased = str(row.get("חכור", "")).strip() == "כן"
    if _body_type == "צר גוף" and _is_leased:
        body_icon     = "🛩️"                    # צר גוף (חכור)
        aircraft_disp = f":violet[{aircraft_short}]"
    elif _body_type == "צר גוף":
        body_icon     = "🛩️"                    # מטוס קטן = צר גוף
        aircraft_disp = f":blue[{aircraft_short}]"
    elif _body_type == "רחב גוף":
        body_icon     = "✈️"                    # ג'מבו גדול = רחב גוף
        aircraft_disp = f":red[{aircraft_short}]"
    else:
        body_icon     = "🛫"
        aircraft_disp = aircraft_short

    has_missing  = "❌" in left_text or "❌" in right_text
    missing_icon = " ⚠️" if has_missing else ""

    # בדיקה אם לטיסה הזו יש שיבוצים חדשים שממתינים לאישור
    flight_task_indices = set(
        schedule_df[schedule_df["טיסה"].astype(str).str.strip() == fnum].index
    )
    has_new_assignment = bool(flight_task_indices & highlighted_tasks)
    new_icon = " ✨" if has_new_assignment else ""

    # מספר הטיסה והיעד מודגשים (bold) כדי שיבלטו; פירוט התפקידים הדרושים ירד
    # מהתווית המקוצרת ומוצג בתוך ה-expander (רק בלחיצה/פתיחה).
    _gate = str(row.get("גייט", "")).strip()
    gate_str = f"  🚪 {_gate}" if _gate and _gate.lower() != "nan" else ""
    # Terminal-1 flights get a clear orange badge for at-a-glance separation
    term_str = "  :orange[**T1**]" if str(row.get("טרמינל", "")).strip() == "1" else ""

    _is_cancelled = is_cancelled_flight(fnum)
    _is_ferry = str(row.get("פרי", "")).strip() == "כן"
    if _is_cancelled:
        expander_label = (
            f"🚫 :red[**{fnum} ← {row['יעד']} — CANCELLED — מבוטלת עד להודעה חדשה**]"
            f"   |   🕒 ~{row['זמנים']}~"
        )
    elif _is_ferry:
        expander_label = (
            f"{body_icon} **{fnum} ← {row['יעד']}**  :gray[**FERRY — ללא נוסעים**]"
            f"   |   🕒 {row['זמנים']}"
            f"   |   {aircraft_disp}"
        )
    else:
        expander_label = (
            f"{body_icon} **{fnum} ← {row['יעד']}**{term_str}{gate_str}{missing_icon}{new_icon}"
            f"   |   🕒 {row['זמנים']}"
            f"   |   {aircraft_disp}"
        )

    if _is_cancelled:
        with st.expander(expander_label, expanded=False):
            st.markdown(
                '<div style="direction:rtl;color:#e05252;font-weight:700;padding:6px 2px;">'
                '🚫 הטיסה מבוטלת עד להודעה חדשה — לא נדרש שיבוץ צוות.</div>',
                unsafe_allow_html=True,
            )
        return

    if _is_ferry:
        with st.expander(expander_label, expanded=False):
            st.markdown(
                '<div style="direction:rtl;color:#8a8f98;font-weight:700;padding:6px 2px;">'
                '✈️ טיסת FERRY — יוצאת ללא נוסעים (עמודת Pax ריקה ב-FIDS). לא נדרש צוות.</div>',
                unsafe_allow_html=True,
            )
        return

    with st.expander(expander_label, expanded=False):

        # Hoisted out of the per-line loop: one schedule scan + one role-label
        # pass PER CARD instead of per line (the per-line scans made every click
        # in this tab — e.g. opening a swap popup — rerender painfully slowly).
        _card_flight_tasks = schedule_df[schedule_df["טיסה"].astype(str).str.strip() == fnum]
        _card_role_labels = _card_flight_tasks["תפקיד בסיס"].astype(str).apply(normalize_role_label)

        def render_lines_with_swap(panel_lines):
            for line_i, line in enumerate(panel_lines):
                role_part, text_part = (line.split("||", 1) if "||" in line else ("", line))

                worker_raw = text_part.split(" - ")[0].strip() if " - " in text_part else text_part.split(" (")[0].strip()
                # strip trailing markers/role parentheses — "שם (ט) (דיילת)" → "שם"
                worker_raw = re.sub(r"(\s*\([^)]*\))+$", "", worker_raw).strip()
                role_base  = normalize_role_label(role_part) if role_part else ""

                flight_tasks = _card_flight_tasks
                match = flight_tasks[
                    (flight_tasks["עובד"].astype(str).str.contains(re.escape(worker_raw), na=False)) &
                    (_card_role_labels == role_base)
                ]
                if match.empty:
                    match = flight_tasks[_card_role_labels == role_base]

                if match.empty:
                    render_line(text_part, role_part)
                    continue

                task_idx  = match.index[0]
                uid       = f"{fnum}_{task_idx}_{line_i}"
                popup_key = f"popup_open_{uid}"
                if popup_key not in st.session_state:
                    st.session_state[popup_key] = False

                is_new    = task_idx in highlighted_tasks
                is_transfer_delay = task_idx in transfer_delay_tasks
                _removed_emps = st.session_state.get("removed_employees", {})
                is_removed = any(
                    worker_raw.strip().startswith(rname) or rname.startswith(worker_raw.strip())
                    or worker_raw.strip() == rname
                    for rname in _removed_emps
                )
                if is_transfer_delay:
                    style_cls = "role-transfer-delay"
                elif is_new:
                    style_cls = "role-new-assignment"
                elif is_removed:
                    style_cls = "role-removed-worker"
                else:
                    style_cls = line_style_by_role(role_part, text_part)
                shift_badge = ""
                shift_match = re.search(r"\((\d{2}:\d{2}-\d{2}:\d{2})\)\s*$", text_part)
                if shift_match:
                    shift_badge  = shift_match.group(1)
                    display_text = text_part[:shift_match.start()].rstrip()
                else:
                    display_text = text_part

                if is_transfer_delay:
                    new_badge_html = (
                        '<span style="float:right;background:#1a5fb4;color:#fff;'
                        'font-size:10px;font-weight:900;border-radius:5px;'
                        'padding:2px 6px;margin-left:6px;white-space:nowrap;">🚦 מעכב מעבר טרמינלים</span>'
                    )
                elif is_new:
                    new_badge_html = (
                        '<span style="float:right;background:#f5a623;color:#fff;'
                        'font-size:10px;font-weight:900;border-radius:5px;'
                        'padding:2px 6px;margin-left:6px;white-space:nowrap;">✨ חדש</span>'
                    )
                elif is_removed:
                    _rem_reason = next(
                        (v for k, v in _removed_emps.items()
                         if worker_raw.strip() == k or worker_raw.strip().startswith(k) or k.startswith(worker_raw.strip())),
                        "הוסרה"
                    )
                    new_badge_html = (
                        f'<span style="float:right;background:#c0392b;color:#fff;'
                        f'font-size:10px;font-weight:900;border-radius:5px;'
                        f'padding:2px 6px;margin-left:6px;white-space:nowrap;">🚫 {safe_html(_rem_reason)}</span>'
                    )
                else:
                    new_badge_html = ""
                badge_html = (
                    f'<span style="float:left;background:#e8f0fe;color:#1a3d7a;'
                    f'font-size:11px;font-weight:900;border-radius:6px;'
                    f'padding:2px 7px;margin-right:6px;white-space:nowrap;">🕐 {safe_html(shift_badge)}</span>'
                    if shift_badge else ""
                )

                if is_removed:
                    # עובד מוסר — הצג שורה בלבד, ללא כפתור החלפה
                    st.markdown(
                        f'<div class="assignment-line {style_cls}" style="margin-bottom:0">'
                        f'{new_badge_html}{badge_html}{safe_html(display_text)}</div>',
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        '<div style="font-size:11px;color:#aaa;text-align:right;margin:2px 0 6px;">'
                        'לשיבוץ מחדש — השתמש ב״שיבוץ אוטומטי״ במרכז הבקרה</div>',
                        unsafe_allow_html=True,
                    )
                    continue

                col_line, col_arrow = st.columns([11, 1])
                with col_line:
                    st.markdown(
                        f'<div class="assignment-line {style_cls}" style="margin-bottom:0">'
                        f'{new_badge_html}{badge_html}{safe_html(display_text)}</div>',
                        unsafe_allow_html=True,
                    )
                with col_arrow:
                    arrow = "▲" if st.session_state[popup_key] else "▼"
                    if st.button(arrow, key=f"arrow_{uid}", help="החלף עובד"):
                        st.session_state[popup_key] = not st.session_state[popup_key]
                        st.rerun()

                if st.session_state[popup_key]:
                    candidates = get_qualified_candidates_for_swap(
                        schedule_df, employees_df, fnum, role_base, task_idx
                    )
                    # Near-miss candidates: qualified workers whose shift ends
                    # just short (≤30 min) of the task end — assignable only if
                    # they agree to extend. Never overlap the normal pool.
                    _extendable = get_extendable_candidates_for_swap(
                        schedule_df, employees_df, fnum, role_base, task_idx
                    )
                    _extendable = [(n, g) for (n, g) in _extendable if n not in set(candidates)]
                    st.markdown(
                        f'<div class="swap-popup">'
                        f'<div class="swap-popup-title">🔄 החלפת עובד — {safe_html(worker_raw)} ({safe_html(role_base)})</div>',
                        unsafe_allow_html=True,
                    )

                    if not candidates and not _extendable:
                        st.warning("אין עובדים מוסמכים ופנויים להחלפה כרגע.")
                    elif not candidates and _extendable:
                        # Only extendable options exist — render them directly.
                        _ext_names = [f"{n}  (משמרת עד — פער {g} דק׳)" for (n, g) in _extendable]
                        st.markdown(
                            '<div class="swap-popup-label" style="color:#b8860b;">'
                            '⏱️ אין עובד שמשמרתו מכסה במלואה — ניתן לשבץ בכפוף לאישור '
                            'העובד/ת להארכת משמרת:</div>',
                            unsafe_allow_html=True,
                        )
                        _ext_pick = st.selectbox(
                            "", options=_ext_names, key=f"ext_only_select_{uid}",
                            label_visibility="collapsed",
                        )
                        if st.button("שבץ בכפוף לאישור", key=f"ext_only_confirm_{uid}",
                                     use_container_width=True):
                            _ext_name = _extendable[_ext_names.index(_ext_pick)][0]
                            updated = do_swap(st.session_state["schedule_df"], task_idx,
                                              _ext_name, "unassign")
                            st.session_state["schedule_df"] = updated
                            for _k in ["labeled_df", "workload_df", "continuity_df",
                                       "output_df", "_ar_highlighted"]:
                                st.session_state.pop(_k, None)
                            st.session_state[popup_key] = False
                            st.rerun()
                    else:
                        st.markdown('<div class="swap-popup-label">בחר עובד חלופי:</div>', unsafe_allow_html=True)
                        selected_new = st.selectbox("", options=candidates, key=f"swap_select_{uid}", label_visibility="collapsed")
                        st.markdown(f'<div class="swap-popup-label">מה לעשות עם {safe_html(worker_raw)}?</div>', unsafe_allow_html=True)
                        action = st.radio("", options=["השאר ללא שיבוץ", "העבר לחריץ פנוי בטיסה אחרת"],
                                          key=f"swap_action_{uid}", horizontal=True, label_visibility="collapsed")

                        target_flight = None
                        if action == "העבר לחריץ פנוי בטיסה אחרת":
                            # Only offer a target flight/slot the displaced
                            # worker can ACTUALLY cover — shift hours and no
                            # conflict with their other tasks — instead of
                            # any same-role ❌ slot regardless of time. The
                            # move used to place a worker outside their own
                            # shift entirely with no warning at all (found
                            # via real 12.07 data: trainee-agent#2, shift
                            # 14:00-01:30, moved onto an 03:00-03:50 slot).
                            # Vacate THIS slot in a scratch copy first so the
                            # worker's own current assignment (which they're
                            # being moved OFF of) doesn't count against them
                            # as a false conflict.
                            _sched_for_move_check = schedule_df.copy()
                            _sched_for_move_check.at[task_idx, "עובד"] = "❌"
                            _other_slot_candidates = schedule_df[
                                (schedule_df["טיסה"].astype(str).str.strip() != fnum) &
                                (schedule_df["עובד"].astype(str).str.contains("❌")) &
                                (schedule_df["תפקיד בסיס"].astype(str).apply(normalize_role_label) == role_base)
                            ]
                            _seen_flights = set()
                            other_flights = []
                            for _tf, _tidx in zip(
                                _other_slot_candidates["טיסה"].astype(str).str.strip(),
                                _other_slot_candidates.index,
                            ):
                                if _tf in _seen_flights:
                                    continue
                                _move_cands = get_qualified_candidates_for_swap(
                                    _sched_for_move_check, employees_df, _tf, role_base, _tidx
                                )
                                if worker_raw in _move_cands:
                                    other_flights.append(_tf)
                                    _seen_flights.add(_tf)
                            other_flights = sorted(other_flights)
                            if not other_flights:
                                st.info("אין חריצים פנויים מתאימים בטיסות אחרות (מבחינת שעות משמרת והתנגשויות).")
                            else:
                                st.markdown('<div class="swap-popup-label">בחר טיסה יעד:</div>', unsafe_allow_html=True)
                                target_flight = st.selectbox("", options=other_flights, key=f"swap_target_{uid}", label_visibility="collapsed")

                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if st.button("✅ אשר החלפה", key=f"swap_confirm_{uid}", use_container_width=True):
                                displaced_action = "move" if action == "העבר לחריץ פנוי בטיסה אחרת" else "unassign"
                                updated = do_swap(st.session_state["schedule_df"], task_idx, selected_new, displaced_action, target_flight)
                                st.session_state["schedule_df"] = updated
                                for _k in ["labeled_df", "workload_df", "continuity_df", "output_df", "_ar_highlighted"]:
                                    st.session_state.pop(_k, None)
                                st.session_state[popup_key] = False
                                st.rerun()
                        with bc2:
                            if st.button("✖ ביטול", key=f"swap_cancel_{uid}", use_container_width=True):
                                st.session_state[popup_key] = False
                                st.rerun()

                        # Near-miss options shown BELOW the normal ones, clearly
                        # marked as conditional on the employee agreeing to extend.
                        if _extendable:
                            _ext_names2 = [f"{n}  (פער {g} דק׳ מעבר לסיום המשמרת)" for (n, g) in _extendable]
                            st.markdown(
                                '<div class="swap-popup-label" style="color:#b8860b;'
                                'margin-top:8px;">⏱️ אופציות נוספות — בכפוף לאישור '
                                'העובד/ת להארכת משמרת:</div>',
                                unsafe_allow_html=True,
                            )
                            _ext_pick2 = st.selectbox(
                                "", options=_ext_names2, key=f"ext_select_{uid}",
                                label_visibility="collapsed",
                            )
                            if st.button("שבץ בכפוף לאישור", key=f"ext_confirm_{uid}",
                                         use_container_width=True):
                                _ext_name2 = _extendable[_ext_names2.index(_ext_pick2)][0]
                                updated = do_swap(st.session_state["schedule_df"], task_idx,
                                                  _ext_name2, "unassign")
                                st.session_state["schedule_df"] = updated
                                for _k in ["labeled_df", "workload_df", "continuity_df",
                                           "output_df", "_ar_highlighted"]:
                                    st.session_state.pop(_k, None)
                                st.session_state[popup_key] = False
                                st.rerun()

                    st.markdown('</div>', unsafe_allow_html=True)
                st.markdown("<div style='margin-bottom:7px'></div>", unsafe_allow_html=True)

        # פירוט התפקידים הדרושים — מוצג רק כשה-expander פתוח (בלחיצה)
        st.markdown(
            f'<div class="req-line">🎯 תפקידים דרושים: {safe_html(required_line)}</div>',
            unsafe_allow_html=True,
        )

        management_title = str(row.get("כותרת ניהול", "ניהול"))
        agents_title     = str(row.get("כותרת דיילים", "דיילים"))

        st.markdown(f'<div class="panel-title">👔 {safe_html(management_title)}</div>', unsafe_allow_html=True)
        left_lines = [l for l in left_text.split("\n") if l.strip()] if left_text and left_text != "nan" else []
        if left_lines:
            render_lines_with_swap(left_lines)
        else:
            render_line("אין שיבוץ")

        st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)

        st.markdown(f'<div class="panel-title">🧍 {safe_html(agents_title)}</div>', unsafe_allow_html=True)
        right_lines = [l for l in right_text.split("\n") if l.strip()] if right_text and right_text != "nan" else []
        if right_lines:
            render_lines_with_swap(right_lines)
        else:
            render_line("אין שיבוץ")


# =========================
# EXCEL EXPORT
# =========================

def to_excel_bytes(output_df, workload_df, schedule_df, continuity_df=None):
    output = io.BytesIO()
    try:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            output_df.to_excel(writer, index=False, sheet_name="שיבוץ")
            workload_df.to_excel(writer, index=False, sheet_name="עומס")
            schedule_df.to_excel(writer, index=False, sheet_name="פירוט גולמי")
            if continuity_df is not None:
                continuity_df.to_excel(writer, index=False, sheet_name="רצף אזורי")
    except Exception:
        output = io.BytesIO()
        output_df.to_csv(output, index=False, encoding="utf-8-sig")
    output.seek(0)
    return output
    from io import BytesIO
    from openpyxl import load_workbook
    from io import BytesIO
    from openpyxl import load_workbook


def to_departures_report_excel_bytes(flights_df, schedule_df, employees_df, terminal_view="both"):
    # terminal_view: "t1" / "t3" / "both" — drives both the sheet title
    # (user rule 2026-07-29) and the T1-only extra trailing column.
    t1_only = terminal_view == "t1"
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "דוח שיבוץ טיסות - המראות"

    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = "A4"

    headers = [
        "טיסה",
        "שעה",
        "יעד",
        "מטוס",
        "ר״צ",
        "משמרת",
        "דיילים",
        "משמרת",
    ]

    widths = [14, 14, 18, 16, 32, 18, 38, 18]

    # T1-only exports get an extra trailing column, left blank for manual
    # marking — user rule 2026-07-29.
    if t1_only:
        headers.append("LITE\\WC\\TP\\SPECIAL")
        widths.append(20)

    blue_fill = PatternFill("solid", fgColor="4FB3D8")
    title_font = Font(name="Arial", size=18, bold=True)
    header_font = Font(name="Arial", size=11, bold=True)
    normal_font = Font(name="Arial", size=10)
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    _title_by_view = {
        "t1": "סידור יומי טרמינל 1",
        "t3": "סידור יומי טרמינל 3",
        "both": "סידור יומי טרמינל 1+3",
    }

    _last_col_letter = chr(64 + len(headers))
    ws.merge_cells(f"A1:{_last_col_letter}1")
    ws["A1"] = _title_by_view.get(terminal_view, _title_by_view["both"])
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center", readingOrder=2)

    ws.merge_cells(f"A2:{_last_col_letter}2")
    ws["A2"] = ""
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center", readingOrder=2)

    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col_idx)
        cell.value = header
        cell.fill = blue_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center", readingOrder=2, wrap_text=True)

    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    current_row = 4

    # A role label is shown next to a worker's name only for roles where it's
    # actually informative — a plain single ראש צוות or a plain דייל doesn't
    # need one. Shown for: טרייני ר"צ, מפקח/שומר TSA, מתאם תורים, and a
    # NUMBERED ראש צוות (ר"צ 1 / ר"צ 2, on TWO_TEAM_LEADS_DESTS flights) —
    # the numbering already comes from "תפקיד" (build_schedule assigns
    # "{role} {i+1}" there when more than one of a role is needed; "תפקיד
    # בסיס" stays the plain unnumbered category). User rule 2026-07-26.
    # Includes both quote-character variants of 'טרייני ר"צ': a straight
    # ASCII quote and the Hebrew gershayim (״, U+05F4) — schedule_df's own
    # "תפקיד" value for this role uses the gershayim form, which silently
    # never matched the straight-quote-only entry (found via real
    # 04.09.2026 data: TL-trainee#5's trainee-TL label never showed in the
    # report despite her role being set correctly everywhere else).
    _SHOW_LABEL_ROLES_EXACT = {
        "מפקח TSA", "שומר TSA", "מתאם תורים", "טרייני רצ",
        'טרייני ר"צ', "טרייני ר״צ",
    }

    # The next-task text ("המשך אזורי") is written out in full descriptive
    # phrasing ("המשך ישיר ל־395 דיילת", "רענון והמשך ל־321 ראש צוות") — the
    # departures-report target format wants it terser: just "- 395 דייל" or
    # "רענון ו 321 ר"צ", dropping the "המשך [ישיר] ל" phrase and abbreviating
    # "ראש צוות" to 'ר"צ' (user-confirmed via file diff 2026-07-28). Text with
    # no flight reference at all (e.g. "חזרה לדלפקים", "הפסקה עד...") is left
    # as-is — there's nothing to shorten.
    def _simplify_next_task(raw):
        if not raw:
            return ""
        # Must only fire on an actual "המשך [ישיר] ל־X" flight reference —
        # "חזרה ל־77" (the shift-manager home-return label) coincidentally
        # matches the bare "ל[־-](\S+)" pattern too, which used to strip off
        # "חזרה" entirely and leave just "77" with no indication it was a
        # return destination (found via real 04.09.2026 data: TL#9 /
        # TL#53, whose "חזרה ל־77" collapsed to a bare "77").
        if "המשך" not in raw:
            return raw
        m = re.search(r"ל[־-](\S+)\s*(.*)$", raw)
        if not m:
            return raw
        num, role = m.group(1), m.group(2).strip()
        if role == "ראש צוות":
            role = 'ר"צ'
        tail = f"{num} {role}".strip()
        if "רענון" in raw:
            return f"רענון ו {tail}"
        if "הפסקה" in raw:
            return f"הפסקה ו {tail}"
        return tail

    # User-confirmed format 2026-07-28 (diffed against a hand-edited target
    # file): "LY279" -> "LY 279" (space after LY), and departure shown with
    # boarding time in parens when available, matching the same "{dep}
    # ({boarding})" convention already used elsewhere in this file (line ~1292).
    def _spaced_flight_num(num):
        m = re.match(r"^(LY)(\d+)$", num, re.IGNORECASE)
        return f"{m.group(1)} {m.group(2)}" if m else num

    # A worker with exactly ONE task in the whole day's schedule always gets
    # a trailing "*" in the report (user-confirmed convention, matches the
    # hand-built schedules this report is meant to mirror — "מוסיפים תמיד
    # כשיש רק טיסה אחת"). Counted once up front against the full schedule,
    # not per flight-group, so a worker appearing on two different flights
    # never gets marked as single-task.
    _real_tasks = schedule_df[~schedule_df["עובד"].astype(str).str.contains("❌", na=False)]
    _task_counts = _real_tasks["עובד"].astype(str).str.strip().value_counts()

    for _, flight in flights_df.iterrows():
        flight_num = str(flight.get("טיסה", "")).strip()
        dep_time = str(flight.get("המראה", "")).strip()
        boarding_time = clean_text(flight.get("בורדינג", ""))
        # FIDS-sourced flights never populate "בורדינג" at all (data_loader.py
        # hardcodes it to "" — see the FIDS parsing branch) — fall back to a
        # boarding duration before departure. User-confirmed 2026-07-28/29:
        # 40 min narrow-body, 55 min wide-body — but ANY remote position
        # (Terminal 1, where every boarding is remote, OR a T3 hardstand gate
        # in REMOTE_GATES) always gets 50 min regardless of body type,
        # overriding the wide-body 55-min figure.
        if not boarding_time and is_time_text(dep_time):
            _gate_for_board = str(flight.get("גייט", "")).strip()
            if get_terminal(_gate_for_board) == "1" or is_remote_gate(_gate_for_board):
                _board_minutes = 50
            elif get_body_type(flight) == "רחב גוף":
                _board_minutes = 55
            else:
                _board_minutes = 40
            _dep_m = time_to_minutes(dep_time) - _board_minutes
            boarding_time = f"{(_dep_m % 1440) // 60:02d}:{(_dep_m % 1440) % 60:02d}"
        destination = str(flight.get("יעד", "")).strip()
        aircraft = get_aircraft_model(flight)
        gate = str(flight.get("גייט", "")).strip()
        pax = clean_text(flight.get("נוסעים", ""))
        registration = str(flight.get("רישוי", "")).strip()
        # Gate + passenger count under the flight number, aircraft registration
        # under the aircraft model — both on the group's second row (user rule
        # 2026-07-28, confirmed from the hand-edited target: "B6(PAX = X)" /
        # "EAI" were real content, not placeholder noise).
        # Written REVERSED ("(pax) gate") on purpose: Excel mirrors paired
        # punctuation like parentheses inside an RTL-aligned cell (readingOrder=2),
        # so a literal "{gate} ({pax})" string visually renders as "(pax) gate" —
        # writing the source string backwards is what actually displays as
        # "gate (pax)" (user-confirmed via screenshot 2026-07-28: code wrote
        # "B6 (178)" but Excel showed "(178) B6").
        gate_pax_text = f"({pax}) {gate}" if (gate or pax) else ""

        tasks = schedule_df[schedule_df["טיסה"].astype(str).str.strip() == flight_num].copy()

        managers = []
        agents = []

        for _, task in tasks.iterrows():
            worker = str(task.get("עובד", "")).strip()
            role = str(task.get("תפקיד בסיס", "")).strip()
            rich_role = str(task.get("תפקיד", "")).strip()

            if not worker:
                continue

            try:
                shift = employee_shift_text(employees_df, worker)
            except Exception:
                shift = ""

            # Personal-status tag, independent of the flight role: "(ס)" for
            # a restricted worker (ילד עובדים/פורשים — "סיירת"), "(ט)" for a
            # new attendant-in-training (דייל בטרייני). Matches the manual
            # shorthand the schedule is built with by hand (user-confirmed
            # 2026-08-30, diffed against real hand-built 03.08/04.08/11.08
            # schedules) — our own export never surfaced either flag before.
            _emp_row_for_tag = employees_df[
                employees_df["שם"].astype(str).str.strip() == worker
            ]
            _personal_tag = ""
            if not _emp_row_for_tag.empty:
                _er = _emp_row_for_tag.iloc[0]
                if (clean_text(str(_er.get("ילד עובדים", ""))) == "כן"
                        or clean_text(str(_er.get("פורשים", ""))) == "כן"):
                    _personal_tag = "ס"
                elif clean_text(str(_er.get("דייל בטרייני", ""))) == "כן":
                    _personal_tag = "ט"

            # Numbering only ever matters for a dual-team-lead flight ("ראש
            # צוות 1"/"ראש צוות 2") — a numbered plain agent ("דייל 1"/"דייל
            # 2") should NOT show a label (user-confirmed via file diff
            # 2026-07-28: target never shows "(1)"/"(2)" for regular agents).
            _show_label = rich_role in _SHOW_LABEL_ROLES_EXACT or bool(re.match(r"^ראש צוות \d+$", rich_role))
            next_task = _simplify_next_task(str(task.get("המשך אזורי", "")).strip())
            _name_part = worker
            if _personal_tag:
                _name_part += f" ({_personal_tag})"
            if _show_label:
                _name_part += f" ({rich_role})"
            text = f"{_name_part} - {next_task}" if next_task else _name_part
            if _task_counts.get(worker, 0) == 1:
                text += " *"
            item = {"shift": shift, "text": text}

            # שומר TSA belongs with the דיילים (agents) team, not the ר"צ
            # (managers) team — and should be the FIRST agent listed (user
            # rule 2026-07-28). מפקח TSA stays a manager; only the guard role
            # moves, so it's checked before the general "TSA" substring test.
            if "שומר TSA" in role:
                agents.insert(0, item)
            elif (
                "ראש צוות" in role
                or "רצ" in role
                or "ר״צ" in role
                or "TSA" in role
                or "מתאם" in role
                or "מפקד" in role
                or "טרייני" in role
            ):
                managers.append(item)
            else:
                agents.append(item)

        # At least 2 rows per flight group even when there's exactly one
        # manager and one agent — the second row is where gate/PAX/
        # registration are shown (user rule 2026-07-28).
        max_rows = max(len(managers), len(agents), 2)

        for i in range(max_rows):
            values = [
                _spaced_flight_num(flight_num) if i == 0 else (gate_pax_text if i == 1 else ""),
                (f"{boarding_time} ({dep_time})" if boarding_time else dep_time) if i == 0 else "",
                destination if i == 0 else "",
                aircraft if i == 0 else (registration if i == 1 else ""),
                managers[i]["text"] if i < len(managers) else "",
                managers[i]["shift"] if i < len(managers) else "",
                agents[i]["text"] if i < len(agents) else "",
                agents[i]["shift"] if i < len(agents) else "",
            ]
            if t1_only:
                values.append("")

            for col_idx, value in enumerate(values, start=1):
                cell = ws.cell(row=current_row, column=col_idx)
                cell.value = value
                cell.font = normal_font
                cell.border = border
                cell.alignment = Alignment(
                    horizontal="center",
                    vertical="center",
                    readingOrder=2,
                    wrap_text=True,
                )

            current_row += 1

        # Blank separator row between flight groups still needs borders on
        # every cell — "all borders" across the whole table, not just rows
        # that happen to have a value (user rule 2026-07-28).
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=current_row, column=col_idx).border = border
        current_row += 1

    # Auto-fit column widths to their actual content (user rule 2026-07-28)
    # instead of the fixed widths above — wide text (long worker/continuation
    # strings) needs more room than a bare flight number or shift range.
    for col_idx in range(1, len(headers) + 1):
        col_letter = chr(64 + col_idx)
        max_len = len(headers[col_idx - 1])
        for row in ws.iter_rows(min_row=4, max_row=current_row - 1, min_col=col_idx, max_col=col_idx):
            for cell in row:
                if cell.value:
                    max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, 12), 45)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()