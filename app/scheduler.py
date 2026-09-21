from datetime import time, timedelta
from unicodedata import name
from unittest import result

import pandas as pd

from utils.constants import (
    NARROW_REG_PREFIXES, WIDE_REG_PREFIXES, LEASED_NARROW_REG_PREFIXES,
    AIRCRAFT_MODEL_EXACT, AIRCRAFT_MODEL_PREFIX,
    REMOTE_GATES, TERMINAL1_GATES,
    USA_TSA_DESTS, QUEUE_DESTS, TWO_TEAM_LEADS_DESTS,
    ROLE_ORDER, ROLE_COLUMNS, LATE_SHIFT_END_MAX,
    MAX_CONTINUOUS_WORK_MINUTES, NIGHT_BREAK_WINDOW_START, NIGHT_BREAK_WINDOW_END,
    INACTIVE_COL, RESTRICTED_WORKER_COLS, TRAINEE_ATTENDANT_COL,
    INSTRUCTOR_COL, CREW_CHIEF_MANAGER_COL,
)
from utils.helpers import (
    clean_text, is_time_text, to_datetime_time, time_to_minutes, minutes_between,
    name_key, normalize_role_label,
    area_switch_penalty, classify_shift, shift_length, required_break, required_refresh,
    preferred_break_window_by_shift, is_cancelled_flight,
)


# =========================
# FLIGHT RULES
# =========================

def is_leased_aircraft(flight):
    """מטוס חכור (רישוי LOC/BBN/PEX/EAC/EAI/MGM/PMI) — צר גוף, מסומן בסגול."""
    reg = clean_text(flight.get("רישוי", "")).upper()
    return reg.startswith(LEASED_NARROW_REG_PREFIXES)


def is_ferry_flight(flight):
    """טיסת FERRY — אין מידע בעמודת Pax ב-FIDS (או 0) = יוצאת ללא נוסעים.
    לא משובץ אליה צוות כלל (לא דיילים ולא ראש צוות). דוגמה: LY2221."""
    pax = clean_text(flight.get("נוסעים", ""))
    return pax in ("", "0")


def get_body_type(flight):
    reg = clean_text(flight.get("רישוי", "")).upper()
    aircraft = clean_text(flight.get("סוג מטוס", "")).upper()

    # מטוסים חכורים הם צרי גוף — נבדק ראשון, לפני ברירות המחדל של הרישוי
    if reg.startswith(LEASED_NARROW_REG_PREFIXES):
        return "צר גוף"
    if reg.startswith(NARROW_REG_PREFIXES):
        return "צר גוף"
    if reg.startswith(WIDE_REG_PREFIXES):
        return "רחב גוף"
    # "LOC" הוא קוד מטוס צר-גוף בנתונים (אושר ע"י המשתמשת 2026-07); בלי זה הוא
    # נפל לברירת המחדל "רחב גוף" למטה.
    if aircraft.startswith(("737", "738", "739", "E", "LOC")):
        return "צר גוף"

    return "רחב גוף"


def get_aircraft_model(flight):
    """Specific aircraft model (e.g. "737-8") by registration prefix — for
    the "דוח שיבוץ טיסות" export's "מטוס" column. Distinct from
    get_body_type (narrow/wide only). Returns "" when the registration
    doesn't match any known prefix (user-confirmed mapping 2026-07-26)."""
    reg = clean_text(flight.get("רישוי", "")).upper()
    if reg in AIRCRAFT_MODEL_EXACT:
        return AIRCRAFT_MODEL_EXACT[reg]
    return AIRCRAFT_MODEL_PREFIX.get(reg[:2], "")


def is_remote_gate(gate):
    return clean_text(gate).upper() in REMOTE_GATES


def get_pax(flight):
    value = flight.get("נוסעים", 0)
    if pd.isna(value) or clean_text(value) == "":
        return 0
    try:
        return int(float(value))
    except Exception:
        return 0


def get_requirements(flight):
    dest = clean_text(flight["יעד"]).upper()
    body = get_body_type(flight)
    pax  = get_pax(flight)

    req = {
        "ראש צוות":   1,
        "דייל":       1,
        "מתאם תורים": 0,
        "מפקח TSA":   0,
        "שומר TSA":   0,
        "טרייני רצ":  0,
    }

    if body == "צר גוף":
        req["דייל"] = 1 if pax <= 150 else 2
    else:
        if dest in USA_TSA_DESTS:
            req["דייל"]     = 4
            req["שומר TSA"] = 1
        else:
            req["דייל"] = 3

    if dest in TWO_TEAM_LEADS_DESTS:
        req["ראש צוות"] = 2

    if dest in QUEUE_DESTS:
        req["מתאם תורים"] = 1

    if dest in USA_TSA_DESTS:
        req["מפקח TSA"] = 1

    # טיסת הכשרת טרייני ר"צ — מסומנת ידנית בעורך הטיסות (עמודת 'טרייני ר"צ').
    # נפרד לחלוטין מ"דייל בטרייני" (דייל חדש שמוצמד לדייל ותיק דרך הסידור היומי).
    if clean_text(str(flight.get("טרייני רצ", ""))).strip() == "כן":
        req["טרייני רצ"] = 1

    return req


def requirements_text(flight):
    req = get_requirements(flight)
    parts = []
    for role in ROLE_ORDER:
        amount = req.get(role, 0)
        if amount > 0:
            parts.append(role if amount == 1 else f"{role} × {amount}")
    return parts


def role_start_time(flight, role):
    departure    = to_datetime_time(flight["המראה"])
    body         = get_body_type(flight)
    remote_narrow = body == "צר גוף" and is_remote_gate(flight.get("גייט", ""))

    if role in {"ראש צוות", "מתאם תורים", "טרייני רצ"}:
        minutes_before = 60 if body == "צר גוף" else 75
    elif role == "דייל":
        minutes_before = 50 if body == "צר גוף" else 65
    elif role in {"מפקח TSA", "שומר TSA"}:
        minutes_before = 120
    else:
        minutes_before = 60

    if remote_narrow:
        minutes_before += 10

    return departure - timedelta(minutes=minutes_before)


def role_end_time(flight):
    return to_datetime_time(flight["המראה"])


# =========================
# SHIFT / AVAILABILITY
# =========================


def is_within_shift(emp, task_start, task_end, task_terminal=None):
    # Upcoming-night workers (tonight's night band, night N→N+1) are NOT hard-
    # excluded anymore — they may staff tonight's LATE flights as a last resort
    # (sort_candidates keeps them last). Their זמינות is restricted to the
    # late-night tail (start→01:30) in apply_shift_map_to_employees, and that
    # window governs eligibility below. GUARD: if such a worker somehow has NO
    # explicit זמינות window, fall back to the old hard exclusion so their full
    # 22:00-07:00 shift can't leak into the schedule's early-morning flights.
    try:
        _is_upcoming = int(emp.get("upcoming_night", 0)) == 1
    except (TypeError, ValueError):
        _is_upcoming = False
    if _is_upcoming and not clean_text(emp.get("זמינות", "")):
        return False

    shift_start = clean_text(emp.get("תחילת משמרת", ""))
    shift_end   = clean_text(emp.get("סוף משמרת", ""))

    if not is_time_text(shift_start) or not is_time_text(shift_end):
        return False

    # Terminal gating: when the task's terminal is known ("1" = Terminal 1,
    # anything else = Terminal 3 side), a worker's window only qualifies if it
    # belongs to that terminal. Window terminals come from the aligned
    # "זמינות טרמינל" column; workers/windows without terminal data are "3".
    # task_terminal=None (legacy callers) skips the check entirely.
    _want_term = None
    if task_terminal is not None:
        _want_term = "1" if clean_text(task_terminal) == "1" else "3"

    ts = task_start.hour * 60 + task_start.minute
    te = task_end.hour   * 60 + task_end.minute

    avail_str = clean_text(emp.get("זמינות", ""))
    if avail_str:
        _terms = [t.strip() for t in clean_text(emp.get("זמינות טרמינל", "")).split(",")] if clean_text(emp.get("זמינות טרמינל", "")) else []
        for _wi, window in enumerate(avail_str.split(",")):
            if "-" not in window:
                continue
            if _want_term is not None:
                _wterm = _terms[_wi] if _wi < len(_terms) and _terms[_wi] else "3"
                if _wterm != _want_term:
                    continue
            try:
                ws, we = window.strip().split("-", 1)
                ws_m = time_to_minutes(ws.strip())
                we_m = time_to_minutes(we.strip())
                if we_m < ws_m: we_m += 1440
                ts_n = ts if ts >= ws_m else ts + 1440
                te_n = te if te >= ts_n % 1440 else te + 1440
                if te_n <= ts_n: te_n += 1440
                if ts_n >= ws_m and te_n <= we_m:
                    return True
            except Exception:
                pass
        return False

    # No explicit windows: the worker's base terminal (default "3") must match.
    if _want_term is not None:
        _base_term = clean_text(emp.get("טרמינל", "")) or "3"
        if _base_term != _want_term:
            return False

    s = time_to_minutes(shift_start)
    e = time_to_minutes(shift_end)

    if e <= s:
        e += 1440

    for ts_norm in [ts, ts + 1440]:
        te_norm = te if te > ts_norm % 1440 else te + 1440
        if te_norm <= ts_norm:
            te_norm += 1440
        if ts_norm >= s and te_norm <= e:
            return True

    return False


def assigned_minutes(assignments, emp_name, emp_tasks=None):
    tasks = emp_tasks if emp_tasks is not None else [t for t in assignments if t["עובד"] == emp_name]
    total = 0
    for task in tasks:
        if clean_text(task.get("התחלה", "")) == "" or clean_text(task.get("סיום", "")) == "":
            continue
        total += minutes_between(to_datetime_time(task["התחלה"]), to_datetime_time(task["סיום"]))
    return total


def has_room_for_break(assignments, emp, emp_name, start, end, emp_tasks=None):
    length = shift_length(emp)
    if length == 0:
        return False
    current = assigned_minutes(assignments, emp_name, emp_tasks=emp_tasks)
    new = minutes_between(start, end)
    return current + new + required_break(emp) <= length


def has_break_gap_in_schedule(assignments, emp_name, emp, new_task_start, emp_tasks=None):
    """
    Returns False when the employee has already worked ≥ 3 h and their
    existing schedule (up to new_task_start) contains no gap of at least
    required_break minutes — meaning they have had no real opportunity for a break.
    Prevents the scheduler from stacking back-to-back flights on an employee
    until the shift is full with no break window.
    """
    rb = required_break(emp)
    if rb <= 0:
        return True

    ss_str = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss_str):
        return True

    shift_start_m = time_to_minutes(ss_str)
    new_start_m = new_task_start.hour * 60 + new_task_start.minute
    if new_start_m < shift_start_m:
        new_start_m += 1440

    tasks = emp_tasks if emp_tasks is not None else [t for t in assignments if t["עובד"] == emp_name]
    intervals = []
    _task_by_end: dict = {}
    for task in tasks:
        ts_str = clean_text(task.get("התחלה", ""))
        te_str = clean_text(task.get("סיום",   ""))
        if not ts_str or not te_str:
            continue
        ts = time_to_minutes(ts_str)
        te = time_to_minutes(te_str)
        if ts < shift_start_m: ts += 1440
        if te < shift_start_m: te += 1440
        if te < ts: te += 1440
        if ts < new_start_m:
            _clipped_te = min(te, new_start_m)
            intervals.append((ts, _clipped_te))
            _task_by_end[_clipped_te] = task

    if not intervals:
        return True  # no tasks yet

    intervals.sort()
    total_worked = sum(te - ts for ts, te in intervals)

    # A restricted worker (ילד עובדים/פורשים) who just finished a שומר TSA duty
    # gets their break treated as urgent IMMEDIATELY — not only after the usual
    # 3h cumulative grace period. Restricted workers can only fill שומר TSA /
    # דייל slots (see _RESTRICTED_FORBIDDEN_ROLES), so a guard shift followed
    # right away by another flight leaves them with no real break opportunity
    # until much later, if at all (user rule 2026-07-14, scoped narrowly:
    # "רק שומר TSA שמסתיים מוקדם [ב]מוגבלות" — found via real data: restricted-agent#6, guard duty ending 05:00, immediately stacked onto LY279 at
    # 05:25 with only 25 min between — below his 45-min break requirement,
    # yet the 3h grace period let it through since he'd only worked ~2h).
    _is_restricted_emp = (
        clean_text(str(emp.get("ילד עובדים", ""))) == "כן"
        or clean_text(str(emp.get("ילדי עובדים", ""))) == "כן"
        or clean_text(str(emp.get("פורשים", ""))) == "כן"
    )
    _last_te = max(te for _, te in intervals)
    _last_task = _task_by_end.get(_last_te)
    _last_was_guard = (
        _last_task is not None
        and clean_text(str(_last_task.get("תפקיד בסיס", ""))) == "שומר TSA"
    )
    _break_urgent_now = _is_restricted_emp and _last_was_guard

    if total_worked < 3 * 60 and not _break_urgent_now:
        return True  # less than 3 h worked — break not urgent yet

    # Look for a gap >= rb between any two consecutive tasks.
    # The stretch BEFORE the FIRST task is counters time, not a break — a worker
    # standing at check-in from shift start until their first flight never rested.
    # Counting it used to let the scheduler stack flights back-to-back for the rest
    # of the shift (e.g. an 11:30-21:00 worker whose first flight was 45+ min after
    # shift start got zero real break all day). It only counts as a break
    # opportunity where it overlaps the shift's PREFERRED break window — the one
    # legitimate case being early 02:00 shifts, whose break is by design taken
    # before the flights (preferred window 03:00-05:00).
    prev_end = shift_start_m
    first_gap = True
    for ts, te in intervals:
        if first_gap:
            _ps, _pe = preferred_break_window_by_shift(emp)
            if _ps and _pe and is_time_text(_ps) and is_time_text(_pe):
                _ps_m = time_to_minutes(_ps)
                _pe_m = time_to_minutes(_pe)
                if _ps_m < shift_start_m:
                    _ps_m += 1440
                if _pe_m < _ps_m:
                    _pe_m += 1440
                if min(ts, _pe_m) - max(prev_end, _ps_m) >= rb:
                    return True  # pre-first-task rest falls inside the break window
        elif ts - prev_end >= rb:
            return True  # found a sufficient break gap
        prev_end = max(prev_end, te)
        first_gap = False

    # Also check gap between last task end and new_task_start
    if new_start_m - prev_end >= rb:
        return True

    return False  # worked >= 3 h with no real break opportunity


def get_terminal(gate):
    g = clean_text(gate).upper().strip()
    if not g or g in ("NAN", "NONE", "NAT"):
        return ""            # no gate assigned yet (FIDS often leaves late-night
                             # flights gate-less) — an empty pier, NOT "N"
    if g[0].isalpha():
        return g[0]          # gate letter prefix, e.g. "D9" → "D" (Terminal 3 concourse)
    if g in TERMINAL1_GATES:
        return "1"           # numeric-only gate 30-33/35-38/40 → Terminal 1
    if g.isdigit():
        return g             # terminal number from FIDS, e.g. "3" → "3"
    return ""


def _is_blocked_by_window(emp_row, ts_m, te_m, role=None):
    """True if [ts_m, te_m) (minutes, midnight-safe-adjusted by the caller)
    overlaps a חסימות (blocked-window) entry on emp_row — a training
    course, committee meeting, TSA refresher, etc. Shared by is_available()
    and every post-pass that does its own candidate/conflict search instead
    of calling is_available() (user rule 2026-09-05 — extracted after
    finding this check was only ever wired into is_available() itself,
    leaving every other pass blind to חסימות entirely)."""
    if emp_row is None:
        return False
    blocked_str = clean_text(emp_row.get("חסימות", ""))
    if not blocked_str:
        return False
    for window in blocked_str.split(","):
        if "-" not in window:
            continue
        try:
            ws_str = window.strip().split("-")[0]
            we_str = window.strip().split("-")[1] if len(window.strip().split("-")) > 1 else ""
            ws_m = time_to_minutes(ws_str.strip())
            we_m = time_to_minutes(we_str.strip())
            if we_m < ws_m: we_m += 1440
            if not (ts_m >= we_m + 5 or te_m <= ws_m - 5):
                blocked_role = emp_row.get("_blocked_role_" + window.strip(), "")
                if role == "מפקח TSA" and "פיקוח" in blocked_role:
                    continue
                return True
        except Exception:
            pass
    return False


def is_available(assignments, emp_name, start, end, emp_row=None, role=None, flight_gate=None, emp_tasks=None):
    if emp_row is not None and emp_row.get("חולה", False):
        return False

    if emp_row is not None:
        ts = start.hour * 60 + start.minute
        te = end.hour   * 60 + end.minute
        if te < ts: te += 1440
        if _is_blocked_by_window(emp_row, ts, te, role=role):
            return False

    is_tsa_inspector = (role == "מפקח TSA")
    new_terminal     = get_terminal(flight_gate or "")

    def to_m(dt): return dt.hour * 60 + dt.minute

    start_m = to_m(start)
    end_m   = to_m(end)
    if end_m < start_m: end_m += 1440

    # Shift-relative normalization for cross-midnight employees (e.g. 22:00-06:00).
    # Without this, a task at 05:00 (300 min) vs existing task at 22:30 (1350 min)
    # would appear to overlap in raw minute arithmetic.
    # The anchor must be the worker's TRUE chronological start — the earliest
    # of תחילת משמרת and every זמינות window start. Using only תחילת משמרת
    # broke two ways: (a) a task boarding BEFORE the shift-start field but
    # inside an earlier availability window wrapped to ~1420 (כהן טל, avail
    # opens 14:00 though shift field says 16:00); (b) worse, anchoring at
    # min(shift, task_starts) picked an after-midnight 00:00 task as the
    # anchor and wrapped a same-worker 23:25 task to the far end, MISSING their
    # real overlap (agent#50 14:00-01:30, on LY005 00:00-01:05, wrongly deemed
    # free for the overlapping LY027 23:25-00:30 → upgrade_teamleads moved her
    # off the late USA flight, leaving it short). "Earliest" uses a noon pivot
    # so a night shift's 22:00 start counts as earlier than a 01:00 tail window.
    _ss_av_m = None
    if emp_row is not None:
        # NOTE: is_time_text/time_to_minutes/clean_text are already imported
        # at module scope — a local "from utils.helpers import ... time_to_
        # minutes ..." used to sit here, which made Python treat
        # time_to_minutes as a LOCAL name for this entire function (Python's
        # normal scoping rule: any assignment anywhere in a function body,
        # including a local import, makes that name local throughout the
        # function). That silently broke the חסימות (blocked-window) check
        # far ABOVE this point in the function — it calls time_to_minutes()
        # too, but before this local import line ever ran, so it hit
        # UnboundLocalError every single time, caught by that check's own
        # bare `except Exception: pass` and treated as "not blocked". Found
        # via real 16.07 data: multiple workers (trainee-agent#12, trainee-agent#10, agent#78, TL#50, and others) were scheduled squarely inside their
        # own documented חסימות windows (2026-09-05) — the personal-block
        # mechanism (training courses, committee meetings, TSA refreshers)
        # has been non-functional across the whole build pipeline.
        _cand_starts = []
        _ss_av_str = clean_text(emp_row.get("תחילת משמרת", ""))
        if is_time_text(_ss_av_str):
            _cand_starts.append(time_to_minutes(_ss_av_str))
        for _avw in clean_text(emp_row.get("זמינות", "")).split(","):
            _avw = _avw.strip()
            if "-" in _avw:
                _aws = _avw.split("-", 1)[0].strip()
                if is_time_text(_aws):
                    _cand_starts.append(time_to_minutes(_aws))
        if _cand_starts:
            _ss_av_m = min(_cand_starts, key=lambda _m: (_m - 720) % 1440)

    # Use pre-filtered list when available — avoids O(all_assignments) scan
    tasks = emp_tasks if emp_tasks is not None else [t for t in assignments if t["עובד"] == emp_name]
    for task in tasks:
        if clean_text(task.get("התחלה", "")) == "" or clean_text(task.get("סיום", "")) == "":
            continue

        es = to_datetime_time(task["התחלה"])
        ee = to_datetime_time(task["סיום"])
        es_m = to_m(es)
        ee_m = to_m(ee)
        if ee_m < es_m: ee_m += 1440

        # Terminal 1 uses bus gates / hard-stand with faster processes and
        # closely-spaced gates, so two CONSECUTIVE T1 flights may be worked
        # back-to-back with NO gap at all (user 2026-07-20: "בטרמינל 1... ניתן
        # לשבץ ללא רווחים בכלל... לדוגמא המראה של 05:00 ומיד אחריה המראה של
        # 06:00" — a ר"צ task is departure-60min, so 05:00→04:00-05:00 and
        # 06:00→05:00-06:00 abut exactly). Drop the 5-min gap buffer to 0 only
        # when BOTH the new task and this existing task are at Terminal 1;
        # everything else keeps the normal 5-min separation.
        buf = 5
        if new_terminal == "1" and get_terminal(task.get("_gate", "")) == "1":
            buf = 0
        if _ss_av_m is not None:
            # Normalize all times relative to the worker's TRUE chronological
            # start (_ss_av_m, computed above as the earliest of shift-start +
            # all availability windows) so cross-midnight sequences compare
            # correctly — an evening 23:25 task and an after-midnight 00:00 task
            # both map into the same monotonic window instead of one wrapping to
            # the far end and hiding a real overlap.
            _anchor = _ss_av_m
            _n_es = (es_m - _anchor) % 1440
            _n_ee = (to_m(ee) - _anchor) % 1440
            if _n_ee <= _n_es: _n_ee += 1440
            _n_sm = (start_m - _anchor) % 1440
            _n_em = (to_m(end) - _anchor) % 1440
            if _n_em <= _n_sm: _n_em += 1440
            _overlaps = not (_n_sm >= _n_ee + buf or _n_em <= _n_es - buf)
        else:
            _overlaps = not (start_m >= ee_m + buf or end_m <= es_m - buf)

        if _overlaps:
            if is_tsa_inspector and task.get("תפקיד בסיס") == "מפקח TSA":
                existing_gate     = task.get("_gate", "")
                existing_terminal = get_terminal(existing_gate)
                if new_terminal and existing_terminal and new_terminal == existing_terminal:
                    continue
            return False

    # TSA 4-flight concurrent limit — only for TSA inspector role
    if is_tsa_inspector and new_terminal:
        concurrent_count = 0
        for task in tasks:
            if task.get("תפקיד בסיס") != "מפקח TSA":
                continue
            if get_terminal(task.get("_gate", "")) != new_terminal:
                continue
            es_m2 = to_m(to_datetime_time(task["התחלה"]))
            ee_m2 = to_m(to_datetime_time(task["סיום"]))
            if ee_m2 < es_m2: ee_m2 += 1440
            if not (start_m >= ee_m2 + 5 or end_m <= es_m2 - 5):
                concurrent_count += 1
        if concurrent_count >= 4:
            return False

    return True


# =========================
# ASSIGNMENT ENGINE
# =========================

def count_all_tasks_local(assignments, emp_name):
    return 0


def count_team_lead_tasks_local(assignments, emp_name):
    return sum(
        1 for task in assignments
        if task["עובד"] == emp_name and str(task["תפקיד"]).startswith("ראש צוות")
    )


def tasks_in_window_local(assignments, emp_name, window_start, window_end):
    from datetime import timedelta as _td
    buffer = _td(hours=3)
    count = 0
    for task in assignments:
        if task["עובד"] != emp_name:
            continue
        if clean_text(task.get("התחלה", "")) == "":
            continue
        try:
            ts = to_datetime_time(task["התחלה"])
            te = to_datetime_time(task["סיום"])
            if not (ts > window_end + buffer or te < window_start - buffer):
                count += 1
        except Exception:
            pass
    return count


def minutes_worked_since_shift_start(assignments, emp_name, emp, until_time_minutes, emp_tasks=None):
    ss = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss):
        return 0
    shift_start_m = time_to_minutes(ss)
    tasks = emp_tasks if emp_tasks is not None else [t for t in assignments if t["עובד"] == emp_name]
    total = 0
    for task in tasks:
        ts_str = clean_text(task.get("התחלה", ""))
        te_str = clean_text(task.get("סיום",   ""))
        if not ts_str or not te_str:
            continue
        try:
            ts = time_to_minutes(ts_str)
            te = time_to_minutes(te_str)
            if ts < shift_start_m: ts += 1440
            if te < shift_start_m: te += 1440
            if te < ts: te += 1440
            until = until_time_minutes
            if until < shift_start_m: until += 1440
            te_capped = min(te, until)
            if te_capped > ts:
                total += te_capped - ts
        except Exception:
            pass
    return total


def would_exceed_max_continuous(assignments, emp_name, emp, task_start, task_end, emp_tasks=None):
    ss = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss):
        return False
    shift_start_m = time_to_minutes(ss)
    ts = task_start.hour * 60 + task_start.minute
    te = task_end.hour   * 60 + task_end.minute
    if ts < shift_start_m: ts += 1440
    if te < shift_start_m: te += 1440
    if te < ts: te += 1440

    # Use required_break as the minimum gap that resets the continuous-work counter.
    # This correctly handles night-shift employees who rest between night flights
    # and early-morning flights: a gap >= rb means the counter resets.
    rb = required_break(emp) or 30

    tasks = emp_tasks if emp_tasks is not None else [t for t in assignments if t["עובד"] == emp_name]
    intervals = []
    for task in tasks:
        ts_str = clean_text(task.get("התחלה", ""))
        te_str = clean_text(task.get("סיום", ""))
        if not ts_str or not te_str:
            continue
        t_s = time_to_minutes(ts_str)
        t_e = time_to_minutes(te_str)
        if t_s < shift_start_m: t_s += 1440
        if t_e < shift_start_m: t_e += 1440
        if t_e < t_s: t_e += 1440
        if t_s < ts:
            intervals.append((t_s, min(t_e, ts)))
    intervals.sort()

    # Find the start of the current continuous-work segment by scanning for the
    # most recent gap >= rb.  The counter resets at the BEGINNING of the task
    # that follows the break.
    continuous_start = shift_start_m
    prev_end = shift_start_m
    for t_s, t_e in intervals:
        if t_s - prev_end >= rb:
            continuous_start = t_s
        prev_end = max(prev_end, t_e)
    # If the gap from the last task to the new task is >= rb, the counter resets
    # entirely — only the new task itself counts.
    if ts - prev_end >= rb:
        continuous_start = ts

    # Sum task minutes within the current continuous segment, then add new task.
    continuous_work = sum(
        max(0, t_e - max(t_s, continuous_start))
        for t_s, t_e in intervals
        if t_e > continuous_start
    )
    continuous_work += (te - ts)
    return continuous_work > MAX_CONTINUOUS_WORK_MINUTES


def night_break_window_passed(assignments, emp_name):
    tasks = sorted(
        [t for t in assignments if t.get("עובד") == emp_name and clean_text(t.get("סיום",""))],
        key=lambda t: time_to_minutes(clean_text(t.get("התחלה","00:00")))
    )
    for i in range(len(tasks) - 1):
        te_str = clean_text(tasks[i].get("סיום", ""))
        ts_str = clean_text(tasks[i+1].get("התחלה", ""))
        if not te_str or not ts_str: continue
        try:
            te = time_to_minutes(te_str)
            ts = time_to_minutes(ts_str)
            if ts < te: ts += 1440
            gap = ts - te
            te_in_window = NIGHT_BREAK_WINDOW_START <= (te % 1440) <= NIGHT_BREAK_WINDOW_END
            if gap >= 30 and te_in_window:
                return True
        except Exception:
            pass
    return False

def is_night_shift_for_return_rule(emp_row):
    """
    משמרת לילה לצורך חוק:
    מתחילה מ־20:00 ואילך,
    עוברת חצות,
    ומסתיימת ב־04:00 או מאוחר יותר.
    """
    shift_start = clean_text(emp_row.get("תחילת משמרת", ""))
    shift_end = clean_text(emp_row.get("סוף משמרת", ""))

    if not is_time_text(shift_start) or not is_time_text(shift_end):
        return False

    s = time_to_minutes(shift_start)
    e = time_to_minutes(shift_end)

    # המשמרת חייבת להתחיל בערב ולעבור חצות. הסף היה 20:30 ופספס משמרות
    # 20:00-07:00 (עגן ב-20:00 בדיוק, 30 דק' לפני הסף) — נמצא בנתונים
    # אמיתיים 2026-07-15: TL#34, 20:00-07:00, קיבל הפסקה שנייה מיותרת
    # אחרי טיסות הבוקר המוקדם למרות שכעובד לילה הוא כבר חייב היה להיות
    # בהפסקה קודם לכן (אחרי טיסות הלילה או בסגירת הדלפקים). הסף הורד ל-20:00
    # כדי להתאים ל-_is_late_evening_shift (streamlit_app.py/display.py) שכבר
    # משתמש ב-20:00 כתחילת הטווח לאותה משפחת משמרות.
    if s < 20 * 60:
        return False

    if e >= s:
        return False

    # סוף משמרת אחרי חצות, לפחות 04:00
    return e >= 4 * 60


def is_night_flight_task(task):
    """
    טיסת לילה:
    תחילת הצ׳ק אין / משימה לפני חצות,
    ההמראה / סיום המשימה אחרי חצות.
    """
    start_text = clean_text(task.get("התחלה", ""))
    end_text = clean_text(task.get("סיום", ""))

    if not is_time_text(start_text) or not is_time_text(end_text):
        return False

    start_m = time_to_minutes(start_text)
    end_m = time_to_minutes(end_text)

    return start_m > 20 * 60 and end_m < start_m


def had_night_flight_as_agent_or_tsa_guard(assignments, emp_name):
    """
    בודק אם העובד כבר שובץ במשמרת הנוכחית
    כ־דייל או שומר TSA לטיסת לילה.
    """
    for task in assignments:
        if task.get("עובד") != emp_name:
            continue

        role_base = clean_text(task.get("תפקיד בסיס", task.get("תפקיד", "")))

        if role_base not in {"דייל", "שומר TSA"}:
            continue

        if is_night_flight_task(task):
            return True

    return False

# ── Crew-chief MANAGERS ("מנהל כר\"צ") ─────────────────────────────────────────
# User rule 2026-09-20: a manager flagged as a ר"צ is used ONLY when there is no
# other choice, ONLY for ר"צ / מפקח TSA, and ONLY inside the hours the roster
# marks him as "תגבור ר"צ" (the shortage window). No תגבור mark next to his
# name → he is never scheduled. Managers added from the daily "מנהלי משמרות"
# sheet as backups (`_is_manager_backup`) exist only BECAUSE of such a תגבור
# entry, and their availability windows are that entry's hours.
_MANAGER_ALLOWED_ROLES = ("ראש צוות", "מפקח TSA")


def _is_crew_manager(row):
    try:
        if clean_text(str(row.get(CREW_CHIEF_MANAGER_COL, ""))) == "כן":
            return True
        v = row.get("_is_manager_backup")
        return v is True or str(v).strip() in ("True", "1", "1.0")
    except Exception:
        return False


def _manager_is_backup(row):
    v = row.get("_is_manager_backup")
    return v is True or str(v).strip() in ("True", "1", "1.0")


def _time_in_window(ws, we, start, end):
    """Does the task [start, end) fall entirely inside the clock window ws-we?"""
    if not (is_time_text(ws) and is_time_text(we)) or start is None or end is None:
        return True
    ws_m, we_m = time_to_minutes(ws), time_to_minutes(we)
    if we_m <= ws_m:
        we_m += 1440
    ts_m = start.hour * 60 + start.minute
    te_m = end.hour * 60 + end.minute
    ts_n = ts_m if ts_m >= ws_m else ts_m + 1440
    te_n = te_m if te_m > ts_m else te_m + 1440
    if te_n <= ts_n:
        te_n += 1440
    return ws_m <= ts_n and te_n <= we_m


def _manager_marked(row):
    """Is this manager explicitly marked as תגבור ר"צ for today?"""
    if _manager_is_backup(row):
        return True
    return clean_text(str(row.get("תפקיד מוגבל בזמן", ""))) == "ראש צוות"


def _manager_may_take(row, role, start=None, end=None):
    """True unless `row` is a crew-chief manager who may not take this slot."""
    if not _is_crew_manager(row):
        return True
    if normalize_role_label(str(role)) not in _MANAGER_ALLOWED_ROLES:
        return False
    if not _manager_marked(row):
        return False
    if _manager_is_backup(row):
        return True
    return _time_in_window(
        clean_text(str(row.get("תחילת חלון תפקיד", ""))),
        clean_text(str(row.get("סוף חלון תפקיד", ""))), start, end)


def _manager_mask(df):
    """Vectorised: which rows of a candidates frame are crew-chief managers."""
    import numpy as np
    mask = np.zeros(len(df), dtype=bool)
    if CREW_CHIEF_MANAGER_COL in df.columns:
        mask |= (df[CREW_CHIEF_MANAGER_COL].astype(str).str.strip() == "כן").to_numpy()
    if "_is_manager_backup" in df.columns:
        mask |= df["_is_manager_backup"].map(
            lambda v: v is True or str(v).strip() in ("True", "1", "1.0")
        ).to_numpy(dtype=bool)
    return mask


def _drop_blocked_managers(df, role, start, end):
    """DataFrame of candidates without the managers who may not take this slot."""
    import numpy as np
    if df.empty:
        return df
    mgr = _manager_mask(df)
    if not mgr.any():
        return df
    keep = ~mgr
    idx = np.flatnonzero(mgr)
    for i, r in zip(idx, df.iloc[idx].to_dict("records")):
        keep[i] = _manager_may_take(r, role, start, end)
    return df[keep].copy()


def sort_candidates(candidates, assignments, role, task_start=None, task_end=None,
                    task_terminal=None):
    """Sort candidates using column-wise numpy scoring — avoids to_dict overhead."""
    import numpy as np
    from utils.helpers import classify_shift
    from datetime import timedelta as _td

    if candidates.empty:
        return candidates.copy()

    candidates = _drop_blocked_managers(candidates, role, task_start, task_end)
    if candidates.empty:
        return candidates.copy()

    # Build name→tasks dict from assignments once
    by_name: dict = {}
    for task in assignments:
        _n = task["עובד"]
        if _n not in by_name:
            by_name[_n] = []
        by_name[_n].append(task)

    # Use column-wise list extraction — much faster than to_dict("records")
    names   = candidates["שם"].tolist()
    n       = len(names)

    # ── task_count — per-name lookup ────────────────────────────────────────
    task_count = np.array([len(by_name.get(nm, [])) for nm in names], dtype=np.float32)

    # ── nearby — tasks in ±3h window ────────────────────────────────────────
    if task_start and task_end:
        buf = _td(hours=3)
        ws, we = task_start - buf, task_end + buf
        nearby = np.empty(n, dtype=np.float32)
        for i, nm in enumerate(names):
            cnt = 0
            for t in by_name.get(nm, []):
                if not clean_text(t.get("התחלה", "")):
                    continue
                try:
                    ts = to_datetime_time(t["התחלה"])
                    te = to_datetime_time(t["סיום"])
                    if not (ts > we or te < ws):
                        cnt += 1
                except Exception:
                    pass
            nearby[i] = -cnt
    else:
        nearby = np.zeros(n, dtype=np.float32)

    # ── shift proximity + bad-shift mask ────────────────────────────────────
    ss_col = candidates["תחילת משמרת"].tolist() if "תחילת משמרת" in candidates.columns else [""] * n
    se_col = candidates["סוף משמרת"].tolist()   if "סוף משמרת"   in candidates.columns else [""] * n
    av_col = candidates["זמינות"].tolist()       if "זמינות"       in candidates.columns else [""] * n

    # ── group_balance: night-shift-carryover vs 02:00-shift split for early
    # דייל/מתאם תורים work (2026-07-17) ────────────────────────────────────
    # `nearby` above is a continuity bonus (more existing nearby tasks =
    # preferred) that compounds: night-shift-carryover workers are the ONLY
    # ones on shift for the very first flights of the day (00:00-01:35,
    # before a 02:00-shift worker's shift even starts), so they get picked
    # there first, then `nearby`/`recent_task`/`shift_tail` keep favoring
    # them for every SUBSEQUENT early flight too — a "rich get richer" effect
    # that, left unchecked, gave night-shift-carryover workers 55 of 57
    # early-morning דייל/מתאם-תורים tasks vs only 2 for 02:00-shift workers in
    # real data. User rule 2026-07-17: split these roughly evenly between the
    # two groups, scoped to flights departing before 06:00. Tracks how many
    # such tasks each group has ALREADY been assigned (from `assignments` so
    # far) and prefers whichever group is currently BEHIND — positioned MORE
    # significant than `nearby` so it can override the continuity bonus
    # specifically in this narrow window; has no effect outside it (stays 0).
    group_balance = np.zeros(n, dtype=np.float32)
    if role in ("דייל", "מתאם תורים") and task_start is not None and task_start.hour < 6:
        def _ss_min(_ss_raw):
            _c = clean_text(_ss_raw)
            return time_to_minutes(_c) if is_time_text(_c) else None

        _night_names = set()
        _early02_names = set()
        for _i2, _nm2 in enumerate(names):
            _m2 = _ss_min(ss_col[_i2])
            if _m2 is None:
                continue
            if _m2 >= 20 * 60:
                _night_names.add(_nm2)
            elif 90 <= _m2 <= 150:
                _early02_names.add(_nm2)

        _night_cnt, _early02_cnt = 0, 0
        for _t2 in assignments:
            if "❌" in str(_t2.get("עובד", "")):
                continue
            if str(_t2.get("תפקיד בסיס", "")).strip() not in ("דייל", "מתאם תורים"):
                continue
            _t2_start = clean_text(str(_t2.get("התחלה", "")))
            if not is_time_text(_t2_start) or time_to_minutes(_t2_start) >= 6 * 60:
                continue
            _t2_name = str(_t2.get("עובד", "")).strip()
            if _t2_name in _night_names:
                _night_cnt += 1
            elif _t2_name in _early02_names:
                _early02_cnt += 1

        for _i2, _nm2 in enumerate(names):
            if _nm2 in _night_names:
                group_balance[_i2] = float(_night_cnt)
            elif _nm2 in _early02_names:
                group_balance[_i2] = float(_early02_cnt)

    # ── idle_first: a candidate who has NO task at all yet, and whose shift is
    # already under way, beats one who is already working (2026-08-06).
    # `task_count` (the real per-worker load) is the LEAST significant key in
    # every tuple below — np.lexsort treats the last key as primary — so
    # individual fairness practically never decides anything, and the
    # continuity/shift-tail keys produce a "rich get richer" schedule.
    # group_balance (above) only equalises the night-vs-02:00 GROUP totals; it
    # cannot stop the same few members of a group taking every task while
    # others sit out the whole shift. Measured on real 30.07 data: 9 dedicated
    # early-morning דיילים (02:00-09:30 / 03:30-11:00) finished the day with
    # ZERO flights while other attendants held 4-7 each.
    # Coarse 0/1 bucket only — placed BELOW recent_task in significance, so
    # genuine back-to-back continuity chains (the confirmed-optimal ג'ולי /
    # פרגסליך / אלימלך patterns) still win; it only overrides shift_tail /
    # shift_pri, i.e. "whose shift ends sooner" and "which shift group".
    idle_first = np.ones(n, dtype=np.float32)
    if task_start is not None:
        _ts_now_m = task_start.hour * 60 + task_start.minute
        for _i3, _nm3 in enumerate(names):
            if by_name.get(_nm3):
                continue  # already has work
            _ss3 = clean_text(ss_col[_i3])
            if not is_time_text(_ss3):
                continue
            # only "idle" once their shift has actually begun
            if (_ts_now_m - time_to_minutes(_ss3)) % 1440 <= 12 * 60:
                idle_first[_i3] = 0.0

    # ── em_split: split the morning between the shifts that END early and the
    # 03:30 shift that has just STARTED (user rule 2026-08-06, soft — "הכלל הזה
    # לא חייב להיות מוחלט, רק לנסות לשבץ ככה כמה שיותר"):
    #   • flights finishing by ~06:30  → attendants whose shift ENDS
    #     06:00 / 07:00 / 08:30 / 09:30 (night carry-over + the 02:00 family).
    #     They do the pre-dawn wave and head back to the counters at ~06:30.
    #   • flights departing from ~06:30 → attendants whose shift STARTS 03:30.
    #     They come on duty exactly as the early group finishes.
    # Only a preference (0 = fits the intended half, 1 = doesn't); it never
    # blocks anyone, so a flight is still covered when only the "wrong" half is
    # available.
    em_split = np.zeros(n, dtype=np.float32)
    if role in ("דייל", "מתאם תורים") and task_start is not None and task_end is not None:
        _sp_s_m = task_start.hour * 60 + task_start.minute
        _sp_e_m = task_end.hour * 60 + task_end.minute
        _EARLY_END_SHIFTS = {6 * 60, 7 * 60, 8 * 60 + 30, 9 * 60 + 30}
        _is_early_wave = 1 * 60 <= _sp_s_m <= 6 * 60 + 30 and _sp_e_m <= 6 * 60 + 45
        _is_late_wave = 6 * 60 + 15 <= _sp_s_m <= 11 * 60
        if _is_early_wave or _is_late_wave:
            for _i4 in range(n):
                _ss4 = clean_text(ss_col[_i4])
                _se4 = clean_text(se_col[_i4])
                _ss4_m = time_to_minutes(_ss4) if is_time_text(_ss4) else None
                _se4_m = time_to_minutes(_se4) if is_time_text(_se4) else None
                _starts_0330 = _ss4_m is not None and 3 * 60 + 15 <= _ss4_m <= 3 * 60 + 45
                _ends_early = _se4_m is not None and _se4_m in _EARLY_END_SHIFTS
                if _is_early_wave:
                    em_split[_i4] = 0.0 if (_ends_early and not _starts_0330) else 1.0
                else:
                    em_split[_i4] = 0.0 if _starts_0330 else 1.0

    if task_start and task_end:
        ts_m = task_start.hour * 60 + task_start.minute
        te_m = task_end.hour   * 60 + task_end.minute
        if te_m < ts_m: te_m += 1440

        prox     = np.empty(n, dtype=np.float32)
        shift_bad = np.zeros(n, dtype=np.float32)   # 1 = shift can't cover this task
        for i, nm in enumerate(names):
            if by_name.get(nm):
                prox[i] = 0
                # A worker who already took a genuine "return to counters"
                # break (idle >=60min beyond their own required break time —
                # same threshold as the "הפסקה וחזרה לדלפקים" wording in
                # app/display.py) must not be pulled back down for another
                # flight — unless their shift is 12:30-00:30, 14:00-01:30, or
                # a genuine night shift, and even then only after a multi-hour
                # gap. User 2026-07-19: "אם עובד עושה הפסקה וחזרה לדלפקים, לא
                # מורידים אותו שוב לטיסות, אלא אם כן מדובר במשמרת 12:30-00:30
                # 14:00-01:30 או משמרת לילה, חייב שיהיה טווח של כמה שעות...
                # עדיף לא לטרטר את העובד" — found via real data: agent#41,
                # 02:00-11:00, break ends 07:10, then re-summoned for LY315 at
                # 09:05 (~2h later) even though her shift type isn't exempt.
                _ss_i = clean_text(ss_col[i]); _se_i = clean_text(se_col[i])
                _last_end_m = None
                _first_start_m = None
                for _ht in by_name[nm]:
                    _het = to_datetime_time(_ht.get("סיום", ""))
                    _hst = to_datetime_time(_ht.get("התחלה", ""))
                    if _het is not None:
                        _hem = _het.hour * 60 + _het.minute
                        if _last_end_m is None or _hem > _last_end_m:
                            _last_end_m = _hem
                    if _hst is not None:
                        _hsm = _hst.hour * 60 + _hst.minute
                        if _first_start_m is None or _hsm < _first_start_m:
                            _first_start_m = _hsm
                if _last_end_m is not None:
                    _gap_m = (ts_m - _last_end_m) % 1440
                    _rb_i = required_break({"תחילת משמרת": _ss_i, "סוף משמרת": _se_i}) or 45
                    # For the 02:00-09:30-family shift, a real break already
                    # happened BEFORE the worker's very first task (the
                    # unconditional pre-flight placement — see the
                    # ("03:00","05:00") window fixes above), which this
                    # scheduler-side model can't see directly (that placement
                    # is computed later, in app/display.py). Infer it the same
                    # way: if the gap between shift start and their first task
                    # already had room for the break, don't ALSO subtract a
                    # break duration from a LATER gap — that later gap is pure
                    # idle waste, not a second break opportunity. Found via
                    # real data 2026-07-19: agent#50, 02:00-09:30, break
                    # already taken 03:25-04:10 (before LY2521), then a bare
                    # 75-min idle gap before LY541 with no break in it at all
                    # — the old subtract-then-compare logic wrongly treated
                    # that 75min as "45 for a break + 30 idle", under the
                    # 60min bar, when it's really "75 min of pure waste".
                    _pre_break_used = False
                    if (is_time_text(_ss_i) and _first_start_m is not None
                            and preferred_break_window_by_shift({"תחילת משמרת": _ss_i, "סוף משמרת": _se_i}) == ("03:00", "05:00")):
                        _ss_i_m = time_to_minutes(_ss_i)
                        if (_first_start_m - _ss_i_m) % 1440 >= _rb_i:
                            _pre_break_used = True
                    _idle_beyond = _gap_m if _pre_break_used else (_gap_m - _rb_i)
                    if _idle_beyond >= 60:
                        _is_exempt_shift = False
                        if is_time_text(_ss_i):
                            _ss_i_m = time_to_minutes(_ss_i)
                            if (12 * 60 + 15 <= _ss_i_m <= 12 * 60 + 45
                                    or 13 * 60 + 45 <= _ss_i_m <= 14 * 60 + 15):
                                _is_exempt_shift = True
                        if is_night_shift_for_return_rule({"תחילת משמרת": _ss_i, "סוף משמרת": _se_i}):
                            _is_exempt_shift = True
                        if not _is_exempt_shift or _gap_m < 180:
                            shift_bad[i] = 1
                # A task ending exactly at this worker's own shift end leaves
                # zero room for their entitled רענון/הפסקה afterward — user
                # 2026-07-19: "עדיף לא לתת לעובד טיסה שנגמרת בדיוק בזמן סיום
                # המשמרת שלו... יש לשבץ אותו לרענון וחזרה ולשבץ דייל אחר
                # לתפקיד במקומו". Deprioritize (soft — another candidate is
                # preferred, but this one still covers it if truly the only
                # option) rather than hard-block, same as the other shift_bad
                # uses in this loop.
                _se_i2 = clean_text(se_col[i])
                if is_time_text(_se_i2) and (te_m % 1440) == time_to_minutes(_se_i2):
                    shift_bad[i] = 1
                continue
            ss = clean_text(ss_col[i]); se = clean_text(se_col[i])
            if not is_time_text(ss) or not is_time_text(se):
                prox[i] = 9999; shift_bad[i] = 1
                continue
            s = time_to_minutes(ss); e = time_to_minutes(se)
            if e <= s: e += 1440
            t = ts_m if ts_m >= s else ts_m + 1440
            prox[i] = t - s
            if (te_m % 1440) == time_to_minutes(se):
                shift_bad[i] = 1
            # Skip overlap check when employee has explicit availability windows
            if clean_text(av_col[i]):
                continue
            fits = False
            for ts_n in (ts_m, ts_m + 1440):
                te_n = te_m if te_m > ts_m else te_m + 1440
                if te_n <= ts_n: te_n += 1440
                if ts_n >= s and te_n <= e:
                    fits = True; break
            if not fits:
                shift_bad[i] = 1
    else:
        ts_m = task_start.hour * 60 + task_start.minute if task_start else 0
        prox = np.zeros(n, dtype=np.float32)
        shift_bad = np.zeros(n, dtype=np.float32)

    # ── Workers absent from the daily-Excel get last-resort priority ──────────
    # Employees not in today's daily schedule have stale shift times from
    # employees_clean.xlsx.  When a daily Excel was loaded (detected by the
    # presence of at least one in_daily_excel=1 row), absent workers are
    # marked shift_bad=1 so the scheduler always prefers today's actual staff.
    if "in_daily_excel" in candidates.columns:
        import pandas as _pd_ide
        _ide = _pd_ide.to_numeric(candidates["in_daily_excel"], errors="coerce").fillna(0).to_numpy()
        _absent = _ide < 1
        if _absent.any() and not _absent.all():  # daily Excel was loaded
            shift_bad = np.where(_absent, 1.0, shift_bad)
            prox      = np.where(_absent, 9999.0, prox)

    # ── Upcoming-night last-resort mask ──────────────────────────────────────
    # Bottom-of-page night workers (night N→N+1) are eligible but must be picked
    # ONLY after every day/evening worker is exhausted. Placed just above
    # shift_bad in the sort keys so it dominates every other preference yet still
    # loses to a truly out-of-shift worker. Purely additive: these workers were
    # previously hard-excluded, so this can only fill slots that would otherwise
    # go to an absent/❌ worker — never displaces an existing today-staff pick.
    if "upcoming_night" in candidates.columns:
        import pandas as _pd_up
        upcoming_lr = _pd_up.to_numeric(candidates["upcoming_night"], errors="coerce").fillna(0).to_numpy(dtype=np.float32)
    else:
        upcoming_lr = np.zeros(n, dtype=np.float32)

    # ── Terminal-transfer last-resort mask (T3 flights only) ─────────────────
    # Workers who transfer mid-shift between terminals (מעבר טרמינל = "1@02:00"
    # style, with a time) should be avoided for Terminal-3 flights unless no
    # other candidate exists — they must leave time for break + travel, and
    # their post-transfer duty belongs to the other terminal. Not applied to
    # T1 flights (there the transfer workers ARE the staff).
    if (task_terminal is not None and clean_text(task_terminal) != "1"
            and "מעבר טרמינל" in candidates.columns):
        transfer_lr = candidates["מעבר טרמינל"].astype(str).str.contains(
            r"@\d", regex=True, na=False
        ).to_numpy(dtype=np.float32)
    else:
        transfer_lr = np.zeros(n, dtype=np.float32)

    # ── shift type priority (for late-night / midnight-crossing flights) ──────
    flight_before_130 = task_end and (task_end.hour * 60 + task_end.minute) <= LATE_SHIFT_END_MAX
    _ts_m = task_start.hour * 60 + task_start.minute if task_start else -1
    _te_m = task_end.hour   * 60 + task_end.minute   if task_end   else -1
    # Midnight-crossing: boarding starts >=22:00 (evening) AND departure is 00:xx-05:xx
    _is_midnight_cross = _ts_m >= 22 * 60 and 0 <= _te_m < 6 * 60

    if _is_midnight_cross:
        # For night flights that straddle midnight: prefer afternoon/evening workers
        # over night-shift workers (night shift = last resort for these flights).
        # Within night-shift workers, sort_candidates naturally assigns to earlier
        # flights first (since flights are processed in time order by the main loop).
        _sc_map = {
            "noon": 0, "after": 0, "evening": 1,
            "day": 2, "night": 3, "early_morning": 4, "unknown": 5,
        }
        shift_pri = np.array([_sc_map.get(classify_shift({"תחילת משמרת": ss_col[i]}), 5)
                               for i in range(n)], dtype=np.float32)
    elif flight_before_130:
        # Regular late-night flights (end <=01:30 but not midnight-crossing):
        # night/evening workers first
        _sc_map = {"night": 0, "evening": 1, "after": 2, "noon": 3, "day": 4, "early_morning": 5, "unknown": 6}
        shift_pri = np.array([_sc_map.get(classify_shift({"תחילת משמרת": ss_col[i]}), 6)
                               for i in range(n)], dtype=np.float32)
    elif task_end and _te_m < 12 * 60 + 30:
        # Early-morning-shift priority window (01:30–12:29).
        # ר"צ / מפקח TSA: prefer night/evening workers from the previous day,
        #       then 02:00 starters, then 03:30 starters.
        # דייל (user-confirmed order 2026-07): NIGHT workers and 02:00 starters
        #       FIRST (same tier — they have been on-site for hours), 03:30
        #       starters only after them.
        # Widened from "<8:00" to "<12:30" (2026-07-15, user rule): "כל עוד יש
        # עובדים פנויים ממשמרות שהתחילו לפנות בוקר יש לתת להם עדיפות בשיבוץ
        # על פני עובדי משמרת יום שמתחילים החל משעה 08:00" — as long as
        # early-morning-shift (02:00/03:30) workers are free, they should
        # outrank 08:00+ day-shift workers; day-shift workers should only be
        # used once early-morning options are exhausted. The <8:00 threshold
        # only ever applied this ranking to flights ENDING before 08:00,
        # leaving flights ending 08:00-12:30 (very common — e.g. a 10:50-11:50
        # flight) with shift_pri=0 for everyone, letting a day-shift worker
        # compete on equal footing with early-morning-shift workers who were
        # still on shift. Found via real data 2026-07-15: TL#26
        # (08:30-16:00, "day") got LY315 (09:05-10:10) and LY5467
        # (10:50-11:50, ר"צ role) ahead of early-morning-shift candidates.
        # 12:30 matches the latest early-morning shift variant's own end time
        # (03:30-12:30) — the internal _em_pri tiering below is unchanged, it
        # already correctly ranks day/noon/after LAST for ר"צ/TSA roles; only
        # the window it applies to was too narrow.
        def _em_pri(i, _rz):
            _cls = classify_shift({"תחילת משמרת": ss_col[i]})
            if _cls == "early_morning":
                _ssm = time_to_minutes(clean_text(ss_col[i])) if is_time_text(clean_text(ss_col[i])) else 210
                _is_0330 = _ssm >= 3 * 60   # 03:00+ starter (03:30 group)
                if _rz:
                    return 3 if _is_0330 else 2
                return 1 if _is_0330 else 0
            if _rz:
                return {"night": 0, "evening": 1, "day": 4, "noon": 4, "after": 4}.get(_cls, 5)
            return {"night": 0, "evening": 1, "day": 2, "noon": 2, "after": 2}.get(_cls, 3)

        _rz_role = role in ("ראש צוות", "מפקח TSA", "שומר TSA")
        shift_pri = np.array([_em_pri(i, _rz_role) for i in range(n)], dtype=np.float32)
    else:
        shift_pri = np.zeros(n, dtype=np.float32)

    # ── TSA / TL flags — use pre-cached __yn_* bool columns when available ──
    def _yn(col):
        cache = f"__yn_{col}"
        if cache in candidates.columns:
            return candidates[cache].to_numpy(dtype=np.float32)
        if col not in candidates.columns:
            return np.zeros(n, dtype=np.float32)
        return (candidates[col].astype(str).str.strip() == "כן").to_numpy(dtype=np.float32)

    _tsa_col  = "מפקח TSA" if "מפקח TSA" in candidates.columns else "מפקח tsa" if "מפקח tsa" in candidates.columns else None
    is_tsa    = _yn(_tsa_col) if _tsa_col else np.zeros(n, dtype=np.float32)
    is_tl     = _yn("ראש צוות")
    # A manager who IS allowed here (marked תגבור ר"צ, inside the hours) is
    # still the LAST resort: after every in-shift non-manager, before the
    # out-of-shift ones (shift_bad stays the most significant key).
    mgr_last = _manager_mask(candidates).astype(np.float32)

    # ── night_flight_done: a ר"צ who has ALREADY worked a midnight-crossing
    # night flight is a worse pick for the pre-dawn flights than a night-shift
    # ר"צ who has not — the latter has been at the counters all night and is
    # fresh, while the former has just come off a flight block and is owed a
    # break (user rule 2026-08-06: "אם יש ר"צ של משמרת לילה שלא עשה טיסת לילה,
    # יש לו עדיפות לשיבוץ לטיסת לפנות בוקר על פני ר"צ משמרת לילה שכן עשה טיסת
    # לילה" — TL#15 / TL#7). A ר"צ who DID work the night block
    # still keeps their normal priority for the morning; this only orders the
    # two against each other.
    night_flight_done = np.zeros(n, dtype=np.float32)
    if role == "ראש צוות":
        for _i5, _nm5 in enumerate(names):
            for _t5 in by_name.get(_nm5, []):
                _s5 = to_datetime_time(_t5.get("התחלה", ""))
                _e5 = to_datetime_time(_t5.get("סיום", ""))
                if _s5 is None or _e5 is None:
                    continue
                _s5m = _s5.hour * 60 + _s5.minute
                _e5m = _e5.hour * 60 + _e5.minute
                if _s5m >= 21 * 60 and _e5m < 6 * 60:
                    night_flight_done[_i5] = 1.0
                    break

    # ── tl_ready: a ר"צ needs prep time before the flight, so among otherwise
    # equal candidates prefer the one with real slack before this role starts.
    # 0 = comfortable, 1 = steps straight out of another task with (almost) no
    # turnaround (user rule 2026-08-06, LY5155: TL#4 finished LY5107
    # at 05:30 and the ר"צ role starts 05:30 — zero minutes — while TL#32,
    # free since 05:15, was put on the same flight as a plain דייל).
    # A TIE-BREAK only: it sits below the continuity/shift keys so it never
    # breaks an established chain, it just decides between equals.
    tl_ready = np.zeros(n, dtype=np.float32)
    if role == "ראש צוות" and task_start is not None:
        _tr_start = task_start.hour * 60 + task_start.minute
        for _i6, _nm6 in enumerate(names):
            for _t6 in by_name.get(_nm6, []):
                _e6 = clean_text(_t6.get("סיום", ""))
                if not _e6:
                    continue
                if 0 <= (_tr_start - time_to_minutes(_e6)) % 1440 < 15:
                    tl_ready[_i6] = 1.0
                    break

    dual_qual    = (is_tsa * is_tl) if role == "ראש צוות" else np.zeros(n, dtype=np.float32)
    tsa_preserve = is_tsa if role in {"דייל", "מתאם תורים"} else np.zeros(n, dtype=np.float32)
    tl_preserve  = is_tl  if role in {"דייל", "מתאם תורים"} else np.zeros(n, dtype=np.float32)

    # ── Attendant-type priority for "דייל" ──────────────────────────────────
    # 0=רצים  1=פורשים/ילדי עובדים  2=דיילים רגילים  3=מתדרכת
    # מתדרכת (TL instructor) used to sit at tier 2 — BETTER priority than a
    # regular attendant (tier 3) — meaning the sort actively PREFERRED an
    # instructor for a plain דייל slot over an ordinary attendant. That's
    # backwards: an instructor's real role is ר"צ overlap/training duty, and
    # she should only ever fill a דייל slot as an absolute LAST resort, once
    # no regular attendant is available — never preferred over one. Swapped
    # her to the worst tier (3) instead (user rule 2026-07-25, found via real
    # data: worker#1, defined as מתדרכת, scheduled as a plain דייל ahead of
    # available regular attendants).
    if role == "דייל":
        is_runner = (_yn("חונך רצים") + _yn("מסמיך רצים") + _yn("טרייני רצ")) > 0
        is_restr  = (_yn("ילד עובדים") + _yn("פורשים")) > 0
        is_inst   = _yn("מתדרכת") > 0
        att_priority = np.where(is_runner, 0, np.where(is_restr, 1, np.where(is_inst, 3, 2))).astype(np.float32)
    else:
        att_priority = np.zeros(n, dtype=np.float32)

    # ── שומר TSA priority ────────────────────────────────────────────────────
    # Fill order (user rule 2026-07-15, refined 2026-08-30): 0=פורשים/ילד
    # עובדים (always first priority) 1=a REGULAR דייל who is currently
    # completely free (no other task today) 2=דייל בטרייני
    # (attendant-in-training — this is the one situation where a trainee may
    # be separated from their paired mentor, but only once no conflict-free
    # regular candidate exists) 3=a regular דייל who already has other tasks.
    # Two bugs fixed here originally: (1) the tiers were in the wrong order —
    # trainees were sorted AHEAD of restricted workers, when restricted
    # workers must always come first; (2) "trainee" was checked via "טרייני
    # רצ" (TL/ר"צ trainee), an unrelated column — the correct flag for an
    # attendant trainee is TRAINEE_ATTENDANT_COL ("דייל בטرייני").
    # Free-regular-over-trainee refinement: using a trainee for guard duty
    # forces pair_trainee_attendants to still shadow them onto their mentor's
    # flights around it, which can leave too little room for their own
    # required break — found via real 12.07.2026 data: trainee-agent#4
    # (02:00-11:00 trainee) took LY007's guard slot ahead of agent#41 /
    # agent#54 (same shift, TSA-certified, completely unused all day), then
    # only had a 40-minute gap before it against her 45-minute required
    # break. A trainee with no better-fitting alternative is still fine for
    # guard duty (that's the whole point of the exception) — only demoted
    # when a genuinely free, non-restricted, non-trainee candidate is right
    # there for the taking.
    if role == "שומר TSA":
        _is_restricted  = (_yn("ילד עובדים") + _yn("פורשים")) > 0
        _is_att_trainee = _yn(TRAINEE_ATTENDANT_COL) > 0
        _is_free_regular = (~_is_restricted.astype(bool)) & (~_is_att_trainee.astype(bool)) & (task_count == 0)
        tsa_guard_priority = np.where(
            _is_restricted, 0,
            np.where(_is_free_regular, 1, np.where(_is_att_trainee, 2, 3))
        ).astype(np.float32)
    else:
        tsa_guard_priority = np.zeros(n, dtype=np.float32)

    # ── TSA-inspector shift priority ─────────────────────────────────────────
    # 0 = employee is rostered as THE inspector for this shift — either their
    #     shift_sheet is explicitly "מפקח", OR the daily roster carries a
    #     "פיקוח TSA" section header over their name ("מפקח TSA מוגדר" column,
    #     set in apply_shift_map_to_employees).
    # 1 = qualified (holds the מפקח-TSA CERTIFICATION) but not rostered as
    #     inspector today — a valid last-resort candidate, never preferred
    #     over someone actually designated (user rule 2026-07-25: found via
    #     real data — TL#37 holds the certification but only TL#43/TSA-inspector#3 are under a "פיקוח TSA" header for this shift).
    # Only meaningful when assigning the מפקח TSA role.
    if role == "מפקח TSA":
        _sheet_designated = (
            candidates["shift_sheet"].astype(str).str.strip().str.lower().str.contains("מפקח", na=False)
            if "shift_sheet" in candidates.columns else pd.Series(False, index=candidates.index)
        )
        _note_designated = (
            candidates["מפקח TSA מוגדר"].astype(str).str.strip() == "כן"
            if "מפקח TSA מוגדר" in candidates.columns else pd.Series(False, index=candidates.index)
        )
        inspector_shift = np.where(
            (_sheet_designated | _note_designated).to_numpy(), 0, 1
        ).astype(np.float32)
    else:
        inspector_shift = np.zeros(n, dtype=np.float32)

    # ── Same-pier consolidation preference (מפקח TSA / שומר TSA) ──────────────
    # get_terminal() returns the GATE-LETTER for alphabetic gates (C7→"C",
    # D8→"D"), and a TSA inspector may hold overlapping tasks ONLY within the
    # SAME pier letter (is_available line ~419). So one inspector physically
    # posted at pier D can cover every overlapping D-gate flight at once, but
    # NOT a C-gate flight. Without a consolidation preference the greedy
    # per-flight fill spreads inspectors thin — e.g. two inspectors both landing
    # in pier D (one on D8, another on D6+D7) when ONE could cover all of D,
    # leaving pier C with no inspector at all (found via real data 2026-07-13:
    # LY017/027/001 all in pier D took both TL#43 and TL#1, so
    # LY005/C7 and LY025 were left ❌ despite the inspectors having spare
    # concurrent capacity). Strongly prefer a candidate who ALREADY has a
    # same-pier inspector task overlapping this one — they absorb the whole
    # pier, freeing the other inspector for a different pier.
    if role in {"מפקח TSA", "שומר TSA"} and task_start and task_end and task_terminal:
        _sp_ts_m = task_start.hour * 60 + task_start.minute
        _sp_te_m = task_end.hour * 60 + task_end.minute
        if _sp_te_m < _sp_ts_m:
            _sp_te_m += 1440
        _want_pier = clean_text(task_terminal)
        same_pier_pref = np.zeros(n, dtype=np.float32)
        for i, nm in enumerate(names):
            _pier_overlap_cnt = 0
            for t in by_name.get(nm, []):
                if t.get("תפקיד בסיס") not in {"מפקח TSA", "שומר TSA"}:
                    continue
                if get_terminal(t.get("_gate", "")) != _want_pier:
                    continue
                _et_s = to_datetime_time(t.get("התחלה", ""))
                _et_e = to_datetime_time(t.get("סיום", ""))
                if _et_s is None or _et_e is None:
                    continue
                _es_m = _et_s.hour * 60 + _et_s.minute
                _ee_m = _et_e.hour * 60 + _et_e.minute
                if _ee_m < _es_m:
                    _ee_m += 1440
                # normalize the existing task into the same midnight frame
                if _es_m + 1440 < _sp_ts_m:
                    _es_m += 1440; _ee_m += 1440
                # overlap (parallel same-pier coverage) → this candidate is
                # already the pier's inspector; -1 sorts them ahead. CAP (user
                # rule 2026-07-22): at most 4 flights in parallel on one pier —
                # a 5th overlapping flight there needs a SECOND inspector, not
                # more of the same one.
                if not (_sp_ts_m >= _ee_m or _sp_te_m <= _es_m):
                    _pier_overlap_cnt += 1
            if 0 < _pier_overlap_cnt < 4:
                same_pier_pref[i] = -1.0
    else:
        same_pier_pref = np.zeros(n, dtype=np.float32)

    # ── Shift-tail urgency ───────────────────────────────────────────────────
    # Prefer employees whose shift ends within 3 h after this task ends.
    # Prevents idle tail-of-shift time: an employee with 3 h remaining is
    # preferred over a fresh employee with 8 h remaining (all else equal).
    if task_end:
        _te_m_st = task_end.hour * 60 + task_end.minute
        shift_tail = np.empty(n, dtype=np.float32)
        for i in range(n):
            _se_raw_st = clean_text(se_col[i])
            if not is_time_text(_se_raw_st):
                shift_tail[i] = 1.0
                continue
            _se_m_st = time_to_minutes(_se_raw_st)
            _remaining = (_se_m_st - _te_m_st) % 1440
            # Use bucketed values so the tie-break is clear but not too aggressive.
            # A task ending EXACTLY at shift end (zero buffer) is a soft
            # last-resort — a delay would run the worker straight past their
            # shift end with no slack (user rule 2026-07-14: "עדיף לא לשבץ
            # עובד לטיסה שממריאה באותו זמן שבו נגמרת המשמרת שלו, למקרה ויהיו
            # עיכובים"). Still preferred over someone with hours left (keeps
            # the "avoid idle tail-of-shift time" intent), just ranked below
            # a candidate with an actual small buffer.
            if _remaining == 0:
                shift_tail[i] = 0.3   # zero buffer — soft last resort
            elif _remaining < 60:
                shift_tail[i] = 0.0   # small real buffer, ending soon — preferred
            elif _remaining < 180:
                shift_tail[i] = 0.5   # somewhat urgent
            else:
                shift_tail[i] = 1.0   # plenty of time left
    else:
        shift_tail = np.ones(n, dtype=np.float32)

    # ── Night-block lateness fit ─────────────────────────────────────────────
    # For a genuine NIGHT FLIGHT (crosses midnight), prefer matching the task's
    # own lateness within the block to the candidate's shift profile: a
    # "short-tail" worker (shift ends soon after this task — already favored
    # by shift_tail above) should get the LATER-departing flights in the
    # block, since their shift is basically over right after anyway. A
    # genuine long-night-shift worker (shift continues well past the block,
    # e.g. 21:00-07:00) should instead get the EARLIER-departing flights, so
    # their task ends as soon as possible and they can break + return to the
    # counters sooner — maximizing counter staffing right after the night
    # closing (user rule 2026-07-25). Scored as each candidate's own "fit":
    # short-tail candidates are scored by how close the task sits to THEIR
    # shift end; long-night candidates are scored by how close the task sits
    # to THEIR shift start. Both use the SAME "minutes of slack" scale, so a
    # tight fit on either side sorts first regardless of which type it is —
    # only a genuine choice between the two types is affected; it's a soft
    # tie-breaker, not a hard override of coverage/qualification.
    if task_start and task_end:
        _ts_m_nb = task_start.hour * 60 + task_start.minute
        _te_m_nb = task_end.hour * 60 + task_end.minute
        _is_night_task = _te_m_nb < _ts_m_nb  # end < start ↔ crosses midnight
    else:
        _is_night_task = False
    if _is_night_task:
        night_lateness_fit = np.empty(n, dtype=np.float32)
        for i in range(n):
            _ss_raw_nb = clean_text(ss_col[i])
            _se_raw_nb = clean_text(se_col[i])
            if not (is_time_text(_ss_raw_nb) and is_time_text(_se_raw_nb)):
                night_lateness_fit[i] = 9999.0
                continue
            _ss_m_nb = time_to_minutes(_ss_raw_nb)
            _se_m_nb = time_to_minutes(_se_raw_nb)
            _is_short_tail_nb = 0 <= (_se_m_nb - _te_m_nb) % 1440 <= 90
            if _is_short_tail_nb:
                night_lateness_fit[i] = (_se_m_nb - _te_m_nb) % 1440
            else:
                night_lateness_fit[i] = (_ts_m_nb - _ss_m_nb) % 1440
    else:
        night_lateness_fit = np.zeros(n, dtype=np.float32)

    # ── Continuity bonus ─────────────────────────────────────────────────────
    # Give priority to employees whose last task ended 5–150 min before task_start.
    # This fills gaps for employees already mid-shift rather than always picking
    # idle employees — which leaves assigned workers with long, unfilled gaps.
    if task_start:
        _ts_cont = task_start.hour * 60 + task_start.minute
        recent_task = np.empty(n, dtype=np.float32)
        for i, nm in enumerate(names):
            # Check ALL of the employee's tasks — bonus if ANY ends 5-150 min
            # before task_start (not just the latest, which may be a future task).
            _bonus = False
            for _rt in by_name.get(nm, []):
                _re = clean_text(_rt.get("סיום", ""))
                if not _re:
                    continue
                # 0 counts: a task starting the very minute the previous one
                # ends is the BEST possible continuation, but the old 5-minute
                # floor excluded exactly that and handed the flight to someone
                # with nothing to continue from (found via real 30.07 data:
                # TL#15 finished LY081 at 23:00 and LY121's ר"צ role
                # starts at 23:00 — zero gap, no bonus). A genuine OVERLAP is
                # still excluded: it comes out negative and the % 1440 turns it
                # into a large number, well past the 150 bound.
                if 0 <= (_ts_cont - time_to_minutes(_re)) % 1440 <= 150:
                    _bonus = True
                    break
            recent_task[i] = 0.0 if _bonus else 1.0
    else:
        recent_task = np.ones(n, dtype=np.float32)

    # ── Peak-hour ראש צוות preference (departures ~05:30–06:50) ─────────────
    # During the morning peak, night-shift ראש צוות (ending 06:00–07:30) are
    # preferred over early-morning ראש צוות (starting 03:30) for the ראש צוות
    # role.  Early-morning ראש צוות should instead fill דייל/מתאם תורים slots.
    _PEAK_BOARD_S = 4 * 60 + 30   # 04:30 boarding → ~05:30 departure
    _PEAK_BOARD_E = 5 * 60 + 50   # 05:50 boarding → ~06:50 departure
    _in_peak = task_start and _PEAK_BOARD_S <= ts_m <= _PEAK_BOARD_E

    peak_tl_pref = np.zeros(n, dtype=np.float32)   # 0=night-shift, 1=other, 2=early-morning
    peak_em_tl   = np.zeros(n, dtype=np.float32)   # for דייל/מתאם: 0=early-morning TL, 1=other

    if _in_peak:
        for i in range(n):
            _ss_raw = clean_text(ss_col[i])
            _se_raw = clean_text(se_col[i])
            _ss_m = time_to_minutes(_ss_raw) if is_time_text(_ss_raw) else -1
            _se_m = time_to_minutes(_se_raw) if is_time_text(_se_raw) else -1
            _is_night_tl = (_ss_m >= 20 * 60 and 5 * 60 + 30 <= _se_m <= 7 * 60 + 30)
            # This rule is about TEAM LEADERS only: during peak, early-morning
            # ר"צ should fill דייל/מתאם slots (night ר"צ take the ראש-צוות ones).
            # It must NOT boost every 03:30 attendant over night/02:00 workers —
            # the user-confirmed shift order (night & 02:00 first, then 03:30)
            # applies to regular attendants during peak too.
            _is_em_tl    = (3 * 60 <= _ss_m <= 4 * 60 + 30) and is_tl[i] > 0
            if _is_night_tl:
                peak_tl_pref[i] = 0   # preferred for ראש צוות during peak
                peak_em_tl[i]   = 1   # not an early-morning TL
            elif _is_em_tl:
                peak_tl_pref[i] = 2   # deprioritise as ראש צוות — use as דייל instead
                peak_em_tl[i]   = 0   # preferred for דייל/מתאם slots during peak
            else:
                peak_tl_pref[i] = 1
                peak_em_tl[i]   = 1

    # ── First-task arrival buffer (soft constraint) ──────────────────────────
    # Prefer employees who have ≥ 45 min between their shift start and this task.
    # Only relevant for employees with NO tasks yet (first assignment of the day).
    # 0 = buffer OK or already has tasks; 1 = too close to shift start (< 45 min).
    # This key is placed HIGH in the sort (just below upcoming_lr/shift_bad) so a
    # worker whose shift just started loses to EVERY other valid candidate —
    # e.g. a 03:30 starter must not grab a 03:50 flight while night workers and
    # 02:00 workers who have been on-site for hours are free — yet still gets the
    # task when nobody else can cover it (soft, not a hard exclusion).
    if task_start:
        _ts_m_buf = task_start.hour * 60 + task_start.minute
        first_task_buffer = np.empty(n, dtype=np.float32)
        for i, nm in enumerate(names):
            if by_name.get(nm, []):
                first_task_buffer[i] = 0.0  # already has tasks — buffer irrelevant
            else:
                _ss_raw_buf = clean_text(ss_col[i])
                if not is_time_text(_ss_raw_buf):
                    first_task_buffer[i] = 0.0
                    continue
                _ss_m_buf = time_to_minutes(_ss_raw_buf)
                _buffer_m = (_ts_m_buf - _ss_m_buf) % 1440
                first_task_buffer[i] = 0.0 if _buffer_m >= 45 else 1.0
    else:
        first_task_buffer = np.zeros(n, dtype=np.float32)

    # ── TL ר"צ assignment count (shared across roles) ───────────────────────
    tl_cnt = np.array([
        sum(1 for t in by_name.get(nm, []) if str(t.get("תפקיד", "")).startswith("ראש צוות"))
        for nm in names
    ], dtype=np.float32)

    # ── Remaining-shift-time urgency (ר"צ role, non-early-morning flights) ───
    # tl_cnt above balances load by preferring whoever has done FEWER ר"צ
    # tasks so far — correct in general (fixed agent#56 / TL#7 being
    # shut out all day), but it can backfire against a worker who is simply
    # running low on remaining shift time: a busier candidate whose shift
    # ends soon has fewer REMAINING chances to be used at all, while a
    # less-busy candidate with hours of shift left can just as easily be used
    # on a LATER flight instead. Found via real data 2026-07-15: TL#8
    # (03:30-12:30, already had 3 tasks) lost LY5467 (10:50-11:50) to TL#26 (08:30-16:00, only 1 task) purely on tl_cnt, even though TL#26
    # had 4+ more hours of shift left to be used on a different flight while
    # TL#8 had under 2h left and this was effectively her last real
    # opportunity — she ended up returned to counters ~3h before her own
    # shift end despite being ר"צ-qualified.
    # Discretized into 3 coarse tiers (mirrors the existing shift_tail bucket
    # pattern elsewhere in this function) rather than raw continuous minutes
    # — a raw value would let a trivial few-minute difference override
    # tl_cnt's load-balancing entirely for EVERY pair of candidates, which is
    # too aggressive. Tiers only matter (override tl_cnt) when a candidate is
    # meaningfully close to running out of shift time; within the same tier,
    # tl_cnt still decides exactly as before.
    # 0 = <2h remaining (urgent) 1 = 2-4h remaining 2 = >4h remaining (relaxed)
    shift_urgency = np.full(n, 2, dtype=np.float32)
    if task_end is not None:
        _te_m_urg = task_end.hour * 60 + task_end.minute
        for _i_urg in range(n):
            _se_raw_urg = clean_text(se_col[_i_urg])
            if is_time_text(_se_raw_urg):
                _remaining_urg = (time_to_minutes(_se_raw_urg) - _te_m_urg) % 1440
                if _remaining_urg < 120:
                    shift_urgency[_i_urg] = 0
                elif _remaining_urg < 240:
                    shift_urgency[_i_urg] = 1

    # ── First-task elapsed-time preference (all roles except restricted workers) ──
    # For any employee's FIRST task: prefer those who have been at work LONGER.
    # e.g. for a 05:00 flight: employee starting 02:00 (3 h elapsed) beats one
    # starting 04:45 (15 min elapsed) — the early arrival had time for a break first.
    # Exception: פורשים / ילדי עובדים are excluded (restricted-worker pool, different rules).
    # Capped at 90 min (2026-07-17): uncapped, this continuously favors whoever
    # has been on shift longest with no ceiling — night-shift workers carrying
    # over from the PREVIOUS day (6+ hours elapsed by early morning) always beat
    # today's 02:00-shift workers (1-2 hours elapsed) for early-morning
    # דייל/מתאם תורים tasks, even though both groups are meant to split that
    # work roughly evenly (both sit in the SAME shift_pri tier for these roles
    # — see _em_pri, night=0 and early_morning-non-0330=0). Found via real
    # data 2026-07-17: 12.07 reproduction showed 55 early-morning
    # דייל/מתאם-תורים tasks going to night-shift-from-11.07 workers vs only 2
    # to 02:00-shift-from-12.07 workers. Once BOTH groups have had 90+ min
    # (enough for a genuine break/settle-in), treat them as equally rested —
    # the original "prefer whoever's had time for a break" intent still holds
    # for short differences (e.g. 15 min vs 60 min), it just no longer keeps
    # scaling unbounded once everyone involved is already well-rested, letting
    # task_count (natural load balancing) decide the rest.
    _is_restr_arr = (_yn("ילד עובדים") + _yn("פורשים")) > 0
    first_task_elapsed = np.zeros(n, dtype=np.float32)
    if task_start:
        _ts_m_el = task_start.hour * 60 + task_start.minute
        for i, nm in enumerate(names):
            if _is_restr_arr[i]:
                continue  # excluded
            if not by_name.get(nm, []):  # first task only
                _ss_raw_el = clean_text(ss_col[i])
                if is_time_text(_ss_raw_el):
                    _ss_m_el = time_to_minutes(_ss_raw_el)
                    _elapsed = min((_ts_m_el - _ss_m_el) % 1440, 90)
                    first_task_elapsed[i] = -float(_elapsed)  # more elapsed → more negative → preferred

    # ── Sort (lexsort: last key = most significant) ──────────────────────────
    # shift_bad is always most significant: employees outside their shift go last,
    # so _try_select finds a valid candidate in the first few iterations.
    # first_task_elapsed sits between prox and shift_pri so that:
    #   - recent_task / shift_tail (more significant) still drive multi-flight continuity
    #   - within the same shift_pri bucket, longer-serving employees beat fresh ones
    #   - prox (less significant) is only a final tiebreaker
    #
    # For early-morning flights (01:30–07:59), night-shift ר"צ must win over
    # 02:00/03:30 workers regardless of how many tasks they've already done.
    # shift_pri is promoted to just below shift_bad so it overrides tl_cnt.
    _is_early_morning_flight = (
        task_end is not None
        and _te_m < 8 * 60
        and not _is_midnight_cross
        and not flight_before_130
    )
    if role == "ראש צוות":
        # dual_qual (TL+TSA-inspector-certified) used to sit in this tuple to
        # pre-emptively hold dual-certified workers back from ר"צ slots all
        # day, "just in case" a TSA inspector slot needed them later. That
        # cost was paid even on days where every TSA slot ends up covered by
        # other specialists — found via real data 2026-07-15: TL#1 (TL+TSA dual-certified) went from 6 ר"צ tasks to 0 after an
        # unrelated fix widened the ר"צ candidate pool, because dual_qual
        # alone was enough to push her behind ~30 other candidates for every
        # ר"צ slot all day — while the one TSA slot that existed was already
        # filled by non-dual-certified specialists, so the "reservation" never
        # paid off. The actual protection against a left-uncovered TSA slot
        # is reserve_dual_certified_for_tsa (a post-build pass that moves a
        # dual-certified worker OFF a ר"צ task ONLY when a real TSA slot is
        # still ❌ after the main build) — that pass is demand-driven and only
        # acts when actually needed, making this upfront blanket handicap
        # redundant. Removed from both branches below.
        if _is_early_morning_flight:
            # Promotion: shift_pri dominates after shift_bad.
            # tl_cnt still balances load *within* the same shift-type bucket.
            # shift_urgency intentionally NOT added here — this branch's
            # ordering was previously tuned and confirmed ("RESOLVED",
            # commit 6ffd1f4) and early-morning candidates are usually all
            # early in their shift anyway, where remaining-time urgency is
            # less meaningful than for a long-shift worker nearing shift end.
            # night_flight_done sits just BELOW shift_pri: the shift group
            # still decides first (night ר"צ ahead of 02:00/03:30), and within
            # the night group the one who has NOT yet worked a night flight
            # goes down to the pre-dawn flight first.
            keys = (task_count, nearby, prox, first_task_elapsed, tl_ready, peak_tl_pref, shift_tail, recent_task, tl_cnt, night_flight_done, shift_pri, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)
        else:
            # shift_urgency sits just above tl_cnt (more significant) so a
            # candidate genuinely running low on remaining shift time can win
            # over one with a lower task_count but hours of shift left —
            # see the shift_urgency computation above for the real-data case
            # (TL#8 / TL#26, LY5467) this was added for.
            keys = (task_count, nearby, prox, first_task_elapsed, tl_ready, peak_tl_pref, shift_pri, shift_tail, recent_task, tl_cnt, shift_urgency, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)
    elif role in ("דייל", "מתאם תורים"):
        # tl_att_pref: among TL workers forced into a דייל slot, prefer those who already
        # have more ראש צוות tasks (they've fulfilled their primary role; fresh TLs should
        # be kept available for upcoming ראש צוות slots).
        tl_att_pref = np.where(is_tl > 0, -tl_cnt, 0.0).astype(np.float32)
        # tl_preserve is moved to position 1-from-right (just below shift_bad) so that
        # TL-qualified workers ALWAYS sort after non-TL workers for דייל/מתאם תורים slots,
        # regardless of their att_priority or tl_att_pref values.
        # EXCEPTION (2026-07-15): once a TL worker is genuinely running low on
        # remaining shift time (shift_urgency tier 0, <2h left), "preserving"
        # them for a future ר"צ slot rarely pays off — there's little time
        # left to use them there anyway — and doing so anyway just leaves them
        # idle. Relax the reservation in that tier so they compete normally
        # for plain דייל/מתאם תורים work instead, maximizing floor time. Same
        # anti-pattern as the dual_qual/mentor-reservation fixes earlier this
        # session: an upfront blanket handicap paid even when the reserved-for
        # need never materializes. Found via real data 2026-07-15: בן TL#13 (02:00-11:00, ר"צ-qualified, last ר"צ task ends 09:05 — under
        # 2h left) stayed idle 09:05-11:00 despite LY315 (plain דייל, fully
        # within her shift) being open — she was ranked ~158th/340 purely on
        # tl_preserve, well behind ordinary attendants who had no such
        # reservation need.
        tl_preserve = np.where(shift_urgency < 1, 0.0, tl_preserve)
        # att_priority (runners first) sits just BELOW shift_pri: the shift group
        # decides first (night/02:00 before 03:30 for early-morning flights), and
        # WITHIN the same group runners are always preferred over regular
        # attendants — user-confirmed 2026-07: "תמיד עדיף לשבץ רצים, רק אם אין
        # ברירה לשבץ דיילים", with night/02:00 → 03:30 as the outer order.
        # group_balance is positioned ABOVE shift_tail/recent_task (more
        # significant), not just above nearby/prox/first_task_elapsed/
        # att_priority — a first attempt placed it just below shift_pri only,
        # which had NO effect: shift_tail (favors whoever's shift ends SOONER
        # — night-shift-carryover workers, who started 22:00ish, always have
        # less runway left by early morning than a 02:00-shift worker with an
        # 09:30 shift end) and recent_task (continuity bonus) both sit above
        # that position and silently overrode group_balance every time, found
        # via direct sort-key inspection on real data 2026-07-17. Moved above
        # both so it actually dominates within the tied night/02:00 tier.
        # em_split sits ABOVE group_balance: group_balance scores only the
        # night and 02:00 groups (everyone else keeps its 0 default), so a
        # 03:30 worker silently outranked both balanced groups on it — the
        # very outcome the user's morning split is meant to prevent. Above it,
        # em_split decides which half of the morning a candidate belongs to
        # and group_balance then balances night-vs-02:00 WITHIN that half.
        keys = (task_count, nearby, prox, first_task_elapsed, att_priority, shift_pri, shift_tail, idle_first, night_lateness_fit, recent_task, group_balance, em_split, dual_qual, tsa_preserve, peak_em_tl, tl_att_pref, tl_preserve, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)
    elif role == "מפקח TSA":
        # same_pier_pref goes FIRST so pier consolidation dominates load-
        # balancing (task_count/nearby/prox) but never overrides shift
        # validity or terminal restrictions (shift_bad/transfer_lr/
        # upcoming_lr, which stay last as absolute overrides). The comment
        # here used to claim same_pier_pref "sits high... dominates
        # load-balancing", but it was actually placed AFTER task_count/
        # nearby/prox/.../inspector_shift — 10th out of 13 keys — so those
        # earlier keys silently broke the tie first whenever two candidates
        # differed on ordinary fairness/proximity scoring, splitting a
        # single pier's coverage across two inspectors instead of
        # consolidating on the one already there (found via real data
        # 2026-07-25: בר שיילو + TL#37 both landed on pier D's 4 flights
        # instead of one of them alone, leaving TL#37 unavailable as ר"צ).
        keys = (same_pier_pref, inspector_shift, task_count, nearby, prox, first_task_elapsed, shift_pri, shift_tail, recent_task, dual_qual, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)
    elif role == "שומר TSA":
        keys = (same_pier_pref, task_count, nearby, prox, first_task_elapsed, shift_pri, recent_task, tsa_guard_priority, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)
    else:
        keys = (task_count, nearby, prox, first_task_elapsed, shift_pri, shift_tail, night_lateness_fit, recent_task, dual_qual, tsa_preserve, first_task_buffer, transfer_lr, upcoming_lr, mgr_last, shift_bad)

    idx = np.lexsort(keys)
    # Return only the sorted names — _try_select looks up full employee data
    # from the pre-built _emp_data dict in build_schedule (O(1) per lookup).
    return [names[int(i)] for i in idx]

def has_required_mentor(assignments_for_flight, employees_df, training_type):
    required_col = "מסמיך רצים" if training_type == "הסמכה" else "חונך רצים"
    for task in assignments_for_flight:
        if str(task["תפקיד"]).startswith("ראש צוות") and "❌" not in str(task["עובד"]):
            emp = employees_df[employees_df["שם"] == task["עובד"]]
            if not emp.empty and str(emp.iloc[0].get(required_col, "")).strip() == "כן":
                return True
    return False


def has_trainee_available(employees_df):
    if "טרייני רצ" not in employees_df.columns:
        return False
    return (employees_df["טרייני רצ"].astype(str).str.strip() == "כן").any()


def flight_has_mentor_teamlead(assignments_for_flight, employees_df, training_type):
    required_cols = ["חונך רצים", "מסמיך רצים"]
    if clean_text(training_type) == "הסמכה":
        required_cols = ["מסמיך רצים"]

    for task in assignments_for_flight:
        if not str(task.get("תפקיד", "")).startswith("ראש צוות"):
            continue
        worker = str(task.get("עובד", ""))
        if "❌" in worker:
            continue
        emp = employees_df[employees_df["שם"] == worker]
        if emp.empty:
            continue
        row = emp.iloc[0]
        for col in required_cols:
            if str(row.get(col, "")).strip() == "כן":
                return True
    return False


def trainee_already_used(assignments):
    for task in assignments:
        if str(task.get("תפקיד בסיס", "")) == "טרייני רצ" and "❌" not in str(task.get("עובד", "")):
            return True
    return False


def _post_break_gap_too_large(emp, new_task_start, emp_tasks):
    """
    Returns True (block assignment) when the employee has worked ≥ 3 h AND
    the gap from their last existing task to the new task exceeds break_duration
    + castra allowance — meaning they would need "חזרה לדלפקים" before this task,
    which contradicts assigning it to them.
    Exception: night-shift employees (they may return after a break).
    """
    if not emp_tasks:
        return False
    from utils.helpers import classify_shift, required_break, is_time_text, time_to_minutes, clean_text
    if classify_shift(emp) == "night":
        return False
    ss_str = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss_str):
        return False
    ss_m = time_to_minutes(ss_str)
    new_m = new_task_start.hour * 60 + new_task_start.minute
    if new_m < ss_m:
        new_m += 1440
    task_intervals = []
    for t in emp_tasks:
        ts = clean_text(t.get("התחלה", ""))
        te = clean_text(t.get("סיום", ""))
        if not is_time_text(ts) or not is_time_text(te):
            continue
        ts_m = time_to_minutes(ts)
        te_m = time_to_minutes(te)
        if ts_m < ss_m: ts_m += 1440
        if te_m < ss_m: te_m += 1440
        if te_m < ts_m: te_m += 1440
        if ts_m < new_m:
            task_intervals.append((ts_m, min(te_m, new_m)))
    if not task_intervals:
        return False
    total_worked = sum(te - ts for ts, te in task_intervals)
    if total_worked < 3 * 60:
        return False
    last_te = max(te for _, te in task_intervals)
    gap = new_m - last_te
    rb = required_break(emp) or 45
    is_tl = clean_text(str(emp.get("ראש צוות", ""))) == "כן"
    castra = 30 if is_tl else 20
    return gap > rb + castra


_BREAK_LATE_GRACE = 60  # minutes past shift_start+MAX_CONTINUOUS before we call a break "too late"


def _would_break_too_late(emp, emp_tasks, task_start, task_end, assignments, emp_name):
    """
    True when assigning this task would require the employee's break to fall
    more than _BREAK_LATE_GRACE minutes past shift_start + MAX_CONTINUOUS.
    Night-shift employees are exempt (their long prior break resets the counter).
    Only fires when the employee would actually exceed MAX_CONTINUOUS work.
    """
    if not emp_tasks:
        return False
    if classify_shift(emp) == "night":
        return False
    ss_str = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss_str):
        return False
    ss_m = time_to_minutes(ss_str)
    te_m = task_end.hour * 60 + task_end.minute
    if te_m < ss_m:
        te_m += 1440
    deadline = ss_m + MAX_CONTINUOUS_WORK_MINUTES + _BREAK_LATE_GRACE
    if te_m <= deadline:
        return False
    # Only block when they would actually need a break (reuses the fixed continuous-work logic)
    return would_exceed_max_continuous(assignments, emp_name, emp, task_start, task_end,
                                       emp_tasks=emp_tasks)


def _try_select(sorted_names, assignments, role, start, end, flight_gate,
                check_break=True, check_continuous=True, check_late_break=True,
                _by_name=None, _emp_lookup=None):
    """
    sorted_names: list[str] from sort_candidates — already sorted.
    _emp_lookup: {name: emp_dict} built once in build_schedule from object-dtype DataFrame.
    _by_name:    {name: [tasks]} for O(1) task lookup.
    Falls back to dict iteration if sorted_names is a list[dict] or DataFrame.
    """
    by_name = _by_name
    # Terminal of this task, derived from the gate ("1" = Terminal 1 gates
    # 30-40 or the T1 roster's שלוחה; everything else is the Terminal 3 side).
    _task_term = get_terminal(flight_gate or "")

    # Support legacy list[dict] / DataFrame callers (e.g. upgrade_teamleads)
    if not sorted_names or isinstance(sorted_names[0] if isinstance(sorted_names, list) and sorted_names else None, dict):
        rows = sorted_names if isinstance(sorted_names, list) else sorted_names.to_dict("records") if hasattr(sorted_names, "to_dict") else []
        for emp in rows:
            name = emp["שם"]
            if not is_within_shift(emp, start, end, task_terminal=_task_term): continue
            emp_tasks = by_name.get(name, []) if by_name is not None else None
            if not is_available(assignments, name, start, end, emp, role=role, flight_gate=flight_gate, emp_tasks=emp_tasks): continue
            if check_break and not has_room_for_break(assignments, emp, name, start, end, emp_tasks=emp_tasks): continue
            if check_break and not has_break_gap_in_schedule(assignments, name, emp, start, emp_tasks=emp_tasks): continue
            if check_continuous and would_exceed_max_continuous(assignments, name, emp, start, end, emp_tasks=emp_tasks): continue
            if check_continuous and _post_break_gap_too_large(emp, start, emp_tasks): continue
            if check_late_break and _would_break_too_late(emp, emp_tasks, start, end, assignments, name): continue
            return name
        return None

    for name in (sorted_names if isinstance(sorted_names, list) else []):
        emp = _emp_lookup.get(name) if _emp_lookup else None
        if emp is None:
            continue
        if not is_within_shift(emp, start, end, task_terminal=_task_term):
            continue
        emp_tasks = by_name.get(name, []) if by_name is not None else None
        if not is_available(assignments, name, start, end, emp,
                            role=role, flight_gate=flight_gate, emp_tasks=emp_tasks):
            continue
        if check_break and not has_room_for_break(assignments, emp, name, start, end,
                                                  emp_tasks=emp_tasks):
            continue
        if check_break and not has_break_gap_in_schedule(assignments, name, emp, start,
                                                         emp_tasks=emp_tasks):
            continue
        if check_continuous and would_exceed_max_continuous(assignments, name, emp, start, end,
                                                            emp_tasks=emp_tasks):
            continue
        if check_continuous and _post_break_gap_too_large(emp, start, emp_tasks):
            continue
        if check_late_break and _would_break_too_late(emp, emp_tasks, start, end,
                                                      assignments, name):
            continue
        return name
    return None


# ── Diagnostic: explain why a role slot could not be filled ──────────────────

def explain_missing_role(flight, role, employees_df, assignments):
    """
    Returns a list of dicts {name, reason} explaining why each certified
    employee was NOT selected for `role` on `flight`.
    """
    results = []

    # Filter inactive
    active_df = employees_df
    if INACTIVE_COL in employees_df.columns:
        active_df = employees_df[
            employees_df[INACTIVE_COL].astype(str).str.strip() != "לא"
        ]

    role_col = role if role in active_df.columns else next(
        (c for c in active_df.columns if clean_text(c).upper() == clean_text(role).upper()), None
    )
    if not role_col:
        return [{"name": "—", "reason": f"עמודת הסמכה '{role}' לא קיימת בקובץ העובדים"}]

    certified = active_df[active_df[role_col].astype(str).str.strip() == "כן"]

    # Only explain workers who are actually IN today's uploaded daily schedule.
    # A role-qualified worker absent from today's roster (in_daily_excel=0) is
    # irrelevant to this shortage and just floods the reasons list (real 20.07:
    # 69 ר"צ-qualified in employees_clean but only 29 on today's roster — the
    # other 40 were listed with "שיבוץ לא רלוונטי" clutter). User 2026-07-20:
    # "יש להציג רק אנשים שרלוונטיים לקובץ הסידור היומי שהועלה". Same
    # "daily-Excel-loaded" guard as sort_candidates (skip when no daily Excel
    # was loaded, i.e. nobody is marked present).
    if "in_daily_excel" in active_df.columns:
        _ide_all = pd.to_numeric(active_df["in_daily_excel"], errors="coerce").fillna(0)
        if (_ide_all >= 1).any():
            _ide_cert = pd.to_numeric(certified["in_daily_excel"], errors="coerce").fillna(0)
            certified = certified[_ide_cert >= 1]

    start = role_start_time(flight, role)
    end   = role_end_time(flight)

    gate = clean_text(flight.get("גייט", ""))
    if not gate:
        _terminal_val = clean_text(str(flight.get("שלוחה", "")))
        if _terminal_val:
            gate = _terminal_val
    _task_term = get_terminal(gate)
    _flight_side = "1" if _task_term == "1" else "3"

    for _, emp in certified.iterrows():
        name = str(emp.get("שם", "")).strip()
        if not name:
            continue

        # Check 1: shift covers the task (terminal-aware — a Terminal-1 worker
        # is not eligible for a Terminal-3 flight and vice versa)
        if not is_within_shift(emp, start, end, task_terminal=_task_term):
            if is_within_shift(emp, start, end):
                # הטרמינל האפקטיבי של העובד/ת בשעת המשימה (מודע להערת מעבר)
                _w_term = clean_text(str(emp.get("טרמינל", ""))) or "3"
                _tr_note = clean_text(str(emp.get("מעבר טרמינל", "")))
                if "@" in _tr_note:
                    _tr_to, _tr_at = _tr_note.split("@", 1)
                    if is_time_text(_tr_at):
                        _at_m = time_to_minutes(_tr_at)
                        _ts_m = start.hour * 60 + start.minute
                        # ציר צהריים — שעות לילה שייכות לסוף היום התפעולי
                        if _at_m < 720: _at_m += 1440
                        if _ts_m < 720: _ts_m += 1440
                        if _ts_m >= _at_m:
                            _w_term = _tr_to.strip() or _w_term
                results.append({"name": name, "reason": (
                    f"הפרדת טרמינלים — משובץ/ת לטרמינל {_w_term} בשעות אלו, "
                    f"והטיסה יוצאת מטרמינל {_flight_side}"
                )})
            else:
                ss = clean_text(emp.get("תחילת משמרת", "—"))
                se = clean_text(emp.get("סוף משמרת", "—"))
                _base_reason = f"משמרת ({ss}–{se}) לא מכסה את זמן התפקיד ({start.strftime('%H:%M')}–{end.strftime('%H:%M')})"
                # Near-miss: the employee's shift covers the task's START but
                # ends only slightly BEFORE the task's END (≤ 30 min short) —
                # a real, qualified candidate who could plausibly extend their
                # shift a few minutes, not someone genuinely unavailable. Flag
                # distinctly so a human scheduler knows to ask the employee
                # rather than treat this as a dead end and leave the slot
                # silently understaffed (user rule 2026-07-13: for a flight
                # ending just after a qualified worker's shift end, ask if
                # they're willing to extend it; if not, the slot correctly
                # stays vacant).
                if is_time_text(ss) and is_time_text(se):
                    _ss_m = time_to_minutes(ss)
                    _se_m = time_to_minutes(se)
                    _se_norm = _se_m if _se_m > _ss_m else _se_m + 1440
                    _task_start_m = start.hour * 60 + start.minute
                    _task_end_m = end.hour * 60 + end.minute
                    _ts_norm = _task_start_m if _task_start_m >= _ss_m else _task_start_m + 1440
                    _te_norm = _task_end_m if _task_end_m >= _ts_norm else _task_end_m + 1440
                    _shift_covers_start = _ss_m <= _ts_norm <= _se_norm
                    _gap_m = _te_norm - _se_norm
                    if _shift_covers_start and 0 < _gap_m <= 30:
                        _base_reason = (
                            f"❓ ניתן לשקול הארכת משמרת — משמרת מסתיימת ב-{se}, "
                            f"התפקיד מסתיים ב-{end.strftime('%H:%M')} "
                            f"(פער של {_gap_m} דק' בלבד). יש לשאול את העובד/ת "
                            f"אם מוכן/ה להאריך; אם לא — להשאיר בחוסר."
                        )
                results.append({"name": name, "reason": _base_reason})
            continue

        # Check 2: not already assigned at the same time
        if not is_available(assignments, name, start, end, emp, role=role, flight_gate=gate):
            # Find the actually-conflicting task (within 5-min buffer, midnight-safe)
            start_m = start.hour * 60 + start.minute
            end_m   = end.hour   * 60 + end.minute
            if end_m < start_m: end_m += 1440
            conflict = None
            for t in assignments:
                if t.get("עובד") != name:
                    continue
                ts_str = clean_text(t.get("התחלה", ""))
                te_str = clean_text(t.get("סיום", ""))
                if not ts_str or not te_str:
                    continue
                try:
                    ts_m = time_to_minutes(ts_str)
                    te_m = time_to_minutes(te_str)
                    if te_m < ts_m: te_m += 1440
                    if not (start_m >= te_m + 5 or end_m <= ts_m - 5):
                        conflict = t
                        break
                except Exception:
                    pass
            if conflict:
                results.append({"name": name, "reason": f"כבר משובץ ל-{conflict.get('תפקיד','?')} בטיסה {conflict.get('טיסה','?')} ({conflict.get('התחלה','?')}–{conflict.get('סיום','?')})"})
            else:
                results.append({"name": name, "reason": "חסום (חפיפה, חסימה, או גבול TSA)"})
            continue

        # Check 3: room for break
        if not has_room_for_break(assignments, emp, name, start, end):
            results.append({"name": name, "reason": "שיבוץ יגרום לחריגה ממכסת שעות המשמרת (כולל הפסקה)"})
            continue

        # Check 4: continuous work limit
        if would_exceed_max_continuous(assignments, name, emp, start, end):
            results.append({"name": name, "reason": "עבודה רצופה תחרוג מ-4 שעות ללא הפסקה"})
            continue

        # If all checks pass — this employee COULD have been selected (shouldn't happen for missing)
        results.append({"name": name, "reason": "✅ זמין — ייתכן שנחסם בשלב קדימויות"})

    if not results:
        results.append({"name": "—", "reason": "אין עובדים עם הסמכת ראש צוות זמינים במשמרת זו"})

    return results


# ── Employee-type helpers ─────────────────────────────────────────────────────

def _is_restricted_worker(emp_row):
    """ילדי עובדים / פורשים — can only fill שומר TSA and דייל slots."""
    for col in RESTRICTED_WORKER_COLS:
        if str(emp_row.get(col, "")).strip() == "כן":
            return True
    return False


def _is_trainee_attendant(emp_row):
    """דייל בטרייני — must be paired; counts as 2 in a 2-attendant flight."""
    return str(emp_row.get(TRAINEE_ATTENDANT_COL, "")).strip() == "כן"


def _is_broadly_restricted(emp_row):
    """Any of: ילדי עובדים, פורשים, דייל בטרייני."""
    return _is_restricted_worker(emp_row) or _is_trainee_attendant(emp_row)


def _is_instructor(emp_row):
    return str(emp_row.get(INSTRUCTOR_COL, "")).strip() == "כן"


def _max_restricted_attendants(req):
    """
    How many broadly-restricted workers (ילדי עובדים/פורשים/דייל בטרייני)
    may appear in the attendant/guard slots for this flight.
    Rule: at least one regular attendant always required, except that
    two דייל-בטרייני together are allowed in a 2-attendant flight.
    """
    total = req.get("דייל", 0) + req.get("שומר TSA", 0)
    if total <= 1:
        return 0        # single slot → must be a regular attendant
    elif total <= 3:
        return 1        # narrow body ×2, wide body ×3 → max 1 restricted
    else:
        return 2        # wide body USA ×4 + 1 TSA guard → max 2 restricted


# Roles that restricted workers (ילדי עובדים / פורשים) may NOT fill
_RESTRICTED_FORBIDDEN_ROLES = {"ראש צוות", "מתאם תורים", "מפקח TSA", "טרייני רצ"}

# Set by data_loader when the roster posts a worker to a non-flight duty
# ("מזכירת DKK"). Non-empty ⇒ no automatic flight assignment.
DUTY_BLOCK_COL = "תפקיד חוסם"


def schedulable_employees(employees_df):
    """
    employees_df minus anyone posted to a non-flight duty.

    Every scheduling pass must run on this, not on the raw frame: filtering
    only inside build_schedule was not enough, because the post-passes take the
    caller's employees_df and put such a worker straight back (found via real
    data 2026-08-09: TSA-inspector#1, מזכירת DKK, reappeared as מפקח on LY011).
    The raw frame is still what the swap UI reads, so they remain available
    there as an explicit approval-required choice.
    """
    if DUTY_BLOCK_COL not in employees_df.columns:
        return employees_df
    return employees_df[
        employees_df[DUTY_BLOCK_COL].astype(str).str.strip() == ""
    ].copy()


# =========================
# SEGMENTED (THREE-TIMES-A-DAY) BUILDS
# =========================
# One operational day is built three times, by three different shift teams:
#   נייט  – from the first pre-dawn flight through the last flight that the
#           morning shift (02:00 / 03:30) can still staff on its own
#   דיי   – from there through the last flight departing before midnight
#   אפטר  – every flight departing after midnight
# Each build CONTINUES the previous one (see build_schedule's pre_assignments);
# only the נייט→דיי boundary needs computing, the rest follows from midnight.

# Shift-start bands. "משמרת בוקר" = the 02:00 and 03:30 shifts; "משמרת יום" =
# 08:00 onwards. The night (22:00) shift is deliberately absent: it is part of
# the נייט build's own pool and its shifts end long before the boundary.
_MORNING_START_BAND = (90, 4 * 60)          # 01:30–04:00
_DAY_START_BAND     = (8 * 60, 11 * 60 + 30)

_SEGMENT_MORNING_RATIO = 0.8

# The 90% test is a BOUNDARY test, applied to daytime flights ONLY. Pre-dawn
# flights are staffed by the night and morning shifts under the existing rules
# and must not be affected by it — and since no day-shift worker is clocked in
# before 08:00 the test would be vacuously true there anyway.
_SEGMENT_DAYTIME_FROM = 9 * 60              # 09:00

# Roles the 80% rule does NOT govern. A ר"צ or מפקח rostered past the נייט
# boundary keeps taking flights that depart inside their shift — those slots are
# built by the נייט team even though the flight itself belongs to דיי (user rule
# 2026-08-09: TL#36, 03:30-13:30, must be ר"צ on LY023 departing 13:15).
_EXTENDED_SEGMENT_ROLES = ("ראש צוות", "מפקח TSA")
_EXTENDED_ROLE_COLUMNS = {"ראש צוות": "ראש צוות", "מפקח TSA": "מפקח TSA"}

# The operational day starts here: flights are ordered (t - this) % 1440, so a
# 00:30 departure sorts AFTER a 23:05 one rather than before the 04:00 wave.
OPERATIONAL_DAY_START = 3 * 60              # 03:00


def _flight_departure_minutes(flight):
    txt = clean_text(flight.get("המראה", ""))
    if not is_time_text(txt):
        return None
    return time_to_minutes(txt)


def _flight_staffing_window(flight):
    """(start, end) of the widest window among the roles this flight requires."""
    req = get_requirements(flight)
    roles = [r for r in ROLE_ORDER if req.get(r, 0) > 0]
    if not roles:
        return None
    try:
        return min(role_start_time(flight, r) for r in roles), role_end_time(flight)
    except Exception:
        return None


def _morning_pool(employees_df):
    """
    Terminal-3 employees whose shift starts in the 02:00/03:30 band.

    Terminal 1 is deliberately excluded on both sides of the boundary test (see
    compute_night_boundary): it has its own roster and its own much smaller
    morning pool, and mixing the two distorts the ratio in both directions.
    """
    lo, hi = _MORNING_START_BAND
    pool = []
    for rec in employees_df.to_dict("records"):
        start = clean_text(rec.get("תחילת משמרת", ""))
        if not is_time_text(start):
            continue
        if clean_text(rec.get("טרמינל", "")) == "1":
            continue
        if lo <= time_to_minutes(start) <= hi:
            pool.append(rec)
    return pool


def compute_night_boundary(flights_df, employees_df):
    """
    Last flight the נייט team should build, or None when it cannot be determined.

    Walks the daytime flights in operational order and keeps extending the
    segment while at least _SEGMENT_MORNING_RATIO of a flight's OWN slots can
    be filled from morning-shift workers who are on shift for it — the
    remainder coming from the day shift. The first daytime flight that fails
    ends the נייט segment; it and everything after it belong to the דיי build.

    ר"צ and מפקח TSA slots are excluded from the count — the rule does not
    apply to them (user rule 2026-08-09). A morning ר"צ/מפקח rostered past the
    boundary is still assigned to flights departing inside their shift; see
    _EXTENDED_SEGMENT_ROLES and compute_segment_boundaries' "extension".

    Deliberately a plain per-flight availability question ("are there enough
    morning workers to staff this flight?"), the way the shift manager asks it.
    Two stricter models were tried and both cut the segment short: comparing
    each flight to the PEAK CONCURRENT demand failed LY5467 (needs 3, had 6
    morning workers free) by measuring it against 14 concurrent slots, and
    consuming the pool greedily across overlapping flights ran it dry at 10:15.
    """
    import math
    pool = _morning_pool(employees_df)
    if not pool:
        return None

    # (shift start, shift end) minutes for the morning pool
    spans = []
    for rec in pool:
        _ss = clean_text(rec.get("תחילת משמרת", ""))
        _se = clean_text(rec.get("סוף משמרת", ""))
        if is_time_text(_ss) and is_time_text(_se):
            spans.append((time_to_minutes(_ss), time_to_minutes(_se)))

    def _covers(span, start, end):
        _s, _e = span
        _len = (_e - _s) % 1440 or 1440
        return (0 <= (start - _s) % 1440 <= _len) and (0 <= (end - _s) % 1440 <= _len)

    rows = []
    for _, flight in flights_df.iterrows():
        dep = _flight_departure_minutes(flight)
        window = _flight_staffing_window(flight)
        if dep is None or window is None or is_cancelled_flight(flight):
            continue
        # Terminal 1 flights are out of scope for the boundary test — the נייט
        # segment's extent is decided by the Terminal-3 morning pool alone.
        if get_terminal(flight.get("גייט", "")) == "1":
            continue
        rows.append({
            "flight": clean_text(flight.get("טיסה", "")),
            "dep": dep,
            "start": window[0].hour * 60 + window[0].minute,
            "end": window[1].hour * 60 + window[1].minute,
            "req": sum(
                _n for _r, _n in get_requirements(flight).items()
                if _r not in _EXTENDED_SEGMENT_ROLES
            ),
        })
    if not rows:
        return None
    rows.sort(key=lambda r: (r["dep"] - OPERATIONAL_DAY_START) % 1440)

    last_ok = None
    for row in rows:
        if not (_SEGMENT_DAYTIME_FROM <= row["dep"] < 24 * 60):
            continue
        free = sum(1 for span in spans if _covers(span, row["start"], row["end"]))
        if free < math.ceil(_SEGMENT_MORNING_RATIO * row["req"]):
            break
        last_ok = row
    return last_ok["flight"] if last_ok else None


def compute_segment_boundaries(flights_df, employees_df):
    """
    Split an operational day into the נייט / דיי / אפטר build segments.

    Returns {"night": seg, "day": seg, "after": seg} where each seg is either
    None (no flights) or a dict with from_minutes / to_minutes (wall clock,
    inclusive), first_flight / last_flight and the ordered flight numbers.

    The נייט segment additionally carries "extension": flights past its boundary
    on which it still fills only the ר"צ / מפקח TSA slots, because a morning
    ר"צ/מפקח rostered that late must keep working (see _EXTENDED_SEGMENT_ROLES).
    Those flights stay in the דיי segment — the דיי team fills the rest of them.
    """
    rows = []
    for _, flight in flights_df.iterrows():
        dep = _flight_departure_minutes(flight)
        if dep is None or is_cancelled_flight(flight):
            continue
        rows.append((dep, clean_text(flight.get("טיסה", ""))))
    rows.sort(key=lambda r: (r[0] - OPERATIONAL_DAY_START) % 1440)
    if not rows:
        return {"night": None, "day": None, "after": None}

    boundary = compute_night_boundary(flights_df, employees_df)
    # אפטר starts at the first departure after midnight; דיי ends just before it.
    after_from = next((i for i, (dep, _) in enumerate(rows) if dep < OPERATIONAL_DAY_START), len(rows))
    night_to = -1
    if boundary:
        night_to = next(
            (i for i, (_, fn) in enumerate(rows) if fn == boundary and i < after_from),
            -1,
        )

    def _seg(lo, hi):
        part = rows[lo:hi]
        if not part:
            return None
        return {
            "from_minutes": part[0][0],
            "to_minutes": part[-1][0],
            "first_flight": part[0][1],
            "last_flight": part[-1][1],
            "flights": [fn for _, fn in part],
        }

    night = _seg(0, night_to + 1) if night_to >= 0 else None
    if night:
        night["extension"] = _extension_flights(
            flights_df, employees_df, rows[night_to + 1:after_from]
        )
        night["extension_roles"] = list(_EXTENDED_SEGMENT_ROLES)
    return {
        "night": night,
        "day": _seg(night_to + 1, after_from),
        "after": _seg(after_from, len(rows)),
    }


def _extension_flights(flights_df, employees_df, later_rows):
    """
    Post-boundary flights whose ר"צ / מפקח TSA slots the נייט team still fills.

    A flight qualifies when a morning-shift worker certified for one of those
    roles is on shift for the whole of it — i.e. the flight departs, and its
    role window starts, inside that worker's shift.
    """
    if not later_rows:
        return []

    candidates = []          # (shift start, shift end) of morning ר"צ/מפקח
    for rec in _morning_pool(employees_df):
        if not any(
            clean_text(rec.get(col, "")) == "כן"
            for col in _EXTENDED_ROLE_COLUMNS.values()
            if col in rec
        ):
            continue
        _ss, _se = clean_text(rec.get("תחילת משמרת", "")), clean_text(rec.get("סוף משמרת", ""))
        if is_time_text(_ss) and is_time_text(_se):
            candidates.append((time_to_minutes(_ss), time_to_minutes(_se)))
    if not candidates:
        return []

    by_flight = {}
    for _, flight in flights_df.iterrows():
        by_flight[clean_text(flight.get("טיסה", ""))] = flight

    out = []
    for _, flight_num in later_rows:
        flight = by_flight.get(flight_num)
        if flight is None:
            continue
        req = get_requirements(flight)
        roles = [r for r in _EXTENDED_SEGMENT_ROLES if req.get(r, 0) > 0]
        if not roles:
            continue
        start = min(role_start_time(flight, r) for r in roles)
        start_m = start.hour * 60 + start.minute
        end_m = role_end_time(flight).hour * 60 + role_end_time(flight).minute
        for _s, _e in candidates:
            _len = (_e - _s) % 1440 or 1440
            if 0 <= (start_m - _s) % 1440 <= _len and 0 <= (end_m - _s) % 1440 <= _len:
                out.append(flight_num)
                break
    return out


def build_schedule(flights_df, employees_df, pre_assignments=None):
    """
    pre_assignments: existing tasks (list of dicts / DataFrame records) from a
    PREVIOUS build that must be respected — used for per-terminal builds, where
    e.g. the Terminal-3 schedule is kept fixed while Terminal-1 flights are
    (re)built. Seeded workers count as busy for availability/continuity checks,
    and the seeded tasks are included in the returned schedule as-is.
    """
    import time
    _t_phases = {}
    _t0_total = time.time()
    assignments = []
    if pre_assignments is not None:
        if hasattr(pre_assignments, "to_dict"):
            assignments = pre_assignments.to_dict("records")
        else:
            assignments = [dict(t) for t in pre_assignments]
    # Normalise Arrow-backed dtypes on both DataFrames (Streamlit returns
    # ArrowDtype, and pandas 3.x's default pyarrow-backed string dtype is
    # equally slow for the heavy per-cell .at[]/iterrows() access throughout
    # this function's assignment loop). This comment used to describe "both"
    # but only flights_df was ever actually converted — employees_df stayed
    # whatever dtype the caller passed in, which on a live Streamlit session
    # (session_state round-trips through Arrow) meant the ENTIRE loop below
    # paid the slow-access cost regardless of what the caller normalized
    # beforehand (found via real data 2026-07-24: build_schedule's own loop
    # measured 13.5s live vs ~3.5s with employees_df already object-dtype).
    try:
        flights_df = flights_df.astype(object)
    except Exception:
        flights_df = flights_df.copy()
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        employees_df = employees_df.copy()
    flights_df["_flight_key"] = flights_df["טיסה"].apply(
        lambda v: clean_text(v).upper().replace(" ", "")
    )
    flights_df = flights_df.drop_duplicates(subset=["_flight_key"], keep="first").drop(columns=["_flight_key"])

    # ── Filter inactive employees ──────────────────────────────────────────────
    if INACTIVE_COL in employees_df.columns:
        employees_df = employees_df[
            employees_df[INACTIVE_COL].astype(str).str.strip() != "לא"
        ].copy()

    # ── Non-flight duty postings ("מזכירת DKK") ───────────────────────────────
    # Rostered to a job that is not flights: never assigned automatically. They
    # stay in the caller's employees_df, so the swap UI can still offer them as
    # an explicit approval-required choice (user rule 2026-08-09).
    if DUTY_BLOCK_COL in employees_df.columns:
        employees_df = employees_df[
            employees_df[DUTY_BLOCK_COL].astype(str).str.strip() == ""
        ].copy()

    # ── Normalise employee DataFrame to plain Python object dtypes ONCE ────────
    # pandas 2+ returns Arrow-backed StringDtype from read_excel / data_editor;
    # to_dict("records") on Arrow columns is ~50× slower due to per-cell boxing.
    # astype(object) is the one reliable call that forces every column to object.
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        employees_df = employees_df.copy()

    # Pre-compute boolean flags — vectorised; also cache sort-key booleans as __yn_* columns
    # so sort_candidates can use fast bool→float32 instead of astype(str).str.strip() == "כן"
    def _yn_s(col):
        if col not in employees_df.columns:
            import pandas as _pd
            return _pd.Series(False, index=employees_df.index)
        return employees_df[col].astype(str).str.strip() == "כן"

    # Both column-name variants: the employees file uses "ילד עובדים" (singular)
    _res = _yn_s("ילדי עובדים") | _yn_s("ילד עובדים") | _yn_s("פורשים")
    _tra = _yn_s(TRAINEE_ATTENDANT_COL)
    employees_df["__restricted"]  = _res
    employees_df["__trainee_att"] = _tra
    employees_df["__broadly_res"] = _res | _tra
    employees_df["__instructor"]  = _yn_s(INSTRUCTOR_COL)

    # Mentors of a דייל-בטרייני who is actually rostered today. Every mentor
    # placed on a flight DRAGS THEIR TRAINEE onto it too (the pairing rule),
    # and a trainee is a restricted worker — so several mentors on one flight
    # blow straight past _max_restricted_attendants (found via real 30.07 data:
    # LY017 ended with 3 restricted workers in 5 attendant slots against a cap
    # of 2, which then left agent#23 unable to join her own mentor there).
    # User rule 2026-08-06: "אני לא רוצה לשנות את הכמות של העובדים המוחרגים על
    # כל טיסה — פשוט אל תשתמש בכל כך הרבה עובדים שיש להם חניך בטיסה אחת."
    _mentor_names = set()
    if "דייל בטרייני" in employees_df.columns and "חונך דייל" in employees_df.columns:
        for _, _mr in employees_df.iterrows():
            if clean_text(str(_mr.get("דייל בטרייני", ""))) != "כן":
                continue
            _mn = clean_text(str(_mr.get("חונך דייל", "")))
            if _mn:
                _mentor_names.add(name_key(_mn))
    employees_df["__is_mentor"] = employees_df["שם"].apply(
        lambda _n: name_key(clean_text(str(_n))) in _mentor_names
    )

    # Cache boolean "כן" columns — covers both sort-keys and role eligibility
    for _c in list(ROLE_COLUMNS) + ["חונך רצים", "מסמיך רצים", "ילדי עובדים",
                                     "ילד עובדים", "פורשים", "מתדרכת", CREW_CHIEF_MANAGER_COL]:
        employees_df[f"__yn_{_c}"] = _yn_s(_c)

    _t_phases["setup"] = round(time.time() - _t0_total, 3)

    # Pre-built employee lookup {name: row_dict} for O(1) per-name access
    _emp_data: dict = {row["שם"]: row for row in employees_df.to_dict("records")}
    _t_phases["emp_dict"] = round(time.time() - _t0_total - _t_phases["setup"], 3)

    # Pre-split employees by role once — avoids repeated role-mask filtering per slot.
    # Per slot we only need to exclude used_on_flight (isin on 0-7 names).
    _nk_arr = employees_df["_name_key"].to_numpy()  # numpy for fast np.isin
    _role_df_cache: dict = {}
    for _r in list(ROLE_ORDER) + [CREW_CHIEF_MANAGER_COL]:
        _yn_r = f"__yn_{_r}"
        if _yn_r in employees_df.columns:
            _role_df_cache[_r] = employees_df[employees_df[_yn_r]]
        elif _r in employees_df.columns:
            _role_df_cache[_r] = employees_df[employees_df[_r].astype(str).str.strip() == "כן"]
        else:
            _role_df_cache[_r] = employees_df.iloc[0:0]

    # Process flights in pure departure-time order so the continuity/recent_task
    # sort key can keep employees in back-to-back flights without gaps.
    # TSA-qualified employees are preserved for inspector roles via the tsa_preserve
    # sort key in sort_candidates (they get lower priority for regular-attendant slots).
    _dest_col = "יעד" if "יעד" in flights_df.columns else None
    if "המראה" in flights_df.columns:
        # NOTE 2026-08-06: raw HH:MM order is midnight-blind — a post-midnight
        # departure (LY121 00:15, LY017 01:05) is processed BEFORE the same
        # night's 23:00 flights, so the continuity bonus this ordering exists
        # to enable cannot fire across midnight. An operational-day sort
        # (pivot at 03:00) was tried and REVERTED: it re-plans the whole night
        # block and measured WORSE on real 30.07 — missing 3→4, one
        # trainee/mentor pair broken, and TL#31 lost her pre-flight break
        # entirely. The narrow cross-midnight continuity case is handled after
        # the build instead, by improve_night_continuity().
        flights_df = flights_df.copy().sort_values("המראה", ascending=True)

    # Pre-build set of TSA-inspector-qualified employee name-keys (for within-flight exclusion)
    _tsa_inspector_col = "מפקח TSA"
    _yn_tsa_col = f"__yn_{_tsa_inspector_col}"
    if _yn_tsa_col in employees_df.columns:
        _tsa_inspector_names = set(employees_df.loc[employees_df[_yn_tsa_col], "_name_key"].tolist())
    elif _tsa_inspector_col in employees_df.columns:
        _tsa_inspector_names = set(employees_df.loc[
            employees_df[_tsa_inspector_col].astype(str).str.strip() == "כן", "_name_key"
        ].tolist())
    else:
        _tsa_inspector_names = set()

    # Pre-build set of team-leader-qualified employee name-keys.
    # Used to: (1) de-prioritise TLs as דייל on USA flights (last resort only),
    # and (2) completely exclude TLs from שומר TSA regardless of flight.
    _tl_col = "ראש צוות"
    if _tl_col in employees_df.columns:
        _tl_names = set(employees_df.loc[
            employees_df[_tl_col].astype(str).str.strip() == "כן", "_name_key"
        ].tolist())
    else:
        _tl_names = set()

    # Maintain a per-name task index — updated after every assignment
    _by_name: dict = {}

    def _record(task):
        n = task["עובד"]
        if n not in _by_name:
            _by_name[n] = []
        _by_name[n].append(task)

    # Seed the index with pre-existing tasks (per-terminal builds) so their
    # workers count as busy for availability/continuity/break checks.
    for _pre_task in assignments:
        _record(_pre_task)

    for _, flight in flights_df.iterrows():
        if clean_text(flight.get("המראה", "")) == "":
            continue

        # טיסות מטען — מספר טיסה בן 3 ספרות המתחיל ב-8 (כגון 841, 833), אין שיבוץ כח אדם
        flight_num = clean_text(str(flight.get("טיסה", ""))).replace(" ", "").lstrip("LYly")
        if len(flight_num) >= 3 and flight_num.startswith("8") and flight_num[:3].isdigit():
            continue

        # טיסות מבוטלות עד להודעה חדשה (כגון LY611) — אין שיבוץ כח אדם
        if is_cancelled_flight(flight_num):
            continue

        # טיסות FERRY (ללא נוסעים — עמודת Pax ריקה ב-FIDS) — אין שיבוץ צוות
        if is_ferry_flight(flight):
            continue

        req = get_requirements(flight)
        used_on_flight = set()
        assignments_for_flight = []

        # trainee_needed is gated on the flight's OWN "טרייני רצ" requirement
        # flag, NOT on whether some trainee's shift merely overlaps the flight
        # time. A broader "any trainee is on shift during this flight" check
        # used to sit here — it restricted EVERY ר"צ slot for a trainee's
        # entire shift (often 9-11.5h) to mentor-certified workers only, even
        # though the trainee is only ever placed on the small number of
        # flights actually flagged for training. With multiple trainees on
        # overlapping shifts this silently locked well-qualified, available
        # ר"צ workers who happen not to be mentors out of nearly all of the
        # day's ר"צ flights — found via real data 2026-07-14/15: agent#56
        # (14:00-01:30, ר"צ-certified, not a mentor) got ZERO tasks all day
        # while 4 TL trainees shared her exact shift window, and the mentor
        # pool (e.g. TL#1) got overloaded with ר"צ flights instead
        # (likely also the cause of her getting pulled into an unrelated
        # early plain-דייל gap-fill task). The explicit per-flight flag below
        # already correctly restricts candidates to mentors on the specific
        # flight(s) a trainee is actually assigned to train on.
        trainee_needed = req.get("טרייני רצ", 0) > 0
        training_type = clean_text(flight.get("סוג הכשרה", "חניכה")) or "חניכה"

        # Track restricted-worker usage in attendant/guard slots for this flight
        max_restricted = _max_restricted_attendants(req)
        restricted_assigned = 0       # count of broadly-restricted workers placed so far
        trainee_att_assigned = 0      # count of דייל-בטרייני placed so far
        mentors_assigned = 0          # count of workers who bring a trainee with them
        total_att_slots = req.get("דייל", 0) + req.get("שומר TSA", 0)

        for role in ROLE_ORDER:
            amount = req.get(role, 0)

            for i in range(amount):
                start = role_start_time(flight, role)
                end   = role_end_time(flight)

                role_col = role
                if role not in employees_df.columns:
                    role_col = next(
                        (c for c in employees_df.columns if clean_text(c).upper() == clean_text(role).upper()),
                        None
                    )
                if not role_col:
                    candidates = employees_df.iloc[0:0]
                else:
                    _role_base = _role_df_cache.get(role_col)
                    if _role_base is None:
                        _yn_role = f"__yn_{role_col}"
                        _role_base = employees_df[employees_df[_yn_role]] if _yn_role in employees_df.columns else (
                            employees_df[employees_df[role_col].astype(str).str.strip() == "כן"]
                        )
                    if used_on_flight:
                        candidates = _role_base[~_role_base["_name_key"].isin(used_on_flight)]
                    else:
                        candidates = _role_base

                # ── Role-based eligibility (uses pre-computed flag columns) ──
                if role in _RESTRICTED_FORBIDDEN_ROLES:
                    candidates = candidates[~candidates["__restricted"]].copy()

                # מפקח TSA: holding the "מפקח TSA" certification flag is not
                # enough on its own — an employee whose "מחלקה מקורית"
                # (original department, e.g. "חוליה"/מל"ן — מלווי נוסעים) marks
                # them as belonging to a DIFFERENT department must only be used
                # here if they're actually rostered as TODAY's inspector (same
                # designation check sort_candidates uses for its top priority
                # tier: shift_sheet contains "מפקח", or "מפקח TSA מוגדר"=="כן").
                # Without this, a cross-certified worker from an unrelated
                # department (e.g. TSA-inspector#4, מל"ן) could get pulled in as a
                # last-resort TSA inspector candidate purely because the
                # certification flag happened to be set (user-reported
                # 2026-07-29). An employee with no מחלקה מקורית value at all
                # is unaffected — this only excludes ones explicitly marked as
                # belonging elsewhere.
                if role == "מפקח TSA" and "מחלקה מקורית" in candidates.columns:
                    _tsa_other_dept = candidates["מחלקה מקורית"].astype(str).str.strip().replace("nan", "") != ""
                    if _tsa_other_dept.any():
                        _tsa_designated = pd.Series(False, index=candidates.index)
                        if "shift_sheet" in candidates.columns:
                            _tsa_designated |= candidates["shift_sheet"].astype(str).str.strip().str.lower().str.contains("מפקח", na=False)
                        if "מפקח TSA מוגדר" in candidates.columns:
                            _tsa_designated |= (candidates["מפקח TSA מוגדר"].astype(str).str.strip() == "כן")
                        candidates = candidates[_tsa_designated | ~_tsa_other_dept].copy()

                if role == "שומר TSA":
                    # A שומר IS one of the flight's required workers (user rule
                    # 2026-08-06: "שומר הוא חלק מ-5 העובדים הדרושים, מלבד
                    # מפקח/ר\"צ/מתאם"), so the restricted quota applies to this
                    # slot as well. `restricted_assigned` always counted guard
                    # placements, but the SELECTION here used to ignore the cap
                    # — 2 restricted דיילים plus a restricted guard then read as
                    # 3 against a cap of 2 (3 such flights on real 30.07).
                    # Ordering within the allowed pool is still
                    # tsa_guard_priority's job (restricted workers first).
                    if restricted_assigned >= max_restricted:
                        _no_res = candidates[~candidates["__broadly_res"]]
                        if not _no_res.empty:
                            candidates = _no_res.copy()
                elif role == "דייל":
                    # REVOKED 2026-08-06: a "two דייל-בטרייני together on a
                    # 2-attendant flight" exception used to let the cap be
                    # exceeded here. The user ended it — "אסור לשבץ 2 מתוך 2
                    # עובדים מוחרגים" — after LY2521 came out with trainee-agent#8 (ט)
                    # + restricted-agent#2 (ס) as its only two attendants. The cap
                    # now always holds: at least one regular attendant on every
                    # flight, no exceptions.
                    if restricted_assigned >= max_restricted:
                        # Limit reached — exclude all broadly-restricted workers
                        candidates = candidates[~candidates["__broadly_res"]].copy()

                    # One mentor per flight: a second mentor would drag a
                    # second trainee (= a second restricted worker) onto the
                    # same flight. Soft — if nobody else can staff the slot the
                    # mentor is still used (see the __is_mentor comment above).
                    if mentors_assigned >= 1 and "__is_mentor" in candidates.columns:
                        _no_mentor = candidates[~candidates["__is_mentor"]]
                        if not _no_mentor.empty:
                            candidates = _no_mentor.copy()

                # ── ראש צוות / דייל — instructor (מתדרכת) as last resort ──
                instructor_fallback = pd.DataFrame()
                if role == "דייל" and "__instructor" in candidates.columns:
                    # A מתדרכת's real role is ר"צ overlap/training duty — she
                    # should NEVER be preferred for a plain דייל slot over a
                    # regular attendant, only used if literally no one else is
                    # available (mirrors the existing ר"צ instructor-fallback
                    # pattern below). User rule 2026-07-25, found via real
                    # data: worker#1 scheduled as דייל on LY081 despite being a
                    # מתדרכת who should be reserved for ר"צ duty only.
                    instructor_fallback = candidates[candidates["__instructor"]].copy()
                    candidates = candidates[~candidates["__instructor"]].copy()
                if role == "ראש צוות":
                    # A daily-roster role-window note ("תפקיד מוגבל בזמן" /
                    # data_loader.ROLE_WINDOW_NOTE_RE) narrows a worker's ר"צ
                    # eligibility to a specific clock range for today — a
                    # shift manager coming in EARLY to cover a ר"צ peak
                    # before their own regular duty starts. This must be
                    # applied to the WHOLE candidate pool, not just the
                    # מנהל-כר"צ bypass below: her general shift override now
                    # legitimately spans the whole day (03:30-13:30), so she
                    # also qualifies as a perfectly normal ר"צ candidate via
                    # the regular shift-based filtering UNLESS this is
                    # applied here too — found via real 12.07.2026 data:
                    # TL#46 (role-window 03:30-06:30) was scheduled
                    # ר"צ on LY5115 at 12:45, hours after her window closed.
                    # No note on a worker → no effect (existing behavior).
                    def _role_window_ok(_row):
                        if clean_text(str(_row.get("תפקיד מוגבל בזמן", ""))) != "ראש צוות":
                            return True
                        if start is None or end is None:
                            return True
                        _ws = clean_text(str(_row.get("תחילת חלון תפקיד", "")))
                        _we = clean_text(str(_row.get("סוף חלון תפקיד", "")))
                        if not (is_time_text(_ws) and is_time_text(_we)):
                            return True
                        _ts_m = start.hour * 60 + start.minute
                        _te_m = end.hour * 60 + end.minute
                        _ws_m, _we_m = time_to_minutes(_ws), time_to_minutes(_we)
                        if _we_m <= _ws_m:
                            _we_m += 1440
                        _ts_n = _ts_m if _ts_m >= _ws_m else _ts_m + 1440
                        _te_n = _te_m if _te_m > _ts_m else _te_m + 1440
                        if _te_n <= _ts_n:
                            _te_n += 1440
                        return _ws_m <= _ts_n and _te_n <= _we_m

                    if not candidates.empty and "תפקיד מוגבל בזמן" in candidates.columns:
                        candidates = candidates[candidates.apply(_role_window_ok, axis=1)].copy()

                    _mgr_yn = f"__yn_{CREW_CHIEF_MANAGER_COL}"
                    if CREW_CHIEF_MANAGER_COL in employees_df.columns:
                        _mgr_mask = employees_df[_mgr_yn] if _mgr_yn in employees_df.columns else (
                            employees_df[CREW_CHIEF_MANAGER_COL].astype(str).str.strip() == "כן"
                        )
                        mgr_extra = employees_df[
                            _mgr_mask &
                            (~employees_df["_name_key"].isin(used_on_flight)) &
                            (~employees_df["_name_key"].isin(
                                candidates["_name_key"].tolist() if not candidates.empty else []
                            ))
                        ].copy()
                        if not mgr_extra.empty and "תפקיד מוגבל בזמן" in mgr_extra.columns:
                            mgr_extra = mgr_extra[mgr_extra.apply(_role_window_ok, axis=1)]
                        if not mgr_extra.empty:
                            candidates = pd.concat([candidates, mgr_extra], ignore_index=True).drop_duplicates("_name_key")

                    # Separate מתדרכת candidates — used only if nothing else works
                    if "__instructor" in candidates.columns:
                        instructor_fallback = candidates[candidates["__instructor"]].copy()
                        candidates = candidates[~candidates["__instructor"]].copy()

                    if trainee_needed:
                        mentor_col = "מסמיך רצים" if training_type == "הסמכה" else "חונך רצים"
                        if mentor_col not in candidates.columns:
                            candidates[mentor_col] = "לא"
                        _mentor_yn = f"__yn_{mentor_col}"
                        mentors = candidates[candidates[_mentor_yn]] if _mentor_yn in candidates.columns else candidates[candidates[mentor_col].astype(str).str.strip() == "כן"]
                        if not mentors.empty:
                            candidates = mentors

                # For USA flights: preserve TSA inspectors for the מפקח TSA slot.
                # Exclude TSA-inspector-qualified employees from דייל candidates
                # only when non-TSA candidates are available (never leave no candidates at all).
                if role == "דייל" and clean_text(str(flight.get("יעד", ""))).strip() in USA_TSA_DESTS:
                    if hasattr(candidates, "columns") and "_name_key" in candidates.columns:
                        _non_tsa_cands = candidates[~candidates["_name_key"].isin(_tsa_inspector_names)]
                        if not _non_tsa_cands.empty:
                            candidates = _non_tsa_cands

                # Same preservation for ראש צוות — a worker certified for BOTH
                # ר"צ and TSA inspection was being grabbed for the (much more
                # common) ר"צ role on an earlier USA flight, leaving no TSA
                # inspector free for a LATER USA flight's מפקח TSA slot even
                # though the sort-order tiebreaker (dual_qual) already exists —
                # that tiebreaker only wins when candidates are otherwise
                # equal, not when the dual-qualified worker is simply the best/
                # only ר"צ option at that moment (user rule 2026-07-13: found
                # via real data — TL#1, TL+TSA-certified, assigned
                # ר"צ on LY027 23:15-00:30, left LY001/JFK 01:00 with no TSA
                # inspector available at all).
                elif role == "ראש צוות" and clean_text(str(flight.get("יעד", ""))).strip() in USA_TSA_DESTS:
                    if hasattr(candidates, "columns") and "_name_key" in candidates.columns:
                        _non_tsa_tl_cands = candidates[~candidates["_name_key"].isin(_tsa_inspector_names)]
                        if not _non_tsa_tl_cands.empty:
                            candidates = _non_tsa_tl_cands

                if hasattr(candidates, "columns") and "_name_key" in candidates.columns:
                    # TLs as שומר TSA: never allowed — hard block, no fallback.
                    if role == "שומר TSA":
                        _no_tl = candidates[~candidates["_name_key"].isin(_tl_names)]
                        if not _no_tl.empty:
                            candidates = _no_tl
                        # If ALL שומר TSA candidates are also TLs, leave candidates as-is
                        # (keep them rather than leaving the slot empty — last resort).

                    # TLs as דייל on USA flights: prefer non-TL; fall back only if
                    # no non-TL דייל candidate is available.
                    elif role == "דייל" and clean_text(str(flight.get("יעד", ""))).strip() in USA_TSA_DESTS:
                        _no_tl_usa = candidates[~candidates["_name_key"].isin(_tl_names)]
                        if not _no_tl_usa.empty:
                            candidates = _no_tl_usa

                t_sort = time.time()
                gate = clean_text(flight.get("גייט", ""))
                # When gate is missing, fall back to terminal number from FIDS (e.g. "3")
                if not gate:
                    _terminal_val = clean_text(str(flight.get("שלוחה", "")))
                    if _terminal_val:
                        gate = _terminal_val
                candidates = sort_candidates(candidates, assignments, role, start, end,
                                             task_terminal=get_terminal(gate))
                selected = (
                    _try_select(candidates, assignments, role, start, end, gate, _by_name=_by_name, _emp_lookup=_emp_data) or
                    _try_select(candidates, assignments, role, start, end, gate,
                                check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data) or
                    _try_select(candidates, assignments, role, start, end, gate,
                                check_break=False, check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data) or
                    # Absolute last resort: also bypass late-break guard
                    _try_select(candidates, assignments, role, start, end, gate,
                                check_break=False, check_continuous=False, check_late_break=False,
                                _by_name=_by_name, _emp_lookup=_emp_data)
                )

                # ── מתדרכת last-resort fallback for ראש צוות / דייל ───────
                instructor_used = False
                if not selected and role in ("ראש צוות", "דייל") and not instructor_fallback.empty:
                    instructor_fallback = sort_candidates(instructor_fallback, assignments, role, start, end,
                                                          task_terminal=get_terminal(gate))
                    selected = (
                        _try_select(instructor_fallback, assignments, role, start, end, gate, _by_name=_by_name, _emp_lookup=_emp_data) or
                        _try_select(instructor_fallback, assignments, role, start, end, gate,
                                    check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data) or
                        _try_select(instructor_fallback, assignments, role, start, end, gate,
                                    check_break=False, check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data) or
                        _try_select(instructor_fallback, assignments, role, start, end, gate,
                                    check_break=False, check_continuous=False, check_late_break=False,
                                    _by_name=_by_name, _emp_lookup=_emp_data)
                    )
                    if selected:
                        instructor_used = True

                if selected:
                    worker = selected
                    if instructor_used:
                        reason = (
                            "⚠️ מתדרכת שובצה כראש צוות — נדרש אישור מפורש" if role == "ראש צוות"
                            else "⚠️ מתדרכת שובצה כדייל (מוצא אחרון) — נדרש אישור מפורש"
                        )
                    else:
                        reason = "שובץ כי העובד מוסמך לתפקיד, פנוי בזמן המשימה וללא חפיפה"
                    used_on_flight.add(name_key(selected))

                    # Update restricted counters (use pre-built lookup)
                    if role in {"דייל", "שומר TSA"}:
                        sel_data = _emp_data.get(selected, {})
                        if sel_data.get("__broadly_res", False):
                            restricted_assigned += 1
                        if sel_data.get("__trainee_att", False):
                            trainee_att_assigned += 1
                        if sel_data.get("__is_mentor", False):
                            mentors_assigned += 1
                else:
                    worker = f"❌ חסר {role}"
                    reason = "לא נמצא עובד מתאים: אין זמינות, יש חפיפה או שחסרה הסמכה"

                # מפקח TSA באותה שלוחה — שעת התחלה = 2ש לפני הטיסה המוקדמת ביותר בשלוחה.
                # חל רק כשהמשימה הקודמת של אותו מפקח בשלוחה חופפת/רציפה עם החדשה
                # (כיסוי מקביל אמיתי). כשיש פער אמיתי בין המשימות (למשל LY117 עד
                # 07:20 ואז LY7 מ-08:15) אין למתוח — המפקחת מקבלת משימה נפרדת
                # שמתחילה שעתיים לפני ההמראה, עם הפסקה בפער (הנחיית משתמש 2026-07-05).
                task_start_str = start.strftime("%H:%M")
                if role in {"מפקח TSA", "שומר TSA"} and selected and gate:
                    new_terminal = get_terminal(gate)
                    if new_terminal:
                        for prev in assignments:
                            if prev.get("עובד") != selected:
                                continue
                            if prev.get("תפקיד בסיס") not in {"מפקח TSA", "שומר TSA"}:
                                continue
                            if get_terminal(prev.get("_gate", "")) != new_terminal:
                                continue
                            prev_start_str = prev.get("התחלה", "")
                            prev_end_str   = prev.get("סיום", "")
                            if prev_start_str and prev_end_str:
                                prev_start_dt = to_datetime_time(prev_start_str)
                                prev_end_dt   = to_datetime_time(prev_end_str)
                                prev_m  = prev_start_dt.hour * 60 + prev_start_dt.minute
                                prev_em = prev_end_dt.hour * 60 + prev_end_dt.minute
                                cur_m   = start.hour * 60 + start.minute
                                # השווה תוך מניעת שגיאות חצות
                                if prev_m  < 12 * 60: prev_m  += 1440
                                if prev_em < prev_m:  prev_em += 1440
                                if cur_m   < 12 * 60: cur_m   += 1440
                                if prev_m < cur_m and prev_em > cur_m:
                                    task_start_str = prev_start_str
                                break

                role_label = role if amount == 1 else f"{role} {i+1}"
                if instructor_used:
                    role_label = f"{role_label} ⚠️"

                task = {
                    "טיסה":       flight["טיסה"],
                    "יעד":        flight["יעד"],
                    "תפקיד":      role_label,
                    "תפקיד בסיס": role,
                    "עובד":       worker,
                    "התחלה":      task_start_str,
                    "סיום":       end.strftime("%H:%M"),
                    "_gate":      gate,
                    "סיבה":       reason,
                }
                assignments.append(task)
                assignments_for_flight.append(task)
                _record(task)

        # טרייני ר״צ
        if (
            has_trainee_available(employees_df)
            and flight_has_mentor_teamlead(assignments_for_flight, employees_df, training_type)
        ):
            role  = "טרייני רצ"
            start = role_start_time(flight, role)
            end   = role_end_time(flight)

            role_col_t = role if role in employees_df.columns else next(
                (c for c in employees_df.columns if clean_text(c).upper() == clean_text(role).upper()), None
            )
            if not role_col_t:
                candidates = employees_df.iloc[0:0]
            else:
                _t_base = _role_df_cache.get(role_col_t)
                if _t_base is None:
                    _yn_t = f"__yn_{role_col_t}"
                    _t_base = employees_df[employees_df[_yn_t]] if _yn_t in employees_df.columns else (
                        employees_df[employees_df[role_col_t].astype(str).str.strip() == "כן"]
                    )
                candidates = _t_base[~_t_base["_name_key"].isin(used_on_flight)] if used_on_flight else _t_base

            gate = clean_text(flight.get("גייט", ""))
            if not gate:
                _terminal_val_t = clean_text(str(flight.get("שלוחה", "")))
                if _terminal_val_t:
                    gate = _terminal_val_t
            candidates = sort_candidates(candidates, assignments, role, start, end,
                                         task_terminal=get_terminal(gate))
            selected = (
                _try_select(candidates, assignments, role, start, end, gate, _by_name=_by_name, _emp_lookup=_emp_data) or
                _try_select(candidates, assignments, role, start, end, gate,
                            check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data) or
                _try_select(candidates, assignments, role, start, end, gate,
                            check_break=False, check_continuous=False, _by_name=_by_name, _emp_lookup=_emp_data)
            )

            if selected:
                task = {
                    "טיסה":       flight["טיסה"],
                    "יעד":        flight["יעד"],
                    "תפקיד":      "טרייני ר״צ",
                    "תפקיד בסיס": "טרייני רצ",
                    "עובד":       selected,
                    "התחלה":      start.strftime("%H:%M"),
                    "סיום":       end.strftime("%H:%M"),
                    "_gate":      gate,
                }
                assignments.append(task)
                assignments_for_flight.append(task)
                _record(task)
                used_on_flight.add(name_key(selected))
    _t_phases["loop"] = round(time.time() - _t0_total - _t_phases["setup"] - _t_phases["emp_dict"], 3)

    # ── Gap-fill pass ─────────────────────────────────────────────────────────
    # Fill gaps > 30 min between an employee's consecutive flight tasks.
    # Runs once: iterates gaps in time order; for each gap walks a pre-sorted,
    # pre-keyed flight list — no redundant re-sorts or re-scans.
    _GAP_MAX = 30

    from collections import Counter as _Counter, defaultdict as _defaultdict
    _slot_filled: _Counter = _Counter()
    # Per-flight-role index for O(1) replacement search (avoids O(n) scan of all assignments)
    _flight_role_idx: dict = _defaultdict(list)  # (fk, role) -> [task_dicts...]
    for _gt in assignments:
        # ❌ tasks DO enter the index (so the gap-fill can fill them in place)
        # but must NOT count as taken slots — otherwise the gap-fill "adds"
        # workers next to the ❌ rows instead of filling them, and the flight
        # shows both a shortage and an assignment at once.
        _gfk = clean_text(str(_gt.get("טיסה", ""))).replace(" ", "").upper()
        _gr  = clean_text(str(_gt.get("תפקיד בסיס", "")))
        if _gfk and _gr:
            if "❌" not in str(_gt.get("עובד", "")):
                _slot_filled[(_gfk, _gr)] += 1
            _flight_role_idx[(_gfk, _gr)].append(_gt)

    # Pre-compute (fk, req, gate, start_by_role, end) for every non-cargo flight
    # keyed by role so inner loop is trivial arithmetic.
    _GF_ROLES = ("ראש צוות", "דייל", "שומר TSA", "מתאם תורים", "מפקח TSA")
    _gf_cache: dict = {}   # role -> list of (fs_m, fe_m, fk, req_n, gate, fl_start, fl_end)
    for _role_key in _GF_ROLES:
        _gf_cache[_role_key] = []
    for _, _fl_row in flights_df.iterrows():
        _dep_s = clean_text(str(_fl_row.get("המראה", "")))
        if not _dep_s:
            continue
        _fn = clean_text(str(_fl_row.get("טיסה", ""))).replace(" ", "").lstrip("LYlyEeAa")
        if len(_fn) >= 3 and _fn.startswith("8") and _fn[:3].isdigit():
            continue
        if is_cancelled_flight(_fn):
            continue
        if is_ferry_flight(_fl_row):
            continue
        _fk2 = clean_text(str(_fl_row.get("טיסה", ""))).replace(" ", "").upper()
        _fl_gate2 = clean_text(str(_fl_row.get("גייט", ""))) or clean_text(str(_fl_row.get("שלוחה", "")))
        _fl_req2 = get_requirements(_fl_row)
        for _rk in _GF_ROLES:
            if _fl_req2.get(_rk, 0) == 0:
                continue
            try:
                _fls2 = role_start_time(_fl_row, _rk)
                _fle2 = role_end_time(_fl_row)
            except Exception:
                continue
            _fs2 = _fls2.hour * 60 + _fls2.minute
            _fe2 = _fle2.hour * 60 + _fle2.minute
            if _fe2 < _fs2:
                _fe2 += 1440
            _body2 = get_body_type(_fl_row)
            _gf_cache[_rk].append((_fs2, _fe2, _fk2, _fl_req2[_rk], _fl_gate2, _fls2, _fle2, _fl_row, _body2))
    # Sort each role's list by flight start time — allows early-break in inner loop
    for _rk in _GF_ROLES:
        _gf_cache[_rk].sort(key=lambda x: x[0])

    for _gf_name, _gf_all_tasks in list(_by_name.items()):
        _gf_row = _emp_data.get(_gf_name)
        if not _gf_row:
            continue
        if _is_crew_manager(_gf_row):
            continue        # a manager is a last-resort ר"צ — never given extra work

        # Primary role from existing assignments; fallback roles tried in gap-fill
        _gf_primary = None
        for _r_try in _GF_ROLES:
            if any(clean_text(str(t.get("תפקיד בסיס", ""))) == _r_try
                   and "❌" not in str(t.get("עובד", ""))
                   for t in _gf_all_tasks):
                _gf_primary = _r_try
                break
        if not _gf_primary:
            continue

        # Roles to try for gap-fill, in priority order:
        # ראש צוות can also fill מתאם תורים or דייל slots when primary is full,
        # and מפקח TSA too when the worker actually holds that certification
        # (user rule 2026-07-09: maximize a TL's tasks across ALL role types —
        # דייל, ראש צוות, מתאם תורים, and מפקח TSA if certified — before ever
        # taking a task away from another team lead).
        if _gf_primary == "ראש צוות":
            # A מתדרכת (TL instructor) is reserved for ר"צ duty only — once her
            # ר"צ gaps have no more ר"צ work to absorb, she returns to the
            # counters like any attendant would, rather than being loaded into
            # a plain דייל/מתאם-תורים slot via this floor-time-maximization
            # gap-fill (that mechanism exists to keep a REGULAR TL busy, not
            # to repurpose an instructor). User rule 2026-07-25, found via
            # real data: worker#1 gap-filled onto LY081 as a plain דייל here,
            # bypassing the main loop's instructor-last-resort fix entirely.
            if clean_text(str(_gf_row.get("מתדרכת", ""))) == "כן":
                _gf_roles_try = ["ראש צוות"]
            else:
                # Only offer מתאם-תורים/דייל gap-fill when the worker is
                # actually certified for them — a regular TL almost always
                # is (they came up through the floor), but a "מנהל כר\"צ"
                # shift manager who is ר"צ-certified ONLY is not, and was
                # being gap-filled into plain דייל/מתאם slots she has no
                # certification for at all (found via real 12.07.2026 data:
                # TL#46, ר"צ-only, gap-filled as a plain דייל on
                # LY5155 once she became visible to the scheduler for the
                # first time via her role-window note).
                _gf_roles_try = ["ראש צוות"]
                if clean_text(str(_gf_row.get("מתאם תורים", ""))) == "כן":
                    _gf_roles_try.append("מתאם תורים")
                if clean_text(str(_gf_row.get("דייל", ""))) == "כן":
                    _gf_roles_try.append("דייל")
            if clean_text(str(_gf_row.get("מפקח TSA", ""))) == "כן":
                _gf_roles_try.append("מפקח TSA")
        elif _gf_primary == "דייל":
            _gf_roles_try = ["דייל", "מתאם תורים"]
        elif _gf_primary == "שומר TSA":
            # שומר TSA workers are commonly also דייל-certified (found via real
            # data 2026-07-14: restricted-agent#5, restricted-agent#9 — שומר TSA/דייל shift
            # 15:00-00:30 with a single early task and huge idle gaps either
            # side). Previously fell into the `else` single-role branch below,
            # so gap-fill never even tried a plain דייל slot for them even
            # when idle for hours — same floor-time-maximization gap the TL
            # role already gets (user rule 2026-07-09), just missing here.
            _gf_roles_try = ["שומר TSA", "דייל"]
        else:
            _gf_roles_try = [_gf_primary]

        # Set of flight keys this employee is already on (for quick lookup)
        _emp_fks = {
            clean_text(str(t.get("טיסה", ""))).replace(" ", "").upper()
            for t in _by_name.get(_gf_name, [])
        }

        # Shift start/end times — used for tail-gap detection and midnight-safe sorting
        _shift_start_str = clean_text(str(_gf_row.get("תחילת משמרת", "")))
        _shift_end_str   = clean_text(str(_gf_row.get("סוף משמרת", "")))
        _shift_start_m   = time_to_minutes(_shift_start_str) if is_time_text(_shift_start_str) else 0
        _shift_end_m     = time_to_minutes(_shift_end_str) if is_time_text(_shift_end_str) else None

        # Repeat until no fillable gap remains (max 6 passes per employee)
        for _gf_pass in range(6):
            # Sort tasks relative to shift start so night-crossing shifts (e.g. 22:00-06:00)
            # place 22:30 before 01:00 instead of creating a false 20-hour "gap" mid-day.
            _gf_timed = sorted(
                [t for t in _by_name.get(_gf_name, [])
                 if clean_text(t.get("התחלה", "")) and clean_text(t.get("סיום", ""))
                 and "❌" not in str(t.get("עובד", ""))],
                key=lambda t: (time_to_minutes(clean_text(t.get("התחלה", "00:00"))) - _shift_start_m) % 1440
            )
            _did_fill = False
            _emp_tasks_now = _by_name.get(_gf_name, [])

            # Team leads tolerate a wider gap (45 min) — avoids pulling extra staff
            # to the departure hall and lets regular attendants return to counters sooner.
            _gap_threshold = 45 if _gf_primary == "ראש צוות" else _GAP_MAX

            # Build list of gaps: head gap from shift start + consecutive inter-task
            # gaps + tail gap to shift end.
            # Each entry: (gap_start_m, gap_end_m, is_tail_gap)
            _gaps = []
            # Head gap: from shift start to the worker's FIRST task — this class of
            # gap was previously invisible to gap-fill entirely (only inter-task and
            # tail gaps were built), so a worker whose sole/first task happens to
            # fall well into the shift got no gap-fill consideration for all the
            # idle time BEFORE it. Found via real data 2026-07-14: TL#12
            # (ר"צ, שומר TSA-certified, shift 23:30-08:30) had exactly ONE task
            # (LY385, 04:55-06:05) — the ~5.5h head gap (23:30-04:55) was never
            # considered for gap-fill at all, though the ~2.5h tail gap after it
            # (06:05-08:30) correctly was.
            if _gf_timed and _shift_start_m is not None:
                _head_e_raw = time_to_minutes(clean_text(_gf_timed[0].get("התחלה", "")))
                _head_e_rel = (_head_e_raw - _shift_start_m) % 1440
                if _head_e_rel > _gap_threshold:
                    _head_e = _head_e_raw if _head_e_raw >= _shift_start_m else _head_e_raw + 1440
                    _gaps.append((_shift_start_m, _head_e, False))
            for _gi in range(len(_gf_timed) - 1):
                _gs = time_to_minutes(clean_text(_gf_timed[_gi].get("סיום", "")))
                _ge = time_to_minutes(clean_text(_gf_timed[_gi + 1].get("התחלה", "")))
                if _ge < _gs:
                    # Negative gap = overlapping tasks (double-booking artifact).
                    # Do NOT wrap around with +1440 — that creates a false ~24-hour gap.
                    # Only add 1440 for genuine midnight-crossing shifts where the gap
                    # is expected to span midnight (shift starts after 20:00).
                    if _shift_start_m >= 20 * 60:
                        _ge += 1440
                    else:
                        continue  # overlapping tasks — skip this "gap"
                if _ge - _gs > _gap_threshold:
                    _gaps.append((_gs, _ge, False))
            # Tail gap: after last task until shift end (shift-relative to avoid midnight flip)
            if _gf_timed and _shift_end_m is not None:
                _tail_s_raw = time_to_minutes(clean_text(_gf_timed[-1].get("סיום", "")))
                _tail_e_raw = _shift_end_m
                # Compute both times relative to shift start to handle midnight crossings
                _tail_s_rel = (_tail_s_raw - _shift_start_m) % 1440
                _tail_e_rel = (_tail_e_raw - _shift_start_m) % 1440
                if _tail_e_rel <= _tail_s_rel:
                    _tail_e_rel += 1440
                if _tail_e_rel - _tail_s_rel > _gap_threshold:
                    # Convert back to absolute minutes for the gap entry
                    _tail_s = _tail_s_raw
                    _tail_e = _tail_e_raw if _tail_e_raw >= _tail_s_raw else _tail_e_raw + 1440
                    _gaps.append((_tail_s, _tail_e, True))  # is_tail=True

            for (_gs, _ge, _is_tail) in _gaps:
                # Try each role in priority order for this gap
                for _fill_role in _gf_roles_try:
                    _fl_list_r = _gf_cache.get(_fill_role, [])
                    for (_fs, _fe, _fk3, _req_n, _gate3, _fls3, _fle3, _fl_row3, _body3) in _fl_list_r:
                        if _fe <= _gs + 5:
                            continue
                        if _fs >= _ge - 10:
                            break
                        if _fs < _gs + 10 or _fe > _ge - 10:
                            continue
                        if _fk3 in _emp_fks:
                            continue
                        # Non-TL workers: skip flights that start too far into the gap.
                        # A flight starting > 90 min after the gap start means the worker
                        # would be idle too long before it — better to return to counters
                        # and have a fresher or more continuous worker cover the flight.
                        # TL-primary workers bypass this (preemption handles their gaps).
                        if _gf_primary != "ראש צוות" and (_fs - _gs) % 1440 > 90:
                            continue

                        # Slot availability:
                        # • ראש צוות: max_extra=1 so replacement logic can run below.
                        # • TL-primary idle in דייל/מתאם תורים gap: preempt a non-TL
                        #   so the TL fills the slot and the non-TL returns to counters.
                        # • All other cases: no extras beyond req_n.
                        _slots_taken = _slot_filled[(_fk3, _fill_role)]
                        # Only allow 1 extra worker for ראש צוות slots (replacement logic below).
                        # דייל / מתאם תורים slots must never exceed their defined requirements —
                        # the previous TL-primary exception was causing flights to be over-staffed.
                        _max_extra = 1 if _fill_role == "ראש צוות" else 0
                        if _slots_taken >= _req_n + _max_extra:
                            # TL-primary preemption: ראש צוות worker can replace a non-TL
                            # דייל/מתאם תורים when their gap coincides with a full-staffed slot.
                            _is_tl_preempt_eligible = (
                                _gf_primary == "ראש צוות"
                                and _fill_role in ("דייל", "מתאם תורים")
                                and _slots_taken == _req_n
                            )
                            if _is_tl_preempt_eligible:
                                pass  # fall through to preemption block below
                            else:
                                # User rule 2026-07-05: attendant slots must NEVER
                                # exceed the flight's requirement — the old
                                # narrow-body "3rd attendant for restricted
                                # categories" exception over-staffed 2-attendant
                                # flights (e.g. 3 employee-children on one flight)
                                # and was removed.
                                continue

                        # TL restrictions in gap-fill:
                        # • Never as שומר TSA (hard block).
                        # • Not as דייל on USA flights unless _gf_primary is already ראש צוות
                        #   (means the TL is filling a gap in a non-TL role — still avoid).
                        _gf_is_tl = clean_text(str(_gf_row.get("ראש צוות", ""))) == "כן"
                        if _gf_is_tl:
                            _fl_dest3 = clean_text(str(_fl_row3.get("יעד", ""))).strip()
                            if _fill_role == "שומר TSA":
                                continue
                            if _fill_role == "דייל" and _fl_dest3 in USA_TSA_DESTS:
                                continue

                        # Guard: boarding and departure must fit inside shift window
                        _fls3_m = _fls3.hour * 60 + _fls3.minute
                        _fle3_m = _fle3.hour * 60 + _fle3.minute
                        _fle3_m_adj = _fle3_m if _fle3_m >= _fls3_m else _fle3_m + 1440
                        # Normalise relative to shift start to handle midnight crossings
                        _fls3_rel = (_fls3_m - _shift_start_m) % 1440
                        _fle3_rel = (_fle3_m_adj - _shift_start_m) % 1440
                        _shift_len = (_shift_end_m - _shift_start_m) % 1440 if _shift_end_m is not None else 1440
                        if _fls3_rel > _shift_len or _fle3_rel > _shift_len:
                            continue

                        # Terminal gating: the worker's availability window at this
                        # time must belong to the flight's terminal (a T1-window
                        # worker must not gap-fill a T3 flight and vice versa).
                        if not is_within_shift(_gf_row, _fls3, _fle3,
                                               task_terminal=get_terminal(_gate3)):
                            continue

                        # Does evicting _rt (a non-TL worker's task, being replaced
                        # by a preempting TL below) leave a large, purposeless idle
                        # gap in the REST of _rt's day? Checks the gap between the
                        # task before and the task after _rt once it's removed —
                        # if that gap is bigger than _rt's own required break by
                        # 60+ min, the eviction just creates dead time with no
                        # break happening in it (user 2026-07-19: "לדיילת הזו יש
                        # רווח גדול מדיי... אין שיבוץ הגיוני יותר... יש להחזירה
                        # לדלפקים ולשבץ עובד אחר" — found via real data: agent#41, 02:00-11:00, LY237/LY347 preempted by TL workers,
                        # leaving only LY279+LY323 with a bare 75-min gap between
                        # them that used to be filled by LY347).
                        def _eviction_creates_bad_gap(_rt_cand, _rt_emp_cand, _rt_erow_cand):
                            _rt_s = to_datetime_time(_rt_cand.get("התחלה", ""))
                            _rt_e = to_datetime_time(_rt_cand.get("סיום", ""))
                            if _rt_s is None or _rt_e is None:
                                return False
                            _rt_s_m = _rt_s.hour * 60 + _rt_s.minute
                            _rt_e_m = _rt_e.hour * 60 + _rt_e.minute
                            _prev_end_m = None
                            _next_start_m = None
                            for _ot in _by_name.get(_rt_emp_cand, []):
                                if _ot is _rt_cand:
                                    continue
                                _o_s = to_datetime_time(_ot.get("התחלה", ""))
                                _o_e = to_datetime_time(_ot.get("סיום", ""))
                                if _o_s is None or _o_e is None:
                                    continue
                                _o_s_m = _o_s.hour * 60 + _o_s.minute
                                _o_e_m = _o_e.hour * 60 + _o_e.minute
                                if _o_e_m <= _rt_s_m and (_prev_end_m is None or _o_e_m > _prev_end_m):
                                    _prev_end_m = _o_e_m
                                if _o_s_m >= _rt_e_m and (_next_start_m is None or _o_s_m < _next_start_m):
                                    _next_start_m = _o_s_m
                            if _prev_end_m is None or _next_start_m is None:
                                return False
                            _gap = (_next_start_m - _prev_end_m) % 1440
                            _rb = required_break(_rt_erow_cand) or 45
                            return (_gap - _rb) >= 60

                        # Don't fragment a gap that could host a real break into two
                        # pieces neither of which can: this applies even to a
                        # genuinely OPEN slot (no eviction), which the residual-gap
                        # guard below doesn't cover since it only fires once
                        # _slots_taken >= _req_n. User rule 2026-09-14, found via real
                        # 04.09.2026 data: TL#27 (ר"צ, shift 03:30-11:00) had a
                        # 07:05-08:45 (100 min) gap after LY541 — plenty for her own
                        # 45-min required break — but gap-fill placed her on LY2369's
                        # (07:35-08:35) plain, never-requested-for-her דייל slot,
                        # leaving only a 30-min lead and 10-min trail residual, neither
                        # long enough for a real break; her break was then pushed all
                        # the way to the end of her shift instead of landing mid-shift.
                        _rb_for_gf = required_break(_gf_row) or 45
                        if (_ge - _gs) >= _rb_for_gf and (_fs - _gs) < _rb_for_gf and (_ge - _fe) < _rb_for_gf:
                            continue

                        # When filling a role that is already at req_n and the employee
                        # is a ראש צוות, try to swap a non-TL so they return to counters.
                        # If no swap is possible, block adding a pure extra.
                        _replaced_task = None
                        if _slots_taken >= _req_n:
                            # Evicting an already-scheduled non-TL worker (preemption)
                            # is only worth the churn when it actually closes the TL's
                            # gap. If a chunk of _gap_threshold+ minutes remains on
                            # either side of this candidate flight even after filling
                            # it, the eviction buys nothing real — the TL is still left
                            # idle for a comparable stretch, but another worker's
                            # schedule was disrupted for no net benefit. User rule
                            # 2026-09-14, found via real 04.09.2026 data: TL TL#21's
                            # 08:00-11:30 head gap was "closed" by evicting agent#39 off
                            # LY323's מתאם תורים slot (08:45-10:00), which still left her
                            # idle 10:00-11:30 — a 1.5h residual gap, just as bad as before,
                            # bought only by bumping a properly-placed worker.
                            _lead_residual = _fs - _gs
                            _trail_residual = _ge - _fe
                            if _lead_residual > _gap_threshold or _trail_residual > _gap_threshold:
                                continue
                        if _slots_taken >= _req_n and _fill_role == "ראש צוות":
                            if _gf_primary == "ראש צוות":
                                # O(crew_size) lookup via pre-built index. Prefer a
                                # candidate whose eviction doesn't create a bad gap;
                                # fall back to the first non-TL match if every
                                # option would (never block the preemption outright
                                # — that would undo the TL floor-time maximization
                                # this mechanism exists for).
                                _fallback_task = None
                                for _rt in _flight_role_idx.get((_fk3, _fill_role), []):
                                    _rt_emp = str(_rt.get("עובד", ""))
                                    if "❌" in _rt_emp or _rt_emp == _gf_name:
                                        continue
                                    _rt_erow = _emp_data.get(_rt_emp, {})
                                    if clean_text(str(_rt_erow.get("ראש צוות", ""))) != "כן":
                                        if _fallback_task is None:
                                            _fallback_task = _rt
                                        if not _eviction_creates_bad_gap(_rt, _rt_emp, _rt_erow):
                                            _replaced_task = _rt
                                            break
                                if _replaced_task is None:
                                    _replaced_task = _fallback_task
                                if _replaced_task is not None:
                                    assignments.remove(_replaced_task)
                                    _rep_emp = _replaced_task["עובד"]
                                    _by_name[_rep_emp] = [
                                        t for t in _by_name.get(_rep_emp, [])
                                        if t is not _replaced_task
                                    ]
                                    _flight_role_idx[(_fk3, _fill_role)].remove(_replaced_task)
                                    _slot_filled[(_fk3, _fill_role)] -= 1
                                else:
                                    # All current ראש צוות on this flight are genuine TLs —
                                    # do not add a pure extra; try next role.
                                    continue
                            else:
                                # Non-TL employee cannot push into a full ראש צוות slot.
                                continue
                        elif (
                            _slots_taken >= _req_n
                            and _fill_role in ("דייל", "מתאם תורים")
                            and _gf_primary == "ראש צוות"
                        ):
                            # TL-primary worker can replace a non-TL דייל/מתאם תורים
                            # to maximize their time in the departure hall. Prefer
                            # a candidate whose eviction doesn't create a bad gap
                            # (see _eviction_creates_bad_gap above); fall back to
                            # the first match if every option would.
                            _fallback_task = None
                            for _rt in _flight_role_idx.get((_fk3, _fill_role), []):
                                _rt_emp = str(_rt.get("עובד", ""))
                                if "❌" in _rt_emp or _rt_emp == _gf_name:
                                    continue
                                _rt_erow = _emp_data.get(_rt_emp, {})
                                if clean_text(str(_rt_erow.get("ראש צוות", ""))) != "כן":
                                    if _fallback_task is None:
                                        _fallback_task = _rt
                                    if not _eviction_creates_bad_gap(_rt, _rt_emp, _rt_erow):
                                        _replaced_task = _rt
                                        break
                            if _replaced_task is None:
                                _replaced_task = _fallback_task
                            if _replaced_task is not None:
                                assignments.remove(_replaced_task)
                                _rep_emp = _replaced_task["עובד"]
                                _by_name[_rep_emp] = [
                                    t for t in _by_name.get(_rep_emp, [])
                                    if t is not _replaced_task
                                ]
                                _flight_role_idx[(_fk3, _fill_role)].remove(_replaced_task)
                                _slot_filled[(_fk3, _fill_role)] -= 1
                            else:
                                # All workers on this slot are TLs — cannot preempt.
                                continue

                        if not has_room_for_break(assignments, _gf_row, _gf_name,
                                                  _fls3, _fle3, emp_tasks=_emp_tasks_now):
                            if _replaced_task is not None:
                                assignments.append(_replaced_task)
                                _by_name.setdefault(_replaced_task["עובד"], []).append(_replaced_task)
                                _flight_role_idx[(_fk3, _fill_role)].append(_replaced_task)
                                _slot_filled[(_fk3, _fill_role)] += 1
                            continue
                        if not is_available(assignments, _gf_name, _fls3, _fle3,
                                            emp_row=_gf_row, role=_fill_role,
                                            flight_gate=_gate3, emp_tasks=_emp_tasks_now):
                            if _replaced_task is not None:
                                assignments.append(_replaced_task)
                                _by_name.setdefault(_replaced_task["עובד"], []).append(_replaced_task)
                                _flight_role_idx[(_fk3, _fill_role)].append(_replaced_task)
                                _slot_filled[(_fk3, _fill_role)] += 1
                            continue

                        # Fill an existing ❌ slot of this role in place when one
                        # exists — never leave a shortage row NEXT TO the worker
                        # who covers it.
                        _missing_slot = next(
                            (t for t in _flight_role_idx[(_fk3, _fill_role)]
                             if "❌" in str(t.get("עובד", ""))),
                            None,
                        )
                        if _missing_slot is not None:
                            _missing_slot["עובד"]  = _gf_name
                            _missing_slot["התחלה"] = _fls3.strftime("%H:%M")
                            _missing_slot["סיום"]  = _fle3.strftime("%H:%M")
                            _missing_slot["סיבה"]  = "gap-fill"
                            _gf_task = _missing_slot
                        else:
                            _gf_task = {
                                "טיסה": clean_text(str(_fl_row3.get("טיסה", ""))),
                                "יעד":  clean_text(str(_fl_row3.get("יעד", ""))),
                                "תפקיד":      _fill_role,
                                "תפקיד בסיס": _fill_role,
                                "עובד":  _gf_name,
                                "התחלה": _fls3.strftime("%H:%M"),
                                "סיום":  _fle3.strftime("%H:%M"),
                                "_gate": _gate3,
                                "סיבה":  "gap-fill",
                            }
                            assignments.append(_gf_task)
                            _flight_role_idx[(_fk3, _fill_role)].append(_gf_task)
                        _record(_gf_task)
                        _emp_fks.add(_fk3)
                        _slot_filled[(_fk3, _fill_role)] += 1
                        _did_fill = True
                        break
                    if _did_fill:
                        break

                if _did_fill:
                    break

            if not _did_fill:
                break

    try:
        import streamlit as _st
        _st.session_state["_debug_phases"] = _t_phases
    except Exception:
        pass
    return pd.DataFrame(assignments)


def upgrade_teamleads(assignments_df, employees_df):
    """
    השלמת ראשי צוות חסרים:
    1. קודם מחפשת ראש צוות פנוי מכל העובדים.
    2. אם אין, מקדמת דייל שכבר שובץ וחוסכת כפילויות.
    3. לא משתמשת בלולאה, כדי שלא תיתקע.
    """
    # Normalise to plain Python object dtypes (same as build_schedule)
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        employees_df = employees_df.copy()

    assignments = assignments_df.to_dict("records")

    def clean_name(x):
        return str(x).strip()

    def is_missing_worker(x):
        return "❌" in str(x)

    emp_map = {
        str(row["שם"]).strip(): row
        for row in employees_df.to_dict("records")
    }
    
    def get_emp_row(name):
        return emp_map.get(clean_name(name))
    
    def task_times(task):
        return to_datetime_time(task["התחלה"]), to_datetime_time(task["סיום"])

    def _span_minutes(s, e):
        """Normalize a task span to comparable minutes with a NOON pivot:
        times before 12:00 belong to the after-midnight tail of the ops day,
        so 23:15-00:30 → (1395, 1470) and 00:20-01:35 → (1460, 1535).
        Naive time-object comparison silently missed every midnight-crossing
        overlap (23:00 < 00:30 is False for time objects), which disabled the
        TL promotion for ALL night flights."""
        sm = s.hour * 60 + s.minute
        em = e.hour * 60 + e.minute
        if sm < 12 * 60:
            sm += 1440
        if em < 12 * 60:
            em += 1440
        if em <= sm:
            em += 1440
        return sm, em

    def _spans_overlap(s1, e1, s2, e2):
        # Compared at all three day offsets instead of after a noon pivot: the
        # pivot (_span_minutes) pushes an early-morning start a day ahead, so a
        # task straddling 12:00 (11:10-12:10) never "overlapped" 12:00-13:00 —
        # found on real 20.09.2026 data (TL#33 double-booked on LY5031 and
        # LY5141 at Terminal 1). Same technique as reserve_dual_certified_
        # for_tsa's _ov().
        def _mm(t):
            return t.hour * 60 + t.minute
        a1, b1, a2, b2 = _mm(s1), _mm(e1), _mm(s2), _mm(e2)
        if b1 <= a1:
            b1 += 1440
        if b2 <= a2:
            b2 += 1440
        return any(a1 < b2 + sh and a2 + sh < b1 for sh in (-1440, 0, 1440))

    missing_tl_tasks = [
        task for task in assignments
        if task.get("תפקיד בסיס") == "ראש צוות"
        and is_missing_worker(task.get("עובד", ""))
    ]

    # Pre-build by_name for upgrade_teamleads
    _utl_by_name: dict = {}
    for t in assignments:
        n = t["עובד"]
        if n not in _utl_by_name:
            _utl_by_name[n] = []
        _utl_by_name[n].append(t)

    for missing in missing_tl_tasks:
        start, end = task_times(missing)
        # Terminal of the slot being filled — a T1 flight must get a worker whose
        # window at this time is at Terminal 1, and vice versa.
        _miss_term = get_terminal(clean_text(str(missing.get("_gate", ""))))

        # שלב 1: חיפוש ראש צוות פנוי מכל העובדים
        tl_candidates = employees_df[
            employees_df["ראש צוות"].astype(str).str.strip() == "כן"
        ].copy()

        tl_sorted = sort_candidates(tl_candidates, assignments, "ראש צוות", start, end,
                                    task_terminal=_miss_term)

        found_tl = None
        for _item in tl_sorted:
            # sort_candidates returns list[str] (names)
            name = clean_name(_item if isinstance(_item, str) else _item["שם"])
            emp = emp_map.get(name)
            if emp is None:
                continue
            emp_tasks = _utl_by_name.get(name, [])
            if (
                is_within_shift(emp, start, end, task_terminal=_miss_term)
                and is_available(assignments, name, start, end, emp, emp_tasks=emp_tasks)
                and has_room_for_break(assignments, emp, name, start, end, emp_tasks=emp_tasks)
            ):
                found_tl = name
                break

        if found_tl:
            missing["עובד"] = found_tl
            if found_tl not in _utl_by_name:
                _utl_by_name[found_tl] = []
            _utl_by_name[found_tl].append(missing)
            continue

        # שלב 2: אם אין ראש צוות פנוי, נסה לקדם עובד מוסמך-ר"צ שכבר משובץ בזמן
        # חופף בתפקיד נחות — דייל או מתאם תורים. תפקיד ראש הצוות גובר (הנחיית
        # משתמש 2026-07-05: "מתאם תורים יכול לעשות גם דייל"), והמשבצת שהתפנתה
        # תמולא בעובד מתאים אחר.
        promoted_task = None
        promoted_name = None

        for task in assignments:
            if task.get("תפקיד בסיס") not in ("דייל", "מתאם תורים"):
                continue

            worker = clean_name(task.get("עובד", ""))

            if not worker or is_missing_worker(worker):
                continue

            emp = get_emp_row(worker)
            if emp is None:
                continue

            if str(emp.get("ראש צוות", "")).strip() != "כן":
                continue

            t_start, t_end = task_times(task)

            # חייב להיות חפיפה עם החוסר (מודע-חצות)
            if not _spans_overlap(t_start, t_end, start, end):
                continue

            # אותו טרמינל: אין לקדם דייל מטיסה בטרמינל אחר (המרחק פיזי)
            if get_terminal(clean_text(str(task.get("_gate", "")))) != _miss_term:
                continue

            # Guard: the promoted worker must not have OTHER assignments that
            # conflict with the missing ר"צ slot.  If they do, promoting them
            # would give them two overlapping tasks.
            other_worker_tasks = [
                ot for ot in assignments
                if ot is not task
                and clean_name(ot.get("עובד", "")) == worker
            ]
            _has_other_conflict = any(
                _spans_overlap(ot_s, ot_e, start, end)
                for ot in other_worker_tasks
                for ot_s, ot_e in [task_times(ot)]
            )
            if _has_other_conflict:
                continue

            # Same חסימות (blocked-window) check is_available() does — this
            # promotion path never called is_available() at all, so a
            # worker posted to a training course/committee/refresher during
            # the missing ר"צ slot's hours could still get promoted onto it
            # (user rule 2026-09-05, same root-cause family as the
            # is_available UnboundLocalError fix).
            _pr_sm, _pr_em = _span_minutes(start, end)
            if _is_blocked_by_window(emp, _pr_sm, _pr_em, role="ראש צוות"):
                continue

            promoted_task = task
            promoted_name = worker
            break

        if not promoted_task or not promoted_name:
            continue

        # מחפשים מחליף למשבצת שהתפנתה — לפי התפקיד שלה (דייל או מתאם תורים)
        agent_start, agent_end = task_times(promoted_task)
        _vac_role = promoted_task.get("תפקיד בסיס", "דייל")
        _vac_col  = "מתאם תורים" if _vac_role == "מתאם תורים" else "דייל"

        agent_candidates = employees_df[
            employees_df[_vac_col].astype(str).str.strip() == "כן"
        ].copy()

        agent_candidates = sort_candidates(
            agent_candidates,
            assignments,
            _vac_role,
            agent_start,
            agent_end,
            task_terminal=get_terminal(clean_text(str(promoted_task.get("_gate", "")))),
        )

        replacement = None
        _promoted_term = get_terminal(clean_text(str(promoted_task.get("_gate", ""))))
        for _item in agent_candidates:
            name = clean_name(_item if isinstance(_item, str) else _item["שם"])
            if name == promoted_name:
                continue
            emp = emp_map.get(name)
            if emp is None:
                continue
            emp_tasks = _utl_by_name.get(name, [])
            if (
                is_within_shift(emp, agent_start, agent_end, task_terminal=_promoted_term)
                and is_available(assignments, name, agent_start, agent_end, emp, emp_tasks=emp_tasks)
                and has_room_for_break(assignments, emp, name, agent_start, agent_end, emp_tasks=emp_tasks)
            ):
                replacement = name
                break

        # Mutate the task dicts (assignments list holds references, so this is in-place)
        missing["עובד"] = promoted_name
        promoted_task["עובד"] = replacement if replacement else f"❌ חסר {_vac_role}"

        # Keep _utl_by_name in sync so subsequent iterations see the updated assignments.
        # Without this, is_available uses stale emp_tasks and allows double-booking.
        _utl_by_name.setdefault(promoted_name, []).append(missing)
        if replacement:
            _utl_by_name.setdefault(replacement, []).append(promoted_task)

    # ── Relaxed pass: fill any ר"צ slot STILL missing after the strict pass ──
    # The strict Step-2 above only promotes a TL-qualified דייל/מ.ת worker when
    # their task is at the SAME PIER as the missing slot (get_terminal returns
    # the gate LETTER) AND they have no OTHER overlapping task. An UNFILLED ר"צ
    # slot is worse than a cross-pier walk within the same terminal building, so
    # for slots that are still ❌ we relax BOTH constraints: allow a promotion
    # across piers as long as it stays within the same building (all Terminal-3
    # concourses D/E/C/B/... count as one; Terminal 1 stays separate), and allow
    # the worker to give up MULTIPLE overlapping דייל/מ.ת flights (each is
    # back-filled with a free worker if possible, else left ❌). User 2026-07-20:
    # "יש לשבץ את TL#33 וTL#15 כראשי צוותים בטיסות החסרות במקום טיסות
    # הדייל שלהן" — LY363 (E2) / LY397 (D5) had no ר"צ while חסון (דייל at pier
    # C/D) and נימרק (דייל at pier B) sat one pier away.
    def _building(_t):
        return "1" if clean_text(_t) == "1" else "3"

    _still_missing_tl = [
        t for t in assignments
        if t.get("תפקיד בסיס") == "ראש צוות" and is_missing_worker(t.get("עובד", ""))
    ]
    for missing in _still_missing_tl:
        start, end = task_times(missing)
        _miss_term = get_terminal(clean_text(str(missing.get("_gate", ""))))
        _miss_bld = _building(_miss_term)

        _promo_name = None
        _promo_drop = None
        for _cand_name, _cand_tasks in list(_utl_by_name.items()):
            if is_missing_worker(_cand_name):
                continue
            _cand_emp = emp_map.get(clean_name(_cand_name))
            if _cand_emp is None or str(_cand_emp.get("ראש צוות", "")).strip() != "כן":
                continue
            # Terminal-aware: a worker whose availability window at this time is
            # at the OTHER terminal (e.g. TL#40, at T1 after her 01:00 transfer)
            # must never be promoted to a flight at a terminal she isn't at —
            # is_within_shift WITHOUT task_terminal ignored the T1/T3 boundary and
            # wrongly put her back on a T3 flight after 01:00 (user 2026-07-20).
            if not is_within_shift(_cand_emp, start, end, task_terminal=_miss_term):
                continue
            _pr2_sm, _pr2_em = _span_minutes(start, end)
            if _is_blocked_by_window(_cand_emp, _pr2_sm, _pr2_em, role="ראש צוות"):
                continue
            _overlap = [
                ot for ot in _cand_tasks
                if _spans_overlap(*task_times(ot), start, end)
            ]
            # Every overlapping task must be a droppable דייל/מ.ת (never bump a
            # ר"צ/TSA task off), and all must be in the same building as the slot.
            if any(ot.get("תפקיד בסיס") not in ("דייל", "מתאם תורים") for ot in _overlap):
                continue
            if any(_building(get_terminal(clean_text(str(ot.get("_gate", ""))))) != _miss_bld
                   for ot in _overlap):
                continue
            _promo_name = _cand_name
            _promo_drop = _overlap
            break

        if _promo_name is None:
            continue

        # Back-fill each dropped דייל/מ.ת flight with a free worker (best effort).
        for _dropped in _promo_drop:
            _d_s, _d_e = task_times(_dropped)
            _d_role = _dropped.get("תפקיד בסיס", "דייל")
            _d_col = "מתאם תורים" if _d_role == "מתאם תורים" else "דייל"
            _d_term = get_terminal(clean_text(str(_dropped.get("_gate", ""))))
            _bf_cands = sort_candidates(
                employees_df[employees_df[_d_col].astype(str).str.strip() == "כן"].copy(),
                assignments, _d_role, _d_s, _d_e, task_terminal=_d_term,
            )
            _bf = None
            for _item in _bf_cands:
                _bn = clean_name(_item if isinstance(_item, str) else _item["שם"])
                if _bn in (_promo_name, _dropped.get("עובד")):
                    continue
                _be = emp_map.get(_bn)
                if _be is None:
                    continue
                _bt = _utl_by_name.get(_bn, [])
                if (is_within_shift(_be, _d_s, _d_e, task_terminal=_d_term)
                        and is_available(assignments, _bn, _d_s, _d_e, _be, emp_tasks=_bt)
                        and has_room_for_break(assignments, _be, _bn, _d_s, _d_e, emp_tasks=_bt)):
                    _bf = _bn
                    break
            _dropped["עובד"] = _bf if _bf else f"❌ חסר {_d_role}"
            _utl_by_name[_promo_name] = [t for t in _utl_by_name.get(_promo_name, []) if t is not _dropped]
            if _bf:
                _utl_by_name.setdefault(_bf, []).append(_dropped)

        missing["עובד"] = _promo_name
        _utl_by_name.setdefault(_promo_name, []).append(missing)

    # Post-process: remove any overlapping assignments that slipped through.
    # For each employee, if two tasks overlap, keep the higher-priority role
    # (ר"צ > ד"יל) and mark the lower one as missing.
    _ROLE_PRIORITY = {"ראש צוות": 0, "מפקח TSA": 1, "שומר TSA": 1, "דייל": 2}

    def _task_priority(t):
        return _ROLE_PRIORITY.get(t.get("תפקיד בסיס", ""), 9)

    # Group by employee
    _by_worker: dict = {}
    for t in assignments:
        n = clean_name(t.get("עובד", ""))
        if n and not is_missing_worker(n):
            _by_worker.setdefault(n, []).append(t)

    for worker_name, tasks in _by_worker.items():
        if len(tasks) < 2:
            continue
        for i, t1 in enumerate(tasks):
            if is_missing_worker(t1.get("עובד", "")):
                continue
            for t2 in tasks[i + 1:]:
                if is_missing_worker(t2.get("עובד", "")):
                    continue
                s1, e1 = task_times(t1)
                s2, e2 = task_times(t2)
                if _spans_overlap(s1, e1, s2, e2):  # overlap (midnight-aware)
                    # TSA supervisor covers several flights in the SAME concourse
                    # in parallel (mirrors the is_available exception) — a pair of
                    # overlapping מפקח-TSA tasks on the same concourse is design,
                    # not a conflict; don't knock either out.
                    if (
                        t1.get("תפקיד בסיס") == "מפקח TSA"
                        and t2.get("תפקיד בסיס") == "מפקח TSA"
                        and get_terminal(t1.get("_gate", ""))
                        and get_terminal(t1.get("_gate", "")) == get_terminal(t2.get("_gate", ""))
                    ):
                        continue
                    # Drop the lower-priority task (higher number = lower priority)
                    if _task_priority(t1) <= _task_priority(t2):
                        t2["עובד"] = "❌ חסר " + t2.get("תפקיד בסיס", "")
                    else:
                        t1["עובד"] = "❌ חסר " + t1.get("תפקיד בסיס", "")
                        break  # t1 is now missing; skip remaining comparisons

    return pd.DataFrame(assignments)


def fix_wasteful_gaps(assignments_df, employees_df):
    """
    Final cleanup pass (2026-07-19, run AFTER upgrade_teamleads/
    optimize_tl_continuity/reserve_dual_certified_for_tsa).

    The TL floor-time PREEMPTION mechanism in build_schedule's gap-fill pass
    can strand a non-TL worker with a purposeless 60+ min gap: it evicts one
    non-TL worker at a time from an already-filled slot, and each eviction
    can look "safe" in isolation (the worker still has some OTHER task
    bridging what would otherwise be a gap) while the COMBINED effect of
    several such evictions leaves a single task stranded, separated from the
    rest of that worker's day by a gap far bigger than their own required
    break. The per-eviction safety check added to the preemption code (see
    _eviction_creates_bad_gap in build_schedule) can't see this cascading
    effect — it only ever looks at one eviction at a time.

    This pass looks at the FINAL schedule instead: for every employee with
    2+ tasks, check whether either their LAST task (trailing island) or their
    FIRST task (leading island) is separated from the rest of their day by
    such a wasteful gap. If so, and a genuinely free, appropriately
    role-qualified, non-TL substitute exists elsewhere (not already on that
    flight, not busy, whose own shift covers the window), hand that lone
    task to them instead — freeing the original worker to drop the isolated
    flight and either return to counters (trailing case) or simply start
    later (leading case) with no wasteful gap (user 2026-07-19: "יש רווח גדול
    מדיי... אין שיבוץ הגיוני יותר... יש להחזירה לדלפקים ולמצוא כיסוי אחר" /
    "להוריד את הטיסה הראשונה"). Never removes the task if no substitute is
    found — the worker simply keeps it (a soft, best-effort cleanup, not a
    hard guarantee).

    - TRAILING island: last task's gap (minus the break, which naturally
      lands in that last gap) is >= 60 min of pure idle. Works for a 2-task
      worker (drop the last → return to counters after the first).
    - LEADING island: first task is separated from the rest by a raw >= 60
      min gap AND a LATER inter-task gap can host the break (so the remaining
      cluster still gets its break). Requires 3+ tasks so a real 2+ cluster
      survives. Found via real data 2026-07-19: agent#54 (02:00-11:00,
      LY2521 04:10 -> 70min gap -> LY5487 06:10 -> break -> LY323 08:25) —
      the 70-min leading gap can't be filled (every flight in it is fully
      staffed), so per user the leading LY2521 is handed off and she starts
      at LY5487.

    A MIDDLE island (isolated task with big gaps on BOTH sides, in a 4+ task
    day) is rarer and riskier to reshuffle; still left alone.
    """
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        employees_df = employees_df.copy()

    assignments = assignments_df.to_dict("records")
    emp_map = {str(row.get("שם", "")).strip(): row for row in employees_df.to_dict("records")}

    def _tmin(t):
        v = to_datetime_time(t)
        return v.hour * 60 + v.minute if v is not None else None

    by_name: dict = {}
    for a in assignments:
        nm = str(a.get("עובד", "")).strip()
        if not nm or "❌" in nm:
            continue
        by_name.setdefault(nm, []).append(a)

    def _find_substitute(task, exclude_name):
        """Best free, non-TL, role-qualified substitute for `task`, or None.
        Not already on that same flight-instance, shift covers the window,
        no overlapping task of their own; prefer whoever has the fewest tasks."""
        _role_needed = str(task.get("תפקיד בסיס", "")).strip()
        if not _role_needed or _role_needed not in employees_df.columns:
            return None
        _t_s = to_datetime_time(task.get("התחלה", ""))
        _t_e = to_datetime_time(task.get("סיום", ""))
        if _t_s is None or _t_e is None:
            return None
        _ls_m = _t_s.hour * 60 + _t_s.minute
        _le_m = _t_e.hour * 60 + _t_e.minute
        # Never reassign an evening/night or midnight-crossing task: those
        # belong to the night-flight staffing rules, not this daytime cleanup.
        if _ls_m >= 17 * 60 or _le_m <= _ls_m:
            return None
        _flight_no = str(task.get("טיסה", "")).strip()
        _flight_start = task.get("התחלה")
        _already_on_flight = {
            str(a.get("עובד", "")).strip()
            for a in assignments
            if str(a.get("טיסה", "")).strip() == _flight_no and a.get("התחלה") == _flight_start
        }
        _best = None
        _best_count = None
        for _cand_name, _cand_row in emp_map.items():
            if _cand_name == exclude_name or _cand_name in _already_on_flight:
                continue
            if str(_cand_row.get(_role_needed, "")).strip() != "כן":
                continue
            if _is_crew_manager(_cand_row):
                continue        # managers are a last-resort ר"צ only
            # Prefer not handing this to a TL — that just risks the SAME
            # preemption churn on a future rebuild.
            if clean_text(str(_cand_row.get("ראש צוות", ""))) == "כן":
                continue
            # A trainee attendant follows their mentor's flights, not a random
            # gap-fill slot — never pull one in as a substitute.
            if clean_text(str(_cand_row.get("דייל בטרייני", ""))) == "כן":
                continue
            # A מתדרכת (TL instructor) belongs on ר"צ duty only, as an
            # absolute last resort — never pull one into a plain דייל
            # gap-fill slot. Once their ר"צ stint ends they should return to
            # the counters to resume their real role, not get loaded up with
            # more attendant work (user rule 2026-07-25).
            if clean_text(str(_cand_row.get("מתדרכת", ""))) == "כן":
                continue
            # Never pull a NIGHT-shift / page-1 worker in as a substitute:
            # this is a DAYTIME cleanup pass, and loading day flights onto a
            # 22:00-06:00 tail worker is exactly what caused the earlier
            # workflow-ordering regression (2026-07-19). Their early-morning
            # coverage is decided by the main scheduler, not reshuffled here.
            if is_night_shift_for_return_rule(_cand_row):
                continue
            _css = clean_text(str(_cand_row.get("תחילת משמרת", "")))
            _cse = clean_text(str(_cand_row.get("סוף משמרת", "")))
            if not is_time_text(_css) or not is_time_text(_cse):
                continue
            _cs_m = time_to_minutes(_css)
            _ce_m = time_to_minutes(_cse)
            if _ce_m <= _cs_m:
                _ce_m += 1440
            _ts_n = _ls_m if _ls_m >= _cs_m else _ls_m + 1440
            _te_n = _le_m if _le_m > _ls_m else _le_m + 1440
            if _te_n <= _ts_n:
                _te_n += 1440
            if not (_ts_n >= _cs_m and _te_n <= _ce_m):
                continue
            # _ts_n/_te_n are anchored to the CANDIDATE's own shift day (may
            # already be pushed +1440 for a midnight-starting task like
            # 00:00-01:05 on a 21:30-start shift). The candidate's OTHER
            # tasks below are raw clock times with no such anchor, so a naive
            # same-day compare silently misses a genuine overlap whenever the
            # two ended up anchored to different "days" — found via real
            # 30.07.2026 data: agent#77 held LY017 (00:00-01:05) and this
            # check still called her free for LY005 (also 00:00-01:05),
            # producing a real double-booking. Compare at all three day
            # offsets instead, same technique as reserve_dual_certified_for_
            # tsa's midnight-safe _ov() helper.
            _busy = False
            for _ot in by_name.get(_cand_name, []):
                _o_s = _tmin(_ot.get("התחלה", ""))
                _o_e = _tmin(_ot.get("סיום", ""))
                if _o_s is None or _o_e is None:
                    continue
                _o_e2 = _o_e if _o_e > _o_s else _o_e + 1440
                if any(
                    _ts_n < _o_e2 + _phase and _o_s + _phase < _te_n
                    for _phase in (-1440, 0, 1440)
                ):
                    _busy = True
                    break
            if _busy:
                continue
            if _is_blocked_by_window(_cand_row, _ts_n, _te_n, role=_role_needed):
                continue
            _cnt = len(by_name.get(_cand_name, []))
            if _best is None or _cnt < _best_count:
                _best = _cand_name
                _best_count = _cnt
        return _best

    for nm in list(by_name.keys()):
        tasks = by_name[nm]
        if len(tasks) < 2:
            continue
        emp_row = emp_map.get(nm)
        if emp_row is None:
            continue
        ss = clean_text(str(emp_row.get("תחילת משמרת", "")))
        se = clean_text(str(emp_row.get("סוף משמרת", "")))
        if not is_time_text(ss) or not is_time_text(se):
            continue
        # Night-shift workers are OUT of scope entirely: a night worker who
        # staffs night flights and then early-morning flights genuinely has a
        # big gap between the two blocks (they return to counters and come
        # back — that's the normal night pattern, not a wasteful gap), and
        # their tasks span midnight so the naive HH:MM sort below would
        # mis-order them anyway. Reshuffling night flights here also broke the
        # workflow-tab ordering (night flights that begin 12.07 and depart
        # 13.07 wrongly jumped to the TOP of the list) — regression found
        # 2026-07-19 after this pass first shipped.
        if is_night_shift_for_return_rule(emp_row):
            continue
        # Trainee attendants (דייל בטרייני) must stay on their veteran mentor's
        # flights — their assignment follows the pairing, NOT gap optimization.
        # Team leads (ראש צוות) have their floor time deliberately maximized by
        # the preemption + optimize_tl_continuity systems; don't undo that here.
        # Both must be left untouched (found 2026-07-19: this pass had freed
        # trainee-agent#4 off LY117, breaking her pairing with TL#13, and
        # freed the TL TL#20 off a דייל slot she'd preempted into).
        if clean_text(str(emp_row.get("דייל בטרייני", ""))) == "כן":
            continue
        if clean_text(str(emp_row.get("ראש צוות", ""))) == "כן":
            continue
        rb = required_break(emp_row) or 45
        tasks_sorted = sorted(tasks, key=lambda t: _tmin(t.get("התחלה", "")) or 0)

        # ── TRAILING island: the LAST task sits after a wasteful gap ──────
        _last = tasks_sorted[-1]
        _prev = tasks_sorted[-2]
        _prev_end_m = _tmin(_prev.get("סיום", ""))
        _last_start_m = _tmin(_last.get("התחלה", ""))
        if _prev_end_m is not None and _last_start_m is not None:
            _gap = (_last_start_m - _prev_end_m) % 1440
            # The break naturally lands in this trailing gap → subtract it.
            if (_gap - rb) >= 60:
                _sub = _find_substitute(_last, nm)
                if _sub is not None:
                    _last["עובד"] = _sub
                    _last["סיבה"] = "gap-fix: הוחזר לדלפקים, שובץ עובד פנוי אחר"
                    by_name.setdefault(_sub, []).append(_last)
                    by_name[nm] = [t for t in by_name[nm] if t is not _last]
                    continue  # one reassignment per worker per pass

        # ── LEADING island: the FIRST task sits before a wasteful gap ─────
        # Only for 3+ tasks so a real 2+ cluster (with room for the break)
        # survives after dropping the first. The leading gap has NO break in
        # it (the break lands in a later gap), so the whole gap is idle →
        # raw >= 60 threshold, and require a later inter-task gap >= rb so
        # the remaining cluster still has a home for the break.
        if len(tasks_sorted) >= 3:
            _first = tasks_sorted[0]
            _second = tasks_sorted[1]
            _first_end_m = _tmin(_first.get("סיום", ""))
            _second_start_m = _tmin(_second.get("התחלה", ""))
            if _first_end_m is not None and _second_start_m is not None:
                _lead_gap = (_second_start_m - _first_end_m) % 1440
                _has_later_break_gap = False
                for _i in range(1, len(tasks_sorted) - 1):
                    _ge = _tmin(tasks_sorted[_i].get("סיום", ""))
                    _gs = _tmin(tasks_sorted[_i + 1].get("התחלה", ""))
                    if _ge is not None and _gs is not None and ((_gs - _ge) % 1440) >= rb:
                        _has_later_break_gap = True
                        break
                if _lead_gap >= 60 and _has_later_break_gap:
                    _sub = _find_substitute(_first, nm)
                    if _sub is not None:
                        _first["עובד"] = _sub
                        _first["סיבה"] = "gap-fix: התחלה מאוחרת יותר, שובץ עובד פנוי אחר"
                        by_name.setdefault(_sub, []).append(_first)
                        by_name[nm] = [t for t in by_name[nm] if t is not _first]

    return pd.DataFrame(assignments)


def optimize_tl_continuity(assignments_df, employees_df):
    """
    Post-processing pass (run AFTER build_schedule + upgrade_teamleads).

    User rule (2026-07-07, refined 2026-07-09): close MID-SHIFT gaps between
    a team lead's OWN ר"צ tasks by taking a ר"צ task away from another team
    lead — but that is a LAST RESORT, only when no other role could fill the
    gap for this same worker. The build_schedule gap-fill pass (which runs
    earlier, before this function) already tries דייל / מתאם תורים / מפקח TSA
    (if certified) for TL workers first — this function only steals from
    another TL when that earlier pass found genuinely nothing else to place
    in the window (checked via `_overlaps_gap` below: skip if ANY other task,
    any role, already occupies part of the gap for this worker):
      - Only gaps BETWEEN two of a worker's own ר"צ tasks count. Dead time
        before their first task or after their last one does NOT count here
        (user-confirmed — that is a SEPARATE, not-yet-built mechanism).
      - A candidate flight must fit ENTIRELY inside the gap (with a small
        buffer) — a ~60-75 min TL prep window cannot be compressed to fit a
        smaller gap (fixed operational procedure, confirmed 2026-07-07). Most
        real gaps under ~60 min are therefore structurally unfixable; this
        pass only ever helps gaps at least as long as a TL role window.
      - The candidate is only taken from its current holder B if BOTH:
          (a) our worker is genuinely eligible (shift/terminal/availability/
              break-room — the same checks used everywhere else), and
          (b) B's own resulting gap after losing it (0 if it was B's first/
              last ר"צ task of the day — no penalty there; else the merged
              gap between B's surrounding tasks) is SMALLER than our
              worker's original gap — a strict net win, not just moving the
              problem elsewhere.
      - Tie-break (equal gap sizes): fewer ר"צ tasks today wins.
    One swap per identified gap; the whole pass runs once (no repeated
    re-optimization loops) to stay bounded and predictable.
    """
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        employees_df = employees_df.copy()

    assignments = assignments_df.to_dict("records")
    emp_map = {str(row["שם"]).strip(): row for row in employees_df.to_dict("records")}

    def clean_name(x):
        return str(x).strip()

    def is_missing_worker(x):
        return "❌" in str(x)

    def task_times(t):
        return to_datetime_time(t["התחלה"]), to_datetime_time(t["סיום"])

    tl_tasks = [
        t for t in assignments
        if t.get("תפקיד בסיס") == "ראש צוות" and not is_missing_worker(t.get("עובד", ""))
    ]

    by_worker: dict = {}
    for t in tl_tasks:
        by_worker.setdefault(clean_name(t.get("עובד", "")), []).append(t)

    def sort_key(t):
        s, _ = task_times(t)
        m = s.hour * 60 + s.minute
        return m + 1440 if m < 12 * 60 else m

    for name in by_worker:
        by_worker[name].sort(key=sort_key)

    for a_name, a_tasks in list(by_worker.items()):
        a_row = emp_map.get(a_name)
        if a_row is None or len(a_tasks) < 2 or _is_crew_manager(a_row):
            continue
        a_term = clean_text(str(a_row.get("טרמינל", ""))) or "3"

        for i in range(len(a_tasks) - 1):
            prev_task, next_task = a_tasks[i], a_tasks[i + 1]
            _, prev_end = task_times(prev_task)
            next_start, _ = task_times(next_task)
            prev_end_m = prev_end.hour * 60 + prev_end.minute
            next_start_m = next_start.hour * 60 + next_start.minute
            gap_m = (next_start_m - prev_end_m) % 1440
            if gap_m < 45:
                continue

            # If the earlier gap-fill pass (in build_schedule) already placed
            # ANY other task — any role: דייל, מתאם תורים, מפקח TSA — for this
            # SAME worker inside this window, the gap is not actually empty.
            # User rule 2026-07-09: maximize a TL's tasks across every role
            # type FIRST; only steal a flight from another team lead when
            # truly nothing else could be scheduled here.
            def _overlaps_gap(t):
                ts, te = task_times(t)
                if ts is None or te is None:
                    return False
                tsm = ts.hour * 60 + ts.minute
                tem = te.hour * 60 + te.minute
                if tem < tsm:
                    tem += 1440
                return tsm < next_start_m - 5 and tem > prev_end_m + 5

            if any(
                clean_name(t.get("עובד", "")) == a_name
                and t is not prev_task and t is not next_task
                and not is_missing_worker(t.get("עובד", ""))
                and _overlaps_gap(t)
                for t in assignments
            ):
                continue

            best = None  # ((b_result_gap, b_task_count), candidate_task, b_name)
            for cand in tl_tasks:
                b_name = clean_name(cand.get("עובד", ""))
                if b_name == a_name:
                    continue
                c_start, c_end = task_times(cand)
                if c_start is None or c_end is None:
                    continue
                cs_m = c_start.hour * 60 + c_start.minute
                ce_m = c_end.hour * 60 + c_end.minute
                if ce_m < cs_m:
                    ce_m += 1440
                if cs_m < prev_end_m + 5 or ce_m > next_start_m - 5:
                    continue
                if get_terminal(clean_text(str(cand.get("_gate", "")))) != a_term:
                    continue

                b_row = emp_map.get(b_name)
                if b_row is None:
                    continue
                if not is_within_shift(a_row, c_start, c_end, task_terminal=a_term):
                    continue
                a_other_tasks = [t for t in a_tasks if t is not prev_task and t is not next_task] + \
                                [t for t in assignments if clean_name(t.get("עובד", "")) == a_name
                                 and t.get("תפקיד בסיס") != "ראש צוות"]
                if not is_available(assignments, a_name, c_start, c_end, a_row,
                                    role="ראש צוות", flight_gate=cand.get("_gate", ""),
                                    emp_tasks=a_other_tasks):
                    continue
                if not has_room_for_break(assignments, a_row, a_name, c_start, c_end,
                                          emp_tasks=a_other_tasks):
                    continue

                b_tasks = by_worker.get(b_name, [])
                b_idx = next((k for k, t in enumerate(b_tasks) if t is cand), None)
                if b_idx is None:
                    continue
                if b_idx == 0 or b_idx == len(b_tasks) - 1:
                    b_result_gap = 0
                else:
                    _, b_prev_end = task_times(b_tasks[b_idx - 1])
                    b_next_start, _ = task_times(b_tasks[b_idx + 1])
                    bpe_m = b_prev_end.hour * 60 + b_prev_end.minute
                    bns_m = b_next_start.hour * 60 + b_next_start.minute
                    b_result_gap = (bns_m - bpe_m) % 1440

                if b_result_gap >= gap_m:
                    continue

                b_task_count = len(b_tasks)
                key = (b_result_gap, b_task_count)
                if best is None or key < best[0]:
                    best = (key, cand, b_name)

            if best is not None:
                _, cand, b_name = best
                cand["עובד"] = a_name
                by_worker[b_name] = [t for t in by_worker[b_name] if t is not cand]
                a_tasks.insert(i + 1, cand)
                a_tasks.sort(key=sort_key)

    return pd.DataFrame(assignments)


def consolidate_tsa_inspectors_by_pier(schedule_df, employees_df):
    """Post-pass: force מפקח-TSA coverage onto ONE inspector per pier whenever
    the pier's total flight count fits under the 4-flight cap.

    The main assignment loop's same_pier_pref sort-key (sort_candidates)
    STRONGLY prefers whoever already covers a pier, but it's still a greedy,
    per-flight-at-a-time pick — a genuine scheduling conflict at just ONE of
    the pier's flights (the chosen inspector busy elsewhere at that exact
    moment) can force a DIFFERENT candidate for that one slot, splitting the
    pier across two people even though one of them could have covered all of
    it. Found via real data 2026-07-25: after manually consolidating all USA
    night flights into 2 piers (expecting 2 inspectors total, freeing a third
    dual-certified worker for ר"צ), TL#37 and TSA-inspector#3 still split pier D
    between them, leaving pier C's LY005 uncovered.

    For each pier with ≤4 total flights split across >1 distinct worker: pick
    the worker already covering the MOST flights there as the "keeper", move
    every other worker's slot on that pier onto the keeper (if within their
    shift), and try to redeploy each freed worker onto a DIFFERENT pier that
    is still missing a מפקח TSA (best-effort — only if qualified/available/
    conflict-free). Runs LAST in the pipeline so it sees the FINAL TSA state.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    def _tmin(t):
        try:
            h, m = str(t).split(":")[:2]
            return int(h) * 60 + int(m)
        except (ValueError, IndexError):
            return None

    tsa_idx = df.index[df["תפקיד בסיס"].astype(str) == "מפקח TSA"].tolist()
    if not tsa_idx:
        return df

    _emp_lookup = {
        clean_text(str(r.get("שם", ""))): r
        for _, r in employees_df.iterrows()
        if clean_text(str(r.get("שם", "")))
    }

    def _tspan(i):
        s, e = _tmin(df.at[i, "התחלה"]), _tmin(df.at[i, "סיום"])
        if s is None or e is None:
            return None
        return (s, e if e > s else e + 1440)

    def _as_time(t):
        """is_within_shift needs time objects — the schedule holds "HH:MM"
        strings. Passing the string straight through raised AttributeError on
        .hour, which the callers' bare `except Exception` swallowed, silently
        skipping the shift check entirely (found via real data 2026-08-09:
        TSA-inspector#2, shift 07:30-10:30, consolidated onto LY023 11:15-13:15 and
        LY011 14:55-16:55)."""
        m = _tmin(t)
        if m is None:
            return None
        return time(m // 60 % 24, m % 60)

    # Group currently-assigned (non-❌) TSA rows by pier — but a pier letter
    # repeats all day (e.g. an unrelated 08:15 pier-D flight has nothing to
    # do with a 22:00 pier-D cluster), so cluster by pier AND overlapping/
    # nearby time, not pier alone. Without this, a busy pier with several
    # UNRELATED daytime flights plus a genuine 4-flight overnight cluster
    # totalled >4 and the whole pier was wrongly skipped as "needs more than
    # one inspector" even though the overnight cluster alone fit perfectly
    # (found via real data 2026-07-25: pier D had 4 overnight + 3 unrelated
    # day flights = 7 total, so the len(idxs) > 4 guard skipped it entirely).
    by_pier = {}
    for i in tsa_idx:
        w = clean_text(str(df.at[i, "עובד"]))
        if "❌" in w or not w:
            continue
        pier = get_terminal(clean_text(str(df.at[i, "_gate"])))
        if not pier:
            continue
        by_pier.setdefault(pier, []).append(i)

    clusters = []  # list of (pier, [idx,...])
    for pier, idxs in by_pier.items():
        idxs = sorted(idxs, key=lambda i: _tspan(i) or (0, 0))
        cur = []
        cur_span = None
        for i in idxs:
            sp = _tspan(i)
            if sp is None:
                if cur:
                    clusters.append((pier, cur)); cur = []; cur_span = None
                continue
            if cur and cur_span is not None and sp[0] < cur_span[1] + 120:
                cur.append(i)
                cur_span = (cur_span[0], max(cur_span[1], sp[1]))
            else:
                if cur:
                    clusters.append((pier, cur))
                cur = [i]
                cur_span = sp
        if cur:
            clusters.append((pier, cur))

    freed_workers = []
    for pier, idxs in clusters:
        if len(idxs) > 4:
            continue  # genuinely needs more than one inspector — leave as-is
        names_here = {}
        for i in idxs:
            nm = clean_text(str(df.at[i, "עובד"]))
            names_here.setdefault(nm, []).append(i)
        if len(names_here) <= 1:
            continue  # already consolidated onto one worker

        # Prefer whoever is actually ROSTERED as inspector for this shift
        # ("מפקח TSA מוגדר" — a "פיקוח TSA" header in the daily roster) over
        # someone merely holding the certification; break ties by whoever
        # already covers the most flights here (user rule 2026-07-25).
        def _is_designated(k):
            _r = _emp_lookup.get(k)
            return bool(_r is not None and clean_text(str(_r.get("מפקח TSA מוגדר", ""))) == "כן")
        keeper = max(names_here, key=lambda k: (_is_designated(k), len(names_here[k])))
        keeper_row = _emp_lookup.get(keeper)

        def _keeper_busy(_s_chk, _e_chk):
            # The keeper may hold overlapping tasks ONLY if they're ALSO a
            # TSA-role task on this SAME pier (the domain exception this whole
            # pass relies on) — any other overlapping task (ר"צ, a different
            # pier, etc.) is a real conflict and blocks the merge.
            _sm, _em = _tmin(_s_chk), _tmin(_e_chk)
            if _sm is None or _em is None:
                return False
            _em2 = _em if _em > _sm else _em + 1440
            for j in df.index:
                if clean_text(str(df.at[j, "עובד"])) != keeper:
                    continue
                if str(df.at[j, "תפקיד בסיס"]).strip() == "מפקח TSA" and \
                        get_terminal(clean_text(str(df.at[j, "_gate"]))) == pier:
                    continue  # same-pier TSA overlap is fine
                _js, _je = _tmin(df.at[j, "התחלה"]), _tmin(df.at[j, "סיום"])
                if _js is None or _je is None:
                    continue
                _je2 = _je if _je > _js else _je + 1440
                if not (_em2 <= _js or _sm >= _je2):
                    return True
            return False

        for nm, nm_idxs in names_here.items():
            if nm == keeper:
                continue
            _moved_all = True
            for i in nm_idxs:
                _s = str(df.at[i, "התחלה"]); _e = str(df.at[i, "סיום"])
                _ok = True
                if keeper_row is not None:
                    # Fails CLOSED: an unverifiable shift is not a licence to
                    # move the slot onto someone who may be off duty.
                    _st, _et = _as_time(_s), _as_time(_e)
                    try:
                        if _st is None or _et is None or not is_within_shift(
                            keeper_row, _st, _et, task_terminal=pier
                        ):
                            _ok = False
                    except Exception:
                        _ok = False
                if _ok and _keeper_busy(_s, _e):
                    _ok = False
                if _ok and keeper_row is not None:
                    _kst, _ket = _tmin(_s), _tmin(_e)
                    if _kst is not None and _ket is not None:
                        _ket2 = _ket if _ket > _kst else _ket + 1440
                        if _is_blocked_by_window(keeper_row, _kst, _ket2, role="מפקח TSA"):
                            _ok = False
                if _ok:
                    df.at[i, "עובד"] = keeper
                    if "סיבה" in df.columns:
                        df.at[i, "סיבה"] = "אוחד עם מפקח TSA אחר באותה שלוחה"
                else:
                    _moved_all = False
            if _moved_all:
                freed_workers.append(nm)

    if not freed_workers:
        return df

    # Best-effort: redeploy each freed worker to a DIFFERENT pier still
    # missing a מפקח TSA, if qualified, within-shift, and conflict-free.
    missing_idx = [i for i in tsa_idx if "❌" in str(df.at[i, "עובד"])]
    for nm in freed_workers:
        row = _emp_lookup.get(nm)
        if row is None:
            continue
        for i in missing_idx:
            if "❌" not in str(df.at[i, "עובד"]):
                continue  # already filled by an earlier freed worker
            _s = str(df.at[i, "התחלה"]); _e = str(df.at[i, "סיום"])
            _gt = get_terminal(clean_text(str(df.at[i, "_gate"])))
            try:
                _st, _et = _as_time(_s), _as_time(_e)
                if _st is None or _et is None or not is_within_shift(
                    row, _st, _et, task_terminal=_gt
                ):
                    continue
            except Exception:
                continue
            _s_m, _e_m = _tmin(_s), _tmin(_e)
            if _s_m is None or _e_m is None:
                continue
            _e_m2 = _e_m if _e_m > _s_m else _e_m + 1440
            _busy = False
            for j in df.index:
                if clean_text(str(df.at[j, "עובד"])) != nm or j == i:
                    continue
                _js, _je = _tmin(df.at[j, "התחלה"]), _tmin(df.at[j, "סיום"])
                if _js is None or _je is None:
                    continue
                _je2 = _je if _je > _js else _je + 1440
                if not (_e_m2 <= _js or _s_m >= _je2):
                    _busy = True
                    break
            if _busy:
                continue
            if _is_blocked_by_window(row, _s_m, _e_m2, role="מפקח TSA"):
                continue
            df.at[i, "עובד"] = nm
            if "סיבה" in df.columns:
                df.at[i, "סיבה"] = "מפקח TSA שוחרר משלוחה אחרת ואוחד לכאן"
            break

    # Best-effort: a worker freed from TSA duty by the consolidation above may
    # ALSO be qualified for a completely different role that's still missing
    # (e.g. ר"צ) — this pass runs LAST in the pipeline, after the main
    # build/upgrade_teamleads/etc. already finished filling non-TSA slots, so
    # nothing else revisits them once this frees up capacity. Without this,
    # a freed dual-certified worker (ר"צ + TSA) sits unused even though the
    # missing-role diagnostic correctly shows them as "✅ available" — found
    # via real data 2026-07-25: TL#37 freed from pier D's TSA duty, but
    # LY005's missing ר"צ slot stayed empty until manually force-assigned.
    _role_col_map = {
        "ראש צוות": "ראש צוות", "דייל": "דייל", "מתאם תורים": "מתאם תורים",
        "מפקח TSA": "מפקח TSA", "שומר TSA": "שומר TSA",
        "טרייני רצ": "טרייני רצ", "טרייני ר״צ": "טרייני רצ",
    }
    _other_missing = [
        i for i in df.index
        if "❌" in str(df.at[i, "עובד"])
        and str(df.at[i, "תפקיד בסיס"]).strip() != "מפקח TSA"
    ]
    for nm in freed_workers:
        row = _emp_lookup.get(nm)
        if row is None:
            continue
        for i in _other_missing:
            if "❌" not in str(df.at[i, "עובד"]):
                continue
            _role = str(df.at[i, "תפקיד בסיס"]).strip()
            _col = _role_col_map.get(_role, _role)
            if _col not in row.index or clean_text(str(row.get(_col, ""))) != "כן":
                continue
            _s = str(df.at[i, "התחלה"]); _e = str(df.at[i, "סיום"])
            _gt = get_terminal(clean_text(str(df.at[i, "_gate"])))
            try:
                _st, _et = _as_time(_s), _as_time(_e)
                if _st is None or _et is None or not is_within_shift(
                    row, _st, _et, task_terminal=_gt
                ):
                    continue
            except Exception:
                continue
            _s_m, _e_m = _tmin(_s), _tmin(_e)
            if _s_m is None or _e_m is None:
                continue
            _e_m2 = _e_m if _e_m > _s_m else _e_m + 1440
            _busy = False
            for j in df.index:
                if clean_text(str(df.at[j, "עובד"])) != nm or j == i:
                    continue
                _js, _je = _tmin(df.at[j, "התחלה"]), _tmin(df.at[j, "סיום"])
                if _js is None or _je is None:
                    continue
                _je2 = _je if _je > _js else _je + 1440
                if not (_e_m2 <= _js or _s_m >= _je2):
                    _busy = True
                    break
            if _busy:
                continue
            # already-on-flight check (another role on the SAME flight)
            _flt = str(df.at[i, "טיסה"]).strip()
            _already_on_flight = any(
                clean_text(str(df.at[k, "עובד"])) == nm
                for k in df.index
                if str(df.at[k, "טיסה"]).strip() == _flt and k != i
            )
            if _already_on_flight:
                continue
            df.at[i, "עובד"] = nm
            if "סיבה" in df.columns:
                df.at[i, "סיבה"] = "שוחרר ממפקח TSA באותה שלוחה ואוחד כאן"
            break

    return df


def backfill_remaining_gaps(schedule_df, employees_df):
    """Final catch-all pass: fill any still-❌ slot with a qualified, free,
    within-shift worker.

    Every other post-pass in this pipeline only reconsiders the SPECIFIC
    gaps/workers it was built around (e.g. consolidate_tsa_inspectors_by_pier
    only redeploys a worker it itself just displaced from TSA duty). A worker
    who becomes genuinely idle as a SIDE EFFECT of an earlier pass — freed
    from one role but never explicitly tracked as "freed" by whichever later
    pass could use them elsewhere — falls through the cracks: the missing-
    role diagnostic correctly shows them as "✅ available" (it re-checks the
    FINAL state), yet nothing in the build pipeline itself ever re-assigns
    them, leaving the gap open until a human clicks "הכrח שיבוץ" manually.
    Found via real data 2026-07-25: TL#37 ended up idle after the TSA
    sort-key fix correctly kept her off pier D from the START (so she was
    never "displaced" by the consolidation pass and never entered its
    freed-worker list), while LY005's ר"צ slot stayed ❌ despite her being
    fully qualified, within-shift, and conflict-free for it.

    Runs LAST in the pipeline, after every other post-pass, so it sees final
    availability. Best-effort only — a slot with no qualified free candidate
    is left ❌, same as always.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    # A דייל-בטרייני may only ever work their mentor's flights (see
    # pair_trainee_attendants). This pass runs AFTER that one, so without an
    # explicit guard it happily drops a trainee into any open slot and silently
    # re-splits the pair (found via real 30.07 data: trainee-agent#10 backfilled onto
    # LY015 and trainee-agent#7 onto LY279 — neither mentor was on those flights).
    def _ws_bf(nm):
        return frozenset(name_key(_w) for _w in clean_text(str(nm)).split() if len(_w) > 1)

    _bf_mentor_of = {}
    if "דייל בטרייני" in employees_df.columns and "חונך דייל" in employees_df.columns:
        for _, _br in employees_df.iterrows():
            if clean_text(str(_br.get("דייל בטרייני", ""))) != "כן":
                continue
            _bm = clean_text(str(_br.get("חונך דייל", "")))
            if not _bm:
                continue
            _bs = _ws_bf(_br.get("שם", ""))
            if _bs:
                _bf_mentor_of[_bs] = _ws_bf(_bm)

    _missing_idx = [i for i in df.index if "❌" in str(df.at[i, "עובד"])]
    for i in _missing_idx:
        if "❌" not in str(df.at[i, "עובד"]):
            continue  # already filled by an earlier iteration below
        _role = str(df.at[i, "תפקיד בסיס"]).strip()
        _flt = str(df.at[i, "טיסה"]).strip()
        _cands = get_qualified_candidates_for_swap(df, employees_df, _flt, _role, i)
        # A שומר TSA slot is the ONE case a trainee is deliberately used
        # without their mentor on the same flight (user rule) — don't filter
        # trainee candidates out of an open GUARD slot for lacking one.
        if _cands and _bf_mentor_of and _role != "שומר TSA":
            _flt_sets = [
                _ws_bf(df.at[_j, "עובד"])
                for _j in df.index
                if str(df.at[_j, "טיסה"]).strip() == _flt
                and "❌" not in str(df.at[_j, "עובד"])
            ]
            _cands = [
                _c for _c in _cands
                if _bf_mentor_of.get(_ws_bf(_c)) is None
                or _bf_mentor_of[_ws_bf(_c)] in _flt_sets
            ]
        if _cands:
            df.at[i, "עובד"] = _cands[0]
            if "סיבה" in df.columns:
                df.at[i, "סיבה"] = "שובץ אוטומטית במעבר סופי — היה זמין ומוסמך"

    return df


def reserve_dual_certified_for_tsa(assignments_df, employees_df):
    """Post-build pass: fill an uncovered מפקח-TSA slot by moving a dual-certified
    (ר"צ + מפקח TSA) worker OFF an overlapping ר"צ task.

    User rule 2026-07-13: a worker qualified for BOTH roles must be prioritised
    as a TSA INSPECTOR over team-lead, so a pier is never left without an
    inspector ("יש לתת עדיפות לשיבוץ ... כמפקח ולא כראש צוות, כדי לא להשאיר מפקח
    ללא כיסוי"). The greedy build can't honour this: flights are processed in
    departure-time order, so an EARLIER-departing flight's ר"צ slot grabs the
    dual-cert worker before a LATER flight's inspector slot is reached (real
    case: LY083/BKK ר"צ 23:40-00:55 took TL#1, leaving LY005/C7's
    inspector slot ❌ even though she was the only inspector who could cover
    pier C). This pass reassigns her ר"צ→inspector; the vacated ר"צ becomes ❌
    (acceptable per the rule — that flight keeps any OTHER ר"צ it has, and the
    empty slot can be back-filled by upgrade_teamleads which runs afterwards).
    """
    df = assignments_df.copy() if hasattr(assignments_df, "copy") else assignments_df
    if not hasattr(df, "iterrows"):
        return df

    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    def _is_dual(name):
        er = emp_map.get(clean_text(str(name)))
        if er is None:
            return False
        # A worker whose מחלקה מקורית marks them as belonging to a different
        # department (e.g. "חוליה"/מל"ן) shouldn't be pulled into a TSA-
        # inspector slot just because the certification flag is set — same
        # rule as the primary build pass (user-reported 2026-07-29: TSA-inspector#4).
        if clean_text(str(er.get("מחלקה מקורית", ""))):
            return False
        if _is_crew_manager(er) and not _manager_marked(er):
            return False        # an un-marked manager is never scheduled
        return (clean_text(str(er.get("מפקח TSA", ""))) == "כן"
                and clean_text(str(er.get("ראש צוות", ""))) == "כן")

    def _ov(s1, e1, s2, e2):
        """Overlap check, midnight-safe. Each interval wraps to next-day only
        when its OWN end is <= its own start (a genuine overnight span); the
        two (possibly-wrapped) intervals are then compared at all three
        relative phases (-1440/0/+1440) so a daytime pair straddling a fixed
        pivot is never missed just because of which side of it each fell on.

        Previously used a NOON pivot (any time before 12:00 got +1440,
        treating it as "belongs to the ops-day tail") — real 21.07.2026
        data: LY023 (11:15-13:15, just before noon) vs LY371 (12:55-13:55,
        just after) genuinely overlap by 20 min, but the noon pivot pushed
        LY023 a full day later relative to LY371, so this function said "no
        overlap" and let TL#1 get double-booked onto both."""
        def _n(t):
            dt = to_datetime_time(t)
            if dt is None:
                return None
            return dt.hour * 60 + dt.minute
        a1, b1, a2, b2 = _n(s1), _n(e1), _n(s2), _n(e2)
        if None in (a1, b1, a2, b2):
            return False
        if b1 <= a1:
            b1 += 1440
        if b2 <= a2:
            b2 += 1440
        return any(a1 < b2 + shift and a2 + shift < b1 for shift in (-1440, 0, 1440))

    # Empty inspector slots
    _missing = df[
        (df["תפקיד בסיס"].astype(str) == "מפקח TSA") &
        (df["עובד"].astype(str).str.contains("❌", na=False))
    ]
    if _missing.empty:
        return df

    def _still_missing(idx):
        return "❌" in str(df.at[idx, "עובד"])

    for _tsa_idx, _tsa in _missing.iterrows():
        if not _still_missing(_tsa_idx):
            # Already filled by an earlier iteration's same-pier parallel-fill
            # (see below) — a worker just placed as inspector for one flight
            # can cover every other OVERLAPPING flight in the SAME pier too,
            # without a second ר"צ vacate.
            continue
        _ts = _tsa.get("התחלה", "")
        _te = _tsa.get("סיום", "")
        _pier = get_terminal(clean_text(str(_tsa.get("_gate", ""))))
        _ts_dt = to_datetime_time(_ts)
        _te_dt = to_datetime_time(_te)
        if _ts_dt is None or _te_dt is None:
            continue
        if not _pier:
            # Gate not yet known (common for late-night US flights) — an
            # inspector is stationed at a specific physical pier, and with no
            # gate the pier can't be verified. is_within_shift only ever
            # discriminates T1 vs T3 (never individual piers), so calling it
            # with an empty pier would silently pass ANY T3-window worker as
            # a "match" — moving a worker here would be a guess that could be
            # wrong once the real gate/pier is entered. Leave the slot ❌;
            # the diagnostic panel already explains this and offers manual
            # gate entry (found via real data 2026-07-13: LY001/JFK had no
            # gate yet this pass moved TL#1 onto it anyway).
            continue

        # Candidate ר"צ tasks held by a dual-cert worker, overlapping this slot
        _rz = df[
            (df["תפקיד בסיס"].astype(str) == "ראש צוות") &
            (~df["עובד"].astype(str).str.contains("❌", na=False))
        ]
        for _rz_idx, _rzt in _rz.iterrows():
            _name = clean_text(str(_rzt.get("עובד", "")))
            if not _is_dual(_name):
                continue
            if not _ov(_rzt.get("התחלה", ""), _rzt.get("סיום", ""), _ts, _te):
                continue
            _er = emp_map.get(_name)
            if _er is None:
                continue
            # Shift must cover the inspector slot (terminal/pier-aware)
            if not is_within_shift(_er.to_dict() if hasattr(_er, "to_dict") else dict(_er),
                                   _ts_dt, _te_dt, task_terminal=_pier):
                continue
            # The worker must have NO other task overlapping the inspector slot
            # besides the ר"צ one we're vacating (else moving them double-books).
            _others = df[
                (df["עובד"].astype(str).apply(lambda v: clean_text(str(v)) == _name)) &
                (df.index != _rz_idx) & (df.index != _tsa_idx)
            ]
            _conflict = False
            for _o_idx, _ot in _others.iterrows():
                if _ov(_ot.get("התחלה", ""), _ot.get("סיום", ""), _ts, _te):
                    # A same-pier inspector task in parallel is fine, but any
                    # other overlapping duty blocks the move.
                    _conflict = True
                    break
            if _conflict:
                continue
            # Move: vacate ר"צ, fill the inspector slot.
            df.at[_rz_idx, "עובד"] = "❌ חסר ראש צוות"
            df.at[_tsa_idx, "עובד"] = _rzt.get("עובד", _name)

            # This worker is now a TSA inspector at this pier — they can cover
            # every OTHER still-❌ inspector slot in the SAME pier whose time
            # OVERLAPS this one, in parallel, with no further ר"צ vacate
            # needed (mirrors is_available's same-pier TSA exception; this is
            # the whole point of pier consolidation). Without this, moving her
            # onto ONE overlapping slot (e.g. LY001) left a SECOND overlapping
            # same-pier slot (LY005) still ❌, since she was no longer sitting
            # on a ר"צ task for the outer loop to find her by on its next
            # iteration (found via real data 2026-07-14: LY001 and LY005 both
            # pier C, both 23:00-01:05ish — filling LY001 alone left LY005
            # showing her as "✅ available" in the diagnostic yet still ❌).
            # CAP (user rule 2026-07-22): one inspector may cover at most 4
            # flights in parallel on the same pier — physically they rotate
            # between gates, not stand at an unlimited number at once. Without
            # this cap a single worker could silently absorb an entire pier's
            # night rush, when a genuine 5th+ flight there needs a SECOND
            # inspector instead.
            _pier_cnt = 1  # the slot just filled above
            for _other_idx, _other in _missing.iterrows():
                if _other_idx == _tsa_idx or not _still_missing(_other_idx):
                    continue
                if _pier_cnt >= 4:
                    break
                _o_pier = get_terminal(clean_text(str(_other.get("_gate", ""))))
                if not _o_pier or _o_pier != _pier:
                    continue
                if not _ov(_ts, _te, _other.get("התחלה", ""), _other.get("סיום", "")):
                    continue
                _o_ts_dt = to_datetime_time(_other.get("התחלה", ""))
                _o_te_dt = to_datetime_time(_other.get("סיום", ""))
                if _o_ts_dt is None or _o_te_dt is None:
                    continue
                if not is_within_shift(_er.to_dict() if hasattr(_er, "to_dict") else dict(_er),
                                       _o_ts_dt, _o_te_dt, task_terminal=_o_pier):
                    continue
                df.at[_other_idx, "עובד"] = _rzt.get("עובד", _name)
                _pier_cnt += 1
            break

    return df


# =========================
# SWAP HELPERS
# =========================

def get_qualified_candidates_for_swap(schedule_df, employees_df, flight_num, role_base, task_idx):
    task_row = schedule_df.loc[task_idx]
    start_str = str(task_row.get("התחלה", ""))
    end_str   = str(task_row.get("סיום",   ""))
    current_worker = str(task_row.get("עובד", ""))

    role_col_map = {
        "ראש צוות":   "ראש צוות",
        "דייל":       "דייל",
        "מתאם תורים": "מתאם תורים",
        "מפקח TSA":   "מפקח TSA",
        "שומר TSA":   "שומר TSA",
        "טרייני רצ":  "טרייני רצ",
        "טרייני ר״צ": "טרייני רצ",
    }
    col = role_col_map.get(normalize_role_label(role_base), role_base)

    if col not in employees_df.columns:
        return []

    certified = employees_df[employees_df[col].astype(str).str.strip() == "כן"].copy()

    # Duty-blocked workers are not ordinary candidates — they surface only in
    # the approval-required list (get_extendable_candidates_for_swap).
    if DUTY_BLOCK_COL in certified.columns:
        certified = certified[certified[DUTY_BLOCK_COL].astype(str).str.strip() == ""]

    # מפקח TSA: exclude anyone whose מחלקה מקורית marks them as belonging to a
    # different department (e.g. "חוליה"/מל"ן) — the certification flag alone
    # isn't enough (same rule as the primary build pass; user-reported
    # 2026-07-29: TSA-inspector#4 offered as a swap candidate despite being מל"ן).
    if col == "מפקח TSA" and "מחלקה מקורית" in certified.columns:
        # An EMPTY מחלקה מקורית means "no other department" — but pandas reads
        # a blank cell as NaN, and str(NaN) is the literal "nan", so comparing
        # to "" excluded 371 of 376 workers and left the inspector swap lists
        # all but permanently empty (found via real data 2026-08-09).
        certified = certified[
            certified["מחלקה מקורית"].apply(lambda v: clean_text(v) == "")
        ]

    # Exclude employees already assigned to this flight in another role
    flight_tasks = schedule_df[
        (schedule_df["טיסה"].astype(str).str.strip() == str(flight_num).strip()) &
        (schedule_df.index != task_idx)
    ]
    already_on_flight = set(
        n for n in flight_tasks["עובד"].astype(str).str.strip().tolist()
        if n and not n.startswith("❌")
    )
    certified = certified[~certified["שם"].astype(str).str.strip().isin(already_on_flight)]

    # Crew-chief managers: ר"צ / מפקח TSA only, only inside their marked
    # תגבור hours, and always listed LAST (see _manager_may_take).
    _sw_s, _sw_e = to_datetime_time(start_str), to_datetime_time(end_str)
    certified = _drop_blocked_managers(certified, role_base, _sw_s, _sw_e)
    _mgr_names = {clean_text(str(r.get("שם", ""))) for r in certified.to_dict("records")
                  if _is_crew_manager(r)}

    def _managers_last(names):
        return sorted(names, key=lambda n: 1 if clean_text(str(n)) in _mgr_names else 0)

    if not is_time_text(start_str) or not is_time_text(end_str):
        return _managers_last([n for n in certified["שם"].tolist() if n != current_worker])

    try:
        task_start = to_datetime_time(start_str)
        task_end   = to_datetime_time(end_str)
    except Exception:
        return []

    # Use minute arithmetic (midnight-safe) — same logic as is_available()
    ts_m = task_start.hour * 60 + task_start.minute
    te_m = task_end.hour   * 60 + task_end.minute
    if te_m < ts_m:
        te_m += 1440  # task crosses midnight

    other_tasks = schedule_df[schedule_df.index != task_idx].copy()
    other_tasks = other_tasks[other_tasks["התחלה"].astype(str).str.strip() != ""]

    # Pre-group other_tasks BY EMPLOYEE once (O(rows)) instead of re-filtering
    # the whole schedule per candidate (O(certified * rows)) — this function is
    # called per-vacated-slot by several callers (force_assign_worker,
    # pair_trainee_attendants, the swap UI), and on a full-day schedule the
    # per-candidate re-filter dominated runtime (found via real data 2026-07-24:
    # a single build calling this ~15 times cost 3+ of its ~5s total).
    _tasks_by_name = {}
    for _nm, _ps, _pe, _role, _gate in zip(
        other_tasks["עובד"].astype(str),
        other_tasks["התחלה"].astype(str),
        other_tasks["סיום"].astype(str),
        other_tasks["תפקיד בסיס"].astype(str),
        other_tasks["_gate"].astype(str) if "_gate" in other_tasks.columns else [""] * len(other_tasks),
    ):
        _tasks_by_name.setdefault(_nm, []).append((_ps, _pe, _role, _gate))

    results = []
    _swap_term = get_terminal(clean_text(str(task_row.get("_gate", ""))))
    _slot_is_tsa = normalize_role_label(role_base) == "מפקח TSA"
    for _, emp_row in certified.iterrows():
        name = emp_row["שם"]
        if name == current_worker:
            continue
        if not is_within_shift(emp_row, task_start, task_end, task_terminal=_swap_term):
            continue
        if _is_blocked_by_window(emp_row, ts_m, te_m, role=normalize_role_label(role_base)):
            continue
        _my_tasks = _tasks_by_name.get(name, [])
        # Same cap as is_available(): at most 4 concurrent same-pier TSA
        # tasks — once already at 4, the parallel-coverage exemption below
        # is withdrawn and a 5th overlapping task is a real conflict again
        # (user rule 2026-09-05).
        _concurrent_same_pier_tsa = 0
        if _slot_is_tsa and _swap_term:
            for (_ps0, _pe0, _role0, _gate0) in _my_tasks:
                if _role0 != "מפקח TSA" or get_terminal(clean_text(_gate0)) != _swap_term:
                    continue
                try:
                    ps_m0 = time_to_minutes(_ps0); pe_m0 = time_to_minutes(_pe0)
                    if pe_m0 < ps_m0:
                        pe_m0 += 1440
                    if not (ts_m >= pe_m0 + 5 or te_m <= ps_m0 - 5):
                        _concurrent_same_pier_tsa += 1
                except Exception:
                    pass
        _tsa_cap_exceeded = _concurrent_same_pier_tsa >= 4
        conflict = False
        for (_ps, _pe, _role, _gate) in _my_tasks:
            try:
                ps_m = time_to_minutes(_ps)
                pe_m = time_to_minutes(_pe)
                if pe_m < ps_m:
                    pe_m += 1440  # existing task crosses midnight
                buf = 5
                if not (ts_m >= pe_m + buf or te_m <= ps_m - buf):
                    # A מפקח TSA may legitimately hold several overlapping
                    # tasks — concurrent coverage of nearby gates in the SAME
                    # PIER (שלוחה, e.g. B/C/D/E — not the same terminal
                    # number). Mirrors is_available()'s own TSA-same-pier
                    # exception, which this candidate search didn't know
                    # about at all — a TSA inspector already legitimately
                    # covering a same-pier flight was wrongly excluded from
                    # the candidate list for another overlapping same-pier
                    # slot (found via real 12.07 data alongside the same gap
                    # in force_assign_worker, user rule 2026-09-05).
                    if (_slot_is_tsa and not _tsa_cap_exceeded and _role == "מפקח TSA"
                            and _swap_term and get_terminal(clean_text(_gate)) == _swap_term):
                        continue
                    conflict = True
                    break
            except Exception:
                pass
        if not conflict:
            results.append(name)

    return _managers_last(results)

def get_extendable_candidates_for_swap(schedule_df, employees_df, flight_num, role_base, task_idx):
    """Near-miss candidates for a swap: certified & conflict-free workers whose
    shift COVERS the task's start but ends ≤ 30 min BEFORE the task's end —
    i.e. they could fill the slot only if willing to extend their shift a few
    minutes. Returned SEPARATELY from get_qualified_candidates_for_swap (which
    hard-excludes them) so the UI can offer them as an "ask the employee first"
    option (user rule 2026-07-13).

    Also returns duty-blocked workers ("מזכירת DKK") whose shift covers the task
    outright — a different reason for the same "ask first" treatment.

    Returns list of (name, gap_minutes, duty_block) — duty_block is "" for the
    ordinary shift-extension case, otherwise the posting they'd be pulled off."""
    task_row = schedule_df.loc[task_idx]
    start_str = str(task_row.get("התחלה", ""))
    end_str   = str(task_row.get("סיום",   ""))

    role_col_map = {
        "ראש צוות": "ראש צוות", "דייל": "דייל", "מתאם תורים": "מתאם תורים",
        "מפקח TSA": "מפקח TSA", "שומר TSA": "שומר TSA",
        "טרייני רצ": "טרייני רצ", "טרייני ר״צ": "טרייני רצ",
    }
    col = role_col_map.get(normalize_role_label(role_base), role_base)
    if col not in employees_df.columns:
        return []
    if not is_time_text(start_str) or not is_time_text(end_str):
        return []

    certified = employees_df[employees_df[col].astype(str).str.strip() == "כן"].copy()

    # מפקח TSA: same מחלקה מקורית restriction as get_qualified_candidates_for_swap.
    if col == "מפקח TSA" and "מחלקה מקורית" in certified.columns:
        # An EMPTY מחלקה מקורית means "no other department" — but pandas reads
        # a blank cell as NaN, and str(NaN) is the literal "nan", so comparing
        # to "" excluded 371 of 376 workers and left the inspector swap lists
        # all but permanently empty (found via real data 2026-08-09).
        certified = certified[
            certified["מחלקה מקורית"].apply(lambda v: clean_text(v) == "")
        ]

    flight_tasks = schedule_df[
        (schedule_df["טיסה"].astype(str).str.strip() == str(flight_num).strip()) &
        (schedule_df.index != task_idx)
    ]
    already_on_flight = set(
        n for n in flight_tasks["עובד"].astype(str).str.strip().tolist()
        if n and not n.startswith("❌")
    )
    certified = certified[~certified["שם"].astype(str).str.strip().isin(already_on_flight)]
    certified = _drop_blocked_managers(
        certified, role_base, to_datetime_time(start_str), to_datetime_time(end_str))

    ts_m = time_to_minutes(start_str)
    te_m = time_to_minutes(end_str)
    if te_m < ts_m:
        te_m += 1440

    other_tasks = schedule_df[schedule_df.index != task_idx].copy()
    other_tasks = other_tasks[other_tasks["התחלה"].astype(str).str.strip() != ""]

    results = []
    for _, emp_row in certified.iterrows():
        name = str(emp_row.get("שם", "")).strip()
        ss = clean_text(emp_row.get("תחילת משמרת", ""))
        se = clean_text(emp_row.get("סוף משמרת", ""))
        if not is_time_text(ss) or not is_time_text(se):
            continue
        _ss_m = time_to_minutes(ss)
        _se_m = time_to_minutes(se)
        _se_norm = _se_m if _se_m > _ss_m else _se_m + 1440
        _ts_norm = ts_m if ts_m >= _ss_m else ts_m + 1440
        _te_norm = te_m if te_m >= _ts_norm else te_m + 1440
        # shift must cover the START, and fall ≤ 30 min short of the END
        if not (_ss_m <= _ts_norm <= _se_norm):
            continue
        if _is_blocked_by_window(emp_row, ts_m, te_m, role=normalize_role_label(role_base)):
            continue
        _gap = _te_norm - _se_norm
        # A duty-blocked worker ("מזכירת DKK") is the other kind of
        # approval-required candidate: their shift covers the task in full,
        # but they are posted to a non-flight job, so they may only be used
        # once someone approves pulling them off it (user rule 2026-08-09).
        _duty_blocked = (
            DUTY_BLOCK_COL in emp_row.index
            and clean_text(str(emp_row.get(DUTY_BLOCK_COL, ""))) != ""
        )
        if _duty_blocked:
            if _gap > 30:
                continue
            _gap = max(_gap, 0)
        elif not (0 < _gap <= 30):
            continue
        # no conflict with the worker's other tasks — but a TSA inspector /
        # guard may hold overlapping tasks in the SAME pier (parallel coverage,
        # mirrors is_available), so a same-pier same-role overlap is NOT a
        # conflict (lets a worker already posted at pier C be offered to extend
        # onto another pier-C flight).
        _this_pier = get_terminal(clean_text(str(task_row.get("_gate", ""))))
        _tsa_role = normalize_role_label(role_base) in {"מפקח TSA", "שומר TSA"}
        person_tasks = other_tasks[other_tasks["עובד"].astype(str) == name]
        conflict = False
        for _, pt in person_tasks.iterrows():
            try:
                ps_m = time_to_minutes(str(pt["התחלה"]))
                pe_m = time_to_minutes(str(pt["סיום"]))
                if pe_m < ps_m:
                    pe_m += 1440
                if not (ts_m >= pe_m + 5 or te_m <= ps_m - 5):
                    # overlap — for TSA roles, a same-role TSA task in the SAME
                    # pier is parallel coverage (not a conflict). When THIS
                    # slot's gate isn't assigned yet (_this_pier == "" — common
                    # for late-night flights), the pier can't be compared, so
                    # allow the overlap and let the scheduler/user resolve the
                    # physical placement once a gate is set (the worker is still
                    # only OFFERED, pending their approval).
                    if (_tsa_role
                            and str(pt.get("תפקיד בסיס", "")) in {"מפקח TSA", "שומר TSA"}
                            and (not _this_pier
                                 or get_terminal(clean_text(str(pt.get("_gate", "")))) == _this_pier)):
                        continue
                    conflict = True
                    break
            except Exception:
                pass
        if not conflict:
            results.append((
                name, _gap,
                clean_text(str(emp_row.get(DUTY_BLOCK_COL, ""))) if _duty_blocked else "",
            ))

    return results


def do_swap(schedule_df, task_idx, new_worker, displaced_action, displaced_target_flight=None):
    df = schedule_df.copy()
    old_worker = str(df.at[task_idx, "עובד"])
    df.at[task_idx, "עובד"] = new_worker

    if displaced_action == "move" and displaced_target_flight:
        role_base = str(df.at[task_idx, "תפקיד בסיס"])
        target_mask = (
            (df["טיסה"].astype(str).str.strip() == displaced_target_flight.strip()) &
            (df["תפקיד בסיס"].astype(str) == role_base) &
            (df["עובד"].astype(str).str.contains("❌"))
        )
        target_slots = df[target_mask]
        if not target_slots.empty:
            first_slot = target_slots.index[0]
            df.at[first_slot, "עובד"] = old_worker

    return df


def force_assign_worker(schedule_df, slot_idx, worker, employees_df, keep_prior=True,
                         reason_tag=None):
    """Manual override for the missing-roles diagnostic buttons.

    FORCE `worker` onto the (currently ❌) task at slot_idx even if they are
    busy elsewhere. Two modes:

    * keep_prior=True — "הכרח שיבוץ" / late arrival (user rule 2026-07-22).
      A task the worker holds that STARTS BEFORE this slot and overlaps it (a
      prior flight running a few minutes past the required floor time) is KEPT
      — the worker stays on it and simply reaches this slot late. The forced
      slot is tagged "הגעה באיחור" so the workflow/schedule views can show it.
      Only tasks that overlap AT OR AFTER this slot's start (the rest of the
      worker's day) are vacated and best-effort back-filled.

    * keep_prior=False — "שבץ במקום" / move in place (user rule 2026-07-22).
      The worker is MOVED here: ALL of their overlapping tasks (including the
      prior one that would otherwise cause a late arrival) are vacated and
      best-effort back-filled — no late-arrival tag. E.g. a worker doing דייל
      on an earlier flight is pulled off it to cover ר"צ here instead.

    reason_tag: optional override for the resulting "סיבה" text — used by the
    UI for a distinct override category (e.g. a worker rejected only for
    "הפרדת טרמינלים" — assigned to the OTHER terminal at this hour but with no
    actual overlapping task, forced here as a last resort, delaying their
    terminal transfer instead of a specific flight). When set, it wins over
    the auto-generated label EXCEPT when a real late-arrival/overlap is
    detected (that flag always takes priority — it describes a concrete
    schedule conflict the caller may not have anticipated).

    Either way each vacated slot is best-effort back-filled with a free
    qualified worker (else left ❌ — the override may simply move the shortage).
    Returns the updated schedule_df.
    """
    df = schedule_df.copy()
    if slot_idx not in df.index:
        return df
    if "הגעה באיחור" not in df.columns:
        df["הגעה באיחור"] = False
    _s = _ar_tm(df.at[slot_idx, "התחלה"])
    _e = _ar_tm(df.at[slot_idx, "סיום"])

    # 1. Classify the worker's OTHER tasks relative to this slot (midnight-safe:
    #    normalize each task's start to within +/-720 min of the slot start so an
    #    evening + after-midnight pair is compared on one continuous timeline).
    #    keep_prior=True: a prior overlapping task (starts before _s) is KEPT
    #    (→ late arrival); an at/after overlapping task is vacated.
    #    keep_prior=False: EVERY overlapping task is vacated (move in place).
    # A "מפקח TSA" may legitimately hold several overlapping tasks at once —
    # concurrent coverage of nearby gates in the SAME PIER (שלוחה, e.g. B/C/
    # D/E — NOT the same terminal number; user correction 2026-09-05). This
    # mirrors is_available()'s own TSA-same-pier exception, which the plain
    # overlap loop below didn't know about at all: it was flagging a
    # legitimate parallel TSA assignment as a conflict — either mislabeling
    # it "late arrival" (keep_prior=True) or wrongly VACATING it outright
    # (keep_prior=False/at-or-after case) — found via real 12.07 data:
    # TL#1 already legitimately covers LY017+LY027 (both gate
    # D, overlapping) when force-assigned onto LY005.
    _slot_role = str(df.at[slot_idx, "תפקיד בסיס"]).strip()
    _slot_is_tsa = _slot_role == "מפקח TSA"
    _slot_pier = get_terminal(clean_text(str(df.at[slot_idx, "_gate"]))) if "_gate" in df.columns else ""

    def _tsa_parallel_ok(_other_idx):
        if not (_slot_is_tsa and _slot_pier):
            return False
        if str(df.at[_other_idx, "תפקיד בסיס"]).strip() != "מפקח TSA":
            return False
        _other_pier = get_terminal(clean_text(str(df.at[_other_idx, "_gate"]))) if "_gate" in df.columns else ""
        return bool(_other_pier) and _other_pier == _slot_pier

    def _overlaps_slot(_idx):
        _rs = _ar_tm(df.at[_idx, "התחלה"]); _re = _ar_tm(df.at[_idx, "סיום"])
        if _rs is None or _re is None or _s is None or _e is None:
            return False
        _e_adj0 = _e if _e > _s else _e + 1440
        while _rs < _s - 720:
            _rs += 1440
        while _rs >= _s + 720:
            _rs -= 1440
        _re_adj0 = _re
        while _re_adj0 < _rs:
            _re_adj0 += 1440
        return not (_re_adj0 <= _s or _rs >= _e_adj0)

    _worker_idxs = df.index[df["עובד"].astype(str).str.strip() == str(worker).strip()].tolist()
    # is_available() also caps concurrent same-pier TSA coverage at 4 flights
    # — mirror that here too, so an unlimited number of same-pier TSA tasks
    # can't be stacked on one inspector via a force-assign with no warning
    # at all (user rule 2026-09-05). If the worker already has 4+ same-pier
    # TSA tasks overlapping this slot, the parallel-coverage exemption is
    # withdrawn entirely and normal conflict handling (late-arrival/vacate)
    # applies instead — same semantics as is_available() rejecting the 5th.
    _concurrent_same_pier_tsa = sum(
        1 for _widx in _worker_idxs
        if _widx != slot_idx and _tsa_parallel_ok(_widx) and _overlaps_slot(_widx)
    )
    _tsa_cap_exceeded = _concurrent_same_pier_tsa >= 4

    _vacated = []
    _late_arrival = False
    if _s is not None and _e is not None:
        _e_adj = _e if _e > _s else _e + 1440
        for _idx in _worker_idxs:
            if _idx == slot_idx:
                continue
            _rs = _ar_tm(df.at[_idx, "התחלה"]); _re = _ar_tm(df.at[_idx, "סיום"])
            if _rs is None or _re is None:
                continue
            # bring _rs into [_s-720, _s+720)
            while _rs < _s - 720:
                _rs += 1440
            while _rs >= _s + 720:
                _rs -= 1440
            _re_adj = _re
            while _re_adj < _rs:
                _re_adj += 1440
            _ov = not (_re_adj <= _s or _rs >= _e_adj)
            if not _ov:
                continue
            if _tsa_parallel_ok(_idx) and not _tsa_cap_exceeded:
                # Legitimate concurrent TSA coverage in the same pier — not a
                # real conflict at all, so neither "late arrival" nor a vacate.
                continue
            if keep_prior and _rs < _s:
                # Prior flight running past the floor time — keep it, arrive late.
                _late_arrival = True
            else:
                _role = str(df.at[_idx, "תפקיד בסיס"]).strip()
                df.at[_idx, "עובד"] = f"❌ חסר {_role}"
                _vacated.append(_idx)

    # 2. Assign the worker to the forced slot.
    df.at[slot_idx, "עובד"] = str(worker).strip()
    df.at[slot_idx, "הגעה באיחור"] = bool(_late_arrival)
    if "סיבה" in df.columns:
        if _late_arrival:
            df.at[slot_idx, "סיבה"] = "הכרח שיבוץ - הגעה באיחור"
        elif reason_tag:
            df.at[slot_idx, "סיבה"] = reason_tag
        else:
            df.at[slot_idx, "סיבה"] = "שיבוץ במקום ידני" if not keep_prior else "הכרח שיבוץ ידני"

    # 3. Best-effort back-fill each vacated slot with a free qualified worker.
    for _vidx in _vacated:
        _vrole = str(df.at[_vidx, "תפקיד בסיס"]).strip()
        _vflt = str(df.at[_vidx, "טיסה"]).strip()
        _cands = get_qualified_candidates_for_swap(df, employees_df, _vflt, _vrole, _vidx)
        _cands = [c for c in _cands if str(c).strip() != str(worker).strip()]
        if _cands:
            df.at[_vidx, "עובד"] = _cands[0]
            if "סיבה" in df.columns:
                df.at[_vidx, "סיבה"] = "מחליף לאחר הכרח שיבוץ"

    return df


def _no_preflight_break_needed(emp_row):
    """True for shift types that never need a pre-flight break accommodation:
    a genuine night shift (already assumed to have broken before counter-
    closing) or the LONG 02:00 shift (02:00-11:00 and similar — per
    preferred_break_window_by_shift's own second branch, this one gets a
    normal between-flights break like a 03:30-11:00 shift, no pre-flight
    squeeze). Used to find swap-in candidates for protect_early_shift_breaks."""
    ss = clean_text(emp_row.get("תחילת משמרת", ""))
    se = clean_text(emp_row.get("סוף משמרת", ""))
    if not is_time_text(ss) or not is_time_text(se):
        return False
    if classify_shift(emp_row) == "night":
        return True
    s = time_to_minutes(ss)
    e = time_to_minutes(se)
    if 1 * 60 + 30 <= s <= 2 * 60 + 30 and e > 10 * 60:
        return True
    return False


def protect_early_shift_preflight_breaks(schedule_df, employees_df):
    """02:00-08:30/09:30 workers are entitled to a break before their first
    flight when that flight would otherwise block their normal 03:30
    proactive break — but a chip merely SAYING they took a break while
    they're still literally on the flight's roster is a lie (user-reported
    2026-08-02: the workflow-tab "הפסקה לפני טיסה" chip showed a break that
    never actually happened — the worker was still assigned to that exact
    flight at that exact time). This pass does the real thing: reassigns that
    first flight to a worker who does NOT need this accommodation themselves
    (a night-shift worker already up, or a long 02:00-11:00-type shift — see
    _no_preflight_break_needed), freeing the original worker for a genuine
    break before whatever their (now later, or now-first) task is.

    Runs after the main build/consolidation passes so it sees the FINAL
    roster of who's actually free at that time.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    emp_by_name = {
        clean_text(str(r.get("שם", ""))): r
        for _, r in employees_df.iterrows()
        if clean_text(str(r.get("שם", "")))
    }

    def _tmin(t):
        try:
            h, m = str(t).split(":")[:2]
            return int(h) * 60 + int(m)
        except (ValueError, IndexError):
            return None

    for name, emp_row in list(emp_by_name.items()):
        ss = clean_text(emp_row.get("תחילת משמרת", ""))
        se = clean_text(emp_row.get("סוף משמרת", ""))
        if not is_time_text(ss) or not is_time_text(se):
            continue
        s_m = time_to_minutes(ss)
        e_m = time_to_minutes(se)
        if not (1 * 60 + 30 <= s_m <= 2 * 60 + 30 and 8 * 60 <= e_m <= 10 * 60):
            continue  # not the 02:00-08:30/09:30 family

        my_tasks = df[
            (df["עובד"].astype(str).str.strip() == name)
            & (~df["עובד"].astype(str).str.contains("❌", na=False))
        ]
        if my_tasks.empty:
            continue
        _starts = my_tasks["התחלה"].apply(_tmin)
        if _starts.isna().all():
            continue
        first_idx = _starts.idxmin()
        first_start_m = _starts.loc[first_idx]
        if first_start_m is None:
            continue

        required_break_dur = required_break(emp_row) or 45
        if first_start_m >= (3 * 60 + 30 + required_break_dur):
            continue  # first flight is late enough — normal 03:30 break is fine

        flight_num = str(df.at[first_idx, "טיסה"]).strip()
        role_base = str(df.at[first_idx, "תפקיד בסיס"]).strip()
        candidates = get_qualified_candidates_for_swap(df, employees_df, flight_num, role_base, first_idx)
        replacement = next(
            (c for c in candidates if _no_preflight_break_needed(emp_by_name.get(clean_text(str(c)), {}))),
            None,
        )
        if replacement is None:
            continue  # no eligible swap-in — leave as-is (workflow-tab chip is still the best available signal)

        df.at[first_idx, "עובד"] = replacement
        if "סיבה" in df.columns:
            df.at[first_idx, "סיבה"] = "הוחלף כדי לאפשר הפסקה מלאה לפני טיסה למשמרת 02:00 המוקדמת"

    # ── Second pass: no gap ANYWHERE (not even the tail) reaches the full
    # break duration — the first-task fix above only helps when the FIRST
    # flight is what's blocking the normal 03:30 slot; it doesn't help a
    # worker whose entire day is back-to-back-ish flights with the tail
    # itself too short (user rule 2026-08-05, found via real data: TL#31, 02:00-09:30, every gap under 45 min including the 25-min tail —
    # she has NO real break opportunity at all with her current roster).
    # Try swapping her out of ONE flight (preferring a דייל role, per the
    # user's own suggestion, before trying ר"צ) to merge two gaps into one
    # that's actually long enough, then give the freed time to a break.
    for name, emp_row in list(emp_by_name.items()):
        ss = clean_text(emp_row.get("תחילת משמרת", ""))
        se = clean_text(emp_row.get("סוף משמרת", ""))
        if not is_time_text(ss) or not is_time_text(se):
            continue
        s_m = time_to_minutes(ss)
        e_m = time_to_minutes(se)
        if not (1 * 60 + 30 <= s_m <= 2 * 60 + 30 and 8 * 60 <= e_m <= 10 * 60):
            continue

        my_tasks = df[
            (df["עובד"].astype(str).str.strip() == name)
            & (~df["עובד"].astype(str).str.contains("❌", na=False))
        ].copy()
        if len(my_tasks) < 2:
            continue
        my_tasks["gap_s"] = my_tasks["התחלה"].apply(_tmin)
        my_tasks["gap_e"] = my_tasks["סיום"].apply(_tmin)
        my_tasks = my_tasks.dropna(subset=["gap_s", "gap_e"]).sort_values("gap_s")
        if len(my_tasks) < 2:
            continue

        required_break_dur = required_break(emp_row) or 45
        _rows = list(my_tasks.itertuples())

        def _has_adequate_gap(_rows_list):
            _prev_e = None
            for _r in _rows_list:
                if _prev_e is not None and (_r.gap_s - _prev_e) >= required_break_dur:
                    return True
                _prev_e = _r.gap_e
            if _prev_e is not None and (e_m - _prev_e) % 1440 >= required_break_dur:
                return True
            return False

        if _has_adequate_gap(_rows):
            continue  # she already has a real opportunity somewhere

        # Try removing each task (דייל roles first, per the user's own
        # suggestion) and check whether doing so merges its neighbours into
        # one adequate gap — only then is finding a swap-in worth trying.
        # itertuples() mangles Hebrew column names unpredictably across
        # pandas versions — index back into the DataFrame by row Index
        # instead of trusting a generated attribute name.
        _candidates_order = [
            idx for idx in my_tasks.index
            if str(my_tasks.at[idx, "תפקיד בסיס"]).strip() == "דייל"
        ] + [
            idx for idx in my_tasks.index
            if str(my_tasks.at[idx, "תפקיד בסיס"]).strip() != "דייל"
        ]

        for _try_idx in _candidates_order:
            _remaining = [r for r in _rows if r.Index != _try_idx]
            if not _has_adequate_gap(_remaining):
                continue
            _flt = str(df.at[_try_idx, "טיסה"]).strip()
            _role = str(df.at[_try_idx, "תפקיד בסיס"]).strip()
            _cands2 = get_qualified_candidates_for_swap(df, employees_df, _flt, _role, _try_idx)
            if not _cands2:
                continue
            df.at[_try_idx, "עובד"] = _cands2[0]
            if "סיבה" in df.columns:
                df.at[_try_idx, "סיבה"] = "הוחלף כדי לפתוח חלון הפסקה אמיתי למשמרת 02:00 המוקדמת"
            break

    return df


def pair_trainee_attendants(schedule_df, employees_df):
    """דייל-בטרייני (a NEW attendant) shadows their named mentor on EXACTLY the
    mentor's flights — identical tasks (user rule 2026-07-22: "טרייני = דייל חדש
    שמוצמד לדייל ותיק, כל המשימות שלהם זהות"). Distinct from a טרייני-ר"צ (a
    veteran in a TL-handover) who is NOT bound to one mentor and is left alone.

    Per the user's refinement (2026-07-22): the trainee FILLS part of the flight's
    REQUIRED דייל quota — NOT an added/extra row — and the flight's דייל crew may
    never be ONLY restricted workers (a trainee + a פורש/ילד-עובדים), so at least
    one regular veteran דייל must remain. For each trainee (identified by
    "דייל בטרייני"=="כן" with a "חונך דייל" name):
      * Remove the trainee from any flight the mentor is NOT on (→ ❌).
      * On every mentor flight, seat the trainee in a REQUIRED דייל slot:
          - fill an open ❌ דייל slot; else
          - displace one veteran (non-restricted, non-mentor) דייל while keeping
            ≥1 veteran דייל on the flight, and best-effort re-seat that veteran in
            an open ❌ דייל slot elsewhere they're free for (else they're freed).
        Never add an extra row, and never leave the flight's דיילים all-restricted.

    Runs LAST (after upgrade_teamleads / continuity / gap-fill) so it mirrors the
    mentor's FINAL flights. Returns the updated schedule_df.
    """
    if "דייל בטרייני" not in employees_df.columns or "חונך דייל" not in employees_df.columns:
        return schedule_df.copy()
    # Normalize Arrow-backed dtypes (Streamlit round-trips return ArrowDtype)
    # BEFORE the heavy scalar .at[] access below — each such lookup on an
    # Arrow-backed column is far slower than plain object dtype, and this
    # function does thousands of them (found via real data 2026-07-24: this
    # pass alone cost ~1-2s of a build that used to take a fraction of that).
    try:
        df = schedule_df.astype(object)
    except Exception:
        df = schedule_df.copy()
    try:
        employees_df = employees_df.astype(object)
    except Exception:
        pass

    def _wordset(nm):
        return frozenset(name_key(_w) for _w in clean_text(str(nm)).split() if len(_w) > 1)

    # Roster-name → row lookup (word-set keyed) for restricted classification.
    _emp_by_set = {}
    for _, _r in employees_df.iterrows():
        _ws = _wordset(_r.get("שם", ""))
        if _ws:
            _emp_by_set[_ws] = _r

    def _is_restricted_cell(worker_cell):
        w = clean_text(str(worker_cell))
        if "❌" in w or not w:
            return False
        _r = _emp_by_set.get(_wordset(w))
        return bool(_r is not None and _is_broadly_restricted(_r))

    def _tmin(t):
        try:
            _h, _m = str(t).split(":")[:2]
            return int(_h) * 60 + int(_m)
        except (ValueError, IndexError):
            return None

    # Collect trainees and their mentor word-sets (permutation-invariant match).
    _trainees = []
    for _, _r in employees_df.iterrows():
        if clean_text(str(_r.get("דייל בטרייני", ""))) != "כן":
            continue
        _mentor = clean_text(str(_r.get("חונך דייל", "")))
        if not _mentor:
            continue
        _trainees.append((clean_text(_r.get("שם", "")), _wordset(_r.get("שם", "")), _wordset(_mentor), _r))
    if not _trainees:
        return df
    # mentor word-set -> the trainees attached to them (a mentor may have more
    # than one). Used by step 2c below.
    _trainees_by_mentor = {}
    for _n, _ts, _ms, _tr in _trainees:
        _trainees_by_mentor.setdefault(_ms, []).append(_ts)

    def _matches(worker_cell, wset):
        w = clean_text(str(worker_cell))
        if "❌" in w or not w:
            return False
        return _wordset(w) == wset

    # ── Incremental bookkeeping (replaces repeated O(n)/O(n²) df.index scans)
    # This pass used to re-scan the WHOLE schedule for every trainee (mentor-
    # flight lookup), every mentor-flight (flight→rows lookup), and — worst of
    # all — the veteran re-seat step scanned every row TWICE per displaced
    # veteran (open-slot search × busy-check). On a real full-day schedule
    # (362 rows) with several dozen trainee/mentor pairs this measured 6.8s
    # alone (found via real data 2026-07-24) vs a fraction of a second once
    # these lookups are precomputed/maintained instead of rebuilt each time:
    #   _rows_by_flight   : flight number -> [row idx]            (static)
    #   _row_time         : row idx -> (start_min, end_min)        (static)
    #   _tasks_by_ws      : worker word-set -> {row idx currently assigned}
    #   _open_dil         : {row idx} currently ❌ AND role == דייל
    # The last two are updated in place on every assignment change below.
    _rows_by_flight = {}
    _row_time = {}
    _tasks_by_ws = {}
    _open_dil = set()
    for _i in df.index:
        _fn0 = str(df.at[_i, "טיסה"]).strip()
        _rows_by_flight.setdefault(_fn0, []).append(_i)
        _row_time[_i] = (_tmin(df.at[_i, "התחלה"]), _tmin(df.at[_i, "סיום"]))
        _w0 = clean_text(str(df.at[_i, "עובד"]))
        if "❌" in _w0 or not _w0:
            if str(df.at[_i, "תפקיד בסיס"]).strip() == "דייל":
                _open_dil.add(_i)
        else:
            _tasks_by_ws.setdefault(_wordset(_w0), set()).add(_i)

    def _assign(idx, name):
        """Set df at idx to `name`, keeping _tasks_by_ws/_open_dil in sync.

        Also clears any CURRENT occupant's stale index reference — this
        matters for the displacement path (step 2b), which assigns the
        trainee directly over a veteran's row without a separate _vacate call
        first; without this, _tasks_by_ws would keep pointing the displaced
        veteran at a slot they no longer hold."""
        _cur = clean_text(str(df.at[idx, "עובד"]))
        if _cur and "❌" not in _cur:
            _tasks_by_ws.get(_wordset(_cur), set()).discard(idx)
        df.at[idx, "עובד"] = name
        _tasks_by_ws.setdefault(_wordset(name), set()).add(idx)
        _open_dil.discard(idx)

    def _vacate(idx):
        """Clear idx to ❌, keeping _tasks_by_ws/_open_dil in sync."""
        _old = clean_text(str(df.at[idx, "עובד"]))
        _role0 = str(df.at[idx, "תפקיד בסיס"]).strip() or "דייל"
        df.at[idx, "עובד"] = f"❌ חסר {_role0}"
        if _old and "❌" not in _old:
            _tasks_by_ws.get(_wordset(_old), set()).discard(idx)
        if _role0 == "דייל":
            _open_dil.add(idx)

    for _tr_name, _tr_set, _mentor_set, _tr_row in _trainees:
        if not _tr_set or not _mentor_set:
            continue
        # The mentor's flights — keyed by FLIGHT NUMBER ONLY, not (flight,
        # start-time): different roles on the same flight have different start
        # times (e.g. TSA guard 23:00, ר"צ/מתאם 23:45, דייל 23:55 — all one
        # flight, LY025). Keying on the mentor's own row's start-time missed
        # every דייל row entirely (found via real data 2026-07-22 — trainee-agent#4 never got seated because her mentor's ר"צ row starts 10 min
        # before the דייל rows on the same flight).
        _mentor_flights = {
            str(df.at[_i, "טיסה"]).strip()
            for _i in _tasks_by_ws.get(_mentor_set, set())
        }

        # 1. Remove the trainee from any flight the mentor is NOT on, then
        #    best-effort back-fill that slot with another qualified, free
        #    worker (mirrors force_assign_worker's backfill) — otherwise the
        #    slot is left permanently ❌ even when ✅-available candidates
        #    exist, since nothing downstream revisits it (found via real data
        #    2026-07-22: LY009 שומר TSA stayed ❌ after its trainee was pulled
        #    off to shadow her mentor elsewhere, despite 8 available candidates
        #    showing in the diagnostic).
        #
        #    EXCEPT a שומר TSA slot: user rule — the ONLY situation where a
        #    trainee may be separated from their mentor at all is to USE the
        #    trainee AS A GUARD (this mirrors tsa_guard_priority's own tier-1
        #    ranking for trainees in sort_candidates). Pulling her off it here
        #    defeats that placement and forces a re-fill that ignores the
        #    early-shift/restricted-worker priority the initial guard
        #    assignment used, handing the slot to a worse (e.g. day-shift)
        #    candidate instead — found via real 12.07.2026 data: trainee-agent#4 (02:00 starter, guard-qualified) was pulled off LY007's
        #    שומר TSA slot to shadow her mentor, and the backfill gave the
        #    guard slot to agent#44 (day shift, 08:00-17:00) instead.
        for _i in list(_tasks_by_ws.get(_tr_set, set())):
            _fk = str(df.at[_i, "טיסה"]).strip()
            if _fk in _mentor_flights:
                continue
            _role = str(df.at[_i, "תפקיד בסיס"]).strip() or "דייל"
            if _role == "שומר TSA":
                continue
            _vacate(_i)
            _fill_cands = get_qualified_candidates_for_swap(df, employees_df, _fk, _role, _i)
            _fill_cands = [c for c in _fill_cands if _wordset(c) != _tr_set]
            if _fill_cands:
                _assign(_i, _fill_cands[0])
                if "סיבה" in df.columns:
                    df.at[_i, "סיבה"] = "מחליף לאחר הסרת טרייני שאינו מלווה חונך כאן"

        def _tr_conflicts(idx):
            """Does seating the trainee at `idx` overlap a task she ALREADY
            holds (now possibly a שומר TSA slot surviving step 1 above, which
            step 2 never checked against before)?"""
            _s, _e = _row_time.get(idx, (None, None))
            if _s is None or _e is None:
                return False
            _e2 = _e if _e > _s else _e + 1440
            for _k in _tasks_by_ws.get(_tr_set, set()):
                if _k == idx:
                    continue
                _ks, _ke = _row_time.get(_k, (None, None))
                if _ks is None or _ke is None:
                    continue
                _ke2 = _ke if _ke > _ks else _ke + 1440
                if not (_e2 <= _ks or _s >= _ke2):
                    return True
            # A trainee posted to a חסימות window (training course, refresher
            # etc.) is not free to be seated here either — this whole
            # trainee-seating path bypassed get_qualified_candidates_for_swap
            # entirely, so it never checked חסימות at all (user rule
            # 2026-09-05, found via real 16.07 data alongside the same gap
            # elsewhere in the pipeline).
            if _is_blocked_by_window(_tr_row, _s, _e2, role="דייל"):
                return True
            return False

        # 2. Seat the trainee in a REQUIRED דייל slot on every mentor flight.
        for _fn in _mentor_flights:
            _flt_idx = _rows_by_flight.get(_fn, [])
            if any(_matches(df.at[_i, "עובד"], _tr_set) for _i in _flt_idx):
                continue  # already shadowing this flight
            _dil = [_i for _i in _flt_idx if str(df.at[_i, "תפקיד בסיס"]).strip() == "דייל"]
            if not _dil:
                continue  # flight has no דייל quota — nothing to fill

            # Restricted-worker quota for this flight (user rule 2026-07-22):
            # counted across BOTH דייל and שומר-TSA slots. total≤3 → max 1
            # restricted (2/3-attendant flights); total≥4 (USA ×5) → max 2.
            # A דייל-בטרייני is a restricted worker, so seating the trainee must
            # not push the flight's restricted count past this cap.
            _att_idx = [
                _i for _i in _flt_idx
                if str(df.at[_i, "תפקיד בסיס"]).strip() in ("דייל", "שומר TSA")
            ]
            _total_att = len(_att_idx)
            _max_restr = 0 if _total_att <= 1 else (1 if _total_att <= 3 else 2)

            def _restr_count(exclude_idx=None):
                _c = 0
                for _i in _att_idx:
                    if _i == exclude_idx:
                        continue
                    _w = df.at[_i, "עובד"]
                    if "❌" in str(_w) or not str(_w).strip():
                        continue
                    if _is_restricted_cell(_w):
                        _c += 1
                return _c

            # a. Open ❌ דייל slot — fill it if the restricted quota still permits
            #    ADDING the trainee (a restricted worker → +1), and it doesn't
            #    clash with a task she already holds (e.g. a שומר TSA slot).
            _open = [_i for _i in _dil if _i in _open_dil and not _tr_conflicts(_i)]
            if _open and _restr_count(exclude_idx=_open[0]) + 1 <= _max_restr:
                _assign(_open[0], _tr_name)
                if "סיבה" in df.columns:
                    df.at[_open[0], "סיבה"] = "דייל בטרייני - מלווה חונך"
                continue

            # b. No open slot → displace one veteran (non-restricted, non-mentor)
            #    דייל, provided replacing them with the trainee keeps the flight
            #    within the restricted quota.
            _disp = None
            for _i in _dil:
                _w = df.at[_i, "עובד"]
                if "❌" in str(_w) or not str(_w).strip():
                    continue
                if _is_restricted_cell(_w) or _matches(_w, _mentor_set):
                    continue  # never bump a restricted worker or the mentor
                if _tr_conflicts(_i):
                    continue  # trainee already holds an overlapping task
                if _restr_count(exclude_idx=_i) + 1 <= _max_restr:
                    _disp = _i
                    break
            if _disp is None:
                # c. The flight genuinely has no seat for the trainee — every
                #    דייל slot is held by the mentor or by another restricted
                #    worker who may not be bumped (typically ANOTHER pair's
                #    trainee; per the user rule 2026-08-06 a team-lead's trainee
                #    counts as one of the flight's required דיילים, so their
                #    seat is legitimate and must not be taken). The pair's tasks
                #    must still be IDENTICAL, so pull the MENTOR off this flight
                #    instead and back-fill their slot — better a different
                #    veteran covers it than the pair being split (found via real
                #    30.07 data: LY2521 — TL#39 ר"צ + her trainee trainee-agent#8 in
                #    דייל 1, agent#47 in דייל 2, leaving her own trainee worker#7 סיום with no seat).
                #    Only done when a genuine replacement exists — never trade a
                #    split pair for an uncovered ❌ slot.
                _mentor_rows = [
                    _i for _i in _dil if _matches(df.at[_i, "עובד"], _mentor_set)
                ]
                _tr_row = _emp_by_set.get(_tr_set)
                for _mi in _mentor_rows:
                    # Pulling the mentor only makes sense if the trainee COULD
                    # actually have taken the seat. A trainee who isn't within
                    # shift for this flight (or is busy elsewhere) was never a
                    # candidate — vacating the mentor would just split an
                    # unrelated pair for nothing (found immediately in testing:
                    # trainee-agent#2, shift 10:00-11:00, is worker#7 סיום's own
                    # trainee — his unseatable 05:10 "mentor flight" pulled שמחה
                    # off LY353, breaking HER pairing with agent#47).
                    if _tr_row is None:
                        break
                    _mi_s = to_datetime_time(str(df.at[_mi, "התחלה"]))
                    _mi_e = to_datetime_time(str(df.at[_mi, "סיום"]))
                    if _mi_s is None or _mi_e is None:
                        continue
                    try:
                        _mi_gt = get_terminal(clean_text(str(df.at[_mi, "_gate"]))) if "_gate" in df.columns else None
                        if not is_within_shift(_tr_row, _mi_s, _mi_e, task_terminal=_mi_gt):
                            continue
                    except Exception:
                        continue
                    _ms_m, _me_m = _row_time.get(_mi, (None, None))
                    if _ms_m is None or _me_m is None:
                        continue
                    _tr_busy = False
                    for _k in _tasks_by_ws.get(_tr_set, set()):
                        _ks, _ke = _row_time.get(_k, (None, None))
                        if _ks is None or _ke is None:
                            continue
                        _ke2 = _ke if _ke > _ks else _ke + 1440
                        _me2 = _me_m if _me_m > _ms_m else _me_m + 1440
                        if not (_me2 <= _ks or _ms_m >= _ke2):
                            _tr_busy = True
                            break
                    if _tr_busy:
                        continue
                    # d. Find where the pair would go TOGETHER: another flight
                    #    with TWO open ❌ דייל slots both are free and
                    #    within-shift for (user rule 2026-08-06: "יש להעביר את
                    #    agent#47 לטיסה אחרת שבה דרושים שני דיילים, היא +
                    #    הטרייני"). This is resolved BEFORE anything is changed:
                    #    pulling the mentor without a landing spot just strands
                    #    BOTH of them (seen in testing — agent#47 lost LY279 and
                    #    LY2521 and the pair ended the day with zero tasks).
                    _mentor_row_e = _emp_by_set.get(_mentor_set)
                    _mentor_name = clean_text(str(_mentor_row_e.get("שם", ""))) if _mentor_row_e is not None else ""
                    if not _mentor_name:
                        continue
                    _reloc = None
                    for _cand_fn, _cand_rows in _rows_by_flight.items():
                        if _cand_fn == _fn:
                            continue
                        _cand_open = [
                            _i for _i in _cand_rows
                            if _i in _open_dil and str(df.at[_i, "תפקיד בסיס"]).strip() == "דייל"
                        ]
                        if len(_cand_open) < 2:
                            continue
                        _a, _b = _cand_open[0], _cand_open[1]
                        _as_m, _ae_m = _row_time.get(_a, (None, None))
                        _a_st = to_datetime_time(str(df.at[_a, "התחלה"]))
                        _a_en = to_datetime_time(str(df.at[_a, "סיום"]))
                        if _as_m is None or _ae_m is None or _a_st is None or _a_en is None:
                            continue
                        try:
                            _a_gt = get_terminal(clean_text(str(df.at[_a, "_gate"]))) if "_gate" in df.columns else None
                            if not is_within_shift(_mentor_row_e, _a_st, _a_en, task_terminal=_a_gt):
                                continue
                            if not is_within_shift(_tr_row, _a_st, _a_en, task_terminal=_a_gt):
                                continue
                        except Exception:
                            continue
                        # Free at that hour — the slot being vacated here (_mi)
                        # doesn't count as a clash, it's what we're giving up.
                        _clash = False
                        for _wset in (_mentor_set, _tr_set):
                            for _k in _tasks_by_ws.get(_wset, set()):
                                if _k == _mi:
                                    continue
                                _ks, _ke = _row_time.get(_k, (None, None))
                                if _ks is None or _ke is None:
                                    continue
                                _ke2 = _ke if _ke > _ks else _ke + 1440
                                _ae2 = _ae_m if _ae_m > _as_m else _ae_m + 1440
                                if not (_ae2 <= _ks or _as_m >= _ke2):
                                    _clash = True
                                    break
                            if _clash:
                                break
                        if not _clash:
                            _reloc = (_a, _b)
                            break
                    # _reloc None → no other flight needs two דיילים. That's
                    # fine: the pair simply stays at the counters for the whole
                    # shift (user rule 2026-08-06: "אם אין צורך בהן לטיסה אחרת,
                    # אפשר גם לא להוריד אותן לטיסות בכלל ושיהיו כל המשמרת
                    # בדלפקים"). Keeping the pairing intact outranks utilization
                    # — but never at the cost of an uncovered slot, hence the
                    # replacement requirement just below.
                    _mrole = str(df.at[_mi, "תפקיד בסיס"]).strip() or "דייל"
                    _mcands = get_qualified_candidates_for_swap(df, employees_df, _fn, _mrole, _mi)
                    _mcands = [
                        c for c in _mcands
                        if _wordset(c) != _tr_set and _wordset(c) != _mentor_set
                        and _wordset(c) not in _trainees_by_mentor.get(_mentor_set, [])
                    ]
                    # The replacement must not push the flight past its
                    # restricted-worker cap. Seating the trainee already spends
                    # one restricted slot, so handing the mentor's slot to
                    # another restricted worker can leave a flight with NO
                    # regular attendant at all — which is never allowed (user
                    # 2026-08-06: "אסור לשבץ 2 מתוך 2 עובדים מוחרגים"; found via
                    # real data: LY2521 came out trainee-agent#8 (ט) + restricted-agent#2
                    # (ס) as its only two attendants).
                    _mi_att = [
                        _k for _k in _rows_by_flight.get(_fn, [])
                        if str(df.at[_k, "תפקיד בסיס"]).strip() in ("דייל", "שומר TSA")
                    ]
                    if _mi in _mi_att:
                        _mi_cap = 0 if len(_mi_att) <= 1 else (1 if len(_mi_att) <= 3 else 2)
                        _mi_res = 0
                        for _k in _mi_att:
                            if _k == _mi:
                                continue
                            _kw = clean_text(str(df.at[_k, "עובד"]))
                            if not _kw or "❌" in _kw:
                                continue
                            _kr = _emp_by_set.get(_wordset(_kw))
                            if _kr is not None and _is_broadly_restricted(_kr):
                                _mi_res += 1
                        if _mi_res + 1 > _mi_cap:
                            _mcands = [
                                c for c in _mcands
                                if not (_emp_by_set.get(_wordset(c)) is not None
                                        and _is_broadly_restricted(_emp_by_set[_wordset(c)]))
                            ]
                    if not _mcands:
                        continue  # never trade a split pair for an uncovered ❌
                    _assign(_mi, _mcands[0])
                    if "סיבה" in df.columns:
                        df.at[_mi, "סיבה"] = "הוחלף בחונך כדי לשמור על זהות משימות חונך-טרייני"
                    if _reloc is not None:
                        _assign(_reloc[0], _mentor_name)
                        _assign(_reloc[1], _tr_name)
                        if "סיבה" in df.columns:
                            df.at[_reloc[0], "סיבה"] = "הועבר עם הטרייני לטיסה הדורשת שני דיילים"
                            df.at[_reloc[1], "סיבה"] = "דייל בטרייני - מלווה חונך"
                    break
                continue
            _vet = clean_text(str(df.at[_disp, "עובד"]))
            _assign(_disp, _tr_name)
            if "סיבה" in df.columns:
                df.at[_disp, "סיבה"] = "דייל בטרייני - מלווה חונך"

            # Best-effort: re-seat the displaced veteran in an open ❌ דייל slot
            # elsewhere they're within-shift for and free (no time clash). If
            # none, they are simply freed (they were surplus to the trainee slot).
            # FAIL CLOSED when the veteran's employees_df row can't be resolved
            # (_vet_row is None) or is_within_shift itself errors — previously
            # both cases silently SKIPPED the shift check entirely, letting a
            # veteran get re-seated onto a slot hours past their actual shift
            # end with no validation at all. The check was ALSO silently a
            # complete no-op even when _vet_row WAS found: is_within_shift
            # requires datetime.time objects (task_start.hour/.minute) but this
            # call passed raw "HH:MM" strings, so it threw AttributeError on
            # every single call — always caught by the old bare "except: pass"
            # and treated as "eligible". Fixed by converting via
            # to_datetime_time first (found via real data 2026-07-31: agent#20, shift 21:00-07:00, re-seated onto LY007 09:10-10:15 — over
            # 2 hours after her shift ended).
            _vet_set = _wordset(_vet)
            _vet_row = _emp_by_set.get(_vet_set)
            _vet_tasks = _tasks_by_ws.get(_vet_set, set())
            for _j in list(_open_dil):
                _js, _je = _row_time.get(_j, (None, None))
                if _js is None or _je is None:
                    continue
                if _vet_row is None:
                    continue
                try:
                    _gt = get_terminal(clean_text(str(df.at[_j, "_gate"]))) if "_gate" in df.columns else None
                    _j_start = to_datetime_time(str(df.at[_j, "התחלה"]))
                    _j_end   = to_datetime_time(str(df.at[_j, "סיום"]))
                    if _j_start is None or _j_end is None:
                        continue
                    if not is_within_shift(_vet_row, _j_start, _j_end, task_terminal=_gt):
                        continue
                except Exception:
                    continue
                _busy = False
                for _k in _vet_tasks:
                    _ks, _ke = _row_time.get(_k, (None, None))
                    if _ks is None or _ke is None:
                        continue
                    _ke2 = _ke if _ke > _ks else _ke + 1440
                    _je2 = _je if _je > _js else _je + 1440
                    if not (_je2 <= _ks or _js >= _ke2):
                        _busy = True
                        break
                if _busy:
                    continue
                _assign(_j, _vet)
                if "סיבה" in df.columns:
                    df.at[_j, "סיבה"] = "שובץ מחדש לאחר פינוי מקום לטרייני"
                break

    return df


def fill_idle_gaps(schedule_df, employees_df):
    """Close a long mid-shift gap by handing the POST-GAP flights to a worker
    who has no assignment at all.

    User rule 2026-08-06: "אם למישהו יש 2 משימות לדוגמא, ואז הפסקה ופער גדול עד
    הטיסה הבאה שלו — עדיף לעשות לעובד זה הפסקה וחזרה לדלפקים, או רק חזרה במידה
    וכבר היה בהפסקה, ולתת לעובד אחר שאפילו לא שובץ כלל את הטיסות האלו." So the
    fix is NOT to pull more flights into the gap (an earlier attempt did that
    — it only shuffled work between two busy people and moved the hole around);
    it is to END the gapped worker's day at their cluster and give everything
    after the gap to someone who is sitting at the counters with nothing.

    This closes both problems at once: the gap disappears (the worker breaks
    and returns to the counters), and one of the never-scheduled workers gets
    real work.

    WHAT COUNTS AS A GAP (user rule 2026-08-06 — this is the whole point):
    a real gap is a worker who came down to the flights, took their break
    between flights, and is then left **waiting in the departure hall with
    nothing to do** until the next one. A worker who finishes a block, breaks,
    goes back to the COUNTERS and does check-in there for a few hours before
    coming down again is NOT a gap — that is the normal shape of the day for
    a night worker (night flights → counters → pre-dawn flights) and for the
    long 11-12h shifts (12:30-00:30, 14:00-01:30, …). Three hours is the line:
    at `_LEGIT_GAP`+ those workers genuinely return to counter duty, so the
    gap is left alone; below it they would just be loitering airside.

    Conditions, all required:
      * the gap is over `_MIN_GAP` of genuine idle time, and is NOT a
        legitimate return-to-counters stretch as defined above;
      * EVERY task after the gap can be handed over — a partial hand-off would
        leave the gap in place, so the move is atomic;
      * each receiver currently has ZERO tasks and passes the normal
        qualification / shift / terminal / conflict checks
        (get_qualified_candidates_for_swap);
      * no receiver is used twice within the same hand-off.

    Best-effort and order-stable: biggest gaps are treated first.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    _MIN_GAP = 120
    # 3h+ back at the counters is real check-in work, not idle time
    _LEGIT_GAP = 180
    # A NIGHT shift's night-block → counters → pre-dawn-flights split is the
    # normal shape of its day, so 2h is already a real stretch of counter duty
    # there (user rule 2026-08-06: "תוריד את קו 3 השעות לשעתיים למשמרות לילה"
    # — TL#15, 130 min between her night block and the 03:15 flight).
    _LEGIT_GAP_NIGHT = 120

    def _tm(t):
        v = to_datetime_time(t)
        return v.hour * 60 + v.minute if v is not None else None

    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    def _yn(row, col):
        return clean_text(str(row.get(col, ""))) == "כן"

    def _is_tl(row):
        return _yn(row, "ראש צוות")

    def _has_mentor_tl_on_shift(row):
        """Is there a ר"צ who can actually MENTOR this טרייני-ר"צ — i.e. a
        חונך-רצים / מסמיך-רצים on shift at the SAME terminal whose hours
        overlap theirs? A TL-trainee is only worth floor priority when someone
        can train them; with no mentor on shift they are just a regular
        attendant (user rule 2026-08-06, TL-trainee#1: "האם יש מי שמוגדר
        כר"צ שעושה טרייני ר"צ שעובד בשעות שחופפות לה בטרמינל 1? אם כן רק אז
        יש לתת לה עדיפות... במידה ואין, יש להתייחס אליה כדייל רגיל")."""
        _ss = clean_text(str(row.get("תחילת משמרת", "")))
        _se = clean_text(str(row.get("סוף משמרת", "")))
        if not (is_time_text(_ss) and is_time_text(_se)):
            return False
        _s0 = time_to_minutes(_ss)
        _e0 = _s0 + ((time_to_minutes(_se) - _s0) % 1440 or 1440)
        _term = clean_text(str(row.get("טרמינל", "")))
        for _o in emp_map.values():
            if not (_yn(_o, "ראש צוות") and (_yn(_o, "חונך רצים") or _yn(_o, "מסמיך רצים"))):
                continue
            if str(_o.get("in_daily_excel", "")).strip() not in ("1", "1.0", "True", "כן"):
                continue
            if clean_text(str(_o.get("טרמינל", ""))) != _term:
                continue
            _os = clean_text(str(_o.get("תחילת משמרת", "")))
            _oe = clean_text(str(_o.get("סוף משמרת", "")))
            if not (is_time_text(_os) and is_time_text(_oe)):
                continue
            _s1 = time_to_minutes(_os)
            _e1 = _s1 + ((time_to_minutes(_oe) - _s1) % 1440 or 1440)
            # overlap on a shift-relative timeline (both may cross midnight)
            if min(_e0, _e1) - max(_s0, _s1) > 0:
                return True
        return False

    def _is_floor_priority(row):
        """ר"צ and (mentored) טרייני-ר"צ — workers whose value is TIME ON THE
        FLOOR. A gap of theirs is filled by preemption, never closed by parking
        them at the counters (user rules 2026-08-06: "אין להחזיר ראשי צוותים
        לדלפקים ושוב להוריד אותם לטיסות, אלא אם כן מדובר במשמרת לילה" / for
        TL#41 "יש לנסות לראות האם יש שיבוץ... מכיוון שהוא ר"צ")."""
        if _is_tl(row):
            return True
        return _yn(row, "טרייני רצ") and _has_mentor_tl_on_shift(row)

    def _is_plain_attendant(row):
        """A regular attendant — not a ר"צ, not a runner/TL-trainee, and not a
        דייל-בטרייני. Only these may be preempted to fill a floor-priority
        worker's gap.

        Excluding דייל-בטרייני matters: a trainee's slot is their MENTOR's
        flight, so taking it splits the pair — and `enforce_trainee_pairing`,
        which runs after this pass, then correctly puts the trainee back,
        undoing the gap fill. The two passes fought each other over real 30.07
        data: LY5419 was taken from trainee-agent#4 to close TL#22's gap, then
        handed straight back, leaving the gap open and the work wasted."""
        return not (_is_tl(row) or _yn(row, "טרייני רצ")
                    or _yn(row, "חונך רצים") or _yn(row, "מסמיך רצים")
                    or _yn(row, "דייל בטרייני"))

    def _may_return_to_counters(row):
        """Whose mid-shift stretch off the floor is legitimate, not a gap.

        * Restricted workers (ילד עובדים / פורשים) — ALWAYS. While off the
          departure floor they cover the other duties (חבלול, קיוסקים,
          מסמכים, ביטחון, דרופ אופ; to be modelled in the app later), so their
          time is never idle (user rule 2026-08-06, restricted-agent#2).
        * A genuine night shift — night flights → counters → pre-dawn flights.
        * Any long 11h+ shift (12:30-00:30, 14:00-01:30, …).
        A ר"צ is EXCLUDED unless their shift is a night shift: a team leader
        must not be parked at the counters and pulled back down later.
        """
        try:
            if _is_broadly_restricted(row):
                return True
        except Exception:
            pass
        _night = False
        try:
            _night = is_night_shift_for_return_rule(row)
        except Exception:
            pass
        if _night:
            return True
        if _is_tl(row):
            return False
        _ss = clean_text(str(row.get("תחילת משמרת", "")))
        _se = clean_text(str(row.get("סוף משמרת", "")))
        if not (is_time_text(_ss) and is_time_text(_se)):
            return False
        _s_m = time_to_minutes(_ss)
        _len = (time_to_minutes(_se) - _s_m) % 1440 or 1440
        # The long-shift exemption is for DAY / NOON / EVENING shifts
        # (11:00-21:00, 12:30-00:30, 14:00-01:30 …) — not for a long MORNING
        # shift, whose worker is on the floor for the morning wave and has no
        # counter block to go back to (user rule 2026-08-06, TL#3
        # 02:00-12:30: "בכלל של 11-12 שעות יש להתייחס למשמרות יום/צהריים/ערב
        # ולא למשמרות בוקר").
        if _s_m < 8 * 60:
            return False
        return _len >= 11 * 60

    # ── Incremental bookkeeping (perf) ─────────────────────────────────────
    # This pass used to re-scan the WHOLE schedule for every _tasks_of() call,
    # and those calls sit inside two nested loops (per gap × per candidate
    # slot), so a single build spent >3s here — half the whole pipeline
    # (profiled 2026-08-06). Row times and the worker→rows index are built once
    # and kept in sync by _reassign() instead.
    _row_time = {}
    _by_name = {}
    for _i in df.index:
        _a, _b = _tm(df.at[_i, "התחלה"]), _tm(df.at[_i, "סיום"])
        if _a is None or _b is None:
            continue
        _row_time[_i] = (_a, _b)
        _by_name.setdefault(clean_text(str(df.at[_i, "עובד"])), []).append(_i)

    def _tasks_of(name):
        return [(_i, _row_time[_i][0], _row_time[_i][1]) for _i in _by_name.get(name, ())]

    _ROLE_COL = {"ראש צוות": "ראש צוות", "דייל": "דייל", "מתאם תורים": "מתאם תורים",
                 "מפקח TSA": "מפקח TSA", "שומר TSA": "שומר TSA"}
    _row_term = {}
    if "_gate" in df.columns:
        for _i in df.index:
            try:
                _row_term[_i] = get_terminal(clean_text(str(df.at[_i, "_gate"])))
            except Exception:
                _row_term[_i] = None

    def _quick_ok(row, idx):
        """Cheap pre-filter before the expensive candidate lookup: right
        terminal, and certified for the role. Without it this pass called
        get_qualified_candidates_for_swap on dozens of slots the worker could
        never take anyway — e.g. a Terminal-1 worker against ~70 Terminal-3
        slots inside their gap (perf, 2026-08-06).

        MUST only reject the clearly-impossible — get_qualified_candidates_for_
        swap stays the authority. Two traps found while writing it:
        `get_terminal` does NOT return "3" for a Terminal-3 gate (it yields the
        pier letter), so only the literal "1" may be compared; and a ר"צ can
        staff a דייל / מתאם-תורים slot even when those columns say "לא".
        Getting either wrong silently blocks real swaps (it took the gap count
        from 7 to 12 before this was fixed)."""
        _is_t1_row = _row_term.get(idx) == "1"
        _w_term = clean_text(str(row.get("טרמינל", "")))
        if _is_t1_row and _w_term not in ("", "1"):
            return False
        if not _is_t1_row and _w_term == "1":
            return False
        _role = str(df.at[idx, "תפקיד בסיס"]).strip()
        _col = _ROLE_COL.get(_role)
        if _col is None or clean_text(str(row.get(_col, ""))) == "כן":
            return True
        # a team leader also covers plain attendant / queue-coordinator slots
        return _role in ("דייל", "מתאם תורים") and clean_text(str(row.get("ראש צוות", ""))) == "כן"

    def _reassign(idx, new_name):
        _old = clean_text(str(df.at[idx, "עובד"]))
        if _old in _by_name and idx in _by_name[_old]:
            _by_name[_old].remove(idx)
        df.at[idx, "עובד"] = new_name
        _by_name.setdefault(clean_text(new_name), []).append(idx)

    def _rel_sorted(name, tasks):
        row = emp_map.get(name)
        ss = clean_text(str(row.get("תחילת משמרת", ""))) if row is not None else ""
        if not is_time_text(ss):
            return None, []
        s_m = time_to_minutes(ss)
        rel = sorted(((_a - s_m) % 1440, ((_a - s_m) % 1440) + ((_b - _a) % 1440), _i)
                     for _i, _a, _b in tasks)
        return s_m, rel

    def _max_gap(name, tasks):
        _s, rel = _rel_sorted(name, tasks)
        if not rel:
            return 0
        return max((rel[k + 1][0] - rel[k][1] for k in range(len(rel) - 1)), default=0)

    # Workers rostered today who ended up with nothing at all — the pool this
    # pass hands work to.
    _assigned = {clean_text(str(df.at[_i, "עובד"])) for _i in df.index}
    _unassigned = {
        nm for nm, r in emp_map.items()
        if nm and nm not in _assigned
        and not _is_crew_manager(r)
        and str(r.get("in_daily_excel", "")).strip() in ("1", "1.0", "True", "כן")
        and clean_text(str(r.get("חולה", ""))) not in ("True", "כן")
    }

    # collect every long gap, biggest first
    gaps = []
    for name in list(_by_name):
        if not name or "❌" in name or name not in emp_map:
            continue
        tasks = _tasks_of(name)
        if len(tasks) < 2:
            continue
        s_m, rel = _rel_sorted(name, tasks)
        if s_m is None:
            continue
        # A restricted worker (ילד עובדים / פורשים) is never idle off the
        # floor — they cover חבלול / קיוסקים / מסמכים / ביטחון / דרופ אופ —
        # so ANY size of gap is legitimate for them, not just 3h+.
        try:
            if _is_broadly_restricted(emp_map[name]):
                continue
        except Exception:
            pass
        _legit = _may_return_to_counters(emp_map[name])
        try:
            _legit_line = (_LEGIT_GAP_NIGHT if is_night_shift_for_return_rule(emp_map[name])
                           else _LEGIT_GAP)
        except Exception:
            _legit_line = _LEGIT_GAP
        # An EVENING shift (15:00-19:30 start) is on duty for the NIGHT
        # flights; an earlier flight it picks up is optional, not an
        # obligation — and it must not cost a gap: such a worker goes down to
        # the flights for ONE round only (user rules 2026-08-06, TL#24
        # 19:00-01:30: "אין חובה לשבץ אותה לטיסות שהן לא טיסות לילה, אלא אם כן
        # אין ברירה" + "אם עובד שהתחיל בין 15:00-19:30 משובץ שוב לטיסת לילה
        # הפער לא בסדר! יש להוריד אותו לטיסות רק לסבב אחד. אין ליצור פער").
        # So for them the OPTIONAL EARLY flights are handed off and the night
        # block is kept, the opposite side from everyone else.
        _ss_ev = clean_text(str(emp_map[name].get("תחילת משמרת", "")))
        _is_evening = is_time_text(_ss_ev) and 15 * 60 <= time_to_minutes(_ss_ev) <= 19 * 60 + 30
        for k in range(len(rel) - 1):
            gap = rel[k + 1][0] - rel[k][1]
            if gap <= _MIN_GAP:
                continue
            if _legit and gap >= _legit_line:
                continue  # break + a real stretch of counter duty — normal
            _force_handoff = _is_evening and ((rel[k + 1][0] + s_m) % 1440) >= 21 * 60
            _head = [r[2] for r in rel[:k + 1]]          # before the gap
            _tail = [r[2] for r in rel[k + 1:]]          # after the gap
            gaps.append((gap, name, _head, _tail,
                         (rel[k][1] + s_m) % 1440, (rel[k + 1][0] + s_m) % 1440,
                         _force_handoff))
    gaps.sort(key=lambda x: -x[0])

    # ── Branch A: ר"צ / טרייני-ר"צ — FILL the gap by preempting a plain
    # attendant inside it, so they stay on the floor instead of being parked
    # at the counters. Matches the confirmed floor-time rule in
    # optimal_schedule_patterns ("TL workers should fill ALL gaps, including
    # via preemption of non-TL דיילים").
    def _gap_after_adding(cand, add_rows):
        """The candidate's worst gap if they also took `add_rows`."""
        _t = _tasks_of(cand) + [(_j, _row_time[_j][0], _row_time[_j][1])
                                for _j in add_rows if _j in _row_time]
        return _max_gap(cand, _t) if len(_t) >= 2 else 0

    def _try_hand_off(side_rows, giver):
        """Hand every row in `side_rows` to other qualified workers, so the
        giver's remaining day is contiguous. All-or-nothing; prefers workers
        with no assignment at all, and never leaves the receiver with a gap of
        their own."""
        _plan, _used = [], set()
        for _j in side_rows:
            _cands = get_qualified_candidates_for_swap(
                df, employees_df, str(df.at[_j, "טיסה"]).strip(),
                str(df.at[_j, "תפקיד בסיס"]).strip(), _j)
            _ranked = sorted(
                (c for c in _cands
                 if clean_text(c) != giver and clean_text(c) not in _used),
                key=lambda c: (clean_text(c) not in _unassigned, len(_tasks_of(clean_text(c)))))
            _pick = next(
                (c for c in _ranked
                 if _gap_after_adding(clean_text(c), [_j]) <= _MIN_GAP),
                None)
            if _pick is None:
                return False
            _used.add(clean_text(_pick))
            _plan.append((_j, _pick))
        for _j, _pick in _plan:
            _reassign(_j, _pick)
            if "סיבה" in df.columns:
                df.at[_j, "סיבה"] = "הועבר כדי שהעובד לא יישאר עם רווח ארוך — יורד לטיסות ברצף אחד"
        _unassigned.difference_update(_used)
        return True

    _still_gapped = []
    for gap, name, head_idx, tail_idx, from_m, to_m, force_handoff in gaps:
        row = emp_map.get(name)
        # force_handoff (an evening worker who also has the night block) wins
        # over floor priority: they go down for ONE round, never two with a
        # gap between — even when they are a ר"צ.
        if row is None or force_handoff or not _is_floor_priority(row):
            _still_gapped.append((gap, name, head_idx if force_handoff else tail_idx))
            continue
        rb = required_break(row) or 45
        span = (to_m - from_m) % 1440
        best = None
        # Cheap geometric/eligibility filters first; the expensive
        # get_qualified_candidates_for_swap call runs only on what survives,
        # and only for the earliest surviving slot (perf, 2026-08-06).
        _shortlist = []
        for _i, (_a, _b) in _row_time.items():
            holder = clean_text(str(df.at[_i, "עובד"]))
            if not holder or "❌" in holder or holder == name:
                continue
            _s_rel = (_a - from_m) % 1440
            _e_rel = _s_rel + ((_b - _a) % 1440)
            if _e_rel > span:
                continue  # not fully inside the gap
            if max(_s_rel, span - _e_rel) < rb:
                continue  # no room left for their own break
            if not _quick_ok(row, _i):
                continue
            holder_row = emp_map.get(holder)
            if holder_row is None or not _is_plain_attendant(holder_row):
                continue
            remaining = [t for t in _tasks_of(holder) if t[0] != _i]
            if len(remaining) >= 2 and _max_gap(holder, remaining) > _MIN_GAP:
                continue  # don't hand the hole to the attendant instead
            _shortlist.append((_s_rel, _i))
        _shortlist.sort()
        for _s_rel, _i in _shortlist:
            _cands = get_qualified_candidates_for_swap(
                df, employees_df, str(df.at[_i, "טיסה"]).strip(),
                str(df.at[_i, "תפקיד בסיס"]).strip(), _i)
            if name in _cands:
                best = (_s_rel, _i)
                break
        if best is not None:
            _reassign(best[1], name)
            if "סיבה" in df.columns:
                df.at[best[1], "סיבה"] = "מילוי רווח לר\"צ/טרייני ר\"צ — עדיפות על פני דייל רגיל"
        else:
            # Nothing to fill it with. A ר"צ is never parked at the counters
            # and pulled back down, so instead make their day CONTIGUOUS by
            # handing the EARLY block to another qualified worker — they then
            # come down once, for the later flights only (user 2026-08-06 on
            # TL#22: "האם אין ראש צוות אחר שימלא את תפקיד ראש הצוות ב-253?
            # וככה TL#22 תרד רק לטיסות מאוחרות יותר").
            #
            # ONLY the early side. Handing off the LATE side also removes the
            # gap on paper, but it just pushes work off the end of the day:
            # tried on real 30.07 and TL#41 / TL#42 / TL#5 each
            # lost their 22:50 flight and sat idle from 20:40 to a 00:30-01:30
            # shift end — a 130-min gap traded for ~4h of nothing. Idle time
            # BEFORE the first flight is ordinary counter duty; idle time after
            # the last one is just waiting.
            if head_idx:
                _try_hand_off(head_idx, name)

    # ── Branch B: everyone else — hand the POST-GAP flights to someone with
    # no assignment at all, so the gapped worker's day ends at their cluster.
    for gap, name, tail_idx in _still_gapped:
        # the gap may already be gone — an earlier hand-off can have taken
        # these very rows away from this worker
        if any(clean_text(str(df.at[_i, "עובד"])) != name for _i in tail_idx):
            continue
        if not _unassigned:
            break
        plan = []
        used = set()
        for _i in tail_idx:
            # Skip the expensive lookup when no free worker could take this
            # slot anyway (right terminal + certified for the role).
            if not any(_quick_ok(emp_map[_u], _i) for _u in _unassigned if _u in emp_map):
                plan = []
                break
            _cands = get_qualified_candidates_for_swap(
                df, employees_df, str(df.at[_i, "טיסה"]).strip(),
                str(df.at[_i, "תפקיד בסיס"]).strip(), _i)
            _pick = next(
                (c for c in _cands
                 if clean_text(c) in _unassigned and clean_text(c) not in used),
                None)
            if _pick is None:
                plan = []
                break  # atomic: a partial hand-off would leave the gap in place
            used.add(clean_text(_pick))
            plan.append((_i, _pick))
        if not plan:
            continue
        for _i, _pick in plan:
            _reassign(_i, _pick)
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "הועבר לעובד שלא שובץ כלל, כדי לסגור רווח ארוך במשמרת"
        _unassigned -= used

    return df


def improve_night_continuity(schedule_df, employees_df):
    """Match night flights to shift ends: the LATER the flight, the EARLIER the
    worker's shift ends.

    User rule 2026-08-06: "עדיף לשבץ את TL#5 על טיסת לילה יותר מאוחרת
    שיותר קרובה לסיום המשמרת שלה, כדי שעובדי משמרת הלילה יהיו משובצים על טיסות
    הלילה המוקדמות יותר." An EVENING worker (12:30-00:30, 14:00-01:30,
    19:00-01:30) goes home right after the night block, so they should take the
    LAST flights of the night; a NIGHT worker (…-06:00/07:00/08:30) stays on
    for the pre-dawn wave and should take the EARLIER ones.

    The main build cannot arrange this on its own: flights are staffed in raw
    departure-time order, so the earliest night flight is filled first and the
    tightest-fitting worker (the one whose shift ends soonest) is used up on
    it. This pass repairs it afterwards with pairwise swaps — for two night
    flights of the same role, if the EARLIER one is held by the worker who
    goes home SOONER, the two are exchanged.

    Both sides are re-validated with the two slots cleared first: the workers
    overlap each other in place, so `get_qualified_candidates_for_swap` would
    reject each of them while the other still holds the slot.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    def _tm(t):
        v = to_datetime_time(t)
        return v.hour * 60 + v.minute if v is not None else None

    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    # night role-slots, keyed by role
    _night = {}
    for _i in df.index:
        _a, _b = _tm(df.at[_i, "התחלה"]), _tm(df.at[_i, "סיום"])
        if _a is None or _b is None:
            continue
        if not (_a >= 20 * 60 or _a <= 2 * 60):
            continue
        _w = clean_text(str(df.at[_i, "עובד"]))
        if not _w or "❌" in _w or _w not in emp_map:
            continue
        _night.setdefault(str(df.at[_i, "תפקיד בסיס"]).strip(), []).append((_i, _a, _b))

    def _remaining(worker, end_m):
        """Minutes left in the worker's shift once this flight ends."""
        _se = clean_text(str(emp_map[worker].get("סוף משמרת", "")))
        if not is_time_text(_se):
            return None
        return (time_to_minutes(_se) - end_m) % 1440

    # ── Perf: index rows once and keep a worker→rows map, instead of walking
    # the whole schedule inside the O(slots²) pair loop below. Profiled
    # 2026-08-06: this pass alone was 13.5s = 71% of the pipeline.
    _rt = {}
    _by_worker = {}
    for _i in df.index:
        _a, _b = _tm(df.at[_i, "התחלה"]), _tm(df.at[_i, "סיום"])
        if _a is None or _b is None:
            continue
        _rt[_i] = (_a, _b)
        _by_worker.setdefault(clean_text(str(df.at[_i, "עובד"])), []).append(_i)

    def _move(idx, new_name):
        _old = clean_text(str(df.at[idx, "עובד"]))
        if _old in _by_worker and idx in _by_worker[_old]:
            _by_worker[_old].remove(idx)
        df.at[idx, "עובד"] = new_name
        _by_worker.setdefault(clean_text(new_name), []).append(idx)

    _ROLE_COL_N = {"ראש צוות": "ראש צוות", "דייל": "דייל", "מתאם תורים": "מתאם תורים",
                   "מפקח TSA": "מפקח TSA", "שומר TSA": "שומר TSA"}
    _term_of = {}
    if "_gate" in df.columns:
        for _i in df.index:
            try:
                _term_of[_i] = get_terminal(clean_text(str(df.at[_i, "_gate"])))
            except Exception:
                _term_of[_i] = None

    def _plausible(worker, idx):
        """Cheap terminal+certification pre-filter, so the expensive
        get_qualified_candidates_for_swap runs only on real possibilities.
        Only rejects the clearly-impossible — see the same guard in
        fill_idle_gaps for the two traps (get_terminal yields the pier LETTER
        for T3, and a ר"צ may staff דייל/מתאם slots)."""
        _r = emp_map.get(worker)
        if _r is None:
            return False
        _is_t1 = _term_of.get(idx) == "1"
        _wt = clean_text(str(_r.get("טרמינל", "")))
        if _is_t1 and _wt not in ("", "1"):
            return False
        if not _is_t1 and _wt == "1":
            return False
        _role = str(df.at[idx, "תפקיד בסיס"]).strip()
        _col = _ROLE_COL_N.get(_role)
        if _col is None or clean_text(str(_r.get(_col, ""))) == "כן":
            return True
        return _role in ("דייל", "מתאם תורים") and clean_text(str(_r.get("ראש צוות", ""))) == "כן"

    # ── Restricted-worker quota (ילדי עובדים / פורשים / דייל-בטרייני).
    # These stages MOVE people between flights, so they must respect the same
    # cap the main build enforces — otherwise they quietly push a flight past
    # it. Found via real 30.07 data: 4 of the 5 over-quota flights carried
    # reasons written by THIS pass. User rule 2026-08-06: "אני לא רוצה לשנות
    # את הכמות של העובדים המוחרגים על כל טיסה."
    _flight_rows = {}
    for _i in df.index:
        _flight_rows.setdefault(str(df.at[_i, "טיסה"]).strip(), []).append(_i)

    def _restricted(name):
        _r = emp_map.get(name)
        try:
            return bool(_r is not None and _is_broadly_restricted(_r))
        except Exception:
            return False

    def _quota_ok(worker, idx):
        """May `worker` take row `idx` without breaking the flight's cap?"""
        if not _restricted(worker):
            return True
        _rows = _flight_rows.get(str(df.at[idx, "טיסה"]).strip(), [])
        _att = [_j for _j in _rows
                if str(df.at[_j, "תפקיד בסיס"]).strip() in ("דייל", "שומר TSA")]
        if idx not in _att:
            return True                      # cap covers attendant/guard slots only
        _cap = 0 if len(_att) <= 1 else (1 if len(_att) <= 3 else 2)
        _cnt = 0
        for _j in _att:
            if _j == idx:
                continue
            _w = clean_text(str(df.at[_j, "עובד"]))
            if _w and "❌" not in _w and _restricted(_w):
                _cnt += 1
        return _cnt + 1 <= _cap

    def _mentor_ok(worker, idx):
        """
        May `worker` take row `idx` without orphaning a ר"צ trainee?

        A טרייני ר"צ shadows the flight's ר"צ, so that ר"צ must be a חונך or
        מסמיך רצים. This pass swaps ר"צ between flights for continuity and used
        to ignore that entirely — found via real 11.08 data: LY005 was built
        correctly with TL#2 (חונכת) and trainee TL-trainee#2, then
        this pass replaced her with TL#32, who mentors nobody, leaving the
        trainee shadowing a non-mentor.
        """
        if str(df.at[idx, "תפקיד בסיס"]).strip() != "ראש צוות":
            return True
        _rows = _flight_rows.get(str(df.at[idx, "טיסה"]).strip(), [])
        _has_trainee = any(
            str(df.at[_j, "תפקיד בסיס"]).strip() == "טרייני רצ"
            and "❌" not in str(df.at[_j, "עובד"])
            for _j in _rows
        )
        if not _has_trainee:
            return True
        _r = emp_map.get(worker)
        if _r is None:
            return False
        return any(
            clean_text(str(_r.get(_c, ""))) == "כן"
            for _c in ("חונך רצים", "מסמיך רצים")
        )

    def _clashes(name, start, end, skip):
        _e2 = end if end > start else end + 1440
        for _j in _by_worker.get(name, ()):
            if _j == skip:
                continue
            _s, _e = _rt.get(_j, (None, None))
            if _s is None or _e is None:
                continue
            _ee = _e if _e > _s else _e + 1440
            if not (_e2 <= _s or start >= _ee):
                return True
        return False

    # ── Stage 1: a NIGHT-shift worker should not hold a night flight at all
    # unless there is no one else — their shift exists for the pre-dawn wave
    # (user rule 2026-08-06: "לעובדי משמרת לילה עדיף לא לתת טיסות לילה כלל,
    # אלא אם כן אין ברירה"). shift_pri already de-prioritises them in the main
    # build, but not strongly enough to beat the continuity/shift-tail keys, so
    # hand the slot over here to any qualified worker who is NOT on a night
    # shift.
    def _is_night_worker(name):
        try:
            return is_night_shift_for_return_rule(emp_map[name])
        except Exception:
            return False

    def _transfers_out(name):
        """Moves to another terminal mid-shift ("1@01:00"). Their T3 night
        flights are worth handing to someone who stays put — they have to
        break and travel, and the rest of their shift belongs to the other
        terminal (user rule 2026-08-06 on TL#4: "אם לא חייבים לשבץ
        אותה עדיף שלא, כי היא עוברת לטרמינל 1"). `transfer_lr` already
        de-prioritises them at build time, but only among candidates who were
        free at that moment."""
        import re as _re_t
        _r = emp_map.get(name)
        if _r is None:
            return False
        return bool(_re_t.search(r"@\d", clean_text(str(_r.get("מעבר טרמינל", "")))))

    for _role, _slots in _night.items():
        for _i, _s, _e in _slots:
            _h = clean_text(str(df.at[_i, "עובד"]))
            if not _h or "❌" in _h or _h not in emp_map:
                continue
            if not (_is_night_worker(_h) or _transfers_out(_h)):
                continue
            _keep = df.at[_i, "עובד"]
            df.at[_i, "עובד"] = f"❌ חסר {_role}"
            _cands = [c for c in get_qualified_candidates_for_swap(
                          df, employees_df, str(df.at[_i, "טיסה"]).strip(), _role, _i)
                      if clean_text(c) in emp_map
                      and not _is_night_worker(clean_text(c))
                      and not _transfers_out(clean_text(c))
                      and _quota_ok(clean_text(c), _i)
                      and _mentor_ok(clean_text(c), _i)]
            if not _cands:
                df.at[_i, "עובד"] = _keep
                continue
            df.at[_i, "עובד"] = _keep
            # among the non-night candidates prefer the one whose shift ends
            # soonest after this flight — the stage-2 rule, applied up front
            _cands.sort(key=lambda c: (_remaining(clean_text(c), _e) is None,
                                       _remaining(clean_text(c), _e) or 9999))
            _move(_i, clean_text(_cands[0]))
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "טיסת לילה — הועברה מעובד/ת משמרת לילה לעובד/ת ערב"

    # NOTE 2026-08-06: a "whole-SET exchange" stage was tried here — swap a
    # night worker's ENTIRE set of night slots with an evening worker's set, to
    # express the 2-for-1 rotation the user asked for (TL#24 087+017 vs
    # TL#15 003). It was REVERTED: the holder→slots index is built once
    # and goes stale as soon as stage 1 or an earlier exchange moves a row, so
    # it double-booked LY087 onto both TL#41 and TL#5 and pushed the
    # night-worker slot count UP (21→24). Redo it only with the index kept in
    # sync after every write, and re-check the double-booking count.

    # ── Stage 2: among the night flights, the LATER the flight the EARLIER the
    # holder's shift should end.
    for _role, _slots in _night.items():
        # operational end time, so a post-midnight end sorts AFTER 23:xx
        _slots.sort(key=lambda t: (t[2] - 3 * 60) % 1440)
        for _x in range(len(_slots)):
            for _y in range(_x + 1, len(_slots)):
                _ia, _sa, _ea = _slots[_x]
                _ib, _sb, _eb = _slots[_y]
                _ha = clean_text(str(df.at[_ia, "עובד"]))
                _hb = clean_text(str(df.at[_ib, "עובד"]))
                if not _ha or not _hb or _ha == _hb or "❌" in _ha or "❌" in _hb:
                    continue
                _ra, _rb = _remaining(_ha, _ea), _remaining(_hb, _eb)
                if _ra is None or _rb is None or _rb - _ra < 60:
                    # already the right way round, or the difference is too
                    # small to be worth churning the schedule for (also keeps
                    # the O(slots²) pair scan from paying for the expensive
                    # candidate lookup on marginal pairs — perf 2026-08-06)
                    continue
                if _clashes(_ha, _sb, _eb, _ia) or _clashes(_hb, _sa, _ea, _ib):
                    continue
                if not (_plausible(_hb, _ia) and _plausible(_ha, _ib)):
                    continue
                if not (_quota_ok(_hb, _ia) and _quota_ok(_ha, _ib)):
                    continue
                if not (_mentor_ok(_hb, _ia) and _mentor_ok(_ha, _ib)):
                    continue
                _keep_a, _keep_b = df.at[_ia, "עובד"], df.at[_ib, "עובד"]
                df.at[_ia, "עובד"] = f"❌ חסר {_role}"
                df.at[_ib, "עובד"] = f"❌ חסר {_role}"
                _ok = (any(clean_text(_c) == _hb for _c in get_qualified_candidates_for_swap(
                           df, employees_df, str(df.at[_ia, "טיסה"]).strip(), _role, _ia))
                       and any(clean_text(_c) == _ha for _c in get_qualified_candidates_for_swap(
                           df, employees_df, str(df.at[_ib, "טיסה"]).strip(), _role, _ib)))
                if not _ok:
                    df.at[_ia, "עובד"], df.at[_ib, "עובד"] = _keep_a, _keep_b
                    continue
                _move(_ia, _hb)
                _move(_ib, _ha)
                if "סיבה" in df.columns:
                    df.at[_ia, "סיבה"] = "טיסת לילה מוקדמת — הועברה לעובד/ת משמרת לילה"
                    df.at[_ib, "סיבה"] = "טיסת לילה מאוחרת — קרובה לסיום המשמרת של העובד/ת"

    return df


def balance_workload(schedule_df, employees_df, terminal="1"):
    """Give a slot from a heavily-loaded worker to one with NOTHING all day.

    NOT WIRED INTO THE PIPELINE (2026-08-06) — kept for reference. Measured on
    real 30.07 it bought almost nothing and cost a gap: scoped to Terminal 1 it
    made exactly ONE hand-over (LY5115 12:55 → agent#42) and pushed the gap
    count 5 → 6, while the three workers it was written for (agent#7,
    trainee-agent#12, agent#19) stayed idle anyway — the candidate ordering returned
    by get_qualified_candidates_for_swap put another unassigned worker first,
    and after one hand-over the giver drops below `_MIN_LOAD` and the loop
    stops. To revive it: pick the receiver by longest idle shift rather than
    candidate order, allow several hand-overs per giver, and re-measure gaps.

    SCOPED TO ONE TERMINAL (default Terminal 1). An unscoped first version
    made 28 hand-overs across the whole day and broke the Terminal-3 schedule
    that had just been tuned — continuity chains came apart and the gap count
    went 5 → 8. Widen the scope only with the same before/after measurements.

    The build's own fairness key (`task_count`) is the LEAST significant entry
    in every lexsort tuple, and `idle_first` only reaches the דייל branch, so a
    day can end with a few workers carrying 5-6 flights while colleagues on the
    same shift and terminal fly none (real 30.07, Terminal 1: TL#21 6 and
    TL#23 5, while agent#7, trainee-agent#12 and agent#19 — all דייל-
    qualified and inside the right hours — held zero).

    Deliberately conservative, because the rest of the schedule is tuned:
      * only takes from a worker with `_MIN_LOAD`+ tasks, and only gives to one
        with none at all;
      * never touches a trainee or their mentor (the pairing invariant);
      * refuses if the giver would be left with a >2h gap — never move the hole;
      * respects the flight's restricted-worker cap;
      * validated through get_qualified_candidates_for_swap, so shift, terminal
        and conflicts are all enforced normally.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    _MIN_LOAD = 4          # only relieve genuinely loaded workers
    _MAX_GAP = 120

    def _tm(t):
        v = to_datetime_time(t)
        return v.hour * 60 + v.minute if v is not None else None

    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    # trainee/mentor names — off limits for both sides of a hand-over
    _paired = set()
    if "דייל בטרייני" in employees_df.columns and "חונך דייל" in employees_df.columns:
        for _, _r in employees_df.iterrows():
            if clean_text(str(_r.get("דייל בטרייני", ""))) != "כן":
                continue
            _m = clean_text(str(_r.get("חונך דייל", "")))
            if _m:
                _paired.add(clean_text(str(_r.get("שם", ""))))
                _paired.add(_m)

    _rt, _by_name, _by_flight = {}, {}, {}
    _in_scope = set()
    for _i in df.index:
        _a, _b = _tm(df.at[_i, "התחלה"]), _tm(df.at[_i, "סיום"])
        if _a is None or _b is None:
            continue
        _rt[_i] = (_a, _b)
        _by_name.setdefault(clean_text(str(df.at[_i, "עובד"])), []).append(_i)
        _by_flight.setdefault(str(df.at[_i, "טיסה"]).strip(), []).append(_i)
        try:
            if "_gate" in df.columns and get_terminal(clean_text(str(df.at[_i, "_gate"]))) == terminal:
                _in_scope.add(_i)
        except Exception:
            pass
    if not _in_scope:
        return df

    def _max_gap_of(name, rows):
        _r = emp_map.get(name)
        _ss = clean_text(str(_r.get("תחילת משמרת", ""))) if _r is not None else ""
        if not is_time_text(_ss) or len(rows) < 2:
            return 0
        _s = time_to_minutes(_ss)
        _rel = sorted(((_rt[_j][0] - _s) % 1440,
                       ((_rt[_j][0] - _s) % 1440) + ((_rt[_j][1] - _rt[_j][0]) % 1440))
                      for _j in rows if _j in _rt)
        return max((_rel[_k + 1][0] - _rel[_k][1] for _k in range(len(_rel) - 1)), default=0)

    def _restricted(name):
        _r = emp_map.get(name)
        try:
            return bool(_r is not None and _is_broadly_restricted(_r))
        except Exception:
            return False

    def _quota_ok(worker, idx):
        if not _restricted(worker):
            return True
        _att = [_j for _j in _by_flight.get(str(df.at[idx, "טיסה"]).strip(), [])
                if str(df.at[_j, "תפקיד בסיס"]).strip() in ("דייל", "שומר TSA")]
        if idx not in _att:
            return True
        _cap = 0 if len(_att) <= 1 else (1 if len(_att) <= 3 else 2)
        _cnt = sum(1 for _j in _att if _j != idx
                   and "❌" not in str(df.at[_j, "עובד"])
                   and _restricted(clean_text(str(df.at[_j, "עובד"]))))
        return _cnt + 1 <= _cap

    _unassigned = sorted(
        nm for nm, r in emp_map.items()
        if nm and nm not in _by_name
        and str(r.get("in_daily_excel", "")).strip() in ("1", "1.0", "True", "כן")
        and clean_text(str(r.get("חולה", ""))) not in ("True", "כן")
        and nm not in _paired
        and clean_text(str(r.get("טרמינל", ""))) == terminal
    )
    if not _unassigned:
        return df

    # heaviest workers first, so relief goes where it is needed most
    _loaded = sorted(
        (nm for nm, rows in _by_name.items()
         if nm and "❌" not in nm and nm in emp_map and nm not in _paired
         and len([_j for _j in rows if _j in _in_scope]) >= _MIN_LOAD),
        key=lambda nm: -len(_by_name[nm]))

    for _giver in _loaded:
        # Compare against the giver's CURRENT worst gap, not a flat 120: a
        # worker who already sits in one of the terminal's dead zones (TL#21's day carries a structural 125-min gap) would otherwise fail
        # the guard on every single slot and never be relieved at all.
        _base_gap = max(_MAX_GAP, _max_gap_of(_giver, _by_name.get(_giver, [])))
        for _i in list(_by_name.get(_giver, [])):
            if _i not in _in_scope:
                continue
            if len([_j for _j in _by_name.get(_giver, []) if _j in _in_scope]) <= _MIN_LOAD - 1:
                break
            _remaining = [_j for _j in _by_name[_giver] if _j != _i]
            if len(_remaining) >= 2 and _max_gap_of(_giver, _remaining) > _base_gap:
                continue          # don't open a NEW hole in the giver's day
            _keep = df.at[_i, "עובד"]
            df.at[_i, "עובד"] = f"❌ חסר {str(df.at[_i, 'תפקיד בסיס']).strip()}"
            _cands = [clean_text(_c) for _c in get_qualified_candidates_for_swap(
                df, employees_df, str(df.at[_i, "טיסה"]).strip(),
                str(df.at[_i, "תפקיד בסיס"]).strip(), _i)]
            _pick = next((_c for _c in _cands
                          if _c in _unassigned and _quota_ok(_c, _i)), None)
            if _pick is None:
                df.at[_i, "עובד"] = _keep
                continue
            df.at[_i, "עובד"] = _pick
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "איזון עומס — הועבר לעובד/ת ללא שיבוץ"
            _by_name[_giver].remove(_i)
            _by_name.setdefault(_pick, []).append(_i)
            _unassigned.remove(_pick)
            if not _unassigned:
                return df

    return df


def avoid_fresh_start_assignments(schedule_df, employees_df, terminal="1"):
    """Keep a Terminal-1 flight off a worker who has only just clocked in.

    User rule 2026-08-06: "אין לשים שיבוצים לטיסות בטרמינל 1 שמאוד קרובים
    לתחילת המשמרת... יש לנסות לשבץ עובדים ממשמרת הבוקר, ולהשתמש בעובדים של
    משמרת 11:00 רק אם אין ברירה אחרת." Example: LY5031's ר"צ role opens 11:20
    and went to TL#21, twenty minutes into her 11:00 shift, while
    TL#3 — ר"צ-qualified and nine hours into a 02:00 shift — sat on
    the same flight as a plain דייל.

    `first_task_buffer` already de-prioritises a just-started worker at build
    time, but only among candidates the selector could see: someone ALREADY on
    the flight in another role is never offered for its other slots, so the
    only way to reach them is to swap the two roles here.

    Two moves, in order:
      1. hand the slot to a qualified candidate who has been on shift longer;
      2. otherwise PROMOTE someone already on the flight who is qualified for
         the role and longer on shift, then back-fill the slot they vacate.
    Both are validated through get_qualified_candidates_for_swap, and the pass
    never makes the elapsed-time picture worse than it found it.
    """
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    _FRESH = 45          # "only just started" — same threshold as first_task_buffer

    def _tm(t):
        v = to_datetime_time(t)
        return v.hour * 60 + v.minute if v is not None else None

    emp_map = {clean_text(str(r.get("שם", ""))): r for _, r in employees_df.iterrows()}

    def _elapsed(name, role_start):
        """Minutes the worker has been on shift when this role starts."""
        _r = emp_map.get(name)
        if _r is None:
            return None
        _ss = clean_text(str(_r.get("תחילת משמרת", "")))
        if not is_time_text(_ss):
            return None
        return (role_start - time_to_minutes(_ss)) % 1440

    _by_flight = {}
    for _i in df.index:
        _by_flight.setdefault(str(df.at[_i, "טיסה"]).strip(), []).append(_i)

    for _i in list(df.index):
        try:
            if "_gate" not in df.columns or get_terminal(clean_text(str(df.at[_i, "_gate"]))) != terminal:
                continue
        except Exception:
            continue
        _who = clean_text(str(df.at[_i, "עובד"]))
        if not _who or "❌" in _who or _who not in emp_map:
            continue
        _rs = _tm(df.at[_i, "התחלה"])
        if _rs is None:
            continue
        _el = _elapsed(_who, _rs)
        if _el is None or _el >= _FRESH:
            continue                      # already well into their shift
        _flt = str(df.at[_i, "טיסה"]).strip()
        _role = str(df.at[_i, "תפקיד בסיס"]).strip()

        # 1) a straight replacement who has been around longer
        _keep = df.at[_i, "עובד"]
        df.at[_i, "עובד"] = f"❌ חסר {_role}"
        _cands = [clean_text(_c) for _c in get_qualified_candidates_for_swap(
            df, employees_df, _flt, _role, _i)]
        _better = [(_elapsed(_c, _rs) or 0, _c) for _c in _cands
                   if (_elapsed(_c, _rs) or 0) >= _FRESH]
        if _better:
            _better.sort(reverse=True)
            df.at[_i, "עובד"] = _better[0][1]
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "הוחלף — לא לשבץ עובד/ת בתחילת משמרת לטיסת טרמינל 1"
            continue
        df.at[_i, "עובד"] = _keep

        # 2) promote someone already on this flight, then back-fill their slot
        for _j in _by_flight.get(_flt, []):
            if _j == _i:
                continue
            _other = clean_text(str(df.at[_j, "עובד"]))
            if not _other or "❌" in _other or _other not in emp_map:
                continue
            _other_el = _elapsed(_other, _rs)
            if _other_el is None or _other_el < _FRESH or _other_el <= _el:
                continue
            _kj, _ki = df.at[_j, "עובד"], df.at[_i, "עובד"]
            df.at[_i, "עובד"] = f"❌ חסר {_role}"
            df.at[_j, "עובד"] = f"❌ חסר {str(df.at[_j, 'תפקיד בסיס']).strip()}"
            _ok = any(clean_text(_c) == _other for _c in get_qualified_candidates_for_swap(
                df, employees_df, _flt, _role, _i))
            if not _ok:
                df.at[_i, "עובד"], df.at[_j, "עובד"] = _ki, _kj
                continue
            df.at[_i, "עובד"] = _other
            _fill = [clean_text(_c) for _c in get_qualified_candidates_for_swap(
                df, employees_df, _flt, str(df.at[_j, "תפקיד בסיס"]).strip(), _j)]
            _fill_better = [(_elapsed(_c, _rs) or 0, _c) for _c in _fill
                            if (_elapsed(_c, _rs) or 0) >= _FRESH]
            if _fill_better:
                _fill_better.sort(reverse=True)
                df.at[_j, "עובד"] = _fill_better[0][1]
            elif any(clean_text(_c) == _who for _c in _fill):
                df.at[_j, "עובד"] = _who      # the fresh worker keeps a lesser role
            else:
                df.at[_i, "עובד"], df.at[_j, "עובד"] = _ki, _kj
                continue
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "קודם/ה לתפקיד — ותק במשמרת על פני עובד/ת שרק התחיל/ה"
                df.at[_j, "סיבה"] = "הוחלף בעקבות קידום לתפקיד בטיסת טרמינל 1"
            break

    return df


def enforce_trainee_pairing(schedule_df, employees_df):
    """Final guard: a דייל-בטרייני appears ONLY on flights their mentor is on.

    pair_trainee_attendants establishes this, but several passes run AFTER it
    (consolidate_tsa_inspectors_by_pier, backfill_remaining_gaps,
    protect_early_shift_preflight_breaks) and each can hand a trainee a slot —
    or move the MENTOR off a shared flight — silently re-splitting the pair.
    Found via real 30.07 data: trainee-agent#10 ended on LY015 and trainee-agent#7 on LY279,
    neither mentor aboard, despite both pairs being correct when
    pair_trainee_attendants finished.

    Runs LAST, after every other post-pass. The vacated slot is back-filled
    with a qualified free worker when one exists (never leaves a new ❌ if it
    can be avoided).
    """
    if "דייל בטרייני" not in employees_df.columns or "חונך דייל" not in employees_df.columns:
        return schedule_df.copy()
    df = schedule_df.copy()
    try:
        df = df.astype(object)
    except Exception:
        pass

    def _ws(nm):
        return frozenset(name_key(_w) for _w in clean_text(str(nm)).split() if len(_w) > 1)

    _mentor_of = {}
    _emp_by_ws = {}
    _name_by_ws = {}
    for _, _r in employees_df.iterrows():
        _s = _ws(_r.get("שם", ""))
        if _s:
            _emp_by_ws[_s] = _r
            _name_by_ws[_s] = clean_text(str(_r.get("שם", "")))
        if clean_text(str(_r.get("דייל בטרייני", ""))) != "כן":
            continue
        _m = clean_text(str(_r.get("חונך דייל", "")))
        if _m and _s:
            _mentor_of[_s] = _ws(_m)
    if not _mentor_of:
        return df

    _by_flight = {}
    for _i in df.index:
        _by_flight.setdefault(str(df.at[_i, "טיסה"]).strip(), []).append(_i)

    for _i in list(df.index):
        _w = str(df.at[_i, "עובד"])
        if "❌" in _w or not _w.strip():
            continue
        _mset = _mentor_of.get(_ws(_w))
        if _mset is None:
            continue
        _flt = str(df.at[_i, "טיסה"]).strip()
        _mentor_here = any(
            _ws(df.at[_j, "עובד"]) == _mset
            for _j in _by_flight.get(_flt, [])
            if "❌" not in str(df.at[_j, "עובד"])
        )
        if _mentor_here:
            continue
        _role = str(df.at[_i, "תפקיד בסיס"]).strip() or "דייל"
        # Same exception as pair_trainee_attendants: a שומר TSA slot is the
        # ONE case a trainee is deliberately separated from their mentor
        # (user rule) — don't undo that placement here.
        if _role == "שומר TSA":
            continue
        df.at[_i, "עובד"] = f"❌ חסר {_role}"
        _cands = get_qualified_candidates_for_swap(df, employees_df, _flt, _role, _i)
        _cands = [
            _c for _c in _cands
            if _mentor_of.get(_ws(_c)) is None or _mentor_of[_ws(_c)] == _mset
        ]
        _cands = [_c for _c in _cands if _ws(_c) not in _mentor_of]
        if _cands:
            df.at[_i, "עובד"] = _cands[0]
            if "סיבה" in df.columns:
                df.at[_i, "סיבה"] = "מחליף לאחר הסרת טרייני שאינו מלווה חונך כאן"

    # ── The other half of the invariant: SEAT the trainee on every mentor
    # flight they are missing. pair_trainee_attendants runs early, so a flight
    # the mentor picks up from a LATER pass (gap-fill, backfill, the break
    # protections, fill_idle_gaps) never got the trainee attached — the pair
    # silently drifts apart (found via real 30.07 data: trainee-agent#6 held only 2 of
    # agent#8's 5 flights; the other three went to her after the pairing
    # pass had already finished).
    _mentor_flights = {}
    for _i in df.index:
        _w = str(df.at[_i, "עובד"])
        if "❌" in _w or not _w.strip():
            continue
        _mentor_flights.setdefault(_ws(_w), set()).add(str(df.at[_i, "טיסה"]).strip())

    for _tset, _mset in _mentor_of.items():
        _tr_name = _name_by_ws.get(_tset, "")
        if not _tr_name:
            continue
        for _flt in sorted(_mentor_flights.get(_mset, ())):
            _rows = _by_flight.get(_flt, [])
            if any(_ws(df.at[_j, "עובד"]) == _tset for _j in _rows):
                continue  # already shadowing this flight
            _dil = [_j for _j in _rows if str(df.at[_j, "תפקיד בסיס"]).strip() == "דייל"]
            _att = [_j for _j in _rows
                    if str(df.at[_j, "תפקיד בסיס"]).strip() in ("דייל", "שומר TSA")]
            if not _dil:
                continue
            # Restricted-worker cap for the flight (same rule as
            # pair_trainee_attendants): ≤3 attendant slots → 1, ≥4 → 2.
            _max_restr = 0 if len(_att) <= 1 else (1 if len(_att) <= 3 else 2)

            def _restr_count(_exclude):
                _c = 0
                for _j in _att:
                    if _j == _exclude:
                        continue
                    _wj = str(df.at[_j, "עובד"])
                    if "❌" in _wj or not _wj.strip():
                        continue
                    _rj = _emp_by_ws.get(_ws(_wj))
                    if _rj is not None and _is_broadly_restricted(_rj):
                        _c += 1
                return _c

            # An open ❌ slot first; then a plain veteran דייל; and only then a
            # RESTRICTED holder — allowed because swapping one restricted
            # worker for another leaves the flight's restricted count exactly
            # as it was, so the cap is never breached (user rule 2026-08-06:
            # "תתיר החלפה של מוגבל במוגבל כשהמספר לא משתנה"). Without this the
            # pair stayed split on a flight whose every attendant slot was
            # already spoken for — e.g. LY551, where the mentor TL#39 flies
            # as a דייל beside trainee-agent#9, a restricted worker.
            _options = [_j for _j in _dil if "❌" in str(df.at[_j, "עובד"])]
            _veterans, _restricted_opts = [], []
            for _j in _dil:
                _wj = str(df.at[_j, "עובד"])
                if "❌" in _wj or not _wj.strip():
                    continue
                _wjs = _ws(_wj)
                if _wjs == _mset or _wjs in _mentor_of:
                    continue  # never bump the mentor or another trainee
                _rj = _emp_by_ws.get(_wjs)
                if _rj is not None and _is_broadly_restricted(_rj):
                    _restricted_opts.append(_j)
                else:
                    _veterans.append(_j)
            # Try EVERY option in preference order and keep the first that fits
            # the flight's restricted cap. Picking one target up front and
            # bailing when it failed the quota was wrong: on a flight already at
            # its cap the veteran target always fails, yet displacing one of the
            # RESTRICTED holders keeps the count identical and is allowed (user
            # 2026-08-06). Found via real 30.07 data: LY027 sat at 2/2 with
            # restricted-agent#4 + restricted-agent#8, so trainee-agent#8 was never seated beside her
            # mentor even though swapping her in for either of them is legal.
            _target = next(
                (_j for _j in _options + _veterans + _restricted_opts
                 if _restr_count(_j) + 1 <= _max_restr),
                None)
            if _target is None:
                continue
            _cands2 = get_qualified_candidates_for_swap(
                df, employees_df, _flt, str(df.at[_target, "תפקיד בסיס"]).strip(), _target)
            _late = False
            if not any(_ws(_c) == _tset for _c in _cands2):
                # The mentor is REQUIRED on this flight, so their trainee comes
                # before an unrelated restricted worker (user rule 2026-08-06,
                # LY081: "צריך לתת עדיפות לטרייני של ר\"צ TL#39 על פני restricted-agent#8, כי TL#39 היא חובה בטיסה הזאת"). Accept a SHORT
                # shortfall at the start of the shift — the role's start time is
                # a computed prep time, and on LY081 the דייל role opens 21:55
                # while trainee-agent#8's shift begins 22:00, five minutes later. The
                # slot is tagged "הגעה באיחור" so the workflow shows it plainly
                # instead of hiding the deviation. Anything else (a real clash,
                # the wrong terminal, a missing certification, the quota) still
                # blocks — this only forgives the start-of-shift edge.
                _tr_row2 = _emp_by_ws.get(_tset)
                _ts2 = to_datetime_time(str(df.at[_target, "התחלה"]))
                _te2 = to_datetime_time(str(df.at[_target, "סיום"]))
                _ss2 = clean_text(str(_tr_row2.get("תחילת משמרת", ""))) if _tr_row2 is not None else ""
                if not (_tr_row2 is not None and _ts2 is not None and _te2 is not None
                        and is_time_text(_ss2)):
                    continue
                _short = (time_to_minutes(_ss2) - (_ts2.hour * 60 + _ts2.minute)) % 1440
                if _short > 15:
                    continue
                # must still be free and allowed from their own shift start on
                _ok_from_start = False
                try:
                    _ok_from_start = is_within_shift(
                        _tr_row2, to_datetime_time(_ss2), _te2,
                        task_terminal=(get_terminal(clean_text(str(df.at[_target, "_gate"])))
                                       if "_gate" in df.columns else None))
                except Exception:
                    _ok_from_start = False
                if not _ok_from_start:
                    continue
                _busy2 = False
                for _k in df.index:
                    if _k == _target or _ws(df.at[_k, "עובד"]) != _tset:
                        continue
                    _ks = to_datetime_time(str(df.at[_k, "התחלה"]))
                    _ke = to_datetime_time(str(df.at[_k, "סיום"]))
                    if _ks is None or _ke is None:
                        continue
                    _ksm = _ks.hour * 60 + _ks.minute
                    _kem = _ke.hour * 60 + _ke.minute
                    _kem = _kem if _kem > _ksm else _kem + 1440
                    _tsm = _ts2.hour * 60 + _ts2.minute
                    _tem = _te2.hour * 60 + _te2.minute
                    _tem = _tem if _tem > _tsm else _tem + 1440
                    if not (_tem <= _ksm or _tsm >= _kem):
                        _busy2 = True
                        break
                if _busy2:
                    continue
                _late = True
            df.at[_target, "עובד"] = _tr_name
            if "הגעה באיחור" not in df.columns:
                df["הגעה באיחור"] = False
            df.at[_target, "הגעה באיחור"] = bool(_late)
            if "סיבה" in df.columns:
                df.at[_target, "סיבה"] = (
                    "דייל בטרייני - מלווה חונך (מגיע/ה עם תחילת המשמרת)" if _late
                    else "דייל בטרייני - מלווה חונך")

    return df


# =========================
# AUTO REASSIGN
# =========================

_MAX_INTER_FLIGHT_GAP = 25   # דקות — פער מקסימלי בין טיסות עוקבות


def _ar_tm(t):
    """HH:MM → minutes, None on error."""
    try:
        h, m = str(t).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _ar_can_fill(emp_row, role):
    """True if employee is qualified to fill the given role."""
    role = str(role).strip()
    if not _manager_may_take(emp_row, role):
        return False
    if role in ("דייל", "מתאם תורים"):
        return True
    if role == "ראש צוות":
        return str(emp_row.get("ראש צוות", "")).strip() == "כן"
    if role == "מפקח TSA":
        # Certification flag alone isn't enough — a worker whose מחלקה מקורית
        # marks them as belonging to a different department (e.g. "חוליה"/
        # מל"ן) shouldn't be auto-reassigned into a TSA-inspector slot (same
        # rule as the primary build pass; user-reported 2026-07-29). An EMPTY
        # מחלקה מקורית means "no other department" — but pandas reads a blank
        # cell as NaN, and the raw str(NaN).strip() this used was the literal
        # "nan" (truthy), so it silently excluded 11 of the 16 TSA-certified
        # employees in real employees_clean.xlsx (anyone with a genuinely
        # blank cell) from ever being offered for auto-reassignment — the
        # SAME root cause already fixed elsewhere (get_qualified_candidates_
        # for_swap, found via real data 2026-08-09) but missed here (user
        # rule 2026-09-05). clean_text() correctly turns NaN into "".
        if clean_text(emp_row.get("מחלקה מקורית", "")):
            return False
        return str(emp_row.get("מפקח TSA", "")).strip() == "כן"
    if role in ("שומר TSA", "שומר"):
        return True  # כל עובד יכול
    return True


def _ar_is_tl(emp_row):
    return str(emp_row.get("ראש צוות", "")).strip() == "כן"


def _ar_role_penalty(emp_row, role):
    """1 = פחות עדיף (ראש צוות לתפקיד שאינו ר"צ)."""
    if _ar_is_tl(emp_row) and str(role).strip() not in ("ראש צוות",):
        return 1
    return 0


def _ar_conflicts(sched, name, start_m, end_m, skip_idx=None, role=None, gate=None, emp_row=None):
    """True if employee has any task overlapping [start_m, end_m].

    role/gate (of the NEW task being reassigned): when it's "מפקח TSA", an
    overlapping task is not a real conflict if IT is also מפקח TSA in the
    SAME PIER (שלוחה, e.g. B/C/D/E) — a TSA inspector legitimately covers
    several nearby gates at once. Mirrors is_available()'s own TSA-same-
    pier exception (and its 4-concurrent cap), which auto-reassign's own
    conflict check didn't know about at all (user rule 2026-09-05, same
    gap found and fixed in force_assign_worker /
    get_qualified_candidates_for_swap).

    emp_row, when given, is also checked against חסימות (blocked windows —
    training courses, committee meetings, TSA refreshers) — auto-reassign's
    own conflict check never looked at these at all, same root-cause family
    as the is_available() UnboundLocalError fix (user rule 2026-09-05)."""
    if emp_row is not None and _is_blocked_by_window(emp_row, start_m, end_m, role=role):
        return True
    _is_tsa = role == "מפקח TSA"
    _pier = get_terminal(clean_text(str(gate))) if (_is_tsa and gate) else ""
    rows = sched[sched["עובד"].astype(str).str.strip() == name]
    _same_pier_tsa_overlaps = 0
    _real_conflict = False
    for idx, r in rows.iterrows():
        if skip_idx is not None and idx == skip_idx:
            continue
        s = _ar_tm(r.get("התחלה", ""))
        e = _ar_tm(r.get("סיום", ""))
        if s is None or e is None:
            continue
        if end_m <= s or e <= start_m:
            continue
        if (_is_tsa and _pier and str(r.get("תפקיד בסיס", "")).strip() == "מפקח TSA"
                and get_terminal(clean_text(str(r.get("_gate", "")))) == _pier):
            _same_pier_tsa_overlaps += 1
            continue
        _real_conflict = True
    if _real_conflict:
        return True
    return _same_pier_tsa_overlaps >= 4


def _ar_in_shift(emp_row, start_m, end_m, task_terminal=None):
    """True if task fits within employee's shift hours (cross-midnight safe).

    task_terminal, when given ("1" or "3"), also gates on the worker's own
    base terminal ("טרמינל" column) — this simplified reimplementation of
    is_within_shift's terminal check was missing entirely, so auto-reassign
    could offer a Terminal-1-only worker for a Terminal-3 flight and vice
    versa (found via real data 2026-07-30: TL#23, טרמינל 1, offered as a
    replacement for LY315/T3). Doesn't replicate the fuller "זמינות טרמינל"
    multi-window transfer-worker logic in is_within_shift — just the same
    base-terminal fallback used when a worker has no explicit windows."""
    if task_terminal is not None:
        _want_term = "1" if clean_text(task_terminal) == "1" else "3"
        _base_term = clean_text(emp_row.get("טרמינל", "")) or "3"
        if _base_term != _want_term:
            return False
    ss = _ar_tm(emp_row.get("תחילת משמרת", ""))
    se = _ar_tm(emp_row.get("סוף משמרת", ""))
    if ss is None or se is None:
        return True
    shift_len = (se - ss) % 1440 or 1440
    # Normalize task times relative to shift start so cross-midnight shifts
    # (e.g. 22:00-06:00) correctly include early-morning slots.
    n_start = (start_m - ss) % 1440
    n_end   = (end_m   - ss) % 1440
    if n_end <= n_start: n_end += 1440
    return n_start >= 0 and n_end <= shift_len


def _ar_task_count(sched, name):
    return int(
        (sched["עובד"].astype(str).str.strip() == name).sum()
    )


def _ar_priority_type(sched, name, break_log, task_start_m, task_end_m):
    """
    1 = lounge-bound (next flight starts within MAX_GAP of task_end)
    2 = returning from break (break ends near task_start, was going to counters)
    None = not priority 1 or 2
    """
    # Priority 1 — already going to lounge for a later flight
    worker_tasks = sched[
        (sched["עובד"].astype(str).str.strip() == name) &
        (~sched["עובד"].astype(str).str.contains("❌", na=False))
    ]
    for _, t in worker_tasks.iterrows():
        t_start = _ar_tm(t.get("התחלה", ""))
        if t_start is None:
            continue
        gap = t_start - task_end_m
        if 0 <= gap <= _MAX_INTER_FLIGHT_GAP:
            return 1

    # Priority 2 — returning from break to counters
    binfo = break_log.get(name, {})
    if binfo.get("active") and not binfo.get("end"):
        # break is active but not yet ended — won't help
        return None
    if binfo.get("end"):
        break_end_m = _ar_tm(binfo.get("end", ""))
        if break_end_m is not None and abs(break_end_m - task_start_m) <= _MAX_INTER_FLIGHT_GAP:
            return 2

    return None


def auto_reassign_removed_employee(schedule_df, employees_df, removed_name, break_log=None, excluded_workers=None):
    """
    מבצע שיבוץ אוטומטי לטיסות של עובד שהורד ממשמרת.
    מחזיר schedule_df מעודכן.

    סדר עדיפויות:
      1. עובד שממילא יורד לאולם (טיסה עתידית, פער ≤ 25 דק')
      2. עובד החוזר מהפסקה לדלפקים
      3. עובד שאינו משובץ לאף טיסה — מקבל את כל הטיסות הנותרות

    excluded_workers: dict {task_idx: set(worker_names)} — עובדים לדלג עליהם לכל משימה
    """
    break_log = break_log or {}
    excluded_workers = excluded_workers or {}

    removed_tasks = schedule_df[
        schedule_df["עובד"].astype(str).str.strip() == removed_name
    ].copy()

    if removed_tasks.empty:
        return schedule_df

    sched = schedule_df.copy()

    emp_map = {
        str(r.get("שם", "")).strip(): r.to_dict()
        for _, r in employees_df.iterrows()
        if str(r.get("שם", "")).strip() and str(r.get("שם", "")).strip() != removed_name
    }

    # Exclude workers who are NOT on today's roster. `_ar_in_shift` returns
    # True when תחילת/סוף משמרת are blank (which they are for an
    # employees_clean worker absent from today's daily Excel), so without this
    # guard the auto-reassign would offer someone who isn't working today.
    # Same "daily Excel loaded" guard as sort_candidates (only filter when
    # SOME workers are marked present, else a no-daily-Excel run drops
    # everyone). Found via real 20.07 data: TL#11 (in_daily_excel=0,
    # blank shift) was proposed to replace TL#9.
    if "in_daily_excel" in employees_df.columns:
        _ide = pd.to_numeric(employees_df["in_daily_excel"], errors="coerce").fillna(0)
        if (_ide >= 1).any() and (_ide < 1).any():
            _present_today = set(
                employees_df.loc[_ide >= 1, "שם"].astype(str).str.strip()
            )
            emp_map = {n: e for n, e in emp_map.items() if n in _present_today}

    # מיין לפי שעת התחלה
    sorted_tasks = removed_tasks.sort_values(
        by="התחלה",
        key=lambda col: col.apply(lambda v: _ar_tm(v) or 9999)
    )

    uncovered_indices = []

    # ── עדיפות 0 — מצא עובד יחיד שיכסה את כל המשימות ───────────────────
    _all_task_indices = set(sorted_tasks.index)
    _best_single = None
    _best_single_score = None

    for _name, _emp in emp_map.items():
        _ok = True
        for _tidx, _task in sorted_tasks.iterrows():
            if _name in excluded_workers.get(_tidx, set()):
                _ok = False
                break
            _role = str(_task.get("תפקיד בסיס", "")).strip()
            _sm = _ar_tm(_task.get("התחלה", ""))
            _em = _ar_tm(_task.get("סיום", ""))
            if _sm is None or _em is None:
                continue
            if not _ar_can_fill(_emp, _role):
                _ok = False
                break
            if not _ar_in_shift(_emp, _sm, _em, task_terminal=get_terminal(clean_text(str(_task.get("_gate", ""))))):
                _ok = False
                break
            # בדוק התנגשויות עם משימות קיימות — מדלג על כל משימות העובד המוסר.
            # A מפקח TSA already covering a same-pier overlapping task isn't
            # a real conflict (up to 4 concurrent) — same exemption as
            # _ar_conflicts() below (user rule 2026-09-05).
            _is_tsa_task = _role == "מפקח TSA"
            _task_pier = get_terminal(clean_text(str(_task.get("_gate", "")))) if _is_tsa_task else ""
            _same_pier_tsa_ct = 0
            _cand_rows = sched[sched["עובד"].astype(str).str.strip() == _name]
            for _cidx, _cr in _cand_rows.iterrows():
                if _cidx in _all_task_indices:
                    continue
                _cs = _ar_tm(_cr.get("התחלה", ""))
                _ce = _ar_tm(_cr.get("סיום", ""))
                if _cs is None or _ce is None:
                    continue
                if _em <= _cs or _ce <= _sm:
                    continue
                if (_is_tsa_task and _task_pier
                        and str(_cr.get("תפקיד בסיס", "")).strip() == "מפקח TSA"
                        and get_terminal(clean_text(str(_cr.get("_gate", "")))) == _task_pier):
                    _same_pier_tsa_ct += 1
                    continue
                _ok = False
                break
            if _ok and _same_pier_tsa_ct >= 4:
                _ok = False
            if _ok and _is_blocked_by_window(_emp, _sm, _em, role=_role):
                _ok = False
            if not _ok:
                break

        if _ok:
            _penalty  = sum(_ar_role_penalty(_emp, str(t.get("תפקיד בסיס", ""))) for _, t in sorted_tasks.iterrows())
            _workload = _ar_task_count(sched, _name)
            _score    = (_penalty, _workload)
            if _best_single_score is None or _score < _best_single_score:
                _best_single_score = _score
                _best_single = _name

    if _best_single:
        for _tidx in _all_task_indices:
            sched.at[_tidx, "עובד"] = _best_single
        return sched

    # ── עדיפות 1 ו-2 — טיפול פר-טיסה ──────────────────────────────────
    for task_idx, task in sorted_tasks.iterrows():
        role = str(task.get("תפקיד בסיס", "")).strip()
        start_m = _ar_tm(task.get("התחלה", ""))
        end_m   = _ar_tm(task.get("סיום", ""))

        if start_m is None or end_m is None:
            sched.at[task_idx, "עובד"] = "❌ (חסר)"
            continue

        best = None
        best_score = None

        for name, emp in emp_map.items():
            if name in excluded_workers.get(task_idx, set()):
                continue
            if not _ar_can_fill(emp, role):
                continue
            if _ar_conflicts(sched, name, start_m, end_m, skip_idx=task_idx,
                              role=role, gate=task.get("_gate", ""), emp_row=emp):
                continue
            if not _ar_in_shift(emp, start_m, end_m, task_terminal=get_terminal(clean_text(str(task.get("_gate", ""))))):
                continue

            ptype = _ar_priority_type(sched, name, break_log, start_m, end_m)
            if ptype is None:
                continue  # עדיפות 1/2 בלבד בסבב זה

            penalty  = _ar_role_penalty(emp, role)
            workload = _ar_task_count(sched, name)
            score    = (ptype, penalty, workload)

            if best_score is None or score < best_score:
                best_score = score
                best = name

        if best:
            sched.at[task_idx, "עובד"] = best
        else:
            uncovered_indices.append(task_idx)
            sched.at[task_idx, "עובד"] = "❌ (חסר)"

    # ── עדיפות 3 — עובד שאינו משובץ לאף טיסה ─────────────────────────
    if uncovered_indices:
        # בנה רשימת עובדים ללא טיסות כלל
        all_assigned = set(sched["עובד"].astype(str).str.strip().unique())
        unscheduled_workers = [
            (name, emp) for name, emp in emp_map.items()
            if name not in all_assigned or _ar_task_count(sched, name) == 0
        ]

        # עבור כל עובד חופשי — בדוק כמה טיסות הוא יכול לכסות
        best_worker = None
        best_coverage = -1

        for name, emp in unscheduled_workers:
            coverage = 0
            for task_idx in uncovered_indices:
                if name in excluded_workers.get(task_idx, set()):
                    continue
                task = removed_tasks.loc[task_idx]
                role    = str(task.get("תפקיד בסיס", "")).strip()
                start_m = _ar_tm(task.get("התחלה", ""))
                end_m   = _ar_tm(task.get("סיום", ""))
                if start_m is None or end_m is None:
                    continue
                if not _ar_can_fill(emp, role):
                    continue
                if _ar_conflicts(sched, name, start_m, end_m, role=role, gate=task.get("_gate", ""), emp_row=emp):
                    continue
                if not _ar_in_shift(emp, start_m, end_m, task_terminal=get_terminal(clean_text(str(task.get("_gate", ""))))):
                    continue
                coverage += 1

            # עדיפות לעובד שמכסה הכי הרבה טיסות; בין שווים — פחות ראש צוות
            is_tl = _ar_is_tl(emp)
            score = (-coverage, 1 if is_tl else 0)
            if best_worker is None or score < (-best_coverage, 0):
                best_worker = (name, emp)
                best_coverage = coverage

        if best_worker:
            name, emp = best_worker
            for task_idx in uncovered_indices:
                if name in excluded_workers.get(task_idx, set()):
                    continue
                task    = removed_tasks.loc[task_idx]
                role    = str(task.get("תפקיד בסיס", "")).strip()
                start_m = _ar_tm(task.get("התחלה", ""))
                end_m   = _ar_tm(task.get("סיום", ""))
                if start_m is None or end_m is None:
                    continue
                if not _ar_can_fill(emp, role):
                    continue
                if _ar_conflicts(sched, name, start_m, end_m, role=role, gate=task.get("_gate", ""), emp_row=emp):
                    continue
                if not _ar_in_shift(emp, start_m, end_m, task_terminal=get_terminal(clean_text(str(task.get("_gate", ""))))):
                    continue
                sched.at[task_idx, "עובד"] = name

    return sched


def _tl_peak_items(schedule_df):
    tl = schedule_df[schedule_df["תפקיד בסיס"].astype(str) == "ראש צוות"].copy()
    tl = tl[tl["התחלה"].apply(lambda x: is_time_text(clean_text(str(x))))]
    tl = tl[tl["סיום"].apply(lambda x: is_time_text(clean_text(str(x))))]
    items = []
    for _, r in tl.iterrows():
        # Use the schedule's own "התחלה"/"סיום" — already exactly
        # role_start_time/role_end_time (60/75/85 min before departure,
        # varying by aircraft body + remote gate), the real window a ר"צ
        # occupies. No flat approximation: a narrow-body 05:30 departure
        # frees its ר"צ at 05:30 for a 06:30 (narrow) or 06:45 (wide-body)
        # next flight, but NOT a 06:25 one (that needs the gate opened at
        # 05:25, while still on the 05:30 flight) — user rule 2026-09-05.
        s = time_to_minutes(clean_text(str(r["התחלה"])))
        e = time_to_minutes(clean_text(str(r["סיום"])))
        # operational-day axis: pivot at 02:00 so the night tail sorts after
        # the fresh day, matching the missing-roles diagnostic's convention.
        s_op = (s - 120) % 1440
        e_op = s_op + ((e - s) % 1440 if e != s else 60)
        worker = str(r.get("עובד", ""))
        items.append({
            "flight": r["טיסה"], "start_clock": s, "end_clock": e,
            "s": s_op, "e": e_op,
            "filled": "❌" not in worker,
            "worker": worker if "❌" not in worker else "",
            "dest": clean_text(str(r.get("יעד", ""))).upper(),
        })
    items.sort(key=lambda x: x["s"])
    return items


def _tl_time_of_day(minutes):
    """Port of the reference tool's time-of-day bucketing — buckets a minute-of-day
    value into the same 6 Hebrew day-part labels used by the company's
    existing peak report."""
    m = minutes % 1440
    if 240 <= m < 420:
        return "בוקר"
    if 420 <= m < 720:
        return "יום"
    if 720 <= m < 900:
        return "צהריים"
    if 900 <= m < 1080:
        return 'אחה"צ'
    if 1080 <= m < 1380:
        return "ערב"
    return "לילה"


def _tl_reference_peaks(items, min_peak_size=5):
    """Port of the reference tool's peak analysis (from an internal
    reference tool — user rule 2026-09-05, ground truth already in
    production use). Repeatedly finds the anchor flight whose forward-
    looking block — every not-yet-assigned flight departing at or after the
    anchor (f["e"] >= anchor["e"]) whose own role-window has already opened
    by the anchor's departure (f["s"] < anchor["e"]) — is the largest, and
    extracts it as one peak; stops once the best remaining block is smaller
    than min_peak_size (5, matching the reference tool). Unlike a fixed-
    width sliding window, this adapts to however many flights are actually
    piled up at each moment, and greedily peels the biggest cluster first.
    Returns a list of flight-groups (each a list of items)."""
    unassigned = list(items)
    peaks = []
    while True:
        best_block = []
        for anchor in unassigned:
            block = [f for f in unassigned if f["e"] >= anchor["e"] and f["s"] < anchor["e"]]
            if len(block) > len(best_block):
                best_block = block
        if len(best_block) >= min_peak_size:
            peaks.append(best_block)
            block_flights = {f["flight"] for f in best_block}
            unassigned = [f for f in unassigned if f["flight"] not in block_flights]
        else:
            break
    return peaks


def _tl_label_peaks(peak_groups):
    """Port of the reference tool's grouping/labeling: peaks are grouped by
    (day, time-of-day part) of their earliest flight; within each group,
    ranked by "effective size" (flight count + a bonus per BKK/HKT flight,
    since those need 2 ר"צ — same TWO_TEAM_LEADS_DESTS rule as the real
    scheduler); the biggest is the plain peak, earlier ones in the group
    are "מקדים" (preceding), later ones "משני" (secondary). Returns a
    chronological list of (flights, label) tuples."""
    groups = {}
    for flights in peak_groups:
        flights = sorted(flights, key=lambda f: f["s"])
        first_dep_op = flights[0]["s"]
        part = _tl_time_of_day(flights[0]["start_clock"])
        day_idx = first_dep_op // 1440
        thai_count = sum(1 for f in flights if f["dest"] in ("BKK", "HKT"))
        groups.setdefault((day_idx, part), []).append({
            "flights": flights,
            "part": part,
            "effective_size": len(flights) + thai_count,
            "first_s": first_dep_op,
        })

    labeled = []
    for (_, part), group in groups.items():
        group.sort(key=lambda p: p["first_s"])
        max_effective = max(p["effective_size"] for p in group)
        main_idx = next(i for i, p in enumerate(group) if p["effective_size"] == max_effective)
        for idx, p in enumerate(group):
            if len(group) > 1:
                if idx < main_idx:
                    label = f"פיק {part} מקדים"
                elif idx > main_idx:
                    label = f"פיק {part} משני"
                else:
                    label = f"פיק {part}"
            else:
                label = f"פיק {part}"
            labeled.append((p["flights"], label))

    labeled.sort(key=lambda x: x[0][0]["s"])
    return labeled


def _tl_min_required(items):
    """Minimum distinct ר"צ needed to cover every flight in items, via a
    greedy min-resources sweep over the REAL role windows (see
    _tl_peak_items) — touching endpoints (one window ends exactly when the
    next starts) are fine, no extra buffer on top (user rule 2026-09-05,
    same principle as the real scheduler's own back-to-back assignment
    logic)."""
    import heapq

    heap = []  # each entry = the time its ר"צ becomes free (== previous departure)
    for it in sorted(items, key=lambda x: x["s"]):
        if heap and heap[0] <= it["s"]:
            heapq.heapreplace(heap, it["e"])
        else:
            heapq.heappush(heap, it["e"])
    return len(heap)


def analyze_tl_peaks(schedule_df):
    """Find the day's ר"צ demand peaks using the same algorithm as the
    an existing internal reference tool (user rule 2026-09-05 —
    ground truth already in production use; ported from that tool's
    its peak-analysis logic), instead of an independently-invented
    sliding-window heuristic. See _tl_reference_peaks for the anchor/block
    extraction and _tl_label_peaks for the מקדים/(plain)/משני labeling.

    For each identified peak, also reports the minimum number of distinct
    ר"צ needed to cover it (via real, body-type-dependent role windows and
    legitimate back-to-back reuse — see _tl_min_required) against how many
    distinct real ר"צ the actual built schedule used — the reference tool
    itself only labels/sizes peaks, it doesn't compute staffing adequacy.

    Returns a chronological list of dicts (operational day pivoted at
    02:00), each with: start_clock, label (e.g. "פיק בוקר מקדים"),
    n_flights, required_rc (minimum distinct ר"צ, via _tl_min_required),
    available_rc (distinct real ר"צ actually assigned), unfilled, and
    flights (list of (flight, start, end, filled) tuples).
    """
    items = _tl_peak_items(schedule_df)
    peak_groups = _tl_reference_peaks(items)
    labeled = _tl_label_peaks(peak_groups)

    results = []
    for flights, label in labeled:
        distinct_workers = {it["worker"] for it in flights if it["worker"]}
        results.append({
            "start_clock": min(it["start_clock"] for it in flights),
            "label": label,
            "flights": [(it["flight"], it["start_clock"], it["end_clock"], it["filled"]) for it in flights],
            "n_flights": len(flights),
            "required_rc": _tl_min_required(flights),
            "available_rc": len(distinct_workers),
            "unfilled": sum(1 for it in flights if not it["filled"]),
        })
    return results


# A gap between two of a worker's tasks up to this many minutes is ordinary
# transition time; longer than this (and shorter than 2h) is idle waiting in the
# hall unless it is the one break / רענון the worker owes.
_GAP_OK = 30


def _idle_gap_waste(emp_row, gaps):
    """Idle minutes among a worker's between-task `gaps` that are NOT owed.

    A gap up to `_GAP_OK` is transition time and 120+ min is a real return to
    the counters. In between is idle waiting in the hall — except for the gap
    that IS the break a 6h+ shift owes (>= required_break), and, on a 10h+
    shift, the second (רענון) gap. A worker on a SHORT shift (under 6h) owes
    no 45-min break; their 20-min רענון fits inside `_GAP_OK`, so a 45-min hole
    between two of their flights is pure waste (user 2026-09-20, trainee-agent#10)."""
    rb = required_break(emp_row) or 0
    long_shift = shift_length(emp_row) >= 10 * 60
    mid = sorted((g for g in gaps if _GAP_OK < g < 120), reverse=True)
    free = 0
    if rb and mid and mid[0] >= rb:
        free = 2 if long_shift else 1
    return sum(mid[free:])


def boost_runner_floor_time(schedule_df, employees_df):
    """ר"צים get priority over plain attendants for floor work.

    User rule 2026-09-20 ("לרצים צריך להיות עדיפות על שיבוץ על פני דיילים"),
    found on the real 20.09 schedule: TL#7 / TL#48 / TL#52
    (ר"צ, 03:30-12:30) held ONE flight each across a nine-hour shift while
    plain attendants worked full chains. Two mechanisms produced that:
      * the main pass ranks every ר"צ-qualified worker AFTER all ordinary
        attendants for דייל / מתאם תורים slots (`tl_preserve`, meant to keep
        ר"צים free for a later ר"צ slot), and
      * no later pass ever hands a ר"צ extra attendant work.
    This pass runs after every ר"צ slot is already staffed, so using a ר"צ as
    an attendant can no longer cost a ר"צ slot. Repeatedly (least-loaded ר"צ
    first, one slot per ר"צ per round) it gives a ר"צ a דייל / מתאם תורים slot
    currently held by a plain attendant, when ALL of:
      * the ר"צ is certified for the slot, inside their shift/terminal window,
        conflict-free (is_available: the same 5-min / T1 zero-gap rules) and
        not blocked (חסימות / duty-block);
      * their break entitlement survives (has_break_gap_in_schedule against
        the shift end) and the move does not ADD idle time between their
        tasks (see _idle_gap_waste);
      * the flight is not a USA/TSA flight (user rule: ר"צ as דייל there only
        when nobody else is available) and carries no דייל-בטרייני (a trainee
        must stay with their mentor);
      * the displaced worker is a plain attendant (not ר"צ / runner / trainee)
        and the row is not locked from an earlier segment.
    Never touches ר"צ, מפקח, שומר or ❌ rows, so it cannot open a shortage."""
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

    def _yn(row, col):
        return clean_text(str(row.get(col, ""))) == "כן"

    def _m(t):
        v = to_datetime_time(t)
        return None if v is None else v.hour * 60 + v.minute

    runners = []
    for nm, row in emp_map.items():
        if not nm or not _yn(row, "ראש צוות"):
            continue
        if _yn(row, "מתדרכת") or _yn(row, "טרייני רצ") or _is_crew_manager(row):
            continue
        if row.get("תגבור שלוחה") is True or row.get("חולה") is True:
            continue
        if DUTY_BLOCK_COL in row and clean_text(str(row.get(DUTY_BLOCK_COL, ""))):
            continue
        if not (is_time_text(clean_text(str(row.get("תחילת משמרת", "")))) and
                is_time_text(clean_text(str(row.get("סוף משמרת", ""))))):
            continue
        runners.append(nm)
    if not runners:
        return schedule_df

    _ATT_ROLES = ("דייל", "מתאם תורים")
    locked_col = "_נעול" if "_נעול" in df.columns else None

    def _real(name):
        return bool(name) and "❌" not in name

    recs = df.to_dict("records")
    for i, r in zip(df.index, recs):
        r["_i"] = i
        r["עובד"] = clean_text(str(r["עובד"]))
        r["טיסה"] = clean_text(str(r["טיסה"]))
        r["תפקיד בסיס"] = clean_text(str(r.get("תפקיד בסיס", "")))
        s, e = _m(r["התחלה"]), _m(r["סיום"])
        r["_s"], r["_e"] = s, (None if s is None or e is None else (e if e > s else e + 1440))
    by_name, flight_workers = {}, {}
    for r in recs:
        if _real(r["עובד"]):
            flight_workers.setdefault(r["טיסה"], set()).add(r["עובד"])
            if r["_s"] is not None and r["_e"] is not None:
                by_name.setdefault(r["עובד"], []).append(r)
    trainee_flights = {f for f, ws in flight_workers.items()
                       if any(w in emp_map and _yn(emp_map[w], "דייל בטרייני") for w in ws)}

    _plain_cache = {}

    def _plain(name):
        if name not in _plain_cache:
            row = emp_map.get(name)
            _plain_cache[name] = row is not None and not any(
                _yn(row, c) for c in ("ראש צוות", "טרייני רצ", "חונך רצים", "מסמיך רצים",
                                      "דייל בטרייני"))
        return _plain_cache[name]

    slots = [r for r in recs
             if r["תפקיד בסיס"] in _ATT_ROLES and _real(r["עובד"])
             and r["_s"] is not None and r["_e"] is not None
             and clean_text(str(r.get("יעד", ""))).strip() not in USA_TSA_DESTS
             and r["טיסה"] not in trainee_flights
             and not (locked_col and r.get(locked_col) is True)]

    def _waste(name, extra=None):
        row = emp_map[name]
        _ss = clean_text(str(row.get("תחילת משמרת", "")))
        if not is_time_text(_ss):
            return 0
        anchor = time_to_minutes(_ss)
        items = [(t["_s"], t["_e"]) for t in by_name.get(name, [])]
        if extra:
            items.append(extra)
        rel = sorted(((s - anchor) % 1440, ((s - anchor) % 1440) + (e - s)) for s, e in items)
        return _idle_gap_waste(row, [b[0] - a[1] for a, b in zip(rel, rel[1:])])

    def _break_ok(name, extra_task):
        row = emp_map[name]
        if (required_break(row) or 0) <= 0 or is_night_shift_for_return_rule(row):
            return True
        se = to_datetime_time(clean_text(str(row.get("סוף משמרת", ""))))
        return has_break_gap_in_schedule(None, name, row, se,
                                         emp_tasks=by_name.get(name, []) + [extra_task])

    def _load(name):
        return sum(t["_e"] - t["_s"] for t in by_name.get(name, []))

    def _best_slot(rn):
        rrow = emp_map[rn]
        my_tasks = by_name.get(rn, [])
        my_flights = {t["טיסה"] for t in my_tasks}
        before = _waste(rn)
        ss = time_to_minutes(clean_text(str(rrow.get("תחילת משמרת", ""))))
        best = None
        for r in slots:
            holder = r["עובד"]
            role = r["תפקיד בסיס"]
            if holder == rn or not _plain(holder) or r["טיסה"] in my_flights:
                continue
            if not _yn(rrow, role):
                continue
            ts_t, te_t = to_datetime_time(r["התחלה"]), to_datetime_time(r["סיום"])
            if ts_t is None or te_t is None:
                continue
            gate = clean_text(str(r.get("_gate", "")))
            term = get_terminal(gate)
            if not is_within_shift(rrow, ts_t, te_t, task_terminal=term):
                continue
            if not is_available(None, rn, ts_t, te_t, rrow, role, gate, emp_tasks=my_tasks):
                continue
            if term == "1" and 0 <= (r["_s"] - ss) % 1440 < 45:      # fresh-start rule (T1)
                continue
            after = _waste(rn, (r["_s"], r["_e"]))
            if after > before:
                continue
            if not _break_ok(rn, r):
                continue
            key = (after - before, -_load(holder), r["_s"])
            if best is None or key < best[0]:
                best = (key, r)
        return best

    changed = 0
    for _round in range(12):
        progressed = False
        for rn in sorted(runners, key=_load):
            slot = _best_slot(rn)
            if slot is None:
                continue
            _key, r = slot
            holder = r["עובד"]
            by_name[holder].remove(r)
            flight_workers[r["טיסה"]].discard(holder)
            r["עובד"] = rn
            by_name.setdefault(rn, []).append(r)
            flight_workers[r["טיסה"]].add(rn)
            df.at[r["_i"], "עובד"] = rn
            df.at[r["_i"], "סיבה"] = ("שובץ/ה לפי עדיפות ראש צוות על דייל — מקסום שהות באולם "
                                      "(במקום " + holder + ")")
            progressed = True
            changed += 1
        if not progressed:
            break
    return df if changed else schedule_df


def compact_idle_gaps(schedule_df, employees_df):
    """Swap two plain attendants' tasks so neither waits idle between flights.

    User rule 2026-09-20, found on the real 20.09 schedule: trainee-agent#10
    (02:00-07:00, under 6 hours so NO break owed) had 45 minutes between her
    two flights — pure waiting in the departure hall. A task is 50-70 min
    long, so a 45-min hole can never be filled with another flight; the only
    cure is to change WHICH flight she works. The main pass and every later
    pass only add or remove work, never trade it, so such holes stayed.

    For a worker W with idle time between two attendant tasks (a gap over
    `_GAP_OK` minutes that is neither the ONE break / רענון they owe nor a
    120+ min return to the counters), look for a task C held by another plain
    attendant Y and trade W's task B for it: W takes C, Y takes B. Applied
    only when
      * both workers are certified for the role they receive, inside their
        shift/terminal window, conflict-free (is_available) and unblocked;
      * neither is a ר"צ / runner / trainee / מתדרכת / restricted worker;
      * neither flight carries a דייל-בטרייני and neither worker is already
        on the flight they would move to;
      * each keeps the break they owe (has_break_gap_in_schedule) and the
        COMBINED idle time of the two strictly drops;
      * neither row is locked from an earlier segment.
    Each round applies only the single best trade, so it terminates quickly."""
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

    def _yn(row, col):
        return clean_text(str(row.get(col, ""))) == "כן"

    def _m(t):
        v = to_datetime_time(t)
        return None if v is None else v.hour * 60 + v.minute

    _ATT_ROLES = ("דייל", "מתאם תורים")
    locked_col = "_נעול" if "_נעול" in df.columns else None

    _plain_cache = {}

    def _plain(name):
        if name not in _plain_cache:
            row = emp_map.get(name)
            _plain_cache[name] = row is not None and not any(
                _yn(row, c) for c in ("ראש צוות", "טרייני רצ", "חונך רצים", "מסמיך רצים",
                                      "דייל בטרייני", "מתדרכת", "ילד עובדים", "ילדי עובדים", "פורשים")
            ) and not _is_crew_manager(row)
        return _plain_cache[name]

    def _may_move(name):
        """The WAITING worker may be any attendant except trainees, מתדרכת,
        restricted workers and managers (a חונך רצים waiting 45 min between
        flights is exactly the case this pass exists for); only the worker who
        RECEIVES a task must be a plain attendant (see _plain)."""
        row = emp_map.get(name)
        return row is not None and not any(
            _yn(row, c) for c in ("טרייני רצ", "דייל בטרייני", "מתדרכת", "ילד עובדים",
                                  "ילדי עובדים", "פורשים")) and not _is_crew_manager(row)

    def _real(name):
        return bool(name) and "❌" not in name

    def _waste_of(name, tasks):
        row = emp_map[name]
        _ss = clean_text(str(row.get("תחילת משמרת", "")))
        if not is_time_text(_ss):
            return 0                 # no shift on today's roster: nothing to measure
        anchor = time_to_minutes(_ss)
        rel = sorted(((t["_s"] - anchor) % 1440, ((t["_s"] - anchor) % 1440) + (t["_e"] - t["_s"]))
                     for t in tasks)
        return _idle_gap_waste(row, [b[0] - a[1] for a, b in zip(rel, rel[1:])])

    def _break_ok(name, tasks):
        row = emp_map[name]
        if (required_break(row) or 0) <= 0 or is_night_shift_for_return_rule(row):
            return True
        se = to_datetime_time(clean_text(str(row.get("סוף משמרת", ""))))
        return has_break_gap_in_schedule(None, name, row, se, emp_tasks=list(tasks))

    def _can_take(name, task, others):
        er = emp_map[name]
        role = task["תפקיד בסיס"]
        if not _yn(er, role):
            return False
        ts_t, te_t = to_datetime_time(task["התחלה"]), to_datetime_time(task["סיום"])
        if ts_t is None or te_t is None:
            return False
        gate = clean_text(str(task.get("_gate", "")))
        if not is_within_shift(er, ts_t, te_t, task_terminal=get_terminal(gate)):
            return False
        return is_available(None, name, ts_t, te_t, er, role, gate, emp_tasks=others)

    changed = 0
    for _round in range(40):
        recs = df.to_dict("records")
        for i, r in zip(df.index, recs):
            r["_i"] = i
            r["עובד"] = clean_text(str(r["עובד"]))
            r["טיסה"] = clean_text(str(r["טיסה"]))
            r["תפקיד בסיס"] = clean_text(str(r.get("תפקיד בסיס", "")))
            s, e = _m(r["התחלה"]), _m(r["סיום"])
            r["_s"], r["_e"] = s, (None if s is None or e is None else (e if e > s else e + 1440))
        by_name = {}
        for r in recs:
            if _real(r["עובד"]) and r["_s"] is not None and r["_e"] is not None:
                by_name.setdefault(r["עובד"], []).append(r)
        trainee_flights = {r["טיסה"] for r in recs
                           if _real(r["עובד"]) and r["עובד"] in emp_map
                           and _yn(emp_map[r["עובד"]], "דייל בטרייני")}
        flight_workers = {}
        for r in recs:
            if _real(r["עובד"]):
                flight_workers.setdefault(r["טיסה"], set()).add(r["עובד"])

        best = None                       # (gain, w, b_idx, y, c_idx)
        for w, w_tasks in by_name.items():
            if w not in emp_map or not _may_move(w):
                continue
            w_before = _waste_of(w, w_tasks)
            if w_before <= 0:
                continue
            for b in w_tasks:
                if b["תפקיד בסיס"] not in _ATT_ROLES or b["טיסה"] in trainee_flights:
                    continue
                if locked_col and b.get(locked_col) is True:
                    continue
                for c in recs:
                    y = c["עובד"]
                    if (not _real(y) or y == w or c["_s"] is None or c["_e"] is None
                            or c["תפקיד בסיס"] not in _ATT_ROLES or not _plain(y)):
                        continue
                    if locked_col and c.get(locked_col) is True:
                        continue
                    if c["טיסה"] == b["טיסה"] or c["טיסה"] in trainee_flights:
                        continue
                    if w in flight_workers.get(c["טיסה"], ()) or y in flight_workers.get(b["טיסה"], ()):
                        continue
                    y_tasks = by_name.get(y, [])
                    w_after = [t for t in w_tasks if t is not b] + [dict(c, עובד=w)]
                    y_after = [t for t in y_tasks if t is not c] + [dict(b, עובד=y)]
                    gain = (w_before + _waste_of(y, y_tasks)) - (_waste_of(w, w_after) + _waste_of(y, y_after))
                    if gain <= 0 or (best is not None and gain <= best[0]):
                        continue
                    if not _can_take(w, c, [t for t in w_tasks if t is not b]):
                        continue
                    if not _can_take(y, b, [t for t in y_tasks if t is not c]):
                        continue
                    if not _break_ok(w, w_after) or not _break_ok(y, y_after):
                        continue
                    best = (gain, w, b["_i"], y, c["_i"])
        if best is None:
            # No profitable trade. Second move: HAND OFF the task that follows
            # the idle gap to someone who has no idle time of their own, so the
            # waiting worker goes back to the counters after their first flight
            # (same idea as fix_wasteful_gaps, but for any gap over _GAP_OK,
            # which is what a worker with NO break owed is entitled to).
            hand = None                   # (gain, -tasks, w, b_idx, z)
            for w, w_tasks in by_name.items():
                if w not in emp_map or not _may_move(w) or _yn(emp_map[w], "ראש צוות"):
                    continue
                w_before = _waste_of(w, w_tasks)
                if w_before <= 0:
                    continue
                for b in w_tasks:
                    if b["תפקיד בסיס"] not in _ATT_ROLES or b["טיסה"] in trainee_flights:
                        continue
                    if locked_col and b.get(locked_col) is True:
                        continue
                    w_after = [t for t in w_tasks if t is not b]
                    gain = w_before - _waste_of(w, w_after)
                    if gain <= 0 or not _break_ok(w, w_after):
                        continue
                    for z, zrow in emp_map.items():
                        if (not z or z == w or not _plain(z) or z in flight_workers.get(b["טיסה"], ())
                                or is_night_shift_for_return_rule(zrow)
                                or not is_time_text(clean_text(str(zrow.get("תחילת משמרת", ""))))
                                or not is_time_text(clean_text(str(zrow.get("סוף משמרת", ""))))):
                            continue
                        z_tasks = by_name.get(z, [])
                        z_after = z_tasks + [dict(b, עובד=z)]
                        if _waste_of(z, z_after) > _waste_of(z, z_tasks):
                            continue
                        key = (gain, -len(z_tasks))
                        if hand is not None and key <= hand[0]:
                            continue
                        if not _can_take(z, b, z_tasks) or not _break_ok(z, z_after):
                            continue
                        hand = (key, w, b["_i"], z)
            if hand is None:
                break
            _key, w, b_idx, z = hand
            df.at[b_idx, "עובד"] = z
            df.at[b_idx, "סיבה"] = ("הועבר/ה מ-" + w + " לצמצום זמן המתנה בין טיסות "
                                    "(חזרה לדלפקים אחרי הטיסה הראשונה)")
            changed += 1
            continue
        _gain, w, b_idx, y, c_idx = best
        df.at[b_idx, "עובד"] = y
        df.at[c_idx, "עובד"] = w
        _why = "הוחלפו משימות בין " + w + " ל-" + y + " לצמצום זמן המתנה בין טיסות"
        df.at[b_idx, "סיבה"] = _why
        df.at[c_idx, "סיבה"] = _why
        changed += 1
    return df if changed else schedule_df
