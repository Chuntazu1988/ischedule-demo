import io
import re
import pandas as pd
def normalize_yes_no(value):
    if value is None:
        return "לא"

    text = str(value).strip().lower()

    yes_values = [
        "כן",
        "yes",
        "true",
        "1",
        "y",
        "x",
    ]

    return "כן" if text in yes_values else "לא"
try:
    from constants import ROLE_COLUMNS
except ModuleNotFoundError:
    ROLE_COLUMNS = [
    "אחמ״ש",
    "אחמש",
    "דלפק",
    "שער",
    "בידוק",
    "תורן",
    "מפעיל",
    "משמרת",
    "תפקיד",
]
def flight_key(value):
    if value is None:
        return ""

    text = str(value).strip().upper()

    text = text.replace(" ", "")
    text = text.replace("-", "")

    return text
def name_key(value):
    if value is None:
        return ""

    text = str(value).strip().lower()

    text = text.replace(" ", "")
    text = text.replace("-", "")
    text = text.replace("_", "")

    return text
def name_key_reversed(value):
    if value is None:
        return ""

    text = str(value).strip()

    parts = text.split()

    if len(parts) < 2:
        return name_key(text)

    reversed_text = " ".join(reversed(parts))

    return name_key(reversed_text)
# ── Terminal-transfer note detection ─────────────────────────────────────────
# Inline formats seen in real rosters (2026-07 samples):
#   "agent#21 המשך מש' בטרמינל 1 החל מ02:00"
#   "נועה דהן מעבר לטרמינל 1 ב02:00"
#   "אדוארד רוזנטל-טרמינל1 החל מ02:00"
#   "משה לוי -טרמינל 1"                (no time → the whole shift is at T1)
#   "הגעה לטרמינל 1 בשעה 01:00 לאחר הפסקה"   (floating, color-linked note)
# "הגעה מטרמינל 3" / "(הגעה מטר3)" is an ARRIVAL-FROM marker, not a transfer-to —
# the (?<![מ]) lookbehind keeps the bare "טרמינל X" branch from matching it.
TERMINAL_NOTE_RE = re.compile(
    r"(?:המשך\s+מש\S{0,2}\s*בטרמינל|מעבר\s+לטרמינל|הגעה\s+לטרמינל|(?<![מ])טרמינל)"
    r"\s*(\d)"
    r"(?:.{0,8}?(?:החל\s*מ|בשעה|משעה|ב)\s*-?\s*(\d{1,2}:\d{2}))?",
    re.UNICODE,
)
# Suffix stripper: removes any transfer/arrival note from a name cell so the
# name_key still matches the employees file (same trick as the עד-HH:MM strip).
TERMINAL_SUFFIX_RE = re.compile(
    r"\s*[-–]?\s*(?:המשך\s+מש\S{0,2}\s*בטרמינל|מעבר\s+לטרמינל|הגעה\s+לטרמינל|"
    r"הגעה\s+מטרמינל|הגעה\s+מטר|טרמינל)\s*\d.*$",
    re.UNICODE,
)

# New-attendant marker in rosters: "טרייני - שם" (a fresh דייל paired with a
# veteran attendant = דייל בטרייני). Distinct from the "טרייני ר\"צ" SUFFIX,
# which marks a team-leader trainee.
TRAINEE_PREFIX_RE = re.compile(r"^\s*טרייני\s*[-–]\s*", re.UNICODE)

# Words that introduce a free-text scheduling note typed directly into a name
# cell ("agent#6 שיחה עם טל 1:30", "יהונתן שחר עיני שעת הדרכה
# 10:00-11:00"). Used both to strip the note off the name (clean_roster_name)
# and to tell such a cell apart from a genuine time-range SECTION HEADER.
NAME_NOTE_LEADIN_WORDS = {"שיחה", "הערה", "לתאם", "לבדוק", "עם", "לגבי",
                          "בנוגע", "סיכום", "להתקשר", "לחזור",
                          "שעת", "הדרכה", "תדריך", "פיקוח"}

# "פיקוח" written next to a name in the daily roster ("TL#37 -פיקוח")
# rosters THAT worker for inspection duty today, exactly as a "פיקוח TSA"
# section header does for the block beneath it (user rule 2026-08-09).
NAME_NOTE_TSA_RE = re.compile(r"פיקוח", re.UNICODE)

# Team-leader-trainee marker in rosters: the name carries a "טרייני ר\"צ"
# suffix (any apostrophe variant: ר"צ / ר''צ / ר׳צ / רצ). The daily roster is
# the source of truth for who is in TL training TODAY (user rule 2026-07-05).
TL_TRAINEE_RE = re.compile(r"טריינ[יי]?\s*ר['\"״׳’]{0,2}צ", re.UNICODE)


# A note can be prefixed with "תחילת מש' טרמינל X," stating the STARTING
# terminal before describing the actual transfer ("...המשך מש' בטרמינל Y
# החל מHH:MM..."). This prefix carries no transfer info of its own (the
# worker's own "terminal" field already says where they start) and must be
# stripped before matching — otherwise TERMINAL_NOTE_RE's own bare "טרמינל X"
# alternative greedily matches THIS leading "X" (the wrong, source terminal)
# instead of the real "בטרמינל Y" later in the text. Found via real data:
# "תחילת מש' טרמינל 3, המשך מש' בטרמינל 1 החל מ01:00 אחרי הפסקה" parsed as
# {"to": "3", "at": ""} (from the leading "טרמינל 3") instead of the real
# {"to": "1", "at": "01:00"} — and separately, the leftover "תחילת מש'" text
# tripped the "this looks like a name cell, not a pure floating note" residue
# check in apply_color_transfer_notes, silently dropping the note entirely.
START_TERMINAL_PREFIX_RE = re.compile(
    r"^\s*תחיל[ת]?\s*מש\S{0,2}\s*(?:ב)?טרמינל\s*\d\s*,?\s*", re.UNICODE
)


def parse_terminal_note(cell_text):
    """Return {"to": "1"/"3", "at": "HH:MM" or ""} if the text carries a
    terminal-transfer note, else None."""
    text = START_TERMINAL_PREFIX_RE.sub("", clean_text(cell_text))
    m = TERMINAL_NOTE_RE.search(text)
    if not m:
        return None
    return {"to": m.group(1), "at": normalize_time_text(m.group(2)) if m.group(2) else ""}


def clean_roster_name(value):
    if value is None:
        return ""

    text = str(value).strip()

    if text.lower() == "nan":
        return ""

    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()

    # Strip the NEW-ATTENDANT prefix "טרייני - שם" (a fresh attendant paired
    # with a veteran — דייל בטרייני). The prefix is detected and tagged
    # separately in build_shift_map_from_excel; here we only clean the name so
    # its name_key matches the employees file directly (no fuzzy fallback).
    # NOTE: this is NOT the same as the "טרייני ר\"צ" role SUFFIX (team-leader
    # trainee), which is stripped by the role-suffix rule below.
    _t = TRAINEE_PREFIX_RE.sub("", text).strip()
    if _t:
        text = _t

    # A trainee note commonly carries a shift time and/or a "-תגבור {place}"
    # reinforcement tag glued straight onto the name (e.g. "ירין מזרחי
    # 22:00-06:00 - תגבור שערים", "agent#23-תגבור שערים") — strip both so the
    # name_key matches the employees file. Guarded by the ≥2-word check like
    # every other suffix strip here (found via real data 2026-07-30: these
    # exact three trainees failed to resolve because "תגבור" also short-
    # circuited them into the unrelated availability-note branch in
    # build_shift_map_from_excel — fixed there — but the leftover time/תגבור
    # text in the name itself still needed stripping separately).
    _TRAINEE_NOTE_RE = re.compile(
        r"\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}.*$|\s*-?\s*תגבור\s+\S+\s*$",
        re.UNICODE,
    )
    _stripped_tn = _TRAINEE_NOTE_RE.sub("", text).strip()
    if len(_stripped_tn.split()) >= 2:
        text = _stripped_tn

    # Strip a terminal-transfer/arrival suffix (e.g. "agent#21 המשך מש'
    # בטרמינל 1 החל מ02:00" → "agent#21") so the worker keeps the same
    # name_key as their employees-file entry. The note itself is parsed
    # separately via parse_terminal_note.
    _stripped0 = TERMINAL_SUFFIX_RE.sub("", text).strip()
    if len(_stripped0.split()) >= 2:
        text = _stripped0

    # Strip known role / department suffixes that appear after the worker's name
    # in daily schedule Excels (e.g. "שם עובד טריינ ר"צ ד. שלג" → "שם עובד").
    # Only strip when the result still has ≥ 2 words (to avoid reducing a full
    # name to a single word and causing spurious matches).
    _ROLE_SUFFIX_RE = re.compile(
        r"\s+(?:טריינ[יי]?\s*(?:ר['\"״׳’]{0,2}צ|רצ)?|ד\.\s*\S*|ד\"\s*\S*)\s*$",
        re.UNICODE,
    )
    _stripped = _ROLE_SUFFIX_RE.sub("", text).strip()
    if len(_stripped.split()) >= 2:
        text = _stripped

    # Strip a trailing "עד HH:MM" (until HH:MM) partial-shift note, e.g.
    # "יובל חן נתן עד 00:30" → "יובל חן נתן". Without this, such a worker's
    # second (genuinely real, separate) shift entry gets a different name_key
    # than their primary entry and is silently dropped instead of merged as
    # an extra_shifts window.
    _UNTIL_SUFFIX_RE = re.compile(r"\s*-?\s*עד\s*\d{1,2}:\d{2}\s*$", re.UNICODE)
    _stripped2 = _UNTIL_SUFFIX_RE.sub("", text).strip()
    if len(_stripped2.split()) >= 2:
        text = _stripped2

    # Strip a trailing "מש'"-family shift-note glued to the name — a shift
    # extension/split abbreviation ("משמרת") written straight into the name
    # cell, optionally hyphen/slash-attached and optionally carrying a time:
    #   "TL#14-מש'"        → "TL#14"   (extension marker only)
    #   "TL#13 מש' מ19:00"  → "TL#13"   (shift-FROM note)
    #   "agent#20 מש'/עד 00:30" → "agent#20"    (shift-UNTIL note)
    # The scheduling-relevant times are already extracted separately
    # (shift_start_override / shift_end_override). Without stripping the
    # residual "מש'" token, the worker's name_key stops matching their
    # employees-file entry (found via real data 2026-07-22: TL#14
    # dropped entirely; TL#13's T1 start-shift entry failed to merge
    # with her של"ן T3 night entry, losing her T1→T3 transfer). The "\b"
    # after "מש" (or an apostrophe/"מרת") keeps a legitimate name like
    # "משה"/"משי" from matching. Guarded by the ≥2-word check below.
    _SHIFT_NOTE_RE = re.compile(
        r"\s*[-–/]?\s*מש(?:מרת|['\"״׳’]|\b)"
        r"(?:\s*[-–/]?\s*(?:מ|עד|החל\s*מ|משעה|בשעה)?\s*-?\s*\d{1,2}:\d{2})?"
        r"\s*[-–/]?\s*$",
        re.UNICODE,
    )
    _stripped3 = _SHIFT_NOTE_RE.sub("", text).strip()
    if len(_stripped3.split()) >= 2:
        text = _stripped3

    # Strip a free-text scheduling note typed directly into the name cell
    # (e.g. "agent#6 שיחה עם טל 1:30" — "call with Tal 1:30" tacked
    # onto the name). Without this, the note survives into name_key, the
    # worker's own key stops matching their employees-file entry, and they
    # fall through to the fuzzy word-overlap fallback in
    # apply_shift_map_to_employees — which can misattribute their entry to
    # an unrelated employee who merely shares a surname (found via real
    # data: agent#6's trainee entry landed on agent#10, a
    # same-surname, different-person colleague). Real names are 2-3 words;
    # cut at the first bare H:MM token or recognized note lead-in word
    # appearing from the 3rd word onward.
    _NOTE_LEADIN_WORDS = NAME_NOTE_LEADIN_WORDS
    _words = text.split()

    def _bare(_t):
        # A note word is often glued to a separator ("-פיקוח", "–פיקוח")
        return _t.strip("-–—,.:;()[]")

    if len(_words) > 2:
        for _wi, _w in enumerate(_words):
            if _wi >= 2 and (re.match(r"^\d{1,2}:\d{2}$", _w) or _bare(_w) in _NOTE_LEADIN_WORDS):
                _kept = _words[:_wi]
                while _kept and _bare(_kept[-1]) in _NOTE_LEADIN_WORDS:
                    _kept.pop()
                if len(_kept) >= 2:
                    text = " ".join(_kept)
                break

    return text
def safe_sort_by_time(df, column_name):
    if column_name not in df.columns:
        return df

    try:
        return df.sort_values(
            by=column_name,
            key=lambda col: col.astype(str)
        ).reset_index(drop=True)

    except Exception:
        return df
def normalize_time_text(value):
    if value is None:
        return ""

    text = str(value).strip()

    if text == "" or text.lower() == "nan":
        return ""

    text = text.replace(".", ":")

    if ":" in text:
        parts = text.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            return f"{hour:02d}:{minute:02d}"
        except Exception:
            return text

    try:
        hour = int(float(text))
        return f"{hour:02d}:00"
    except Exception:
        return text
def clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_str(value):
    if value is None:
        return ""
    return str(value)


def is_time_like(value):
    text = str(value)
    return ":" in text


# =========================
# SHIFT MAP FROM EXCEL
# =========================

def build_shift_map_from_excel(uploaded_file, terminal="3"):
    """
    Scan the workbook and extract per-employee:
      - shift start/end
      - modified shift window
      - blocked time windows
      - unavailable flag (SICK)
      - early end
      - terminal ("3" for the regular daily roster, "1" for the TR1 roster)
        and an optional terminal-transfer note parsed from the name cell

    Returns dict: name_key -> {
        "start": HH:MM, "end": HH:MM, "original": str,
        "blocked": [(start, end), ...],
        "sick": bool,
        "shift_end_override": HH:MM or None,
        "shift_start_override": HH:MM or None,
        "terminal": "3"/"1",
        "transfer": {"to": "1"/"3", "at": "HH:MM" or ""} or None,
    }
    """
    shift_map = {}
    TIME_RANGE_RE  = re.compile(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})")
    SINGLE_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")

    BLOCKED_KEYWORDS = ["בידוק", "מתדרכ", "ועדת היגוי", "רענון tsa", "רענון", "77"]
    DUTY_BLOCK_KEYWORDS = ["dkk"]
    SICK_KEYWORDS    = ["sick", "מחלה"]
    END_KEYWORDS     = ["עד ", "עד:"]
    START_KEYWORDS   = ["מש' מ", "מש מ", "משמרת מ"]
    END_SHIFT_KW     = ["מש' עד", "מש עד", "משמרת עד", "מש'"]

    def is_blocked_label(text):
        tl = text.lower()
        return any(kw in tl for kw in BLOCKED_KEYWORDS)

    def is_duty_block_label(text):
        """A non-flight duty posting. Whoever is rostered under it may not be
        given flights automatically — only with explicit approval (user rule
        2026-08-09: "מזכירת DKK — כאשר משובצים לתפקיד זה העובד חסום מלבצע
        טיסות אלא אם כן המערכת מקבלת אישור לכך")."""
        return any(kw in text.lower() for kw in DUTY_BLOCK_KEYWORDS)

    def is_sick(text):
        tl = text.lower()
        return any(kw in tl for kw in SICK_KEYWORDS)

    try:
        uploaded_file.seek(0)
        sheets = pd.read_excel(uploaded_file, sheet_name=None, header=None, dtype=str)
    except Exception:
        return shift_map

    # A duty label written above a block of names is often MERGED across the
    # columns it covers (e.g. "מזכירת DKK" merged over D27:E28 covers both
    # טליה חנה טטרואשוילי and TSA-inspector#1). pd.read_excel puts the value only
    # in the range's top-left cell, so every other column saw nothing at all —
    # propagate it across the range before parsing (user rule 2026-08-09).
    try:
        import openpyxl
        uploaded_file.seek(0)
        _wb = openpyxl.load_workbook(uploaded_file, data_only=True, read_only=False)
        for _sn, _raw in sheets.items():
            if _sn not in _wb.sheetnames:
                continue
            for _mr in _wb[_sn].merged_cells.ranges:
                _val = _wb[_sn].cell(row=_mr.min_row, column=_mr.min_col).value
                if _val is None or not str(_val).strip():
                    continue
                for _r in range(_mr.min_row - 1, _mr.max_row):
                    for _c in range(_mr.min_col - 1, _mr.max_col):
                        if _r < len(_raw.index) and _c < len(_raw.columns):
                            _raw.iat[_r, _c] = str(_val)
        _wb.close()
    except Exception:
        pass    # merged-cell propagation is best-effort; parsing continues

    for sheet_name, raw in sheets.items():

        # ── Detect "page 2" upcoming-night section boundary ──────────────────
        # Two detection strategies — first succeeds wins:
        #
        # Strategy A ("של"ן" and similar): the title row "סידור יומי ראשון" is
        # repeated in col 0 to separate pages.  Last occurrence = boundary.
        #
        # Strategy B ("דלפקי ש"ש" and similar): no repeated title, but the sheet
        # has multiple "section header" rows containing time ranges.  The LAST
        # header row that also contains at least one night-start time (≥ 17:00)
        # — when there is an earlier such row too — marks the start of the
        # upcoming-night section (workers finishing tomorrow → DO NOT schedule).
        _upcoming_night_threshold = float('inf')
        if 0 in raw.columns:
            _title_rows = [
                ri for ri, cell in raw[0].items()
                if 'סידור יומי ראשון' in clean_text(cell)
            ]
            if len(_title_rows) >= 2:
                _upcoming_night_threshold = _title_rows[-1]

        if _upcoming_night_threshold == float('inf'):
            # Strategy B: scan every row for time-range headers with night starts.
            _night_hdr_rows = []
            for _ri in raw.index:
                _row_has_night = False
                for _cj in raw.columns:
                    _ct = clean_text(str(raw.at[_ri, _cj]))
                    _m = TIME_RANGE_RE.search(_ct)
                    if _m:
                        try:
                            _hh = int(normalize_time_text(_m.group(1)).split(':')[0])
                            if _hh >= 17:
                                _row_has_night = True
                                break
                        except (ValueError, IndexError):
                            pass
                if _row_has_night:
                    _night_hdr_rows.append(_ri)
            # Need ≥ 2 night-header rows (first = previous-day workers,
            # last = upcoming-night workers).
            if len(_night_hdr_rows) >= 2:
                _upcoming_night_threshold = _night_hdr_rows[-1]

        # ── Position-aware zone detection (robust across ALL sheets) ─────────
        # The single "threshold" above is fragile (fails on דלפקי ש"ש where the
        # title is not repeated, misfires on סיירת with two night bands). The
        # reliable signal — confirmed by the user — is POSITION relative to the
        # sheet's "day core":
        #   • A NIGHT shift (start ≥ 20:00) ABOVE the day core  = yesterday's
        #     overnight (night N-1→N, already finishing this morning) → restrict.
        #   • A NIGHT shift (start ≥ 20:00) BELOW the day core  = tonight
        #     (night N→N+1) → upcoming-night.
        #   • Everything else (early-morning, day, EVENING 17:00-19:59) is
        #     TODAY's fresh roster → scheduled normally, full hours.
        # The "day core" = the row span of section headers that START in daytime
        # (05:00-16:59). If no day core is found (unusual), fall back to the old
        # threshold so behaviour is unchanged for such sheets.
        _day_hdr_rows = []
        for _ri in raw.index:
            for _cj in raw.columns:
                _ct = clean_text(str(raw.at[_ri, _cj]))
                _m = TIME_RANGE_RE.search(_ct)
                if _m:
                    try:
                        _hh = int(normalize_time_text(_m.group(1)).split(':')[0])
                        if 5 <= _hh < 17:
                            _day_hdr_rows.append(_ri)
                            break
                    except (ValueError, IndexError):
                        pass
        if _day_hdr_rows:
            _day_core_first = min(_day_hdr_rows)
            _day_core_last  = max(_day_hdr_rows)
        else:
            # Fallback: reproduce the old single-threshold behaviour.
            _tf = _upcoming_night_threshold if _upcoming_night_threshold != float('inf') else float('-inf')
            _day_core_first = _tf + 1
            _day_core_last  = _tf

        def _zone_flags(row_i, start_hhmm):
            """Return (is_top_night, is_bottom_night) for a shift at row_i.
            Only NIGHT starts (≥20:00) can be either; evening/day → (False, False)."""
            try:
                _h = int(str(start_hhmm).split(":")[0])
            except (ValueError, IndexError, AttributeError):
                return (False, False)
            if _h < 20:
                return (False, False)
            return (row_i < _day_core_first, row_i > _day_core_last)

        for col in raw.columns:
            current_start = ""
            current_end   = ""
            current_blocked_range = None
            current_blocked_label = ""
            current_tsa_designated = False
            current_duty_block = ""
            _duty_headers_seen = 0
            # A trainee attendant ("טרייני - שם") is always placed directly
            # below their paired veteran in the SAME column of the roster
            # sheet (user rule 2026-07-19: "אפשר לדעת למי מוצמד הטרייני
            # דייל, מכיוון שתמיד יופיע תחתיו בסידור") -- track the last real
            # worker name seen in this column to resolve that pairing.
            _prev_worker_name = None

            for row_i, cell in raw[col].items():
                cell_text = clean_text(cell)
                if not cell_text:
                    continue

                # Non-flight duty posting ("מזכירת DKK") — a label, not a
                # worker. It was previously parsed as a person named
                # "מזכירת DKK" and its meaning was lost entirely.
                if is_duty_block_label(cell_text) and not TIME_RANGE_RE.search(cell_text):
                    current_duty_block = cell_text
                    _duty_headers_seen = 0
                    continue

                # Special inline format: "NAME-תגבור בין HH:MM-HH:MM"
                # EXCLUDES a "טרייני - שם" cell — trainee notes commonly end in
                # "-תגבור שערים" too (e.g. "טרייני - agent#23-תגבור שערים"),
                # which used to match this branch FIRST and get parsed as an
                # availability note for a worker literally named "טרייני" (the
                # word before the first dash) — the real trainee-prefix
                # handling below (TRAINEE_PREFIX_RE, which sets
                # entry["mentor"] from the previous row in this column) was
                # never reached, so the mentor pairing silently failed for
                # every trainee whose note happened to include "תגבור" (found
                # via real data 2026-07-30: trainee-agent#7/agent#23/ירין מזרחי,
                # all unpaired from their mentors agent#18/agent#4/
                # agent#2).
                is_availability_note = (
                    ('תגבור' in cell_text or 'ואז בין' in cell_text)
                    and not TRAINEE_PREFIX_RE.match(cell_text)
                )
                if is_availability_note:
                    dash_idx = cell_text.find('-')
                    name_part = cell_text[:dash_idx].strip() if dash_idx > 0 else cell_text
                    pn = clean_roster_name(name_part) if len(name_part.split()) >= 2 else name_part.strip()
                    # Only an individual availability NOTE when the text before the
                    # dash is a real NAME (has Hebrew letters). A SECTION HEADER
                    # like "03:30-06:30 תגבור רצ" (whose first '-' sits inside the
                    # time range, so name_part = "03:30") must NOT be swallowed
                    # here — fall through to the TIME_RANGE handling below so it
                    # sets current_start/current_end (03:30-06:30) for the names
                    # that follow. Found via real 20.07 data: TL#9 /
                    # TL#16 sat under "03:30-06:30 תגבור רצ" but inherited the
                    # PREVIOUS section's 02:00-12:30 and were wrongly scheduled as
                    # floor ר"צ until midday instead of reinforcing only 03:30-06:30.
                    _pn_has_letters = bool(pn) and any("א" <= ch <= "ת" for ch in str(pn))
                    if not _pn_has_letters:
                        pass  # not a name → treat as a normal section header below
                    elif pn and current_start and current_end:
                        key = name_key(pn)
                        avail_windows = TIME_RANGE_RE.findall(cell_text)
                        entry = shift_map.get(key, {
                            "start":    current_start,
                            "end":      current_end,
                            "original": pn,
                            "sheet":    sheet_name,
                            "blocked":  [],
                            "sick":     False,
                            "shift_end_override":   None,
                            "shift_start_override": None,
                            "available_windows": [],
                        })
                        if avail_windows:
                            entry["available_windows"] = [(normalize_time_text(ws), normalize_time_text(we)) for ws, we in avail_windows]
                        else:
                            # A plain "NAME-תגבור" in the managers sheet with NO
                            # time window is a concourse/management reinforcement
                            # (תגבור שלוחה) — they staff a concourse desk, NOT floor
                            # flights, so they must be excluded from ר"צ/floor
                            # scheduling even when they're also a regular ר"צ worker
                            # in employees_clean. This is DISTINCT from the
                            # time-windowed "NAME-תגבור בין HH:MM-HH:MM" managers,
                            # who ARE intended ר"צ backups (added in streamlit_app.py
                            # ~line 842). User 2026-07-20 (TL#29-תגבור, under the
                            # "מנהל שלוחה" section, was getting LY011/LY289/LY543 ר"צ).
                            if "תגבור" in cell_text and "מנהל" in str(sheet_name):
                                entry["concourse_reinforcement"] = True
                        shift_map[key] = entry
                        # Also index under the reversed name order (mirrors the
                        # main worker path ~line 551) — without it a worker whose
                        # employees_clean name is written in the OTHER order
                        # (e.g. roster "TL#29-תגבור" vs employees "TL#29")
                        # never matched shift_map.keys(), so they were missing from
                        # the "מי עובד היום" list and couldn't be marked sick,
                        # even though they were being scheduled. Found via real
                        # data 2026-07-20: TL#29 (תגבור שלוחה).
                        _key_rev = name_key(" ".join(reversed(pn.split())))
                        if _key_rev not in shift_map:
                            shift_map[_key_rev] = entry
                    # Only skip the row when it was a genuine NAME note; a section
                    # header ("03:30-06:30 תגבור רצ") falls through to be parsed as
                    # a normal TIME_RANGE header below.
                    if _pn_has_letters:
                        continue

                m = TIME_RANGE_RE.search(cell_text)
                # A cell whose time range is preceded by a free-text note
                # lead-in ("… שעת הדרכה 10:00-11:00") is a WORKER row carrying
                # an inline note, not a section header. Treating it as a header
                # dropped the worker entirely AND re-pointed current_start/end
                # for every name below them in the column (found via real 30.07
                # data: "יהונתן שחר עיני שעת הדרכה 10:00-11:00" — he vanished
                # from the shift map, his trainee trainee-agent#2 inherited an
                # unrelated worker higher up the column as their mentor, and
                # עידו/trainee-agent#4/trainee-agent#10/trainee-agent#6 all got a bogus
                # 10:00-11:00 shift instead of the section's real hours).
                if m and any(
                    _w in NAME_NOTE_LEADIN_WORDS
                    for _w in cell_text[:m.start()].split()
                ):
                    m = None
                if m:
                    s = normalize_time_text(m.group(1))
                    e = normalize_time_text(m.group(2))
                    # A "פיקוח TSA" section header (e.g. "22:00-07:00 פיקוח
                    # TSA") designates the worker(s) listed below it as THE
                    # named TSA inspector(s) for that shift — distinct from
                    # merely holding the מפקח-TSA CERTIFICATION in
                    # employees_clean.xlsx. Certification says who's ALLOWED
                    # to do the role; this header says who's actually
                    # ROSTERED for it today, and should be preferred over an
                    # equally-certified worker who happens to be busy
                    # elsewhere (user rule 2026-07-25: "מפקחים למשמרת ספציפית
                    # מופיעים בקובץ סידור העבודה תחת כותרת מפקח TSA" — found
                    # via real data: TL#37 holds the certification but is
                    # NOT under a "פיקוח TSA" header for this shift, unlike
                    # TL#43/TSA-inspector#3 who are — she is instead designated by a
                    # "פיקוח" note on her own name cell, see NAME_NOTE_TSA_RE).
                    current_tsa_designated = "פיקוח" in cell_text and "tsa" in cell_text.lower()
                    # A duty label governs the ONE name block directly beneath
                    # it; the next time header after that block ends its reach.
                    if current_duty_block:
                        _duty_headers_seen += 1
                        if _duty_headers_seen > 1:
                            current_duty_block = ""
                    if is_blocked_label(cell_text):
                        current_blocked_range = (s, e)
                        current_blocked_label = cell_text
                        current_start = s
                        current_end   = e
                    else:
                        current_start = s
                        current_end   = e
                        current_blocked_range = None
                        current_blocked_label = ""
                    continue

                if not current_start or not current_end:
                    continue

                possible_name = clean_roster_name(cell_text)
                if not possible_name:
                    continue

                key = name_key(possible_name)

                # Inline terminal-transfer note on the name cell, e.g.
                # "agent#21 המשך מש' בטרמינל 1 החל מ02:00". An arrival-FROM
                # marker ("הגעה מטר3") is not a transfer and parses to None.
                terminal_note = parse_terminal_note(cell_text)
                if terminal_note and len(TERMINAL_SUFFIX_RE.sub("", clean_text(cell_text)).strip().split()) < 2:
                    # The cell is ONLY a floating note ("הגעה לטרמינל 1 בשעה
                    # 01:00...") — not a worker; handled by the color-link pass.
                    continue

                sick            = is_sick(cell_text)
                shift_end_ovr   = None
                shift_start_ovr = None
                extra_blocked   = []
                # "טרייני - שם" prefix = NEW attendant (דייל בטרייני, must be
                # paired). Separate from the טרייני-ר"צ role suffix below.
                trainee_att     = bool(TRAINEE_PREFIX_RE.match(clean_text(cell_text)))
                # "שם ... טרייני ר\"צ" suffix = TL trainee today (roster truth)
                tl_trainee      = (not trainee_att) and bool(TL_TRAINEE_RE.search(clean_text(cell_text)))
                # Inspection duty noted on the name cell itself, not via a
                # section header — see NAME_NOTE_TSA_RE. The header branch has
                # already `continue`d by here, so this can only be a worker row.
                name_note_tsa   = bool(NAME_NOTE_TSA_RE.search(clean_text(cell_text)))

                for kw in END_SHIFT_KW:
                    if kw in cell_text:
                        # The bare "מש'"/"מש" keyword must NOT fire on a shift-FROM
                        # note like "מש' מ19:00" — there the time is a START, not an
                        # end. Without this guard, END grabs the "19:00" and the
                        # window collapses to zero length (found via real data
                        # 2026-07-22: TL#13 "מש' מ19:00" → end override 19:00,
                        # her T1→T3 evening window lost). "מש' עד ..." keeps working
                        # (it's an earlier, more specific keyword in END_SHIFT_KW).
                        if kw in ("מש'", "מש"):
                            _after_kw = cell_text[cell_text.find(kw) + len(kw):].lstrip(" -–/")
                            if _after_kw[:1] == "מ":
                                continue
                        mt = SINGLE_TIME_RE.search(cell_text[cell_text.find(kw):])
                        if mt:
                            shift_end_ovr = normalize_time_text(mt.group(1))
                        break

                if not shift_end_ovr:
                    for kw in END_KEYWORDS:
                        if kw in cell_text and kw not in END_SHIFT_KW:
                            mt = SINGLE_TIME_RE.search(cell_text[cell_text.find(kw):])
                            if mt:
                                shift_end_ovr = normalize_time_text(mt.group(1))
                            break

                for kw in START_KEYWORDS:
                    if kw in cell_text:
                        mt = SINGLE_TIME_RE.search(cell_text[cell_text.find(kw):])
                        if mt:
                            shift_start_ovr = normalize_time_text(mt.group(1))
                        break

                inline_ranges = TIME_RANGE_RE.findall(cell_text)
                for ws, we in inline_ranges:
                    ws_n = normalize_time_text(ws)
                    we_n = normalize_time_text(we)
                    if is_blocked_label(cell_text) and (ws_n, we_n) != (current_start, current_end):
                        extra_blocked.append((ws_n, we_n))

                if key not in shift_map:
                    # Workers found only in the bottom "upcoming night" section
                    # belong to tomorrow's schedule and must not be assigned today.
                    # Only a true NIGHT start (20:00+) counts — an 18:00/19:00 start
                    # is an EVENING shift and stays fully eligible (it is used to
                    # staff tonight's night flights), not excluded as "upcoming night".
                    # Zone classification by POSITION relative to the day core
                    # (see _zone_flags above). A NIGHT shift below the day core is
                    # tonight's upcoming night; a NIGHT shift above it is
                    # yesterday's overnight (page-1). Evening (17:00-19:59) and day
                    # starts are always TODAY → neither flag → scheduled normally.
                    _is_top_night, _is_bottom_night = _zone_flags(row_i, current_start)
                    _is_upcoming = _is_bottom_night

                    # is_page1 now means specifically "yesterday's overnight window"
                    # (a NIGHT start positioned ABOVE the day core). Only such a
                    # window represents a stale, already-completed shift whose
                    # availability must be restricted to the early-morning tail.
                    # A fresh evening/night entry from today's roster (e.g. a
                    # worker's ONLY shift is an 18:00-00:30 TODAY entry, or any
                    # bottom-of-page night band) is left completely alone.
                    _is_page1 = _is_top_night

                    shift_map[key] = {
                        "start": current_start,
                        "end": current_end,
                        "original": possible_name,
                        "sheet": sheet_name,
                        "section": "",
                        "blocked": [],
                        "blocked_roles": [],
                        "sick": False,
                        "shift_end_override": None,
                        "shift_start_override": None,
                        "extra_shifts": [],
                        "is_page1": _is_page1,
                        "upcoming_night": _is_upcoming,
                        # Filled ONLY by a repeat occurrence in the bottom night
                        # band — i.e. a genuine SECOND shift. A worker whose only
                        # shift is tonight's has it as their primary window and
                        # must stay a single row in "מי עובד היום".
                        "bottom_night_windows": [],
                        "terminal": terminal,
                        "transfer": terminal_note,
                        "window_terminals": {},
                        "tsa_designated": current_tsa_designated or name_note_tsa,
                    }
                    key_rev = name_key(" ".join(reversed(possible_name.split())))
                    if key_rev not in shift_map:
                        shift_map[key_rev] = shift_map[key]
                else:
                    # Employee appears in a second shift column (e.g. night shift ending
                    # today AND another night shift starting tonight).  Store the second
                    # window so is_within_shift can cover both periods.
                    # Apply any "עד HH:MM" override found on THIS row directly to the
                    # second window's own end time — it must NOT propagate to
                    # entry["shift_end_override"] below, which represents an override
                    # of the PRIMARY (first-seen) window only. Without this split, a
                    # partial-shift note on the SECOND occurrence (e.g. "...עד 00:30")
                    # would corrupt the primary shift's displayed end time.
                    _ex = shift_map[key]
                    _new_start = shift_start_ovr or current_start
                    _new_end   = shift_end_ovr   or current_end
                    _new_window = (_new_start, _new_end)
                    _first_window = (_ex.get("shift_start_override") or _ex["start"],
                                     _ex.get("shift_end_override") or _ex["end"])
                    _existing_windows = {(s, e) for s, e, _p1 in _ex.get("extra_shifts", [])}
                    # A repeat occurrence in the BOTTOM night band is tonight's
                    # upcoming night, even when its hours are identical to the
                    # page-1 window and the duplicate check below discards it —
                    # the fact that it exists at all is what marks the worker as
                    # working again tonight. Losing it left them looking like a
                    # plain page-1 worker, with no late-night tail availability
                    # and none of the last-resort ranking (found via real 11.08
                    # data: agent#22, 21:00-07:00 on page 1 AND page 3, came
                    # out upcoming_night=False and could not be offered to the
                    # אפטר team for the flights departing on 12.08).
                    _new_top_night, _repeat_bottom_night = _zone_flags(row_i, _new_start)
                    if _repeat_bottom_night:
                        _ex["upcoming_night"] = True
                        # Keep the window itself too, even when the duplicate
                        # check below drops it: "מי עובד היום" lists one row per
                        # SHIFT, and a worker rostered for the same hours on two
                        # consecutive nights has two shifts, not one.
                        _bn = _ex.setdefault("bottom_night_windows", [])
                        if _new_window not in _bn:
                            _bn.append(_new_window)
                    elif _new_top_night != bool(_ex.get("is_page1")):
                        # The repeat sits on the OTHER side of the page-1 line
                        # from the primary window, so the two are different
                        # shifts even when neither is a bottom-night band.
                        # agent#13 on 23.08: primary 19:00-00:30 (today,
                        # page 2) plus 22:00-07:00 carried over from page 1 —
                        # she was showing only one of the two.
                        _pv = _ex.setdefault("prev_shift_windows", [])
                        if _new_window not in _pv:
                            _pv.append(_new_window)
                    if _new_window != _first_window and _new_window not in _existing_windows:
                        # Third element = is_page1 for THIS window, i.e. is it a
                        # yesterday's-overnight (top-of-page night) window?
                        _ex.setdefault("extra_shifts", []).append(
                            (_new_start, _new_end, _new_top_night)
                        )
                        _ex.setdefault("window_terminals", {})[_new_window] = terminal
                    if terminal_note and not _ex.get("transfer"):
                        _ex["transfer"] = terminal_note
                    shift_end_ovr = None
                    shift_start_ovr = None

                entry = shift_map[key]
                if current_tsa_designated or name_note_tsa:
                    entry["tsa_designated"] = True
                if current_duty_block:
                    entry["duty_block"] = current_duty_block
                if trainee_att:
                    entry["trainee_attendant"] = True
                    if _prev_worker_name and "mentor" not in entry:
                        entry["mentor"] = _prev_worker_name
                if tl_trainee:
                    entry["tl_trainee"] = True
                if sick:
                    entry["sick"] = True
                if shift_end_ovr and not entry["shift_end_override"]:
                    entry["shift_end_override"] = shift_end_ovr
                if shift_start_ovr and not entry["shift_start_override"]:
                    entry["shift_start_override"] = shift_start_ovr
                if current_blocked_range:
                    if current_blocked_range not in entry["blocked"]:
                        entry["blocked"].append(current_blocked_range)
                        entry.setdefault("blocked_roles", []).append(current_blocked_label)
                for eb in extra_blocked:
                    if eb not in entry["blocked"]:
                        entry["blocked"].append(eb)
                        entry.setdefault("blocked_roles", []).append("")

                # A trainee is ALWAYS paired with a veteran דייל — never with
                # another trainee (user rule 2026-08-06: "טרייני לא יכול להיות
                # משובץ לטרייני אחר אלא רק לדייל ותיק"). Two trainee rows can
                # sit one under the other in a column, and tracking trainee
                # rows here made the lower one shadow the upper one.
                if not (trainee_att or tl_trainee):
                    _prev_worker_name = possible_name

    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    return shift_map


def apply_color_transfer_notes(uploaded_file, shift_map):
    """
    Floating color-linked transfer notes: a standalone cell like
    "הגעה לטרמינל 1 בשעה 01:00 לאחר הפסקה" whose FILL COLOR links it to
    every worker-name cell painted the same color in the same sheet
    (user-confirmed mechanism, 2026-07). Requires openpyxl — pandas
    drops fill colors. Applies the parsed note as entry["transfer"] for
    each linked worker already present in shift_map (name cells there are
    matched by name_key after clean_roster_name).
    """
    from openpyxl import load_workbook

    def _fill_of(cell):
        f = cell.fill
        if f is None or f.patternType is None:
            return None
        rgb = getattr(f.fgColor, "rgb", None)
        return str(rgb) if rgb else None

    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    try:
        wb = load_workbook(uploaded_file, data_only=True)
    except Exception:
        return shift_map

    applied = 0
    for ws in wb.worksheets:
        # Pass 1: floating notes = transfer text in a cell whose cleaned "name"
        # is empty after stripping the note (i.e. the cell is ONLY the note).
        notes_by_fill = {}
        for row in ws.iter_rows():
            for cell in row:
                if not cell.value:
                    continue
                text = clean_text(str(cell.value))
                note = parse_terminal_note(text)
                if not note:
                    continue
                # Strip the same "תחילת מש' טרמינל X," leading prefix
                # parse_terminal_note ignores, before checking residue — else
                # its leftover words ("תחילת מש'") look like a name attached
                # to the note and the whole floating note gets skipped.
                residue = TERMINAL_SUFFIX_RE.sub(
                    "", START_TERMINAL_PREFIX_RE.sub("", text)
                ).strip()
                # "הגעה לטרמינל..." notes start with the note itself → no residue
                if residue and len(residue.split()) >= 2:
                    continue  # inline note attached to a name — handled elsewhere
                fill = _fill_of(cell)
                if fill:
                    notes_by_fill[fill] = note
        if not notes_by_fill:
            continue
        # Pass 2: apply to same-fill worker cells.
        for row in ws.iter_rows():
            for cell in row:
                if not cell.value:
                    continue
                fill = _fill_of(cell)
                if fill not in notes_by_fill:
                    continue
                nm = clean_roster_name(str(cell.value))
                if not nm or len(nm.split()) < 2:
                    continue
                entry = shift_map.get(name_key(nm))
                # An "_inferred" transfer (see merge_t1_shift_map) is a best-
                # guess with no explicit note backing it — a real color-linked
                # note found here is authoritative and must override it.
                if entry is not None and (not entry.get("transfer") or entry["transfer"].get("_inferred")):
                    # Tagged "_color_linked" so merge_t1_shift_map can later
                    # cross-check it against the T1 roster: the same fill color
                    # (pink FFFF9999) is ALSO used in these workbooks for
                    # unrelated highlighting (tonight's night block, other
                    # sheets' bands), so a color match alone routinely links
                    # the note to workers who never move to T1 at all.
                    entry["transfer"] = dict(notes_by_fill[fill], _color_linked=True)
                    applied += 1
    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    return shift_map


def merge_t1_shift_map(shift_map, t1_map):
    """
    Merge the TR1 roster's shift map into the main (T3) one.
    A worker present in both files gets the T1 window appended as an extra
    shift window tagged terminal "1" (e.g. yesterday-night worker whose shift
    continues at T1 02:00-06:00). A worker only in the T1 file is added as-is.
    """
    seen_ids = set()
    for key, t1e in t1_map.items():
        if id(t1e) in seen_ids:
            continue  # skip the reversed-name alias pointing at the same dict
        seen_ids.add(id(t1e))
        main = shift_map.get(key) or shift_map.get(name_key(" ".join(reversed(t1e["original"].split()))))
        if main is None:
            shift_map[key] = t1e
            key_rev = name_key(" ".join(reversed(t1e["original"].split())))
            if key_rev not in shift_map:
                shift_map[key_rev] = t1e
            continue
        _t1_windows = [(t1e.get("shift_start_override") or t1e["start"],
                        t1e.get("shift_end_override") or t1e["end"])]
        for _s, _e, _p1 in t1e.get("extra_shifts", []):
            _t1_windows.append((_s, _e))
        _first = (main.get("shift_start_override") or main["start"],
                  main.get("shift_end_override") or main["end"])
        _existing = {(s, e) for s, e, _p in main.get("extra_shifts", [])}

        def _t2m_local(t):
            try:
                _h, _m2 = str(t).split(":")[:2]
                return int(_h) * 60 + int(_m2)
            except (ValueError, IndexError):
                return None

        # A "to T3" transfer on the T1 entry whose 'at' time is BEFORE the
        # worker's own T1 arrival is contradictory — the T1 file lists them
        # WORKING at T1 from that time, so they can't have "moved to T3"
        # earlier. This happens when a floating color note that belongs to a
        # DIFFERENT worker gets mis-linked. Treat it as absent so the correct
        # T3->T1 transfer is inferred from the T1 window below. Found via real
        # 20.07 data: TL#40 / TL#28 ("הגעה מטר3 01:00-07:00", i.e. arrive
        # at T1 from T3 at 01:00) wrongly picked up "המשך מש' בטרמינל 3 משעה
        # 22:30" → transfer {'to':'3','at':'22:30'}, 22:30 < their 01:00 T1
        # arrival, leaving their T3 window uncapped (they stayed T3-eligible
        # after 01:00 and bounced T1→T3).
        _t1e_tr = t1e.get("transfer") or {}
        _t1e_bogus_transfer = False
        if _t1e_tr.get("to") == "3" and _t1e_tr.get("at"):
            _at_m = _t2m_local(_t1e_tr["at"])
            _ws_m = _t2m_local(t1e.get("shift_start_override") or t1e["start"])
            _we_m = _t2m_local(t1e.get("shift_end_override") or t1e["end"])
            if None not in (_at_m, _ws_m, _we_m):
                _we_adj = _we_m if _we_m > _ws_m else _we_m + 1440
                _at_adj = _at_m if _at_m >= _ws_m else _at_m + 1440
                # A genuine T1->T3 move for THIS worker must happen DURING their
                # own T1 window. A "to T3" transfer whose time falls OUTSIDE it
                # belongs to a different worker (mis-linked color note).
                if not (_ws_m <= _at_adj <= _we_adj):
                    _t1e_bogus_transfer = True

        for _w in _t1_windows:
            if _w == _first or _w in _existing:
                # The T1 file lists the SAME window the T3 roster already has
                # (e.g. סיירת workers whose full 22:00-07:00 shift appears in
                # both files). Don't retag it wholesale to T1 — the worker's
                # transfer note ("מעבר לטרמינל 1 ב02:00") governs the split
                # into a T3 part and a T1 part.
                continue
            main.setdefault("window_terminals", {})[_w] = "1"
            main.setdefault("extra_shifts", []).append((_w[0], _w[1], False))
            _existing.add(_w)
            # A worker listed in BOTH files under a T1 window that OVERLAPS
            # their T3 window (starts later, same/overlapping span) has
            # genuinely moved to T1 mid-shift — even if NEITHER file carries
            # an explicit inline/color transfer note (a worker can simply be
            # listed under the T1 roster's own time-range section header,
            # with no note anywhere naming the move). Without inferring a
            # transfer here, the T3 (source) window is never truncated, so
            # the worker stays fully eligible for T3 flights during the exact
            # hours they're actually at T1 — found via real data: חיה מושקה
            # סלם, listed as "21:00-07:00" (T3, של"ן) with NO transfer note,
            # and separately as "01:00-07:00" under the T1 file's own section
            # — got assigned real T3 flights at 04:35 and 06:00, an hour+
            # after she was actually due at T1.
            if not main.get("transfer") and (not t1e.get("transfer") or _t1e_bogus_transfer):
                _fs_m, _fe_m = _t2m_local(_first[0]), _t2m_local(_first[1])
                _ws_m, _we_m = _t2m_local(_w[0]), _t2m_local(_w[1])
                if None not in (_fs_m, _fe_m, _ws_m, _we_m):
                    _f_dur = (_fe_m - _fs_m) % 1440 or 1440
                    _w_pos = (_ws_m - _fs_m) % 1440
                    if 0 < _w_pos < _f_dur:
                        # Marked "_inferred" so downstream page-1-restriction
                        # logic can tell this transfer was derived specifically
                        # from THIS (main/_first) window's own overlap with the
                        # T1 file — not from an explicit note that might really
                        # belong to a DIFFERENT, separate shift the same worker
                        # has on another night (a worker can legitimately have
                        # two distinct shifts; blindly applying a transfer found
                        # on one to the other's restricted tail would mis-tag
                        # it). See the page-1 restriction block's use of this
                        # flag in apply_shift_map_to_employees.
                        main["transfer"] = {"to": "1", "at": _w[0], "_inferred": True}
        # the T1 file confirms the worker is NOT an excluded upcoming-night
        # leftover for the T1 portion; keep the main entry's flags otherwise.
        if t1e.get("transfer") and not main.get("transfer") and not _t1e_bogus_transfer:
            main["transfer"] = t1e["transfer"]

    # Cross-check color-linked T3->T1 transfers against the T1 roster itself:
    # a worker who genuinely continues at T1 always appears in that night's
    # TR1 file (verified across 12/16/21/30.07 real data — every true transfer
    # is IN the TR1 roster, every color false-positive is NOT). The pink fill
    # that carries the floating note is reused broadly for unrelated blocks
    # (e.g. tonight's night section, other sheets), so a color-only link is
    # unreliable on its own — drop any color-linked "to T1" transfer whose
    # worker is absent from the TR1 file (found via real 30.07 data: אלינור
    # דניאל + agent#60 on דלפקי ש"ש, and 17 tonight-block names on של"ן, all
    # wrongly tagged as moving to T1 at 01:00). Inline notes typed in the
    # worker's own cell are NOT touched — only the floating color mechanism.
    if t1_map:
        _swept = set()
        for entry in shift_map.values():
            if id(entry) in _swept:
                continue
            _swept.add(id(entry))
            _tr = entry.get("transfer") or {}
            if not (_tr.get("_color_linked") and _tr.get("to") == "1"):
                continue
            # An UPCOMING-NIGHT worker transfers during tonight's shift, so the
            # Terminal-1 half of it happens TOMORROW and will appear in
            # tomorrow's TR1 roster — never in today's. Cross-checking them
            # against today's TR1 file therefore proves nothing, and dropped 10
            # genuine transfers on 16.08 (TL#41, TL#39, TSA-inspector#3 ...).
            # User rule 2026-08-16: the colour marking stands on its own for
            # them, even though the next day's roster is not uploaded yet.
            if entry.get("upcoming_night"):
                continue
            _nm = str(entry.get("original", "")).strip()
            _k = name_key(_nm)
            _k_rev = name_key(" ".join(reversed(_nm.split())))
            if t1_map.get(_k) is None and t1_map.get(_k_rev) is None:
                entry["transfer"] = None
    return shift_map


def apply_shift_map_to_employees(employees_df, shift_map_with_names):
    """
    Apply shift times and annotations from the daily schedule to employees_df.
    Adds columns: תחילת משמרת, סוף משמרת, חסימות, חולה
    """
    df = employees_df.copy()

    for col in ["תחילת משמרת", "סוף משמרת", "חסימות", "חולה", "shift_sheet", "in_daily_excel", "upcoming_night", "תגבור שלוחה"]:
        if col not in df.columns:
            if col in ("חולה", "תגבור שלוחה"):
                df[col] = False
            elif col in ("in_daily_excel", "upcoming_night"):
                df[col] = 0
            else:
                df[col] = ""

    def get_entry(emp_name):
        key = name_key(emp_name)
        if key in shift_map_with_names:
            return shift_map_with_names[key]
        key_rev = name_key_reversed(emp_name)
        if key_rev in shift_map_with_names:
            return shift_map_with_names[key_rev]
        emp_words = [w for w in emp_name.split() if len(w) > 1]
        parts = {name_key(w) for w in emp_words}
        best = None; best_score = 1
        for _, entry in shift_map_with_names.items():
            orig_words = [w for w in entry["original"].split() if len(w) > 1]
            orig_parts = {name_key(w) for w in orig_words}
            shared = len(parts & orig_parts)
            # Require one side's word set to be a full SUBSET of the other's
            # (i.e. only STRAY extra words — leftover note fragments, a
            # missing middle name — are tolerated, never a SUBSTITUTED word).
            # A raw shared-count check let two different employees who share
            # a multi-word surname collide (e.g. "agent#6" vs "סימן
            # טוב דנה" both share the 2-word "סימן טוב" surname while
            # differing only in the given name — plain shared-count treats
            # that identically to a harmless 1-word typo). Anchoring on
            # "first word = given name" doesn't work either: the employees
            # file stores some names given-name-first and others
            # surname-first, so word position isn't a reliable signal
            # (found via real data, same אורי/דנה collision).
            _name_anchor_ok = parts <= orig_parts or orig_parts <= parts
            if shared > best_score and _name_anchor_ok:
                best_score = shared; best = entry
        return best

    for idx, row in df.iterrows():
        emp_name = clean_text(row.get("שם", ""))
        if not emp_name:
            continue

        entry = get_entry(emp_name)
        if not entry:
            # Employee not in today's daily Excel — keep whatever is already in df
            # (could be a previous-day value or blank; left as-is)
            continue

        if entry.get("sick"):
            df.at[idx, "חולה"] = True
            continue

        # Concourse/management reinforcement (תגבור שלוחה) — flag it, but still
        # apply the shift below so the worker stays visible in the "מי עובד היום"
        # list; streamlit filters the flagged rows out of the SCHEDULABLE pool.
        if entry.get("concourse_reinforcement"):
            df.at[idx, "תגבור שלוחה"] = True

        _orig_start = entry.get("shift_start_override") or entry["start"]
        _orig_end   = entry.get("shift_end_override")   or entry["end"]
        _orig_is_page1 = entry.get("is_page1", True)
        start = _orig_start
        end   = _orig_end
        start_is_page1 = _orig_is_page1

        # Collect ALL shift windows for this worker (primary + extra columns),
        # each tagged with whether it came from PAGE 1 (yesterday's overnight
        # shift, already completed) or PAGE 2 (today's own fresh roster).
        _all_shift_windows = [(_orig_start, _orig_end, _orig_is_page1)] + list(
            entry.get("extra_shifts", [])
        )

        # When a worker appears in multiple shift columns (e.g. a night-shift
        # column AND a day-shift column), prefer the night/early-morning window
        # as the primary shift so classify_shift gives them the correct priority
        # for early-morning flight assignments.
        if len(_all_shift_windows) > 1:
            for _ws, _we, _wp1 in _all_shift_windows:
                try:
                    _wh = int(_ws.split(":")[0])
                    if _wh >= 20 or _wh < 6:   # night (20:00+) or early-morning (<06:00)
                        start = _ws
                        end   = _we
                        start_is_page1 = _wp1
                        break
                except (ValueError, IndexError):
                    pass

        # Always override from the daily Excel — it is the source of truth for
        # today's shifts.  A stale value from employees_clean.xlsx must not
        # block night-shift workers who appear in the daily schedule.
        df.at[idx, "תחילת משמרת"] = start
        df.at[idx, "סוף משמרת"]   = end
        df.at[idx, "shift_sheet"]   = entry.get("sheet", "")
        # Whoever is rostered under the "סיירת" sheet is a ילד עובדים — the
        # restricted-worker quota and role limits apply to them (user rule
        # 2026-08-23). Confirmed against the file: 8 of the 10 סיירת workers
        # who exist in employees_clean were already flagged.
        # Guarded to entries whose ONLY sheet is סיירת: a worker merged from
        # several sheets carries their PRIMARY sheet here, and flagging that
        # would brand the wrong person — the ר"צ TL#39 shares a name with a
        # different, סיירת worker and would otherwise be marked restricted.
        if "סיירת" in str(entry.get("sheet", "")):
            # The sheet says RESTRICTED; which KIND is the employees file's to
            # say. סיירת holds both ילדי עובדים and פורשים, and overwriting the
            # category mislabels people — restricted-agent#9 is a פורש and was being
            # rewritten as ילד עובדים (user correction 2026-08-23). Only fill in
            # when no restricted category is set at all.
            _already_restricted = any(
                str(df.at[idx, _c]).strip() == "כן"
                for _c in ("ילד עובדים", "ילדי עובדים", "פורשים")
                if _c in df.columns
            )
            if not _already_restricted:
                for _rc in ("ילד עובדים", "ילדי עובדים"):
                    if _rc in df.columns:
                        df.at[idx, _rc] = "כן"
        df.at[idx, "in_daily_excel"] = 1
        df.at[idx, "upcoming_night"] = 1 if entry.get("upcoming_night", False) else 0
        # Tonight's night shift, as its OWN shift — a worker can hold this AND a
        # page-1 overnight that ends this morning. Carried to the UI so
        # "מי עובד היום" can list one row per shift ("HH:MM-HH:MM", comma-joined
        # when there is more than one).
        _bnw = entry.get("bottom_night_windows") or []
        if _bnw:
            if "משמרת לילה הבאה" not in df.columns:
                df["משמרת לילה הבאה"] = ""
            df.at[idx, "משמרת לילה הבאה"] = ",".join(f"{_s}-{_e}" for _s, _e in _bnw)
        # The worker's OTHER shift of the same operational day, if any. It must
        # be derived from the window actually CHOSEN as primary a few lines
        # above (the night/early-morning preference can pick a different one
        # than the parse did), or the two disagree and the second row comes out
        # a duplicate of the first — found via real 23.08 data: agent#13,
        # 19:00-00:30 today plus 22:00-07:00 carried over from page 1, listed
        # 22:00-07:00 twice while the 19:00 shift vanished.
        from utils.helpers import time_to_minutes as _t2m_local

        def _outside_primary(_ws):
            # A separate shift STARTS outside the primary window. Windows that
            # start inside it are parts of the same shift — the post-transfer
            # T1 tail (agent#9: 22:00-06:00 plus 01:00-06:00), a
            # re-stated band, and so on. Without this the page-1 flag alone
            # marked 27 of 189 workers as two-shift on 23.08, nearly all wrong.
            # time_to_minutes is NOT imported at this module's top level; the
            # bare except below was swallowing the resulting NameError and
            # reporting every window as "inside the primary", which is why
            # agent#13's 19:00-00:30 shift never registered as a second one.
            try:
                _wm = _t2m_local(_ws)
                _sm2, _em2 = _t2m_local(start), _t2m_local(end)
            except (ValueError, TypeError, AttributeError, IndexError):
                return False
            _span = (_em2 - _sm2) % 1440 or 1440
            return not (0 <= (_wm - _sm2) % 1440 < _span)

        # A window tagged with a terminal OTHER than the worker's base is the
        # far half of a mid-shift transfer, not a shift of its own — עומר
        # בלטקסה's 18:00-01:30 T1 window belongs to the same shift as his
        # 21:00-07:00 T3 one.
        _wt_shift = entry.get("window_terminals", {}) or {}
        _base_term_shift = entry.get("terminal", "3") or "3"

        _other = [
            (_ws, _we) for _ws, _we, _wp1 in _all_shift_windows
            if (_ws, _we) != (start, end)
            and bool(_wp1) != bool(start_is_page1)
            and _outside_primary(_ws)
            and _wt_shift.get((_ws, _we), _base_term_shift) == _base_term_shift
        ]
        if _other:
            _col_other = "משמרת קודמת" if not start_is_page1 else "משמרת לילה הבאה"
            if _col_other not in df.columns:
                df[_col_other] = ""
            df.at[idx, _col_other] = ",".join(f"{_s}-{_e}" for _s, _e in _other)
        # Roster "טרייני - שם" prefix → NEW attendant, must be paired
        # (existing דייל-בטרייני pairing rules in the scheduler pick this up).
        if entry.get("trainee_attendant"):
            if "דייל בטרייני" not in df.columns:
                df["דייל בטרייני"] = "לא"
            df.at[idx, "דייל בטרייני"] = "כן"
            # Store the named mentor so the scheduler can shadow them EXACTLY
            # (a plain טרייני = new attendant does every one of the mentor's
            # flights — user rule 2026-07-22). Resolve the roster-order name:
            # the mentor is written first-name-first in the daily Excel
            # ("TL#13") but the employees file stores surname-first
            # ("בן דוד נועה"), so match on the permutation-invariant word set
            # and store the employees-file spelling the scheduler will see.
            _mentor_raw = clean_text(str(entry.get("mentor", "")))
            if _mentor_raw:
                if "חונך דייל" not in df.columns:
                    df["חונך דייל"] = ""
                _mentor_words = {name_key(_w) for _w in _mentor_raw.split() if len(_w) > 1}
                _resolved = _mentor_raw
                if _mentor_words:
                    _subset_hits = []
                    for _en in df["שם"].astype(str):
                        _ew = {name_key(_w) for _w in _en.split() if len(_w) > 1}
                        if not _ew:
                            continue
                        if _ew == _mentor_words:
                            _resolved = _en
                            _subset_hits = []
                            break
                        # The roster may carry an extra middle name the
                        # employees file omits (or vice versa) — accept a
                        # SUBSET match, but only when it is unambiguous, so a
                        # same-surname colleague can never be substituted in
                        # (same rule as apply_shift_map_to_employees; found via
                        # real 30.07 data: roster "יהונתן שחר עיני" vs
                        # employees "agent#16" — trainee-agent#2's mentor
                        # never resolved).
                        if _ew < _mentor_words or _mentor_words < _ew:
                            _subset_hits.append(_en)
                    if _subset_hits and len(_subset_hits) == 1:
                        _resolved = _subset_hits[0]
                df.at[idx, "חונך דייל"] = _resolved
        # Roster "טרייני ר\"צ" suffix → TL trainee TODAY (roster is the source
        # of truth for worker data; adds to whatever the employees file says).
        if entry.get("tl_trainee"):
            if "טרייני רצ" not in df.columns:
                df["טרייני רצ"] = "לא"
            df.at[idx, "טרייני רצ"] = "כן"
        # Roster "פיקוח TSA" section header → this worker is THE named TSA
        # inspector for today's shift (distinct from the general מפקח-TSA
        # CERTIFICATION column, which only says who's allowed to do the role
        # at all). Preferred by sort_candidates/consolidate_tsa_inspectors_by_
        # pier over an equally-certified worker who isn't rostered for it
        # today (user rule 2026-07-25).
        if entry.get("tsa_designated"):
            if "מפקח TSA מוגדר" not in df.columns:
                df["מפקח TSA מוגדר"] = "לא"
            df.at[idx, "מפקח TSA מוגדר"] = "כן"

        # Non-flight duty posting: excluded from automatic assignment, may be
        # offered in the swap UI as an approval-required option.
        if entry.get("duty_block"):
            if "תפקיד חוסם" not in df.columns:
                df["תפקיד חוסם"] = ""
            df.at[idx, "תפקיד חוסם"] = entry["duty_block"]

        blocked = entry.get("blocked", [])
        blocked_roles = entry.get("blocked_roles", [])
        if blocked:
            df.at[idx, "חסימות"] = ",".join(f"{s}-{e}" for s, e in blocked)
            for i, (s, e) in enumerate(blocked):
                role_label = blocked_roles[i] if i < len(blocked_roles) else ""
                df.at[idx, f"_blocked_role_{s}-{e}"] = role_label

        avail = list(entry.get("available_windows", []))
        # Include ALL shift windows in זמינות so is_within_shift covers every
        # time period the employee is active today (night ending + day starting etc.).
        for _ws, _we, _wp1 in _all_shift_windows:
            if (_ws, _we) not in avail:
                avail.append((_ws, _we))

        # Windows belonging to the PRIMARY (page-1 / day) shift of a worker who
        # also holds tonight's night shift. Everything not listed here belongs to
        # tonight's shift when "bottom_night_windows" is non-empty.
        _p1_tail_windows = set()

        # Page-1 night workers: their shift started YESTERDAY evening (e.g. 22:00).
        # The window "22:00-06:00" also matches TONIGHT's evening flights (23:xx, 00:xx)
        # because the scheduler has no date context.  Restrict their effective availability
        # to the early-morning portion only (01:30 onward) so they cannot be assigned
        # to tonight's flights.  תחילת משמרת stays "22:00" so classify_shift still
        # returns "night", giving them shift_pri=0 priority for early-morning ר"צ slots.
        # CRITICAL: only apply this when the chosen PRIMARY window actually came from
        # PAGE 1 (yesterday's already-completed overnight shift). A worker whose ONLY
        # shift is a fresh PAGE-2 evening/night entry (e.g. a brand-new 18:00-00:30
        # shift for TODAY, never appearing in the page-1 section at all) is NOT a
        # stale leftover shift — restricting them would fabricate a fake 01:30-06:00
        # window they were never rostered for and erase their real one.
        if not entry.get("upcoming_night") and not entry.get("available_windows") and start_is_page1:
            try:
                _ph = int(start.split(":")[0])
                if _ph >= 17:  # shift started in the evening → page-1 night worker
                    _end_str = end
                    try:
                        _end_h, _end_m = int(_end_str.split(":")[0]), int(_end_str.split(":")[1])
                    except (ValueError, IndexError):
                        _end_h, _end_m = 6, 0
                    if _end_h * 60 + _end_m < 6 * 60:
                        _end_str = "06:00"  # extend so late early-morning flights are covered
                    avail = [("01:30", _end_str)]
                    # Preserve any OTHER genuine window the worker has in full —
                    # workers can legitimately be rostered for TWO separate shifts
                    # the same day (e.g. 20:30-04:00 overnight, go home, then return
                    # 18:00-01:30 for a second shift that night). Confirmed against
                    # the real roster: e.g. "14:00-01:30" is a real, distinct shift
                    # section (not a reinforcement artifact) that some workers who
                    # also appear in the page-1 night section are separately and
                    # genuinely rostered into. Only the CHOSEN PRIMARY window (the
                    # stale, already-completed overnight shift) gets restricted to
                    # 01:30-{end}; every other distinct window is preserved as-is.
                    # NOTE: scan _all_shift_windows (not just extra_shifts) since
                    # the non-primary window may be entry["start"]/["end"] itself
                    # when a LATER-seen window got chosen as primary above.
                    for _xs, _xe, _xp1 in _all_shift_windows:
                        if (_xs, _xe) == (start, end):
                            continue
                        # A SECOND window that is itself page-2-origin (_xp1=False)
                        # and a genuine night start (≥20:00) represents a shift that
                        # starts TONIGHT — its early-morning tail belongs to TOMORROW
                        # morning, not today's. Left unrestricted, it wrongly becomes
                        # available for TODAY's early-morning flights (which belong
                        # to page-1) before the page-2 shift has even started. Cap it
                        # the same way a pure upcoming_night worker is capped, below.
                        # Found via real data: TL#4 has a genuine page-1
                        # night (04.07-05.07, T3) AND a separate page-2 night
                        # (05.07-06.07, T3→T1 transfer at 01:00) — her page-2 window
                        # was preserved in full ("21:00-07:00"), letting her get
                        # assigned to LY5107 (04:30, T1) on the MORNING of 05.07 —
                        # hours before her page-2 shift even begins.
                        if not _xp1:
                            try:
                                _xh = int(_xs.split(":")[0])
                                if _xh >= 20:
                                    avail.append((_xs, "01:30"))
                                    continue
                            except (ValueError, IndexError):
                                pass
                        avail.append((_xs, _xe))
            except (ValueError, IndexError, AttributeError):
                pass

            # A page-1 worker who ALSO has a terminal transfer needs the
            # restricted "01:30-{end}" tail itself checked against the
            # transfer time — this new window's own (start, end) tuple was
            # never seen by whatever built "window_terminals", so the
            # transfer-split loop below (which matches against those exact
            # tuples) would silently skip it and leave it tagged at the
            # SOURCE terminal even when the transfer already happened before
            # "01:30" — i.e. the worker is really at the destination terminal
            # for this entire restricted window. Retag it directly here.
            # Found via real data: TL#6 — page-1 restricted to
            # "01:30-07:00"/T3, but her transfer to T1 (inferred from the
            # T1-file merge) happens at 01:00, strictly before "01:30" —
            # she was fully at T1 the whole time, yet kept showing eligible
            # for T3 flights at 04:35 and 06:00.
            def _t2m_p1(_t):
                try:
                    _h, _m = str(_t).split(":")[:2]
                    return int(_h) * 60 + int(_m)
                except (ValueError, IndexError):
                    return None

            # Only act when there is concrete evidence the transfer belongs to
            # THIS worker's shift (not an unrelated, genuinely separate one —
            # same-name entries share one dict regardless of which occurrence
            # a note was tied to, so a bare "entry.get('transfer')" isn't proof
            # enough by itself; e.g. TL#4 has a real 04.07-05.07
            # night AND a separate 05.07-06.07 shift with its own transfer —
            # a naive check would wrongly retag the FIRST shift's tail too).
            # Evidence = any of:
            # (a) the transfer was specifically INFERRED from this primary
            #     window's own T1/T3 overlap (see merge_t1_shift_map);
            # (b) some OTHER window on this entry is explicitly tagged as the
            #     transfer's destination terminal AND starts exactly at the
            #     transfer's "at" time — i.e. the actual destination window
            #     this transfer describes is visibly present; or
            # (c) the entry has NO other shift window at all — a single,
            #     unambiguous shift, so the transfer can't belong to some
            #     OTHER night. (b) alone misses a worker whose T1-file window
            #     is the exact SAME span as their T3 window (e.g. both list
            #     "22:00-07:00") — merge_t1_shift_map's own dedup then adds no
            #     extra window at all, even though the explicit transfer note
            #     is perfectly valid and there is nothing else it could refer
            #     to (found via real data: TL#6, 12.07.2026 file).
            _tr_p1 = entry.get("transfer")
            _wt_p1 = entry.get("window_terminals", {}) or {}
            _p1_extra = entry.get("extra_shifts", [])
            _p1_dest_window_seen = _tr_p1 and any(
                _xs == _tr_p1.get("at") and _wt_p1.get((_xs, _xe)) == _tr_p1.get("to")
                for _xs, _xe, _ in _p1_extra
            )
            _p1_no_other_shifts = len(_p1_extra) == 0
            if (_tr_p1 and _tr_p1.get("to") and _tr_p1.get("at") and avail
                    and (_tr_p1.get("_inferred") or _p1_dest_window_seen or _p1_no_other_shifts)):
                _p1_win = avail[0]
                _p1_at_m = _t2m_p1(_tr_p1["at"])
                _p1_s_m = _t2m_p1(_p1_win[0])
                if _p1_at_m is not None and _p1_s_m is not None:
                    # Anchor both to the same operational night as the
                    # transfer's own reference (its "at" is always AFTER
                    # midnight relative to an evening shift start) — a
                    # restricted-tail start (e.g. 01:30) that lands at/after
                    # the transfer time means the whole tail is post-transfer.
                    if (_p1_s_m - _p1_at_m) % 1440 < 12 * 60:
                        entry.setdefault("window_terminals", {})[_p1_win] = _tr_p1["to"]

        # Upcoming-night workers (bottom-of-page night band = TONIGHT's night,
        # night N→N+1). Per the confirmed domain model they are NOT excluded from
        # the schedule — they may staff tonight's LATE flights, but ONLY as a last
        # resort after day/evening workers (the sort_candidates last-resort key
        # enforces the priority). Restrict their availability to the late-night
        # tail only (start → 01:30) so they can never grab the schedule's
        # EARLY-MORNING flights (03:xx-06:xx) — those belong to yesterday's
        # overnight (page-1) workers. 01:30 is the same handoff used above for the
        # page-1 restriction, giving a clean complementary split.
        elif entry.get("upcoming_night") and not entry.get("available_windows"):
            try:
                _uh = int(start.split(":")[0])
                # A דייל-בטרייני shadows their MENTOR, so they work the mentor's
                # day — never a different one. When the mentor is a normal
                # (non-upcoming) worker, the trainee sitting under them in the
                # roster's night block must not be capped at 01:30 while the
                # mentor keeps the full night-into-morning window: the pair
                # would be split for every early-morning flight (user rule
                # 2026-08-06 — "לא יכול להיות שעובדי משמרת הלילה לא ירדו לטיסות
                # בוקר"; found via real 30.07 data: trainee-agent#8, capped to
                # 22:00-01:30 under her mentor TL#39's 22:00-08:30).
                _mentor_ok = False
                _m_nm = clean_text(str(entry.get("mentor", "")))
                if _m_nm:
                    _m_entry = (shift_map_with_names.get(name_key(_m_nm))
                                or shift_map_with_names.get(
                                    name_key(" ".join(reversed(_m_nm.split())))))
                    _mentor_ok = bool(_m_entry is not None
                                      and not _m_entry.get("upcoming_night"))
                if _uh >= 20 and not _mentor_ok:  # genuine night start
                    # Preserve a genuine pre-transfer T1 window of the SAME shift:
                    # a worker who STARTS at Terminal 1 in the evening and moves to
                    # Terminal 3 for the night (e.g. 18:00-01:30 T1 → transfer to T3
                    # at 21:30). The clamp exists only to stop night workers grabbing
                    # the EARLY-MORNING (03-06) flights that belong to yesterday's
                    # page-1 overnight; a T1-tagged window that ends by the 01:30
                    # handoff is safe. Without this, such workers lost their whole
                    # T1 evening portion and were scheduled T3-night only (found via
                    # real data 2026-07-22: בן דוד נועה, בן סמו ליטל, trainee-agent#4).
                    _wt_un = entry.get("window_terminals", {}) or {}
                    _keep_t1 = [
                        w for w in avail
                        if _wt_un.get(w) == "1" and w != (start, "01:30")
                    ]
                    avail = [(start, "01:30")] + _keep_t1
                    # A worker can hold BOTH a page-1 overnight that ends this
                    # morning AND tonight's upcoming-night shift — two separate
                    # shifts on consecutive nights. The clamp above covers only
                    # tonight's; without also keeping the page-1 morning tail the
                    # shift they are on RIGHT NOW disappears (found via real 16.08
                    # data: TL#41, 22:00-06:00 on page 1 and 21:00-07:00 on
                    # page 2, was left with 22:00-01:30 alone and could not be
                    # scheduled for any early-morning flight).
                    if start_is_page1:
                        _p1_end = end
                        try:
                            _peh, _pem = int(_p1_end.split(":")[0]), int(_p1_end.split(":")[1])
                            if _peh * 60 + _pem < 6 * 60:
                                _p1_end = "06:00"
                        except (ValueError, IndexError):
                            _p1_end = "06:00"
                        if ("01:30", _p1_end) not in avail:
                            avail.append(("01:30", _p1_end))
                        # This tail belongs to YESTERDAY's shift; everything else
                        # this branch produced belongs to tonight's. Recorded so
                        # each availability window can be attributed to its own
                        # shift (see "שיוך משמרת" below).
                        _p1_tail_windows.add(("01:30", _p1_end))
            except (ValueError, IndexError, AttributeError):
                pass

        # ── Terminal tagging per availability window ─────────────────────────
        # Each window carries the terminal it belongs to ("3" default, "1" for
        # TR1-roster windows). A transfer note (inline or color-linked) splits /
        # truncates windows: per the user rule, when a worker moves between
        # terminals the schedule must leave time for BREAK + SHUTTLE before the
        # arrival time at the destination, so the source-terminal window ends
        # (break+15min) before the transfer time and the destination window
        # starts at it.
        def _t2m(t):
            try:
                _h, _m2 = str(t).split(":")[:2]
                return int(_h) * 60 + int(_m2)
            except (ValueError, IndexError):
                return None

        def _m2t(m):
            return f"{(m // 60) % 24:02d}:{m % 60:02d}"

        # Does this worker hold a SECOND (tonight's) shift? Only then do the
        # windows need attributing to one shift or the other.
        _bnw_present = bool(entry.get("bottom_night_windows"))
        _avail_shifts = [
            ("0" if (not _bnw_present or w in _p1_tail_windows) else "1")
            for w in avail
        ]

        _term_base = entry.get("terminal", "3") or "3"
        _wt = entry.get("window_terminals", {}) or {}
        avail_terms = [_wt.get((s, e), _term_base) for (s, e) in avail]

        _tr = entry.get("transfer")
        if _tr and _tr.get("to"):
            _to = _tr["to"]
            _at = _tr.get("at") or ""
            if not _at:
                # e.g. "משה לוי -טרמינל 1": the whole shift is at the other terminal
                avail_terms = [_to] * len(avail)
                _full_ranges = list(avail)
            else:
                _at_m = _t2m(_at)
                # User rule: a transferring worker needs 30 min travel time to
                # the other terminal before the arrival time. The 45-min break
                # itself does NOT have to be glued to the transfer — it can be
                # taken EARLIER in the shift, exactly like a normal shift's own
                # break placement (e.g. a 14:00-01:30 shift breaks ~18:00-19:00,
                # not right before shift end). So reserve the FULL break+shuttle
                # (75 min) only when there's genuinely no room for an earlier
                # break in this source-terminal window; once there's ≥3h of
                # room before the transfer, only the 30-min shuttle is reserved,
                # letting the worker stay productive right up until then (user
                # rule 2026-07-22, found via real data: TL#14/בן דוד/בן
                # סמו losing 45 min of genuine T1 coverage they didn't need to
                # lose, e.g. LY5181 19:45-21:00 T1 ר"צ).
                _EARLY_BREAK_ROOM_MIN = 3 * 60
                try:
                    from utils.helpers import required_break as _req_break
                    _break_len = _req_break({"תחילת משמרת": start, "סוף משמרת": end}) or 45
                except Exception:
                    _break_len = 45
                _has_explicit_dest = any(t == _to for t in avail_terms)
                _new_avail, _new_terms, _new_full, _new_shift = [], [], [], []
                _shift_of = {
                    w: ("0" if (not _bnw_present or w in _p1_tail_windows) else "1")
                    for w in avail
                }
                for (s, e), tterm in zip(avail, avail_terms):
                    s_m, e_m = _t2m(s), _t2m(e)
                    _sh = _shift_of.get((s, e), "0")
                    if tterm == _to or s_m is None or e_m is None or _at_m is None:
                        _new_avail.append((s, e)); _new_terms.append(tterm)
                        _new_full.append((s, e)); _new_shift.append(_sh)
                        continue
                    _dur = (e_m - s_m) % 1440 or 1440
                    _pos = (_at_m - s_m) % 1440
                    _reserve = 30 if _pos >= _EARLY_BREAK_ROOM_MIN else _break_len + 30
                    if 0 < _pos < _dur:
                        # window spans the transfer → keep the pre-transfer part
                        # (minus break+shuttle reserve) at the source terminal.
                        # Both halves record the FULL original (s, e) range in
                        # _new_full — used only for the workflow-tab DISPLAY label
                        # (user rule: show the worker's full shift span across a
                        # terminal transfer, not just the per-terminal portion);
                        # scheduling/candidacy still uses the precise cut windows.
                        _cut = (_at_m - _reserve) % 1440
                        _cut_pos = (_cut - s_m) % 1440
                        if 0 < _cut_pos < _dur:
                            _new_avail.append((s, _m2t(_cut))); _new_terms.append(tterm)
                            _new_full.append((s, e)); _new_shift.append(_sh)
                        if not _has_explicit_dest:
                            _new_avail.append((_at, e)); _new_terms.append(_to)
                            _new_full.append((s, e)); _new_shift.append(_sh)
                    elif (s, e) in _wt and (s_m - _at_m) % 1440 < 6 * 60:
                        # source-terminal window that STARTS shortly after the
                        # transfer moment (e.g. the page-1 restricted 01:30-07:00
                        # tail of a worker who moved to T1 at 01:00) — the worker
                        # is at the other terminal by then; drop it. Windows far
                        # after (≥6h, e.g. a genuine second evening shift) stay.
                        # Gated on "(s, e) in _wt" (an explicitly terminal-tagged
                        # window from the SAME multi-window bookkeeping as the
                        # transfer) — otherwise this wrongly deleted a worker's
                        # completely UNRELATED, genuinely separate shift on a
                        # different night just because its clock-time happened to
                        # start soon after this transfer's arrival time (found via
                        # real data: TL#4 has a real 04.07-05.07 night
                        # shift AND a separate 05.07-06.07 shift with a mid-shift
                        # T3→T1 transfer at 01:00 — the first shift's restricted
                        # 01:30-07:00 tail was being silently discarded).
                        continue
                    else:
                        _new_avail.append((s, e)); _new_terms.append(tterm)
                        _new_full.append((s, e)); _new_shift.append(_sh)
                avail, avail_terms = _new_avail, _new_terms
                _full_ranges = _new_full
                _avail_shifts = _new_shift
        else:
            _full_ranges = list(avail)

        for _col in ("טרמינל", "מעבר טרמינל", "זמינות טרמינל", "זמינות מלאה"):
            if _col not in df.columns:
                df[_col] = ""
        df.at[idx, "טרמינל"] = _term_base
        if _tr and _tr.get("to"):
            # When the roster gives the destination terminal its OWN window via a
            # section header ("הגעה מטרמינל 1 21:30-01:30"), that window's start IS
            # the arrival time — it is stated per-band and outranks a floating
            # colour-linked note, which is written once and pinned to many workers.
            # Found via real 16.08 data: agent#17 sits under a 21:30 arrival
            # header (so he is in T3, past his break, from 21:30) yet a colour-linked
            # note put his transfer at 22:00; his availability already split at 21:30,
            # so only the reported transfer time was wrong.
            _at_out = _tr.get("at") or ""
            _at_noted = _t2m(_at_out) if _at_out else None
            if _at_noted is not None:
                # Nearest destination window to the noted time — a worker can hold
                # SEVERAL windows at the destination terminal across the night
                # (עומר: 21:30-01:30 and 01:30-07:00 are both T3), so "earliest by
                # clock" would pick the wrong one across midnight.
                _dest_starts = [
                    _s for (_s, _e), _t in zip(avail, avail_terms)
                    if _t == _tr["to"] and _t2m(_s) is not None
                ]
                if _dest_starts:
                    _at_out = min(
                        _dest_starts,
                        key=lambda _v: min((_t2m(_v) - _at_noted) % 1440,
                                           (_at_noted - _t2m(_v)) % 1440),
                    )
            df.at[idx, "מעבר טרמינל"] = f"{_tr['to']}@{_at_out}"

        if avail:
            if "זמינות" not in df.columns:
                df["זמינות"] = ""
            df.at[idx, "זמינות מלאה"] = ",".join(f"{s}-{e}" for s, e in _full_ranges)
            df.at[idx, "זמינות"] = ",".join(f"{s}-{e}" for s, e in avail)
            df.at[idx, "זמינות טרמינל"] = ",".join(avail_terms)
            # Which SHIFT each window belongs to: "0" = the primary (page-1 /
            # day) shift, "1" = tonight's night shift. Aligned with "זמינות",
            # exactly like "זמינות טרמינל". Lets the UI show and edit each of a
            # worker's two shifts separately.
            if "שיוך משמרת" not in df.columns:
                df["שיוך משמרת"] = ""
            df.at[idx, "שיוך משמרת"] = ",".join(
                (_avail_shifts + ["0"] * len(avail))[:len(avail)]
            )

    return df


def stagger_transfer_breaks(employees_df, max_ratio=3):
    """
    For workers who transfer between terminals mid-shift ("מעבר טרמינל"
    column, format "to@at", set by apply_shift_map_to_employees), place their
    pre-transfer break EXPLICITLY as a blocked window — staggered across the
    group so at most ~1/(max_ratio) of the group sharing the same transfer
    window are on break at the same moment (user rule 2026-07-22: don't pull
    too many off the counters/check-in at once; the break itself doesn't have
    to sit right before the transfer — see the ≥3h-room early-break fix in
    apply_shift_map_to_employees above, which is what creates the slack this
    function schedules INTO).

    Only acts on workers whose pre-transfer source-terminal window has
    genuine slack (duration >= break length + 20 min padding); others are
    left untouched — their break stays wherever the scheduler/display
    naturally places it (e.g. a real gap between flights).

    Must run AFTER apply_shift_map_to_employees (needs "מעבר טרמینל" /
    "זמינות" / "זמינות טרמינל") and BEFORE build_schedule (writes into
    "חסימות", the blocked-window column is_available already respects).
    """
    df = employees_df.copy()
    if "מעבר טרמינל" not in df.columns or "זמינות" not in df.columns:
        return df
    if "זמינות טרמינל" not in df.columns:
        return df

    from utils.helpers import is_time_text, time_to_minutes, required_break as _req_break

    def _tm(t):
        return time_to_minutes(t) if is_time_text(t) else None

    def _mt(m):
        return f"{(m // 60) % 24:02d}:{m % 60:02d}"

    _candidates = []  # dicts: idx, ws, we (minutes), brk (minutes), at (minutes)
    for idx, row in df.iterrows():
        _tr = clean_text(row.get("מעבר טרמינל", ""))
        if not _tr or "@" not in _tr:
            continue
        _to, _at = _tr.split("@", 1)
        _to = _to.strip()
        if not is_time_text(_at):
            continue
        _at_m = time_to_minutes(_at)

        _avail = clean_text(row.get("זמינות", "")).split(",")
        _terms = clean_text(row.get("זמינות טרמינל", "")).split(",")
        if len(_avail) != len(_terms):
            continue

        # Find the pre-transfer (source-terminal) window: not the destination
        # terminal, and its own end lands at/near the transfer time (within
        # the possible 30-75 min reserve already cut by the earlier fix).
        _win = None
        for _w, _t in zip(_avail, _terms):
            if "-" not in _w or _t == _to:
                continue
            _ws, _we = _w.split("-", 1)
            _ws_m, _we_m = _tm(_ws), _tm(_we)
            if _ws_m is None or _we_m is None:
                continue
            _gap = (_at_m - _we_m) % 1440
            if _gap <= 80:
                _win = (_ws_m, _we_m)
                break
        if _win is None:
            continue

        # required_break() looks at "תחילת משמרת"/"סוף משמרת" — for a split
        # T1/T3 worker those hold only the NIGHT portion the scheduler chose
        # as primary (e.g. 21:30-01:30, 4h), understating their TRUE combined
        # day (e.g. 18:00-01:30, 7.5h) and wrongly returning 0 (no break owed)
        # below the 6h threshold. Build a synthetic full-span dict from the
        # earliest start / latest end across ALL "זמינות" windows instead.
        _all_starts, _all_ends = [], []
        for _w in _avail:
            if "-" not in _w:
                continue
            _s, _e = _w.split("-", 1)
            _sm, _em = _tm(_s), _tm(_e)
            if _sm is not None:
                _all_starts.append(_sm)
            if _em is not None:
                _all_ends.append(_em if _em >= _sm else _em + 1440)
        if not _all_starts or not _all_ends:
            continue
        _span_s = min(_all_starts)
        _span_e = max(_all_ends)
        _brk = _req_break({"תחילת משמרת": _mt(_span_s), "סוף משמרת": _mt(_span_e % 1440)})
        if not _brk:
            continue
        _ws_m, _we_m = _win
        _room = (_we_m - _ws_m) % 1440 or 1440
        if _room < _brk + 20:
            continue  # not enough slack for an explicit early break

        _candidates.append({"idx": idx, "ws": _ws_m, "we": _we_m, "brk": _brk, "at": _at_m})

    if not _candidates:
        return df

    # Cluster candidates by transfer-time proximity (within 90 min of each
    # other) — these are the ones actually competing for the same real-world
    # counter/check-in coverage window.
    _candidates.sort(key=lambda c: c["at"])
    _clusters = []
    for c in _candidates:
        if _clusters and (c["at"] - _clusters[-1][-1]["at"]) <= 90:
            _clusters[-1].append(c)
        else:
            _clusters.append([c])

    for _cluster in _clusters:
        _cap = max(1, -(-len(_cluster) // max_ratio))  # ceil(n / max_ratio)
        _placed = []  # list of (start_m, end_m) already assigned in this cluster
        for c in _cluster:
            _ws, _we, _brk = c["ws"], c["we"], c["brk"]
            _slack = (_we - _ws) - _brk
            _pref_start = _ws + _slack // 2  # default: middle of the slack
            _start = _pref_start
            _step = 15
            while True:
                _end = _start + _brk
                _overlap = sum(
                    1 for (_ps, _pe) in _placed if not (_end <= _ps or _start >= _pe)
                )
                if _overlap < _cap:
                    break
                _start += _step
                if _start + _brk > _we:
                    _start = _pref_start  # out of room to stagger further — accept overlap
                    break
            _placed.append((_start, _start + _brk))
            _bs, _be = _mt(_start), _mt(_start + _brk)
            _existing = clean_text(df.at[c["idx"], "חסימות"]) if "חסימות" in df.columns else ""
            _new_blocked = f"{_bs}-{_be}"
            df.at[c["idx"], "חסימות"] = f"{_existing},{_new_blocked}" if _existing else _new_blocked

    return df


# =========================
# LOAD FILES
# =========================


def _normalize_flight_cell(value):
    """Return a clean LY flight number from almost any cell text."""
    text = clean_text(value).upper().replace("‏", "").replace("‎", "")
    text = re.sub(r"\s+", "", text)
    m = re.search(r"LY\d{1,4}[A-Z]?", text)
    if not m:
        return ""
    flight = m.group(0)
    # לא מייבאים טיסות 8XXX, לפי החוק שקבענו
    if flight.replace("LY", "").startswith("8"):
        return ""
    return flight


def _normalize_time_cell(value):
    """Return HH:MM from strings / Excel times / pandas timestamps."""
    if pd.isna(value):
        return ""

    # pandas / python datetime-like values
    try:
        if hasattr(value, "hour") and hasattr(value, "minute"):
            return f"{int(value.hour):02d}:{int(value.minute):02d}"
    except Exception:
        pass

    text = clean_text(value)
    if not text:
        return ""

    # Excel sometimes stores time as fraction of day
    try:
        if re.fullmatch(r"\d+(\.\d+)?", text):
            num = float(text)
            if 0 <= num < 1:
                total = round(num * 24 * 60)
                return f"{(total // 60) % 24:02d}:{total % 60:02d}"
    except Exception:
        pass

    m = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?", text)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def _parse_time_pair(value):
    """Return departure and boarding if the same cell contains one or two times."""
    text = clean_text(value)
    times = re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    times = [_normalize_time_cell(t) for t in times]
    times = [t for t in times if t]
    if len(times) >= 2:
        return times[0], times[1]
    if len(times) == 1:
        return times[0], ""
    return _normalize_time_cell(value), ""


def _first_existing_column(df, names):
    """Find a column by exact name or case-insensitive name."""
    by_clean = {clean_text(c): c for c in df.columns}
    by_lower = {clean_text(c).lower(): c for c in df.columns}
    for name in names:
        if name in by_clean:
            return by_clean[name]
        if name.lower() in by_lower:
            return by_lower[name.lower()]
    return None


def _value_from_nearby_row(row_values, start_index, preferred_offsets):
    """Pick the first non-empty value around a detected flight cell."""
    for off in preferred_offsets:
        i = start_index + off
        if 0 <= i < len(row_values):
            val = clean_text(row_values[i])
            if val:
                return val
    return ""


def load_daily_schedule(uploaded_file):
    """
    Load the daily flight roster from the work schedule file.

    Works with:
    1. The official Hebrew sheet: דוח שיבוץ טיסות - המראות
    2. A clean table with headers like טיסה / יעד / המראה / בורדינג
    3. A messy Excel export where the useful columns are Unnamed: 8/7/6
    4. A raw grid where the flight number appears somewhere in the row
    """
    try:
        uploaded_file.seek(0)
    except Exception:
        pass

    excel = pd.ExcelFile(uploaded_file)
    preferred_sheet = "דוח שיבוץ טיסות - המראות"
    sheet_name = preferred_sheet if preferred_sheet in excel.sheet_names else excel.sheet_names[0]

    try:
        uploaded_file.seek(0)
    except Exception:
        pass
    raw = pd.read_excel(uploaded_file, sheet_name=sheet_name, dtype=object)
    raw.columns = raw.columns.astype(str).str.strip()

    flights = []

    def add_flight(flight_text, destination="", departure="", boarding="",
            gate="", aircraft="", reg="", pax="", trainee="", training="",
            source_order=None):

        dep, parsed_boarding = _parse_time_pair(departure)
        boarding_norm = _normalize_time_cell(boarding) or parsed_boarding

        # If the boarding column accidentally contains two times, use the first one there
        if not boarding_norm:
            _, b2 = _parse_time_pair(boarding)
            boarding_norm = b2

        if not dep:
            return

        flights.append({
       "_source_order": source_order if source_order is not None else len(flights),
        "טיסה": flight_text,
        "יעד": clean_text(destination).upper(),
        "המראה": dep,
        "בורדינג": boarding_norm,
        "גייט": clean_text(gate),
        "סוג מטוס": clean_text(aircraft),
        "רישוי": clean_text(reg),
        "נוסעים": clean_text(pax),
        "טרייני רצ": normalize_yes_no(trainee),
        "סוג הכשרה": clean_text(training),
    })
    # Case 1: the known export layout, where flight/time/destination sit in Unnamed columns
    if {"Unnamed: 8", "Unnamed: 7", "Unnamed: 6"}.issubset(set(raw.columns)):
        for row_i, row in raw.iterrows():
            add_flight(
                row.get("Unnamed: 8"),
                destination=row.get("Unnamed: 6"),
                departure=row.get("Unnamed: 7"),
                aircraft=row.get("Unnamed: 5"),
                source_order=int(row_i),
            )

    # Case 2: clean headers
    if not flights:
        flight_col   = _first_existing_column(raw, ["טיסה", "מספר טיסה", "Flight", "FlightNo", "flight", "flightno"])
        dest_col     = _first_existing_column(raw, ["יעד", "Destination", "destination", "Dest", "dest"])
        dep_col      = _first_existing_column(raw, ["המראה", "זמן המראה", "Departure", "STD", "departure", "std"])
        board_col    = _first_existing_column(raw, ["בורדינג", "תחילת בורדינג", "Boarding", "boarding"])
        gate_col     = _first_existing_column(raw, ["גייט", "שער", "Gate", "gate"])
        aircraft_col = _first_existing_column(raw, ["סוג מטוס", "מטוס", "Aircraft", "aircraft", "A/C", "AC"])
        reg_col      = _first_existing_column(raw, ["רישוי", "רישום", "Registration", "registration", "Reg", "REG"])
        pax_col      = _first_existing_column(raw, ["נוסעים", "PAX", "pax", "Passengers", "passengers"])
        trainee_col  = _first_existing_column(raw, ["טרייני רצ", "טרייני ר״צ", 'טרייני ר"צ'])
        training_col = _first_existing_column(raw, ["סוג הכשרה", "הכשרה"])

        if flight_col:
            for row_i, row in raw.iterrows():
                add_flight(
                    row.get(flight_col),
                    destination=row.get(dest_col) if dest_col else "",
                    departure=row.get(dep_col) if dep_col else "",
                    boarding=row.get(board_col) if board_col else "",
                    gate=row.get(gate_col) if gate_col else "",
                    aircraft=row.get(aircraft_col) if aircraft_col else "",
                    reg=row.get(reg_col) if reg_col else "",
                    pax=row.get(pax_col) if pax_col else "",
                    trainee=row.get(trainee_col) if trainee_col else "לא",
                    training=row.get(training_col) if training_col else "",
                    source_order=int(row_i),
                )

    # Case 3: raw grid fallback across all sheets
    if not flights:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        all_sheets = pd.read_excel(uploaded_file, sheet_name=None, header=None, dtype=object)
        for _, grid in all_sheets.items():
            for _, row in grid.iterrows():
                vals = list(row.values)
                for idx, val in enumerate(vals):
                    flight = _normalize_flight_cell(val)
                    if not flight:
                        continue

                    # Try to find time and destination near the flight cell.
                    # Hebrew exports are often RTL, so destination/time may be to the left.
                    near_vals = vals[max(0, idx - 10): min(len(vals), idx + 11)]
                    times = [_normalize_time_cell(v) for v in near_vals]
                    times = [t for t in times if t]
                    departure = times[0] if times else ""
                    boarding = times[1] if len(times) > 1 else ""

                    destination = _value_from_nearby_row(vals, idx, [-2, -1, 1, 2, -3, 3])
                    aircraft = _value_from_nearby_row(vals, idx, [-4, 4, -5, 5])
                    add_flight(
                        flight,
                        destination=destination,
                        departure=departure,
                        boarding=boarding,
                        aircraft=aircraft,
                        source_order=int(row_i) * 100 + int(idx),
                    )

    flights_df = pd.DataFrame(flights)

    wanted_cols = [
    "_source_order",
    "טיסה",
    "יעד",
    "המראה",
    "בורדינג",
    "גייט",
    "סוג מטוס",
    "רישוי",
    "נוסעים",
    "טרייני רצ",
    "סוג הכשרה",
]
    if flights_df.empty:
        return pd.DataFrame(columns=wanted_cols)

    flights_df["_flight_key"] = flights_df["טיסה"].apply(flight_key)
    flights_df = flights_df.drop_duplicates(subset=["_flight_key"], keep="first").drop(columns=["_flight_key"])
    flights_df = flights_df[flights_df["המראה"].astype(str).str.strip() != ""].copy()

    # Filter out cargo flights: 3-digit flight numbers starting with 8 (800–899)
    def _is_cargo(flight_str):
        digits = re.sub(r"[^0-9]", "", str(flight_str).strip())
        return len(digits) == 3 and digits.startswith("8")

    flights_df = flights_df[~flights_df["טיסה"].apply(_is_cargo)].copy()

    def _workday_sort_key(col):
        def _to_minutes(t):
            try:
                h, m = str(t).strip().split(":")
                return int(h) * 60 + int(m)
            except Exception:
                return 9999
        # Day starts at 03:00 — flights before 03:00 are night flights → pushed to end
        return col.apply(lambda t: (_to_minutes(t) - 180) % 1440)

    try:
        flights_df = flights_df.sort_values(
            by="המראה", key=_workday_sort_key
        ).reset_index(drop=True)
    except Exception:
        flights_df = safe_sort_by_time(flights_df, "המראה")

    for col in wanted_cols:
        if col not in flights_df.columns:
            flights_df[col] = ""

    return flights_df[wanted_cols]


def normalize_employees(df):
    df = df.copy()
    df.columns = df.columns.astype(str).str.strip()
    
    if "שם" not in df.columns and "עובד" in df.columns:
        df = df.rename(columns={"עובד": "שם"})
        raise ValueError("בקובץ העובדים חייבת להיות עמודה בשם: שם")

    aliases = {
        "מפקח tsa":        "מפקח TSA",
        "מפקח Tsa":        "מפקח TSA",
        "פיקוח tsa":       "מפקח TSA",
        "פיקוח TSA":       "מפקח TSA",
        "פיקוח Tsa":       "מפקח TSA",
        "שומר tsa":        "שומר TSA",
        "שומר Tsa":        "שומר TSA",
        "טרייני ר״צ":      "טרייני רצ",
        'טרייני ר"צ':      "טרייני רצ",
        "ראש צוות חונך":   "חונך רצים",
        "ראש צוות מסמיך":  "מסמיך רצים",
        "ראש צוות מסמיך ": "מסמיך רצים",
        "טרייני ר״צ ":     "טרייני רצ",
        # New employee-type columns
        'מנהל כר"צ':       "מנהל כר\"צ",
        "דייל טרייני":     "דייל בטרייני",
        "דייל בהכשרה":     "דייל בטרייני",
        "ילד עובד":        "ילדי עובדים",
        "ילד/ת עובד":      "ילדי עובדים",
        "פורש":            "פורשים",
        "פורשת":           "פורשים",
        "מתדרכ":           "מתדרכת",
        "פעיל":            "פעיל/לא פעיל",
    }

    for old, new in aliases.items():
        if old in df.columns:
            if new not in df.columns:
                df[new] = df[old]
            else:
                df[new] = df[new].apply(clean_text)
                df[old] = df[old].apply(clean_text)
                mask = df[new].str.strip().isin(["", "לא"]) & (df[old] != "")
                df.loc[mask, new] = df.loc[mask, old]

    if "שם" not in df.columns:
        possible_name_cols = ["שם עובד", "עובד", "שם מלא", "Employee", "Name"]

        found_name_col = next(
            (col for col in possible_name_cols if col in df.columns),
            None,
        )

        if found_name_col:
            df = df.rename(columns={found_name_col: "שם"})
        else:
            raise KeyError(
                f"לא נמצאה עמודת שם בקובץ העובדים. העמודות שנמצאו הן: {list(df.columns)}"
            )

    df["שם"] = df["שם"].apply(clean_text)
    df = df[df["שם"] != ""].copy()
    df["_name_key"] = df["שם"].apply(name_key)

    # Deduplicate employees whose names are the same words in different order
    # e.g. "TL#13" and "בן דוד נועה" → keep first occurrence only
    # Uses sorted-word-set key so any word permutation of the same name matches
    def _name_sorted_key(s):
        return "".join(sorted(name_key(w) for w in str(s).split() if w.strip()))

    _seen_sorted: dict = {}
    _drop_idx = []
    _YES_VALS = {"כן", "yes", "YES", "Yes", "1", "true", "True"}
    for _idx, _row in df.iterrows():
        _sk = _name_sorted_key(_row["שם"])
        if _sk in _seen_sorted:
            # Merge "כן" qualifications from this duplicate into the kept row,
            # so that if either row has a qualification, the merged row keeps it.
            _kept_idx = _seen_sorted[_sk]
            for _col in df.columns:
                if str(_row.get(_col, "")).strip() in _YES_VALS:
                    if str(df.at[_kept_idx, _col]).strip() not in _YES_VALS:
                        df.at[_kept_idx, _col] = "כן"
            _drop_idx.append(_idx)
        else:
            _seen_sorted[_sk] = _idx
    if _drop_idx:
        df = df.drop(index=_drop_idx).reset_index(drop=True)

    for col in ROLE_COLUMNS:
        if col not in df.columns:
            df[col] = "לא"
        df[col] = df[col].apply(normalize_yes_no)

    # New employee-type columns — normalize as yes/no
    _NEW_FLAG_COLS = ["ילדי עובדים", "פורשים", "דייל בטרייני", "מתדרכת", 'מנהל כר"צ']
    for col in _NEW_FLAG_COLS:
        if col not in df.columns:
            df[col] = "לא"
        df[col] = df[col].apply(normalize_yes_no)

    # פעיל/לא פעיל — normalize; treat "פעיל" as active (כן), "לא פעיל"/"לא" as inactive
    _inactive_col = "פעיל/לא פעיל"
    if _inactive_col not in df.columns:
        df[_inactive_col] = "כן"
    else:
        def _norm_active(v):
            s = str(v).strip().lower()
            # Explicitly inactive only when value clearly says "no" or "inactive"
            if s in {"לא", "לא פעיל", "no", "inactive", "0", "false"}:
                return "לא"
            # Everything else (empty, NaN, "כן", "פעיל", etc.) → active
            return "כן"
        df[_inactive_col] = df[_inactive_col].apply(_norm_active)

    for col in ["תחילת משמרת", "סוף משמרת", "חסימות", "זמינות"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].apply(clean_text)

    if "חולה" not in df.columns:
        df["חולה"] = False

    def to_bool_sick(v):
        if isinstance(v, bool): return v
        s = str(v).strip().lower()
        return s in {"true", "1", "כן", "yes"}
    df["חולה"] = df["חולה"].apply(to_bool_sick)

    if "זמינות" not in df.columns:
        df["זמינות"] = ""

    def clean_avail(v):
        import re as _re
        s = str(v).strip() if not pd.isna(v) else ""
        if not s or s.lower() in {"false", "true", "none", "nan", "0", "1"}:
            return ""
        if not _re.search(r'\d{1,2}:\d{2}', s):
            return ""
        return s
    df["זמינות"] = df["זמינות"].apply(clean_avail)

    return df


# =========================
# FIDS PARSING (pure — no streamlit dependency)
# =========================

from html.parser import HTMLParser as _FidsHTMLParser  # FIDS html tables


class FidsTableParser(_FidsHTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.current_row, self.current_cell, self.in_cell = (
            [],
            [],
            "",
            False,
        )

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self.in_cell = True
            self.current_cell = ""
        elif tag == "tr":
            self.current_row = []
        elif tag == "br" and self.in_cell:
            self.current_cell += " "

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.current_row.append(self.current_cell.strip())
            self.in_cell = False
        elif tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


def fids_parse_html_table(raw):
    p = FidsTableParser()
    p.feed(raw.decode("utf-8", errors="replace"))
    if not p.rows:
        return pd.DataFrame()
    headers = p.rows[0]
    data = [r + [""] * (len(headers) - len(r)) for r in p.rows[1:]]
    return pd.DataFrame(data, columns=headers)


def fids_flt_key(v):
    s = re.sub(r"\s+", "", str(v)).upper()
    s = re.sub(r"^[A-Z]+", "", s)  # הסר כל קידומת אותיות (LY / ELY / EL וכו׳)
    return s.lstrip("0") or "0"


def fids_find_src(columns, aliases):
    cols_lower = {str(c).lower(): c for c in columns}
    for alias in aliases:
        al = alias.lower()
        if al in cols_lower:
            return cols_lower[al]
        for col_l, col in cols_lower.items():
            if al in col_l:
                return col
    return None


FIDS_COL_MAP = {
    "גייט":    ["gate", "pit", "גייט"],
    "רישוי":   ["aircraft", "reg", "registration", "רישוי"],
    "נוסעים":  ["pax", "passengers", "נוסעים"],
    "ETD":     ["actual departure", "etd", "etdl", "etdz", "new departure"],
    "שלוחה":   ["terminal", "שלוחה"],
}


def parse_fids_combined(fids_file_objects):
    """Parse one/two FIDS files (html/csv/xlsx) into a single filtered DataFrame.
    Applies the freight filter and the per-file operational-day time windows.
    Stores the result in session as _fids_combined_raw. Returns None when empty."""
    all_rows = []

    for _file_idx, fobj in enumerate(fids_file_objects):
        if fobj is None:
            continue
        try:
            fobj.seek(0)
            raw = fobj.read()
            fname = (getattr(fobj, "name", "") or "").lower()
            if fname.endswith((".htm", ".html")):
                fids_df = fids_parse_html_table(raw)
            elif fname.endswith(".csv"):
                fids_df = pd.read_csv(io.BytesIO(raw), dtype=str)
            else:
                try:
                    fids_df = pd.read_excel(io.BytesIO(raw), dtype=str)
                except Exception:
                    fids_df = pd.read_csv(io.BytesIO(raw), dtype=str)

            if fids_df.empty:
                continue
            fids_df = fids_df.astype(str)
            fc = fids_find_src(
                fids_df.columns, ["flight number", "flight", "flightno", "flt", "טיסה"]
            )
            if fc:
                fids_df["_fk"] = fids_df[fc].apply(fids_flt_key)
                # Filter out 3-digit freight flight numbers (e.g. 841, 843, 859).
                # Two-digit keys like "81" (ELY081 BKK) and "83" (ELY083 BKK) are
                # legitimate passenger flights and must NOT be excluded.
                fids_df = fids_df[~(
                    (fids_df["_fk"].str.len() >= 3) & fids_df["_fk"].str.startswith("8")
                )]

                # File 0 (today's FIDS): keep only overnight flights (before 02:00).
                # File 1 (tomorrow's FIDS): keep only operational-day flights (03:00+).
                #   Flights before 03:00 in the "tomorrow" file belong to the
                #   PREVIOUS operational day (e.g. LY003 00:05 is June-20 operational).
                def _time_hm(v):
                    _m = re.search(r"(\d{1,2}):(\d{2})", str(v))
                    if not _m:
                        return None
                    return (int(_m.group(1)), int(_m.group(2)))

                _dep_src_f = fids_find_src(
                    fids_df.columns,
                    ["std", "scheduled", "scheduleddeparture", "departure",
                     "המראה", "etd", "time"],
                )
                if _file_idx == 0 and _dep_src_f:
                    # Today's FIDS: keep flights from 03:00 onward.
                    # Overnight flights (00:00-02:59) in today's file belong to the
                    # PREVIOUS operational day and are excluded.
                    fids_df = fids_df[fids_df[_dep_src_f].apply(
                        lambda v: (_time_hm(v) or (0, 0)) >= (3, 0)
                    )]
                elif _file_idx == 1 and _dep_src_f:
                    # Tomorrow's FIDS: keep ONLY overnight flights (< 03:00).
                    # These are the tail-end of today's operational day (e.g. ELY005 01:10).
                    # Daytime flights (04:00+) in tomorrow's FIDS belong to the NEXT
                    # operational day and must be excluded to avoid duplicate enrichment.
                    fids_df = fids_df[fids_df[_dep_src_f].apply(
                        lambda v: (_time_hm(v) or (99, 0)) < (3, 0)
                    )]

                fids_df["_fids_src"] = _file_idx
                if not fids_df.empty:
                    all_rows.append(fids_df)
        except Exception:
            pass

    if not all_rows:
        return None

    combined = pd.concat(all_rows, ignore_index=True)
    return combined


def flights_from_fids(combined):
    """Build the ENTIRE flight board from the FIDS files (user rule 2026-07-05:
    the board is FIDS-only; the daily Excel is used for workers/shifts only).
    Returns a flights DataFrame in the standard board schema, or None when the
    FIDS files yielded no rows."""
    if combined is None or combined.empty:
        return None

    _fc   = fids_find_src(combined.columns, ["flight number", "flight", "flightno", "flt", "טיסה"])
    _dest = fids_find_src(combined.columns, ["arrivalairport", "arrival", "destination", "dest", "יעד"])
    _dep  = fids_find_src(combined.columns, ["scheduleddeparture", "std", "scheduled", "departure", "המראה", "time"])
    _adep = fids_find_src(combined.columns, ["actualdeparture", "actual departure", "etdl", "etdz"])
    _gate = fids_find_src(combined.columns, ["gate", "pit", "גייט"])
    _reg  = fids_find_src(combined.columns, ["aircraft", "reg", "registration", "רישוי"])
    _pax  = fids_find_src(combined.columns, ["pax", "passengers", "נוסעים"])
    _term = fids_find_src(combined.columns, ["terminal", "שלוחה"])
    if not _fc:
        return None

    def _cell(fr, col):
        if not col:
            return ""
        v = re.sub(r"\s+", " ", str(fr.get(col, ""))).strip()
        return "" if v.lower() in {"nan", "none", "&nbsp"} else v

    def _sched_dep_time(fr):
        # The flight's ORIGINAL scheduled departure — always shown as-is in
        # "המראה"; never overwritten by ETD (user rule 2026-07-25: both
        # times must stay visible/independent, ETD gets its own column).
        _m = re.search(r"\d{1,2}:\d{2}", _cell(fr, _dep)) if _dep else None
        return _m.group() if _m else ""

    def _etd_time(fr):
        # שעה משוערת עתידית (ETD עם E בסוף) — מוצגת בעמודת ETD הנפרדת,
        # ומשמשת את בניית השיבוץ כזמן ההמראה בפועל, אך אינה מחליפה את
        # "המראה" המוצגת (ר' _effective_flights_for_schedule ב-streamlit_app).
        # ה-E ליד השעה הוא הסימן היחיד להבחנה בין טיסה שכבר יצאה (ללא E) לבין
        # טיסה שעדיין לא יצאה ועודכן לה זמן משוער (עם E) — עמודות דלק/CTOT
        # אינן נבדקות, הן לא חייבות להיות ריקות (הנחיית משתמש 2026-07-25).
        if _adep:
            _a = _cell(fr, _adep)
            if re.search(r"\d{1,2}:\d{2}\s*E\s*$", _a, re.IGNORECASE):
                _m = re.search(r"\d{1,2}:\d{2}", _a)
                if _m:
                    return _m.group()
        return ""

    rows, seen = [], set()
    for _, fr in combined.iterrows():
        raw_num = _cell(fr, _fc)
        fk = fids_flt_key(raw_num)
        if not fk or fk == "0" or fk in seen:
            continue
        dep = _sched_dep_time(fr)
        if not dep:
            continue
        seen.add(fk)
        ly_num = re.sub(r"^E(LY\d+)$", r"\1", raw_num.replace(" ", "").upper())
        if not ly_num.startswith("LY"):
            ly_num = re.sub(r"^[A-Z]*(\d+)$", r"LY\1", raw_num.replace(" ", "").upper())
        rows.append({
            "_source_order": len(rows),
            "טיסה": ly_num,
            "יעד": _cell(fr, _dest).upper(),
            "המראה": dep,
            "בורדינג": "",
            "ETD": _etd_time(fr),
            "גייט": _cell(fr, _gate),
            "סוג מטוס": "",
            "רישוי": _cell(fr, _reg),
            "נוסעים": _cell(fr, _pax),
            "שלוחה": _cell(fr, _term),
            "טרייני רצ": "לא",
            "סוג הכשרה": "",
        })
    if not rows:
        return None
    board = pd.DataFrame(rows)
    # מיון לפי המראה עם ציר 02:00 — טיסות הלילה (00:xx-01:xx) בסוף היום התפעולי
    def _dep_key(v):
        m = re.search(r"(\d{1,2}):(\d{2})", str(v))
        if not m:
            return 9999
        return (int(m.group(1)) * 60 + int(m.group(2)) - 120) % 1440
    return board.sort_values(by="המראה", key=lambda c: c.apply(_dep_key)).reset_index(drop=True)


