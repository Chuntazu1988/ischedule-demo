# =========================
# CONSTANTS & RULE SETTINGS
# =========================

# SFO added 2026-09-08: missing from both lists here despite being present
# in an internal reference tool's equivalent
# destination lists — cross-checked against that tool,
# same tool the peak-analysis algorithm (analyze_tl_peaks) was ported from.
USA_TSA_DESTS = {"JFK", "LAX", "EWR", "FLL", "MIA", "BOS", "SFO"}
# BCN/LAS removed from QUEUE_DESTS 2026-09-08: present with no documented
# reason (no commit ever added them with an explanation, and they're absent
# from the reference tool's own equivalent list) — user confirmed מתאם
# תורים is not actually needed on these routes.
QUEUE_DESTS = {"CDG", "LHR", "JFK", "LAX", "EWR", "HKT", "NRT", "MIA", "FLL", "BOS", "BKK", "SFO"}
TWO_TEAM_LEADS_DESTS = {"BKK", "HKT"}

NARROW_REG_PREFIXES = ("EH", "EK")
WIDE_REG_PREFIXES = ("EC", "ED", "ER")
# מטוסים חכורים — צרי גוף לכל דבר (חוקי שיבוץ של צר גוף), מסומנים בסגול בתצוגה.
# User-confirmed 2026-07-05: LOC, BBN, PEX, EAC, EAI, MGM, PMI.
# TFS, OEX added 2026-07-25 (user-confirmed): also leased narrow-body — were
# falling through to the "רחב גוף" (wide-body) default and shown in red.
LEASED_NARROW_REG_PREFIXES = ("LOC", "BBN", "PEX", "EAC", "EAI", "MGM", "PMI", "TFS", "OEX")

# Specific aircraft model by registration — for the "דוח שיבוץ טיסות" export's
# "מטוס" column (distinct from get_body_type's narrow/wide classification).
# User-confirmed 2026-07-26. Exact codes checked first (some of these are also
# in LEASED_NARROW_REG_PREFIXES above — being leased and having a specific
# model are separate facts about the same aircraft), then the 2-letter prefix.
AIRCRAFT_MODEL_EXACT = {
    "PEX": "737-8", "TFS": "737-8", "OEX": "737-8", "MGM": "737-8", "BBN": "737-8",
    "LOC": "737-8",  # user-confirmed 2026-07-29
}
AIRCRAFT_MODEL_PREFIX = {
    "EK": "737-8", "EH": "737-9", "EA": "320", "ED": "787-9", "ER": "787-8", "EC": "777",
}
REMOTE_GATES = {"D1", "D1A", "C1", "C1A", "B1", "B1A", "E1", "E1A"}

# Terminal 1 gates are NUMBERS ONLY (T3 gates are letter+number like "D9").
# User-confirmed 2026-07: gates 30-33, 35-38 and 40 belong to Terminal 1.
TERMINAL1_GATES = {"30", "31", "32", "33", "35", "36", "37", "38", "40"}

# Flights cancelled until further notice (user instruction 2026-07-05: LY611).
# Matched by the numeric part of the flight number (no LY prefix, no leading
# zeros). Shown in red as CANCELLED and never staffed.
CANCELLED_FLIGHTS = {"611"}

ROLE_COLUMNS = [
    "ראש צוות",
    "דייל",
    "מתאם תורים",
    "מפקח TSA",
    "שומר TSA",
    "חונך רצים",
    "מסמיך רצים",
    "טרייני רצ",
]

# New employee-type columns
INACTIVE_COL          = "פעיל/לא פעיל"   # "לא" = skip entirely from scheduling
# can only do שומר TSA + דייל. NOTE: the employees file uses the SINGULAR
# column name "ילד עובדים" — both variants must be checked (a mismatch here
# let employee-children slip past the restricted-majority key entirely).
RESTRICTED_WORKER_COLS = ["ילדי עובדים", "ילד עובדים", "פורשים"]
TRAINEE_ATTENDANT_COL  = "דייל בטרייני"   # must be paired; 2 trainees can fill a 2-attendant flight
INSTRUCTOR_COL         = "מתדרכת"          # last-resort ראש צוות candidate (needs confirmation)
CREW_CHIEF_MANAGER_COL = "מנהל כר\"צ"     # eligible for ראש צוות like regular team lead

ROLE_ORDER = ["ראש צוות", "טרייני רצ", "דייל", "מתאם תורים", "מפקח TSA", "שומר TSA"]

# Early morning shift: starts 02:00-02:59, ends 08:00-09:30
EARLY_MORNING_START_MAX = 3 * 60       # 03:00
EARLY_MORNING_END_MIN   = 8 * 60       # 08:00
EARLY_MORNING_END_MAX   = 9 * 60 + 30  # 09:30

# Night shift: starts before midnight (21:00-23:59), ends 06:00-08:30
NIGHT_START_MIN = 21 * 60       # 21:00
NIGHT_END_MIN   = 6  * 60       # 06:00
NIGHT_END_MAX   = 8  * 60 + 30  # 08:30

# Late shift: ends by 01:30 — preferred for flights up to 01:30
LATE_SHIFT_END_MAX = 1 * 60 + 30  # 01:30

MAX_CONTINUOUS_WORK_MINUTES = 4 * 60  # 4 hours max without break
NIGHT_BREAK_WINDOW_START = 0 * 60    # 00:00
NIGHT_BREAK_WINDOW_END   = 2 * 60    # 02:00
