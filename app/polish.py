"""Final "polish" pass — a small local search over the finished schedule.

Why it exists (user review of the real 23.08 night build, 2026-09-25, 35 workers
commented one by one): the ordinary passes each fix ONE thing greedily
(runner floor time, gap compaction, trainee pairing, ...) and none of them looks
at the schedule as a whole, so the same complaints kept coming back:

  * one ר"צ holds three ראש-צוות flights while another (same shift) holds one;
  * "עזרה באולם" for 70-115 minutes between two flights of a ר"צ;
  * a break that only lands at the very end of the shift;
  * plenty of workers brought down for exactly ONE flight while somebody who
    is already on shift sits idle;
  * a last task that ends exactly when the shift does.

Instead of another special-purpose rule this pass scores the whole schedule with
one cost function (dead time, ר"צ-flight imbalance, single-flight workers, a
break that never happens mid-shift, a task at the shift edge) and repeatedly
applies the best improving move — hand ONE slot to a different worker, or swap
the holders of TWO slots — as long as every hard rule the other passes already
enforce still holds for the receiver (certification, shift/terminal window, no
overlap, break still possible, restricted-worker limits, first task not right
at shift start, last task not right at shift end).

Only דייל / מתאם תורים / ראש צוות slots move; מפקח / שומר TSA, ר"צ-trainee rows,
flights that carry any trainee (the pairing passes own those), USA/TSA flights
and rows locked from an earlier segment are never touched, and a slot's ROLE
never changes — so this pass cannot open a shortage or break a pairing.
"""

from app import scheduler as S
from utils.helpers import clean_text, is_time_text, time_to_minutes, to_datetime_time

_GAP_OK = S._GAP_OK

# ── weights (all "minutes of pain") ──────────────────────────────────────────
W_DEAD = 1.0          # per idle minute between two flights that is not owed as break
W_SINGLE = 45.0       # a worker brought down for exactly one flight
W_SINGLE_LATE_START = 12.0  # ...and so is an early-shift worker who has been idle for 4h+ and whose
                            # FIRST task this is: "להוריד עובד ממשמרת עד 11/12:30/13:30 לטיסה הזאת
                            # כטיסה ראשונה ולנסות למשוך אותו לטיסות מאוחרות יותר" (user 2026-09-25)
LATE_START_AFTER = 240
W_SINGLE_RESTRICTED = 4.0   # ...but a ילד עובדים / פורש is the right person for a lone flight
                            # (user 2026-09-25: "להעדיף ילד עובדים על טיסה בודדת")
W_TL = 40.0           # ר"צ-flight imbalance: delta = 2 * W_TL * (receiver - giver + 1)
W_NOBREAK = 90.0      # >= 3h of work and no between-flight gap that fits the break
W_LATE_BREAK = 0.7    # per minute of continuous work beyond LATE_BREAK_AFTER
W_EDGE = 400.0        # task at the very start / end of the worker's shift
W_CROWD = 2.0         # per minute a gap between two tasks is shorter than CROWD_UNDER
CROWD_UNDER = 15
W_GRP = 12.0          # workers on the SAME shift should carry a similar number of flights
W_TRIP = 45.0         # a gap of TRIP_GAP+ minutes between two tasks = one more trip down to the hall
TRIP_GAP = 80         # user 2026-09-25: after a gap this big the attendant is better back at the counters
W_UNDER = 25.0        # per task a worker on a 7h+ early shift is short of UNDER_TARGET (user 2026-09-25: "לתת עוד טיסות")
UNDER_TARGET = 3
W_RESTRICTED_TASK = 15.0    # a ילד עובדים / פורש working is cheap labour — favour giving them tasks
RESTRICTED_TASK_CAP = 3
W_MOVE = 25.0         # churn: every slot that ends up with a different worker than before the pass
W_LATE = 80.0         # NIGHT build only: a later-shift (day) worker pulled in for it (see late_shift_from)
LATE_BREAK_AFTER = 210
SHIFT_END_BUFFER = 15  # a task must end at least this long before the shift does

LAST_DEBUG = {}   # filled on every run — for tests / debugging only
_ATT_ROLES = ("דייל", "מתאם תורים")
_MOVE_ROLES = ("ראש צוות", "דייל", "מתאם תורים")
# The end-of-shift repair also covers the security guard: 23.08 אפטר had agent#91
# (12:30-00:30) guarding LY027 22:30-00:30 — exactly to her shift end — while a dozen
# qualified workers had nothing (user rule 2026-09-25: a last task may not end at shift end).
_REPAIR_ROLES = _MOVE_ROLES + ("שומר TSA",)


def _yn(row, col):
    return clean_text(str(row.get(col, ""))) == "כן"


def polish_schedule(schedule_df, employees_df, max_rounds=40, late_shift_from=None):
    """Return a polished copy of `schedule_df` (or the input itself when no
    improving move exists).

    late_shift_from: minutes since midnight. When set (the NIGHT segment of a
    segmented build), a worker whose shift starts between that time and 20:00 —
    a day-shift worker — is costly to use at all: user 2026-09-25, "עדיף לשבץ
    את ירדן במקום להוריד אנשים ממשמרת דיי למרות הרווח הגדול"."""
    LAST_DEBUG.clear()      # debug/test hook only — never let it grow across builds
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass
    try:
        emps = employees_df.astype(object)
    except Exception:
        emps = employees_df.copy()
    emp_map = {clean_text(str(r.get("שם", ""))): r for r in emps.to_dict("records")}

    def _m(t):
        try:
            v = to_datetime_time(t)
        except ValueError:
            return None          # blank / unparsable time (flight not in the frame)
        return None if v is None else v.hour * 60 + v.minute

    def _real(name):
        return bool(name) and "❌" not in name

    locked_col = "_נעול" if "_נעול" in df.columns else None
    recs = df.to_dict("records")
    for i, r in zip(df.index, recs):
        r["_i"] = i
        r["עובד"] = clean_text(str(r["עובד"]))
        r["טיסה"] = clean_text(str(r["טיסה"]))
        r["תפקיד בסיס"] = clean_text(str(r.get("תפקיד בסיס", "")))
        s, e = _m(r["התחלה"]), _m(r["סיום"])
        r["_s"] = s
        r["_e"] = None if s is None or e is None else (e if e > s else e + 1440)
        # get_terminal returns the PIER letter for Terminal 3 gates ("D", "C", ...)
        # and "1" for Terminal 1 — what matters here is only which TERMINAL.
        r["_term"] = "1" if S.get_terminal(clean_text(str(r.get("_gate", "")))) == "1" else "3"

    # ── who may take ר"צ slots (same definition as boost_runner_floor_time) ──
    runner_set = set()
    for nm, row in emp_map.items():
        if not nm or not _yn(row, "ראש צוות"):
            continue
        if _yn(row, "מתדרכת") or _yn(row, "טרייני רצ") or S._is_crew_manager(row):
            continue
        if row.get("תגבור שלוחה") is True or row.get("חולה") is True:
            continue
        if S.DUTY_BLOCK_COL in row and clean_text(str(row.get(S.DUTY_BLOCK_COL, ""))):
            continue
        if not (is_time_text(clean_text(str(row.get("תחילת משמרת", "")))) and
                is_time_text(clean_text(str(row.get("סוף משמרת", ""))))):
            continue
        runner_set.add(nm)

    def _eligible_worker(nm):
        row = emp_map.get(nm)
        if row is None or S._is_crew_manager(row) or S._is_trainee_attendant(row):
            return False
        if S._is_instructor(row) or row.get("חולה") is True or row.get("תגבור שלוחה") is True:
            return False
        if S.DUTY_BLOCK_COL in row and clean_text(str(row.get(S.DUTY_BLOCK_COL, ""))):
            return False
        return (is_time_text(clean_text(str(row.get("תחילת משמרת", "")))) and
                is_time_text(clean_text(str(row.get("סוף משמרת", "")))))

    by_name, flight_recs = {}, {}
    for r in recs:
        flight_recs.setdefault(r["טיסה"], []).append(r)
        if _real(r["עובד"]) and r["_s"] is not None and r["_e"] is not None:
            by_name.setdefault(r["עובד"], []).append(r)

    def _trainee_kinds(flight):
        """(carries a דייל-בטרייני, carries a ר"צ trainee)."""
        att = tl = False
        for x in flight_recs.get(flight, []):
            row = emp_map.get(x["עובד"])
            if x["תפקיד בסיס"] in ("טרייני רצ", "טרייני ר״צ", "טרייני ר\"צ"):
                tl = True
            if row is not None:
                if _yn(row, "דייל בטרייני"):
                    att = True
                if _yn(row, "טרייני רצ"):
                    tl = True
        return att, tl

    skip_flights = set()      # nothing on these flights moves (attendant trainee → mentor pairing)
    no_tl_move = set()        # the ראש-צוות slot stays with its mentor
    usa_flights = set()       # no ר"צ works as a plain attendant there
    for f, rs in flight_recs.items():
        att, tl = _trainee_kinds(f)
        if att:
            skip_flights.add(f)
        elif tl:
            no_tl_move.add(f)
        if any(clean_text(str(x.get("יעד", ""))).strip() in S.USA_TSA_DESTS for x in rs):
            usa_flights.add(f)

    def _restricted_cap(flight):
        rs = flight_recs.get(flight, [])
        nd = sum(1 for x in rs if x["תפקיד בסיס"] == "דייל")
        ns = sum(1 for x in rs if x["תפקיד בסיס"] == "שומר TSA")
        return S._max_restricted_attendants({"דייל": nd, "שומר TSA": ns})

    def _restricted_now(flight, ignore=None):
        n = 0
        for x in flight_recs.get(flight, []):
            if x is ignore or x["תפקיד בסיס"] not in ("דייל", "שומר TSA"):
                continue
            row = emp_map.get(x["עובד"])
            if row is not None and S._is_restricted_worker(row):
                n += 1
        return n

    # ── per-worker cost ──────────────────────────────────────────────────────
    def _shift_bounds(row):
        ss = clean_text(str(row.get("תחילת משמרת", "")))
        se = clean_text(str(row.get("סוף משמרת", "")))
        if not (is_time_text(ss) and is_time_text(se)):
            return None
        a, b = time_to_minutes(ss), time_to_minutes(se)
        return a, (b - a) % 1440 or 1440

    restricted = {nm: S._is_restricted_worker(row) for nm, row in emp_map.items()}
    group_of = {}
    static = {}       # per worker: (shift bounds, break, refresh, long shift, night rule)
    windows = {}      # per worker: absolute minute windows the worker may be on shift
    for nm, row in emp_map.items():
        group_of[nm] = (clean_text(str(row.get("תחילת משמרת", ""))),
                        clean_text(str(row.get("סוף משמרת", ""))))
        sb = _shift_bounds(row)
        static[nm] = (sb, S.required_break(row) or 0, S.required_refresh(row) or 20,
                      S.shift_length(row) >= 10 * 60, S.is_night_shift_for_return_rule(row))
        wl = []
        if sb is not None:
            wl.append((sb[0], sb[0] + sb[1]))
        for w in clean_text(str(row.get("זמינות", ""))).split(","):
            if "-" in w:
                a_, b_ = (x.strip() for x in w.split("-", 1))
                if is_time_text(a_) and is_time_text(b_):
                    a_m, b_m = time_to_minutes(a_), time_to_minutes(b_)
                    wl.append((a_m, b_m if b_m > a_m else b_m + 1440))
        windows[nm] = wl

    def _fits_window(nm, rec):
        """Cheap superset of is_within_shift (ignores terminal tags): is the slot
        inside one of the worker's shift / availability windows?"""
        s0, e0 = rec["_s"] % 1440, rec["_s"] % 1440 + (rec["_e"] - rec["_s"])
        for ws, we in windows.get(nm, ()):
            if (s0 >= ws and e0 <= we) or (s0 + 1440 >= ws and e0 + 1440 <= we):
                return True
        return False

    def _idle_waste(rb, long_shift, gaps):
        """Idle minutes of an attendant. The gap that holds the owed break is
        free only up to break + a 15-min walk: a 90-min gap for a 45-min break
        still wastes 30 (user 2026-09-25, agent#87: 221 -> 323 with 90 minutes in
        between — better to hand one of the two flights to someone whose day
        fits, than to keep her waiting)."""
        mid = sorted((g for g in gaps if _GAP_OK < g < 120), reverse=True)
        free = 0
        if rb and mid and mid[0] >= rb:
            free = 2 if long_shift else 1
        return sum(mid[free:]) + sum(max(0, g - (rb + 15)) for g in mid[:free])

    def _dead_waste(rb, rr, long_shift, gaps):
        owed = ([rb] + ([rr] if long_shift else [])) if rb else []
        dead = 0
        for k, g in enumerate(sorted((g for g in gaps if g > _GAP_OK), reverse=True)):
            dead += (g - owed[k]) if k < len(owed) and g >= owed[k] else g
        return dead
    group_mean = {}
    group_tl_mean = {}

    def _refresh_group_means():
        tot, cnt, tl_tot, tl_cnt_ = {}, {}, {}, {}
        for nm, ts in by_name.items():
            if ts and nm in group_of:
                g = group_of[nm]
                tot[g] = tot.get(g, 0) + len(ts)
                cnt[g] = cnt.get(g, 0) + 1
                if nm in runner_set:
                    tl_tot[g] = tl_tot.get(g, 0) + sum(1 for t in ts if t["תפקיד בסיס"] == "ראש צוות")
                    tl_cnt_[g] = tl_cnt_.get(g, 0) + 1
        group_mean.clear()
        group_tl_mean.clear()
        for g in tot:
            group_mean[g] = tot[g] / cnt[g]
        for g in tl_tot:
            group_tl_mean[g] = tl_tot[g] / tl_cnt_[g]

    def worker_cost(name, tasks, acc=None):
        """Cost of one worker's day; `acc` (a dict) collects the components."""
        if not tasks:
            return 0.0
        st = static.get(name)
        if st is None or st[0] is None:
            return 0.0
        (anchor, length), rb, rr, long_shift, night = st
        items = sorted(((t["_s"] - anchor) % 1440, ((t["_s"] - anchor) % 1440) + (t["_e"] - t["_s"]))
                       for t in tasks)
        gaps = [b[0] - a[1] for a, b in zip(items, items[1:])]
        is_runner = name in runner_set
        comp = {}
        comp["dead"] = (_dead_waste(rb, rr, long_shift, gaps) if is_runner
                        else _idle_waste(rb, long_shift, gaps)) * W_DEAD
        late_starter = (items[0][0] >= LATE_START_AFTER and length >= 7 * 60 and anchor < 5 * 60
                        and (anchor + length) % 1440 >= 11 * 60 and (anchor + length) % 1440 <= 14 * 60)
        comp["single"] = ((W_SINGLE_RESTRICTED if restricted.get(name)
                           else (W_SINGLE_LATE_START if late_starter else W_SINGLE))
                          if len(items) == 1 else 0.0)
        comp["trip"] = 0.0 if is_runner else W_TRIP * sum(1 for g in gaps if g >= TRIP_GAP)
        comp["crowd"] = sum((CROWD_UNDER - g) * W_CROWD for g in gaps if g < CROWD_UNDER)
        m = group_mean.get(group_of.get(name))
        comp["group"] = W_GRP * (len(items) - m) ** 2 if (m is not None and W_GRP) else 0.0
        comp["tl"] = 0.0
        if is_runner:
            tm_ = group_tl_mean.get(group_of.get(name))
            if tm_ is not None:
                comp["tl"] = W_TL * (sum(1 for t in tasks if t["תפקיד בסיס"] == "ראש צוות") - tm_) ** 2
        comp["break"] = 0.0
        if rb and not night and len(items) >= 2:
            span = items[-1][1] - items[0][0]
            if span >= 180:
                cont = None
                for a, b in zip(items, items[1:]):
                    if b[0] - a[1] >= rb:
                        cont = a[1] - items[0][0]
                        break
                if cont is None:
                    comp["break"] = W_NOBREAK
                elif cont > LATE_BREAK_AFTER:
                    comp["break"] = (cont - LATE_BREAK_AFTER) * W_LATE_BREAK
        comp["under"] = (W_UNDER * max(0, UNDER_TARGET - len(items))
                         if length >= 7 * 60 and anchor < 5 * 60 and not late_starter else 0.0)
        comp["restricted"] = (-W_RESTRICTED_TASK * min(len(items), RESTRICTED_TASK_CAP)
                              if restricted.get(name) else 0.0)
        comp["late"] = (W_LATE if (late_shift_from is not None
                                   and late_shift_from <= anchor < 20 * 60) else 0.0)
        comp["edge"] = sum(W_EDGE for a, b in items if 0 <= length - b < SHIFT_END_BUFFER)
        if items[0][0] < S._FRESH_START_MIN:
            comp["edge"] += W_EDGE
        if acc is not None:
            for k, v in comp.items():
                acc[k] = acc.get(k, 0.0) + v
        return sum(comp.values())

    def tl_count(name):
        return sum(1 for t in by_name.get(name, []) if t["תפקיד בסיס"] == "ראש צוות")

    # ── hard feasibility of `name` taking `rec` (with `tasks` = their tasks) ─
    def can_take(name, rec, tasks):
        row = emp_map.get(name)
        if row is None or not _eligible_worker(name):
            return False
        role = rec["תפקיד בסיס"]
        flight = rec["טיסה"]
        if any(t["טיסה"] == flight for t in tasks):
            return False
        if role == "ראש צוות":
            if name not in runner_set:
                return False
        elif not _yn(row, role):
            return False
        elif flight in usa_flights and name in runner_set:
            return False        # a ר"צ only fills an attendant slot on a USA flight as a last resort
        if S._is_restricted_worker(row):
            if role != "דייל":
                return False
            if _restricted_now(flight, ignore=rec) + 1 > _restricted_cap(flight):
                return False
        if any(t["_term"] != rec["_term"] for t in tasks):
            return False
        ts_t, te_t = to_datetime_time(rec["התחלה"]), to_datetime_time(rec["סיום"])
        if ts_t is None or te_t is None:
            return False
        gate = clean_text(str(rec.get("_gate", "")))
        if not S.is_within_shift(row, ts_t, te_t, task_terminal=rec["_term"]):
            return False
        if not S.is_available(None, name, ts_t, te_t, row, role, gate, emp_tasks=tasks):
            return False
        sb = _shift_bounds(row)
        if sb is None:
            return False
        anchor, length = sb
        rel_s = (rec["_s"] - anchor) % 1440
        rel_e = rel_s + (rec["_e"] - rec["_s"])
        if length - rel_e < SHIFT_END_BUFFER:
            return False
        if rel_s < S._FRESH_START_MIN and not any(
                ((t["_s"] - anchor) % 1440) < rel_s for t in tasks):
            return False
        if (S.required_break(row) or 0) > 0 and not S.is_night_shift_for_return_rule(row):
            se = to_datetime_time(clean_text(str(row.get("סוף משמרת", ""))))
            if se is not None and not S.has_break_gap_in_schedule(
                    None, name, row, se, emp_tasks=tasks + [rec]):
                return False
        return True

    movable = [r for r in recs
               if r["תפקיד בסיס"] in _MOVE_ROLES and _real(r["עובד"])
               and r["_s"] is not None and r["_e"] is not None
               and r["טיסה"] not in skip_flights
               and not (r["תפקיד בסיס"] == "ראש צוות" and r["טיסה"] in no_tl_move)
               and not (locked_col and r.get(locked_col) is True)
               and _eligible_worker(r["עובד"])
               and (r["תפקיד בסיס"] != "ראש צוות" or r["עובד"] in runner_set)]

    LAST_DEBUG.update(movable=movable, skip_flights=skip_flights, runner_set=runner_set,
                      worker_cost=worker_cost, by_name=by_name, refresh_means=_refresh_group_means, can_take=can_take, tl_count=tl_count)
    for r in recs:
        r["_orig"] = r["עובד"]

    def _moved(rec, holder):
        return 0.0 if holder == rec["_orig"] else W_MOVE

    cost_cache = {}

    def base_cost(name):
        if name not in cost_cache:
            cost_cache[name] = worker_cost(name, by_name.get(name, []))
        return cost_cache[name]

    workers_by_role = {
        "ראש צוות": sorted(runner_set),
        "דייל": sorted(n for n, r in emp_map.items() if _yn(r, "דייל") and _eligible_worker(n)),
        "מתאם תורים": sorted(n for n, r in emp_map.items() if _yn(r, "מתאם תורים") and _eligible_worker(n)),
    }

    def _apply_transfer(rec, new_name, reason):
        old = rec["עובד"]
        by_name[old].remove(rec)
        by_name.setdefault(new_name, []).append(rec)
        rec["עובד"] = new_name
        df.at[rec["_i"], "עובד"] = new_name
        df.at[rec["_i"], "סיבה"] = reason.format(old=old)
        cost_cache.pop(old, None)
        cost_cache.pop(new_name, None)

    changed = 0
    for _round in range(max_rounds):
        _refresh_group_means()
        cost_cache.clear()
        cands = []
        # A) hand one slot to somebody else
        for r in movable:
            giver = r["עובד"]
            g_tasks = [t for t in by_name.get(giver, []) if t is not r]
            g_after = worker_cost(giver, g_tasks)
            g_gain = g_after - base_cost(giver)
            for w in workers_by_role.get(r["תפקיד בסיס"], []):
                if w == giver or not _fits_window(w, r):
                    continue
                w_tasks = by_name.get(w, [])
                d = (g_gain + worker_cost(w, w_tasks + [r]) - base_cost(w)
                     + _moved(r, w) - _moved(r, giver))
                if d < -1.0:
                    cands.append((d, "T", r, w, None))
        # B) swap the holders of two slots
        for ai in range(len(movable)):
            r1 = movable[ai]
            h1 = r1["עובד"]
            for bi in range(ai + 1, len(movable)):
                r2 = movable[bi]
                h2 = r2["עובד"]
                if h1 == h2 or r1["טיסה"] == r2["טיסה"]:
                    continue
                if not (_fits_window(h1, r2) and _fits_window(h2, r1)):
                    continue
                t1 = [t for t in by_name[h1] if t is not r1] + [r2]
                t2 = [t for t in by_name[h2] if t is not r2] + [r1]
                d = (worker_cost(h1, t1) + worker_cost(h2, t2) - base_cost(h1) - base_cost(h2)
                     + _moved(r1, h2) - _moved(r1, h1) + _moved(r2, h1) - _moved(r2, h2))
                if d < -1.0:
                    cands.append((d, "S", r1, h2, r2))
        if not cands:
            break
        cands.sort(key=lambda c: (c[0], c[2]["_i"]))
        LAST_DEBUG.setdefault("rounds", []).append(
            [(round(c[0], 1), c[1], c[2]["טיסה"], c[2]["תפקיד בסיס"], c[2]["עובד"], c[3]) for c in cands[:12]])
        touched_workers, touched_flights = set(), set()
        progressed = False
        for d, kind, r1, w, r2 in cands:
            giver = r1["עובד"]
            if kind == "T":
                if giver in touched_workers or w in touched_workers or r1["טיסה"] in touched_flights:
                    continue
                g_tasks = [t for t in by_name.get(giver, []) if t is not r1]
                # re-check the move against the CURRENT state
                if r1["עובד"] != giver:
                    continue
                w_tasks = by_name.get(w, [])
                d_now = (worker_cost(giver, g_tasks) - base_cost(giver)
                         + worker_cost(w, w_tasks + [r1]) - base_cost(w)
                         + _moved(r1, w) - _moved(r1, giver))
                if d_now >= -1.0 or not can_take(w, r1, w_tasks):
                    continue
                _apply_transfer(r1, w, "שובץ/ה בליטוש סופי — איזון ר\"צים וצמצום זמן מת (במקום {old})")
                touched_workers |= {giver, w}
                touched_flights.add(r1["טיסה"])
            else:
                h1, h2 = giver, w
                if r2["עובד"] != h2 or r1["עובד"] != h1:
                    continue
                if h1 in touched_workers or h2 in touched_workers or \
                        r1["טיסה"] in touched_flights or r2["טיסה"] in touched_flights:
                    continue
                t1 = [t for t in by_name[h1] if t is not r1]
                t2 = [t for t in by_name[h2] if t is not r2]
                if not (can_take(h1, r2, t1) and can_take(h2, r1, t2)):
                    continue
                _apply_transfer(r1, h2, "הוחלף/ה בליטוש סופי — איזון ר\"צים וצמצום זמן מת (עם {old})")
                # r2 goes to h1 (r1 already moved h1 -> h2)
                by_name[h2].remove(r2)
                by_name.setdefault(h1, []).append(r2)
                r2["עובד"] = h1
                df.at[r2["_i"], "עובד"] = h1
                df.at[r2["_i"], "סיבה"] = "הוחלף/ה בליטוש סופי — איזון ר\"צים וצמצום זמן מת (עם " + h2 + ")"
                cost_cache.pop(h1, None)
                cost_cache.pop(h2, None)
                touched_workers |= {h1, h2}
                touched_flights |= {r1["טיסה"], r2["טיסה"]}
            progressed = True
            changed += 1
        if not progressed:
            break
    return df if changed else schedule_df


def repair_shift_end_edges(schedule_df, employees_df, buffer=SHIFT_END_BUFFER):
    """Last resort for tasks the polish pass cannot move (they sit on flights it
    leaves alone — trainee / USA flights): a task that ends exactly when the
    worker's shift does (user 2026-09-25: TL#54, 20:00-06:00, last flight
    05:10-06:00) is handed to a qualified worker who still has `buffer` minutes
    of shift left. Same qualification test the swap UI uses
    (get_qualified_candidates_for_swap), plus the restricted-worker cap and the
    45-minute fresh-start rule; a task with no such candidate is left alone."""
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass
    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    def _bounds(row):
        ss = clean_text(str(row.get("תחילת משמרת", "")))
        se = clean_text(str(row.get("סוף משמרת", "")))
        if not (is_time_text(ss) and is_time_text(se)):
            return None
        a, b = time_to_minutes(ss), time_to_minutes(se)
        return a, ((b - a) % 1440 or 1440)

    def _rel(row, t):
        bd = _bounds(row)
        v = to_datetime_time(t)
        if bd is None or v is None:
            return None
        return (v.hour * 60 + v.minute - bd[0]) % 1440

    def _slack(name, task_end):
        row = emp_map.get(name)
        if row is None:
            return None
        bd = _bounds(row)
        r_end = _rel(row, task_end)
        if bd is None or r_end is None:
            return None
        return bd[1] - (r_end if r_end else 1440)

    trainee_flights = {clean_text(str(df.at[j, "טיסה"])) for j in df.index
                       if clean_text(str(df.at[j, "תפקיד בסיס"])) == "טרייני רצ"
                       and "❌" not in str(df.at[j, "עובד"])}
    changed = 0
    for i in list(df.index):
        who = clean_text(str(df.at[i, "עובד"]))
        role = clean_text(str(df.at[i, "תפקיד בסיס"]))
        if not who or "❌" in who or role not in _REPAIR_ROLES:
            continue
        slack = _slack(who, df.at[i, "סיום"])
        if slack is None or not (0 <= slack < buffer):
            continue
        flight = clean_text(str(df.at[i, "טיסה"]))
        df.at[i, "עובד"] = f"❌ חסר {role}"
        cands = [clean_text(c) for c in S.get_qualified_candidates_for_swap(df, employees_df, flight, role, i)]
        best = None
        for c in cands:
            row = emp_map.get(c)
            if row is None or S._is_trainee_attendant(row) or S._is_instructor(row) or S._is_crew_manager(row):
                continue
            if role == "ראש צוות" and flight in trainee_flights and not S._mentor_cert(row):
                continue        # the mentor of a ר"צ trainee stays a mentor
            sl = _slack(c, df.at[i, "סיום"])
            if sl is None or sl < buffer:
                continue
            rs = _rel(row, df.at[i, "התחלה"])
            mine = df[df["עובד"].astype(str).str.strip() == c]
            if rs is not None and rs < S._FRESH_START_MIN and not any(
                    (_rel(row, x) or 0) < rs for x in mine["התחלה"]):
                continue
            if S._is_restricted_worker(row):
                if role != "דייל":
                    continue
                fl = df[df["טיסה"].astype(str).str.strip() == flight]
                nd = int((fl["תפקיד בסיס"] == "דייל").sum())
                ns = int((fl["תפקיד בסיס"] == "שומר TSA").sum())
                rc = sum(1 for j in fl.index if j != i and clean_text(str(fl.at[j, "תפקיד בסיס"])) in ("דייל", "שומר TSA")
                         and emp_map.get(clean_text(str(fl.at[j, "עובד"]))) is not None
                         and S._is_restricted_worker(emp_map[clean_text(str(fl.at[j, "עובד"]))]))
                if rc + 1 > S._max_restricted_attendants({"דייל": nd, "שומר TSA": ns}):
                    continue
            key = (0 if len(mine) else 1, len(mine), -sl)
            if best is None or key < best[0]:
                best = (key, c)
        if best is None:
            df.at[i, "עובד"] = who
            continue
        df.at[i, "עובד"] = best[1]
        df.at[i, "סיבה"] = f"הוחלף/ה — משימה אחרונה לא מסתיימת בדיוק בסוף המשמרת (במקום {who})"
        changed += 1
    return df if changed else schedule_df


def _restricted_room(df, emp_map, flight, skip_idx, cand_row, role):
    """May `cand_row` (a restricted worker: ילד עובדים / פורשים) fill a `role`
    slot on `flight` without breaking the per-flight cap?"""
    if not S._is_restricted_worker(cand_row):
        return True
    if role != "דייל":
        return False
    fl = df[df["טיסה"].astype(str).str.strip() == flight]
    nd = int((fl["תפקיד בסיס"] == "דייל").sum())
    ns = int((fl["תפקיד בסיס"] == "שומר TSA").sum())
    rc = 0
    for j in fl.index:
        if j == skip_idx or clean_text(str(fl.at[j, "תפקיד בסיס"])) not in ("דייל", "שומר TSA"):
            continue
        row = emp_map.get(clean_text(str(fl.at[j, "עובד"])))
        if row is not None and S._is_restricted_worker(row):
            rc += 1
    return rc + 1 <= S._max_restricted_attendants({"דייל": nd, "שומר TSA": ns})


def convert_attendant_trainees(schedule_df, employees_df, flights_df):
    """A ר"צ trainee who is on a flight as a plain דייל, while a mentor ר"צ
    leads that same flight and nobody shadows the mentor, becomes the flight's
    "טרייני ר"צ" — the vacated דייל slot is back-filled by a qualified worker.

    maximize_tl_trainee_coverage only ever offers the trainee slot to a trainee
    who is NOT already on the flight, so a trainee working the same flight as an
    attendant (real 23.08: TL-trainee#6, LY5113 next to the mentor TL#5)
    never got it (user 2026-09-25, "למה לא טרייני ר"צ?"). A conversion that finds
    no back-fill is undone."""
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass
    try:
        emps = employees_df.astype(object)
    except Exception:
        emps = employees_df.copy()
    emp_map = {clean_text(str(r.get("שם", ""))): r for r in emps.to_dict("records")}
    flights_by_num = {clean_text(str(r.get("טיסה", ""))): r for _, r in flights_df.iterrows()}
    locked_col = "_נעול" if "_נעול" in df.columns else None

    def _on_roster(row):
        return clean_text(str(row.get("in_daily_excel", ""))) in ("1", "1.0")

    trainee_names = {n for n, row in emp_map.items()
                     if n and _yn(row, "טרייני רצ") and _on_roster(row) and row.get("חולה") is not True}
    if not trainee_names:
        return schedule_df

    def _mentor_ok(name, training_type):
        row = emp_map.get(name)
        if row is None:
            return False
        if training_type == "הסמכה":
            return _yn(row, "מסמיך רצים")
        return _yn(row, "חונך רצים") or _yn(row, "מסמיך רצים")

    def _etd(fn):
        f = flights_by_num.get(fn)
        v = to_datetime_time(f.get("המראה", "")) if f is not None else None
        return 0 if v is None else v.hour * 60 + v.minute

    changed = 0
    for fn in sorted({clean_text(str(x)) for x in df["טיסה"]}, key=lambda f: (_etd(f), f)):
        flight = flights_by_num.get(fn)
        if flight is None:
            continue
        idx = [i for i in df.index if clean_text(str(df.at[i, "טיסה"])) == fn]
        real = [i for i in idx if "❌" not in str(df.at[i, "עובד"])]
        if any(clean_text(str(df.at[i, "תפקיד בסיס"])) == "טרייני רצ" for i in real):
            continue
        training_type = clean_text(flight.get("סוג הכשרה", "")) or "חניכה"
        tl = [i for i in real if clean_text(str(df.at[i, "תפקיד בסיס"])) == "ראש צוות"]
        if not any(_mentor_ok(clean_text(str(df.at[i, "עובד"])), training_type) for i in tl):
            continue
        att = [i for i in real if clean_text(str(df.at[i, "תפקיד בסיס"])) == "דייל"
               and clean_text(str(df.at[i, "עובד"])) in trainee_names
               and not (locked_col and df.at[i, locked_col] is True)]
        ts_t, te_t = S.role_start_time(flight, "טרייני רצ"), S.role_end_time(flight)
        gate = clean_text(flight.get("גייט", "")) or clean_text(str(flight.get("שלוחה", "")))
        term = S.get_terminal(gate)
        for i in att:
            who = clean_text(str(df.at[i, "עובד"]))
            crow = emp_map[who]
            mine = [dict(r) for j, r in df[df["עובד"].astype(str).str.strip() == who].iterrows() if j != i]
            if not S.is_within_shift(crow, ts_t, te_t, task_terminal=term):
                continue
            if not S.is_available(None, who, ts_t, te_t, crow, "טרייני רצ", gate, emp_tasks=mine):
                continue
            saved = df.loc[i].copy()
            new_row = df.loc[i].copy()
            df.at[i, "תפקיד"] = 'טרייני ר"צ'
            df.at[i, "תפקיד בסיס"] = "טרייני רצ"
            df.at[i, "התחלה"] = ts_t.strftime("%H:%M")
            df.at[i, "סיום"] = te_t.strftime("%H:%M")
            df.at[i, "סיבה"] = 'שובץ/ה לחניכה — טרייני ר"צ במקום דייל (עם ראש הצוות החונך)'
            ag_start = S.role_start_time(flight, "דייל")
            new_row["עובד"] = "❌ חסר דייל"
            new_row["תפקיד בסיס"] = "דייל"
            new_row["התחלה"] = ag_start.strftime("%H:%M")
            new_row["סיום"] = te_t.strftime("%H:%M")
            new_row["סיבה"] = ""
            df = df.append(new_row, ignore_index=True) if hasattr(df, "append") else \
                __import__("pandas").concat([df, new_row.to_frame().T], ignore_index=True)
            new_i = df.index[-1]
            cands = [clean_text(c) for c in S.get_qualified_candidates_for_swap(
                df, employees_df, fn, "דייל", new_i)]
            best = None
            for c in cands:
                row = emp_map.get(c)
                if (row is None or c == who or S._is_trainee_attendant(row) or S._is_instructor(row)
                        or S._is_crew_manager(row)):
                    continue
                if not _restricted_room(df, emp_map, fn, new_i, row, "דייל"):
                    continue
                n_tasks = int((df["עובד"].astype(str).str.strip() == c).sum())
                key = (0 if n_tasks else 1, 0 if c not in trainee_names else 1, n_tasks)
                if best is None or key < best[0]:
                    best = (key, c)
            if best is None:
                df = df.drop(index=new_i)
                df.loc[i] = saved
                continue
            df.at[new_i, "עובד"] = best[1]
            df.at[new_i, "סיבה"] = f"שובץ/ה במקום {who} שעברה לחניכת טרייני ר\"צ"
            changed += 1
            break
    return df.reset_index(drop=True) if changed else schedule_df


# Roles worth freeing someone for, and the roles they may be freed FROM.
_CRITICAL_ROLES = ("מפקח TSA", "ראש צוות")
_RELEASABLE_ROLES = ("דייל", "מתאם תורים", "שומר TSA")


def fill_critical_by_release(schedule_df, employees_df):
    """Fill a still-missing מפקח TSA / ראש צוות slot by freeing a certified worker
    from an overlapping דייל / מתאם תורים / שומר TSA task, which is handed to
    someone else.

    The polish only moves filled slots and knows nothing about ❌ ones, so it
    happily parked the day's only in-shift inspector on a דייל slot and left an
    inspector slot unfillable next to it — real 23.08 after-segment: TSA-inspector#3
    on LY081 (דייל 21:15-22:20) while LY017's inspector slot (22:15, pier C —
    the same pier as her LY027/LY025) stayed ❌ (user 2026-09-27). All-or-
    nothing per slot: applied only when every released task gets a replacement,
    so it never trades one hole for another."""
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass
    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}
    locked_col = "_נעול" if "_נעול" in df.columns else None

    def _span(i):
        a, b = to_datetime_time(df.at[i, "התחלה"]), to_datetime_time(df.at[i, "סיום"])
        if a is None or b is None:
            return None
        s, e = a.hour * 60 + a.minute, b.hour * 60 + b.minute
        return s, (e if e > s else e + 1440)

    def _overlaps(x, y, pad=5):
        return any(x[0] < y[1] + pad + ph and y[0] + ph < x[1] + pad for ph in (-1440, 0, 1440))

    def _locked(i):
        return locked_col is not None and df.at[i, locked_col] is True

    def _pier(i):
        return S.get_terminal(clean_text(str(df.at[i, "_gate"]))) if "_gate" in df.columns else ""

    changed = 0
    for i in list(df.index):
        role = clean_text(str(df.at[i, "תפקיד בסיס"]))
        if role not in _CRITICAL_ROLES or "❌" not in str(df.at[i, "עובד"]) or _locked(i):
            continue
        flight = clean_text(str(df.at[i, "טיסה"]))
        sp = _span(i)
        if sp is None:
            continue
        options = []
        for name, row in emp_map.items():
            if clean_text(str(row.get(role, ""))) != "כן":
                continue
            if S._is_trainee_attendant(row) or S._is_instructor(row):
                continue
            mine = [j for j in df.index if clean_text(str(df.at[j, "עובד"])) == name]
            if any(clean_text(str(df.at[j, "טיסה"])) == flight for j in mine):
                continue
            clash = []
            for j in mine:
                sj = _span(j)
                if sj is None or not _overlaps(sp, sj):
                    continue
                rj = clean_text(str(df.at[j, "תפקיד בסיס"]))
                if role == "מפקח TSA" and rj == "מפקח TSA" and _pier(j) == _pier(i):
                    continue          # parallel same-pier inspection is allowed (cap checked below)
                clash.append(j)
            if not clash:
                continue              # a free worker would already be in the swap list below
            if any(clean_text(str(df.at[j, "תפקיד בסיס"])) not in _RELEASABLE_ROLES or _locked(j)
                   for j in clash):
                continue
            options.append((len(clash), len(mine), name, clash))
        # A plainly free candidate first (e.g. freed by an earlier fill in this loop).
        direct = [clean_text(c) for c in S.get_qualified_candidates_for_swap(df, employees_df, flight, role, i)]
        if direct:
            df.at[i, "עובד"] = direct[0]
            if "סיבה" in df.columns:
                df.at[i, "סיבה"] = "השלמת משבצת חסרה — עובד/ת פנוי/ה"
            changed += 1
            continue
        for _n_clash, _n_tasks, name, clash in sorted(options):
            saved = {j: df.at[j, "עובד"] for j in clash}
            for j in clash:
                df.at[j, "עובד"] = f"❌ חסר {clean_text(str(df.at[j, 'תפקיד בסיס']))}"
            if name not in {clean_text(c) for c in
                            S.get_qualified_candidates_for_swap(df, employees_df, flight, role, i)}:
                for j, w in saved.items():
                    df.at[j, "עובד"] = w
                continue
            df.at[i, "עובד"] = name
            fills, ok = {}, True
            for j in clash:
                fj = clean_text(str(df.at[j, "טיסה"]))
                rj = clean_text(str(df.at[j, "תפקיד בסיס"]))
                pick = None
                _usa = any(clean_text(str(df.at[k, "יעד"])) in S.USA_TSA_DESTS
                           for k in df.index if clean_text(str(df.at[k, "טיסה"])) == fj) if "יעד" in df.columns else False
                _cands = [clean_text(c) for c in S.get_qualified_candidates_for_swap(df, employees_df, fj, rj, j)]
                # on a USA flight a ר"צ fills a plain attendant slot only as a last resort
                _cands.sort(key=lambda c: bool(_usa and rj == "דייל" and emp_map.get(c) is not None
                                               and _yn(emp_map[c], "ראש צוות")))
                for c in _cands:
                    crow = emp_map.get(c)
                    if c == name or crow is None or S._is_trainee_attendant(crow) or S._is_instructor(crow):
                        continue
                    if not _restricted_room(df, emp_map, fj, j, crow, rj):
                        continue
                    pick = c
                    break
                if pick is None:
                    ok = False
                    break
                df.at[j, "עובד"] = pick
                fills[j] = pick
            if not ok:
                df.at[i, "עובד"] = f"❌ חסר {role}"
                for j, w in saved.items():
                    df.at[j, "עובד"] = w
                continue
            if "סיבה" in df.columns:
                df.at[i, "סיבה"] = f"השלמת {role} חסר — שוחרר/ה ממשימה חופפת בתפקיד נמוך יותר"
                for j in fills:
                    df.at[j, "סיבה"] = f"הוחלף/ה כדי ש-{name} ימלא/תמלא {role} חסר"
            changed += 1
            break
    return df


# ── evening workers go down once ─────────────────────────────────────────────
EVENING_START_BAND = (14 * 60, 19 * 60 + 30)   # shift start 14:00-19:30
EVENING_NIGHT_BLOCK_FROM = 21 * 60             # a task from here on is the "night block"
EVENING_MIN_GAP = 150                          # a gap this long between two of their tasks = second trip


def unify_evening_rounds(schedule_df, employees_df):
    """An evening-shift worker (start 14:00-19:30, on duty for the late flights)
    who has an earlier task AND a night block, with a long gap between, goes down
    for ONE round only: the movable side is handed to a rostered worker who has
    no task at all.

    User rules 2026-08-06 (TL#24 19:00-01:30: "אם עובד שהתחיל בין 15:00-19:30
    משובץ שוב לטיסת לילה הפער לא בסדר! יש להוריד אותו לטיסות רק לסבב אחד. אין
    ליצור פער") and 2026-09-27, who found the same on the real 23.08 day: 14:00-
    01:30 attendants (agent#90 15:20 → 23:55, agent#89, agent#88...) taking
    one afternoon flight and then coming back down at midnight, while ~15 people
    sat at the counters with nothing. The fill_idle_gaps rule only covered starts
    from 15:00 and treated any 3h+ gap of a long shift as counter duty.

    The early flights are the optional ones: they are handed off and the night
    block is kept. When the early task belongs to an earlier segment (locked —
    the דיי team already published it) the night block is handed off instead.
    All-or-nothing per worker; every receiver must be qualified, in shift, free,
    and currently without any task, so no hole is moved around."""
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass
    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}
    locked_col = "_נעול" if "_נעול" in df.columns else None

    def _locked(i):
        return locked_col is not None and df.at[i, locked_col] is True

    def _mins(t):
        v = to_datetime_time(t)
        return None if v is None else v.hour * 60 + v.minute

    def _tasks(name):
        return [j for j in df.index if clean_text(str(df.at[j, "עובד"])) == name]

    def _rel(name, j):
        row = emp_map[name]
        a = time_to_minutes(clean_text(str(row.get("תחילת משמרת", ""))))
        s, e = _mins(df.at[j, "התחלה"]), _mins(df.at[j, "סיום"])
        rs = (s - a) % 1440
        return rs, rs + ((e - s) % 1440 or 1440)

    def _has_no_task(name):
        return not _tasks(name)

    changed = 0
    for name in sorted(emp_map):
        row = emp_map[name]
        ss = clean_text(str(row.get("תחילת משמרת", "")))
        if not is_time_text(ss):
            continue
        # Any non-night shift, not only the 14:00-19:30 evening band this rule
        # started from: real 23.08 also showed it on 12:30-00:30 workers (agent#92,
        # agent#93 — an early-afternoon flight then a lone ~23:00 one,
        # ~7h apart). A genuine night shift (22:00-ish start, is_night_shift_for_
        # return_rule) keeps its own separate return-to-counters pattern and is
        # excluded, same as fix_wasteful_gaps / fill_idle_gaps already do.
        if S.is_night_shift_for_return_rule(row):
            continue
        if clean_text(str(row.get("טרמינל", ""))) == "1" or S._is_restricted_worker(row) \
                or S._is_trainee_attendant(row) or S._is_instructor(row) or S._is_crew_manager(row):
            continue
        idx = _tasks(name)
        if len(idx) < 2:
            continue
        idx.sort(key=lambda j: _rel(name, j)[0])
        cut = None
        for a, b in zip(idx, idx[1:]):
            gap = _rel(name, b)[0] - _rel(name, a)[1]
            start_b = _mins(df.at[b, "התחלה"])
            # the later task is the night block: from 21:00 through the small hours
            in_night_block = start_b is not None and (start_b >= EVENING_NIGHT_BLOCK_FROM or start_b < 6 * 60)
            if gap >= EVENING_MIN_GAP and in_night_block:
                cut = b
                break
        if cut is None:
            continue
        pos = idx.index(cut)
        early, night = idx[:pos], idx[pos:]
        movers = early if not any(_locked(j) for j in early) else night
        if any(_locked(j) for j in movers) or any(
                clean_text(str(df.at[j, "תפקיד בסיס"])) not in _MOVE_ROLES for j in movers):
            continue
        saved = {j: df.at[j, "עובד"] for j in movers}
        for j in movers:
            df.at[j, "עובד"] = f"❌ חסר {clean_text(str(df.at[j, 'תפקיד בסיס']))}"
        picks, used, ok = {}, set(), True
        for j in movers:
            fj = clean_text(str(df.at[j, "טיסה"]))
            rj = clean_text(str(df.at[j, "תפקיד בסיס"]))
            pick = None
            for c in S.get_qualified_candidates_for_swap(df, employees_df, fj, rj, j):
                c = clean_text(c)
                crow = emp_map.get(c)
                if c == name or c in used or crow is None or S._is_trainee_attendant(crow) \
                        or S._is_instructor(crow) or S._is_crew_manager(crow):
                    continue
                if not _has_no_task(c) or not _restricted_room(df, emp_map, fj, j, crow, rj):
                    continue
                # a ר"צ never fills a plain attendant slot just to be "someone with nothing"
                if rj == "דייל" and clean_text(str(crow.get("ראש צוות", ""))) == "כן":
                    continue
                pick = c
                break
            if pick is None:
                ok = False
                break
            used.add(pick)
            picks[j] = pick
            df.at[j, "עובד"] = pick
        if not ok:
            for j, w in saved.items():
                df.at[j, "עובד"] = w
            continue
        if "סיבה" in df.columns:
            for j, pick in picks.items():
                df.at[j, "סיבה"] = f"הועבר כדי ש{name} תרד לסבב אחד בלבד — משמרת ערב, בלי חזרה לדלפקים באמצע"
        changed += 1
    return df
