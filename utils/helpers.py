import re
import html
from datetime import datetime, timedelta
from functools import lru_cache

import pandas as pd

from utils.constants import (
    EARLY_MORNING_START_MAX, EARLY_MORNING_END_MIN, EARLY_MORNING_END_MAX,
    NIGHT_START_MIN, NIGHT_END_MIN, NIGHT_END_MAX, LATE_SHIFT_END_MAX,
    CANCELLED_FLIGHTS,
)


def is_cancelled_flight(flight_num):
    """True when the flight is cancelled until further notice (e.g. LY611).
    Matches by the numeric part only: 'LY 611', 'LY611', '611' all match."""
    digits = re.sub(r"\D", "", str(flight_num))
    return digits.lstrip("0") in CANCELLED_FLIGHTS


# =========================
# BASIC HELPERS
# =========================

_EMPTY_STRS = {"nan", "none", "nat", ""}

def clean_text(value) -> str:
    # Fast path for the most common types — avoids pd.isna() overhead
    if value is None:
        return ""
    if type(value) is str:
        t = value.strip()
        return "" if t.lower() in _EMPTY_STRS else t
    if type(value) is float:
        if value != value:   # NaN
            return ""
        t = str(value).strip()
        return "" if t.lower() in _EMPTY_STRS else t
    # Fallback for numpy/pandas special types
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    t = str(value).strip()
    return "" if t.lower() in _EMPTY_STRS else t


def safe_html(value) -> str:
    return html.escape(str(value))


def normalize_yes_no(value) -> str:
    text = clean_text(value).upper()
    return "כן" if text in {"Y", "YES", "כן", "TRUE", "1", "V", "✓", "✔"} else "לא"


def normalize_time_text(value: str) -> str:
    value = clean_text(value)
    match = re.search(r"(\d{1,2}):(\d{2})(?::\d{2})?", value)
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def extract_shift_range_from_text(value):
    text = clean_text(value)
    text = (
        text.replace("–", "-")
            .replace("—", "-")
            .replace("−", "-")
            .replace("\u200f", "")
            .replace("\u200e", "")
            .replace("\xa0", " ")
    )
    times = re.findall(r"\d{1,2}:\d{2}(?::\d{2})?", text)
    if len(times) < 2:
        return "", ""
    start = normalize_time_text(times[0])
    end = normalize_time_text(times[1])
    return (start, end) if start and end else ("", "")


def clean_roster_name(value):
    text = clean_text(value)
    if not text:
        return ""

    if extract_shift_range_from_text(text)[0]:
        return ""

    text = re.sub(r"טרייני\s*[-–]\s*", "", text)
    text = re.sub(r"\bטרייני\s*ר[״\"]?צ\b", "", text)
    text = text.replace("טרייני ר״צ", "").replace('טרייני ר"צ', "").replace("טרייני רצ", "")
    text = re.sub(r"מש[׳']?\s*.*$", "", text)
    text = re.sub(r"עד\s+\d{1,2}:\d{2}.*$", "", text)

    bad_words = ["סידור", "יומי", "שלישי", "ד.", "שלנ", "שלן", "רצ", "ר״צ", "בידוק", "חוליה", "דיילים", "דלפק", "סיירת", "ש״ש", "שש"]
    stripped = text.strip()
    if any(word in stripped for word in bad_words) and len(stripped.split()) <= 3:
        return ""

    stripped = re.sub(r"[^א-תA-Za-z\s'\-]", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    if len(stripped.split()) < 2:
        return ""

    return stripped


# =========================
# TIME UTILITIES
# =========================

def name_key(value) -> str:
    """Normalize name: no spaces, lowercase, double-yod = single-yod."""
    text = re.sub(r"\s+", "", clean_text(value)).lower()
    text = text.replace("יי", "י")
    return text


def name_key_reversed(value) -> str:
    """Return name_key of the reversed word order."""
    words = clean_text(value).split()
    return name_key(" ".join(reversed(words))) if len(words) >= 2 else name_key(value)


def flight_key(value) -> str:
    return re.sub(r"\s+", "", clean_text(value)).upper()


def parse_times(value):
    text = clean_text(value)
    match = re.search(r"(\d{2}:\d{2})\s*\((\d{2}:\d{2})\)", text)
    if match:
        return match.group(1), match.group(2)

    match = re.search(r"(\d{2}:\d{2})", text)
    if match:
        return match.group(1), ""

    return "", ""


@lru_cache(maxsize=4096)
def is_time_text(value):
    return bool(re.fullmatch(r"\d{2}:\d{2}", clean_text(value)))


@lru_cache(maxsize=1024)
def _parse_hhmm(s: str):
    return datetime.strptime(s, "%H:%M")

def to_datetime_time(value):
    return _parse_hhmm(clean_text(value))


@lru_cache(maxsize=512)
@lru_cache(maxsize=4096)
def time_to_minutes(value):
    h, m = clean_text(value).split(":")
    return int(h) * 60 + int(m)


def minutes_between(start, end):
    return int((end - start).total_seconds() / 60)


def short_flight_number(value):
    text = clean_text(value).upper().replace("LY", "").replace(" ", "")
    return text if text else clean_text(value)


def safe_sort_by_time(df, col):
    df = df.copy()
    df["_sort_time"] = pd.to_datetime(df[col], format="%H:%M", errors="coerce")
    df = df.sort_values("_sort_time").drop(columns=["_sort_time"])
    return df


def find_column(df, options):
    normalized = {str(c).strip(): c for c in df.columns}
    for opt in options:
        if opt in normalized:
            return normalized[opt]
    return None


# =========================
# ROLE HELPERS
# =========================

def normalize_role_label(role):
    role = clean_text(role)
    if role.startswith("ראש צוות"):
        return "ראש צוות"
    if role.startswith("דייל"):
        return "דיילת"
    if role.startswith("מתאם"):
        return "מתאם תורים"
    if role.startswith("מפקח"):
        return "מפקח TSA"
    if role.startswith("שומר"):
        return "שומר TSA"
    if role.startswith("טרייני"):
        return "טרייני ר״צ"
    return role


# A team leader is ALWAYS written ר"צ — never "ראש צוות"/"ראשת צוות" (user
# rule 2026-08-06: "ראש צוות זה לא ראשת צוות, יש תמיד להשתמש במונח ר\"צ").
# The abbreviation is gender-neutral, which is exactly why it is the one form.
TEAM_LEAD_LABEL = 'ר"צ'

_ROLE_BY_GENDER = {
    # canonical role -> (male, female)
    "ראש צוות":   (TEAM_LEAD_LABEL, TEAM_LEAD_LABEL),
    "דיילת":      ("דייל",        "דיילת"),
    "מתאם תורים": ("מתאם תורים",  "מתאמת תורים"),
    "מפקח TSA":   ("מפקח TSA",    "מפקחת TSA"),
    "שומר TSA":   ("שומר TSA",    "שומרת TSA"),
    # "טרייני" has no feminine form — it stays טרייני for everyone, exactly
    # like ר"צ (user rule 2026-08-06: "אין כזה דבר טריינית").
    "טרייני ר״צ": ('טרייני ר"צ',  'טרייני ר"צ'),
}

MALE_VALUES = {"זכר", "M", "MALE", "ז"}


def role_label_for(role, is_male):
    """The role title written for a worker of this gender.

    `normalize_role_label` returns the FEMININE "דיילת" as its canonical form,
    so any text built straight from it reads as feminine for everyone — which
    is how male attendants ended up with "המשך ל־355 דיילת" (84 occurrences on
    real 30.07 data). Always run the label through here before showing it to a
    user alongside a specific worker."""
    base = normalize_role_label(role)
    pair = _ROLE_BY_GENDER.get(base)
    if not pair:
        return base
    return pair[0] if is_male else pair[1]


def gender_role_label(role, employees_df, worker_name):
    """Return a gender-aware role title based on the employee's gender column."""
    base = normalize_role_label(role)
    if not worker_name or "❌" in worker_name:
        return base

    emp = employees_df[employees_df["שם"] == worker_name]
    if emp.empty:
        return base

    gender_col = next((c for c in emp.columns if clean_text(c) in {"מין", "gender", "זכר/נקבה", "מגדר"}), None)
    if not gender_col:
        return base

    gender = clean_text(emp.iloc[0].get(gender_col, "")).upper()
    return role_label_for(base, gender in MALE_VALUES)


def role_area(role):
    """Operational area for each task."""
    role = normalize_role_label(role)
    if role in {"ראש צוות", "דיילת", "מתאם תורים", "מפקח TSA", "שומר TSA", "טרייני רצ"}:
        return "שערי יציאה"
    return "דלפקי צ׳ק אין"


def default_area_for_employee(emp_row):
    if str(emp_row.get("ראש צוות", "")).strip() == "כן":
        return "שערי יציאה"
    return "דלפקי צ׳ק אין"


def employee_area_history(assignments, emp_name):
    areas = set()
    for task in assignments:
        if task.get("עובד") != emp_name:
            continue
        if "❌" in str(task.get("עובד", "")):
            continue
        areas.add(role_area(task.get("תפקיד", "")))
    return areas


def area_switch_penalty(assignments, emp, role):
    emp_name = emp["שם"]
    target_area = role_area(role)
    history = employee_area_history(assignments, emp_name)

    if not history:
        default_area = default_area_for_employee(emp)
        return 0 if default_area == target_area else 2

    if target_area in history:
        return 0

    return 5


# =========================
# SHIFT HELPERS
# =========================

def classify_shift(emp):
    ss = clean_text(emp.get("תחילת משמרת", ""))
    if not is_time_text(ss):
        return "unknown"

    s = time_to_minutes(ss)

    if 0 <= s <= 359:
        return "early_morning"   # 00:00-05:59 בוקר

    if 360 <= s <= 659:
        return "day"             # 06:00-10:59 יום

    if 660 <= s <= 944:
        return "noon"            # 11:00-15:44 צהריים

    if 945 <= s <= 1079:
        return "after"           # 15:45-17:59 אפטר

    if 1080 <= s <= 1259:
        return "evening"         # 18:00-20:59 ערב

    return "night"               # 21:00-23:59 לילה


def shift_length(emp):
    shift_start = clean_text(emp.get("תחילת משמרת", ""))
    shift_end = clean_text(emp.get("סוף משמרת", ""))

    if not is_time_text(shift_start) or not is_time_text(shift_end):
        return 0

    s = time_to_minutes(shift_start)
    e = time_to_minutes(shift_end)

    if e < s:
        e += 24 * 60

    return e - s


def employee_shift_text(employees_df, emp_name):
    _name_stripped = str(emp_name).strip()
    emp = employees_df[employees_df["שם"].astype(str).str.strip() == _name_stripped]
    if emp.empty:
        # fallback: case-insensitive partial match
        emp = employees_df[
            employees_df["שם"].astype(str).str.strip().str.lower() == _name_stripped.lower()
        ]
    if emp.empty:
        return ""
    row = emp.iloc[0]
    s = clean_text(row.get("תחילת משמרת", ""))
    e = clean_text(row.get("סוף משמרת", ""))
    if s and e:
        return f"{s}-{e}"
    return ""


def break_label_for_employee(emp_row):
    length = shift_length(emp_row)

    # Shifts ≥ 10 h get both a full break AND a separate refresh (רענון).
    if length >= 10 * 60:
        return "הפסקה ורענון"

    if length >= 6 * 60:
        return "הפסקה"

    if length > 0:
        return "רענון"

    return ""


def required_break(emp):
    """
    מחזיר רק את משך ההפסקה העיקרית.
    רענון הוא אירוע נפרד ולא מחובר להפסקה.
    """
    length = shift_length(emp)

    if length >= 6 * 60:
        return 45

    return 0


def required_refresh(emp):
    """
    מחזיר משך רענון.
    עד 05:59 שעות: רענון באמצע המשמרת.
    מעל 10 שעות: רענון נוסף ונפרד לקראת סוף המשמרת.
    """
    length = shift_length(emp)

    if 0 < length < 6 * 60:
        return 20

    # Must match break_label_for_employee's own "הפסקה ורענון" boundary
    # (>= 10h, not > 10h) — a shift of EXACTLY 600 min was entitled to a
    # refresh per break_label_for_employee but this returned 0 (no duration),
    # producing a nonsensical "entitled to a refresh that takes 0 minutes"
    # and letting a downstream "does the worker have room for it" check pass
    # trivially even with zero real time left (found via real data 2026-07-14:
    # agent#46, exactly 21:00-07:00 = 600 min, last flight ending AT shift
    # end, still showed a "רענון וחזרה" chip).
    if length >= 10 * 60:
        return 20

    return 0


def shift_end_datetime(emp_row):
    end_text = clean_text(emp_row.get("סוף משמרת", ""))
    if not is_time_text(end_text):
        return None
    return to_datetime_time(end_text)


def minutes_until_shift_end(emp_row, task_end_dt):
    end_dt = shift_end_datetime(emp_row)
    if end_dt is None:
        return None

    end_min = end_dt.hour * 60 + end_dt.minute
    task_min = task_end_dt.hour * 60 + task_end_dt.minute

    if end_min < task_min:
        end_min += 24 * 60

    return end_min - task_min


def return_text_by_shift(emp_row, task_end_dt):
    remaining = minutes_until_shift_end(emp_row, task_end_dt)
    if remaining is None:
        return "חזרה"
    if remaining <= 30:
        return "חזרה הביתה"
    # A reinforcement worker (see data_loader.apply_shift_manager_home_labels)
    # has a real home desk from the "מנהלי משמרות" sheet — name it instead
    # of the generic counters phrase (user rule 2026-09-13, real 04.09.2026
    # data: נטע/רוני/שני, all "X-תגבור..." reinforcements, belong at "77").
    _home = clean_text(emp_row.get("בית תגבור", "")) if emp_row is not None else ""
    if _home:
        return f"חזרה ל־{_home}"
    return "חזרה לדלפקים"


def preferred_break_window_by_shift(emp):
    """
    חלון הפסקה מומלץ לפי סוג המשמרת.
    לא דדליין קשיח, אלא המלצה תפעולית.
    """
    ss = clean_text(emp.get("תחילת משמרת", ""))
    se = clean_text(emp.get("סוף משמרת", ""))

    if not is_time_text(ss) or not is_time_text(se):
        return "", ""

    s = time_to_minutes(ss)
    e = time_to_minutes(se)

    # משמרת לפנות-בוקר מאוד מוקדמת: 02:00-08:30 / 02:00-09:30
    # ההפסקה עדיפה לפני הטיסות (03:00-04:30) או לאחר הטיסה הראשונה לכל המאוחר
    if 1 * 60 + 30 <= s <= 2 * 60 + 30 and 8 * 60 <= e <= 10 * 60:
        return "03:00", "05:00"

    # משמרת 02:00 ארוכה (02:00-11:00 וכד') — כללי הפסקה כמו משמרת 03:30-11:00
    # (הנחיית משתמש 2026-07-06: הפסקה אחת בין הטיסות אחרי הפיק, בלי הפסקת
    # קדם-טיסות של משמרות ה-02:00 הקצרות)
    if 1 * 60 + 30 <= s <= 2 * 60 + 30 and e > 10 * 60:
        return "06:30", "08:00"

    # משמרת בוקר מוקדמת, לדוגמה 03:30-11:00 / 03:30-12:30
    # ההפסקה עדיפה אחרי פיק הבוקר (06:30+)
    if 3 * 60 <= s <= 4 * 60 and 10 * 60 <= e <= 13 * 60:
        return "06:30", "08:00"

    # משמרת יום רגילה, לדוגמה 07:00-16:00 / 08:00-17:00 —
    # ללא חלון מומלץ כאן ההפסקה נבחרה לפי "הפער האמצעי הגדול ביותר", שנוטה
    # ליפול מאוחר מדי (אחרי הפיק, קרוב לשעות אחה"צ) — נמצא בנתונים אמיתיים:
    # agent#56, agent#34 ועוד כמה עובדי 08:00-17:00 קיבלו הפסקה רק סביב
    # 13:00-14:00, כמעט 6 שעות לתוך משמרת של 9 שעות. (הנחיית משתמש 2026-07-14:
    # הפסקה יזומה, לא לפני-טיסה, בין 10:00-12:00 — agent#53, agent#51, agent#44)
    if 6 * 60 + 30 <= s < 9 * 60 and 15 * 60 <= e <= 17 * 60 + 30:
        return "10:00", "12:00"

    # משמרת יום רגילה מאוחרת יותר, לדוגמה 09:00-18:00 / 09:30-17:30 (הנחיית
    # משתמש 2026-07-14: הפסקה יזומה בין 11:30-13:30, לא לפני-טיסה — agent#52,
    # agent#57, סימן agent#10, trainee-agent#9, agent#37, agent#12, agent#6,
    # agent#48)
    if 9 * 60 <= s <= 9 * 60 + 30 and 17 * 60 <= e <= 18 * 60 + 30:
        return "11:30", "13:30"

    # משמרת אמצע-יום קצרה, לדוגמה 09:45-15:30 / 10:00-16:00 — קצרה מדי בשביל
    # חלון הצהריים למטה (שמניח סיום 17:00+). ללא חלון משלה נפלה גם היא ל"פער
    # הכי גדול", שנוחת כמעט תמיד על ההפסקה האחרונה לפני סוף המשמרת, יחד עם
    # החזרה (נמצא בנתונים אמיתיים: TL#23, עדן גריצי'י ועוד עובדי 10:00-16:00
    # קיבלו "הפסקה וחזרה" רק אחרי הטיסה האחרונה, ~30 דק' לפני סוף המשמרת בלבד).
    if 9 * 60 + 45 <= s <= 11 * 60 and 15 * 60 <= e <= 17 * 60:
        return "10:30", "11:30"

    # משמרת צהריים, לדוגמה 10:00-18:00 / 11:00-19:00
    if 10 * 60 <= s <= 12 * 60 + 30 and 17 * 60 <= e <= 20 * 60:
        return "13:00", "15:00"

    # משמרת יום/ערב, לדוגמה 11:30-21:00 / 12:00-21:30
    if 10 * 60 <= s <= 12 * 60 + 30 and 20 * 60 <= e <= 22 * 60:
        return "16:00", "17:00"

    # משמרת ערב־לילה, לדוגמה 14:00-01:30 / 15:45-01:30
    if e < s and 14 * 60 <= s <= 17 * 60 and e <= 2 * 60:
        return "18:00", "19:00"

    # משמרת צהריים־לילה ארוכה, לדוגמה 12:30-00:30 (משמרת 12 שעות עם טיסות
    # ראשונות רק בערב המאוחר) — ללא חלון מומלץ נפלה ההפסקה על הפער היחיד
    # שנמצא (בין שתי הטיסות הערביות, ~22:00), כלומר כמעט בסוף משמרת של 12
    # שעות. הנחיית משתמש 2026-07-14: משמרת ארוכה כזו צריכה הפסקה מוקדמת
    # יותר, לא דחוקה בין הטיסות בשעות הלילה — agent#24, agent#34.
    if e < s and 12 * 60 <= s <= 13 * 60 and e <= 2 * 60:
        return "16:00", "17:30"

    return "", ""


def break_deadline_before_flight(emp, task_start_minutes):
    """
    דדליין להפסקה לפני טיסה רק במקרים שבהם באמת אין זמן לחלון הפסקה רגיל.

    למשל:
    - עובד 03:30-11:00 שהטיסה הראשונה שלו ב־08:00:
      לא צריך דדליין לפני הטיסה, כי עדיף 06:00-07:00 ואז חזרה לדלפקים.

    מחזיר HH:MM או None.
    """
    if classify_shift(emp) != "early_morning":
        return None

    ss = clean_text(emp.get("תחילת משמרת", ""))
    se = clean_text(emp.get("סוף משמרת", ""))

    if not is_time_text(ss) or not is_time_text(se):
        return None

    shift_start_min = time_to_minutes(ss)
    _shift_end_min = time_to_minutes(se)

    # משמרת 02:00 ארוכה (סיום אחרי 10:00) מתנהגת כמו 03:30-11:00 — ההפסקה
    # מתוכננת בין הטיסות אחרי הפיק, ולכן חישוב ה"דדליין לפני טיסה" נעשה
    # כאילו המשמרת התחילה 03:30 (מדכא דדליין-קדם מיותר על טיסות הבוקר).
    if 1 * 60 + 30 <= shift_start_min <= 2 * 60 + 30 and _shift_end_min > 10 * 60:
        shift_start_min = 3 * 60 + 30

    # חלון הפסקה טבעי לבוקר מוקדם
    preferred_start, preferred_end = preferred_break_window_by_shift(emp)

    if preferred_end:
        preferred_end_min = time_to_minutes(preferred_end)

        # אם אפשר לסיים הפסקה טבעית + 15 דקות הליכה לפני הכניסה לשער,
        # אין צורך להציג את העובד בדדליין לפני טיסה.
        if preferred_end_min + 15 <= task_start_minutes:
            return None

    # אם הטיסה ממש מוקדמת ואין זמן לחלון הטבעי, כן נחשב דדליין.
    deadline_min = task_start_minutes - 15 - 45

    # לא מציעים הפסקה מיד בתחילת משמרת, אלא אם באמת אין שום ברירה.
    min_reasonable_break_start = shift_start_min + 90
    if deadline_min < min_reasonable_break_start:
        return None

    h = (deadline_min // 60) % 24
    m = deadline_min % 60
    return f"{h:02d}:{m:02d}"

def gap_minutes_to_next(timed_df, current_idx, emp):
    row = timed_df.loc[current_idx]
    future = timed_df[
        (timed_df["עובד"].astype(str) == emp) &
        (timed_df["_start_dt"] > row["_start_dt"])
    ].sort_values("_start_dt")

    if future.empty:
        return None

    current_end = to_datetime_time(row["סיום"])
    next_start = to_datetime_time(future.iloc[0]["התחלה"])
    gap = minutes_between(current_end, next_start)

    if gap < 0:
        gap += 24 * 60

    return gap


def next_task_plain_text(timed_df, current_idx, emp):
    row = timed_df.loc[current_idx]
    future = timed_df[
        (timed_df["עובד"].astype(str) == emp) &
        (timed_df["_start_dt"] > row["_start_dt"])
    ].sort_values("_start_dt")

    if future.empty:
        return ""

    next_row = future.iloc[0]
    next_flight = short_flight_number(next_row["טיסה"])
    next_role = normalize_role_label(next_row["תפקיד"])
    return f"{next_flight} {next_role}"


def continuation_text_for_employee(timed_df, current_idx, emp, employees_df):
    next_plain = next_task_plain_text(timed_df, current_idx, emp)

    if next_plain:
        return next_plain

    row = timed_df.loc[current_idx]
    emp_row = employees_df[employees_df["שם"] == emp]
    if emp_row.empty:
        return "חזרה"

    task_end_dt = to_datetime_time(row["סיום"])
    return return_text_by_shift(emp_row.iloc[0], task_end_dt)

def build_break_recommendations(unassigned_df, break_log=None, now_dt=None):
    """
    בונה טבלת המלצות להפסקות לפי עובדים לא משובצים.

    כרגע גרסה בסיסית:
    - מזהה מי כרגע בהפסקה
    - מזהה מי כבר סיים הפסקה
    - מחשבת דחיפות לפי שעת תחילת משמרת
    - עדיין לא משלבת טיסה קרובה. נוסיף בשלב הבא.
    """
    import pandas as pd
    from datetime import datetime
    import re

    if break_log is None:
        break_log = {}

    if now_dt is None:
        now_dt = datetime.now()

    def clean_value(value):
        if value is None:
            return ""
        return str(value).strip()

    def time_to_minutes(value):
        value = clean_value(value)
        match = re.search(r"(\d{1,2})[:.](\d{2})", value)

        if not match:
            return None

        hour = int(match.group(1))
        minute = int(match.group(2))

        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None

        return hour * 60 + minute

    def get_shift_start_minutes(row):
        possible_cols = [
            "שעת התחלה",
            "תחילת משמרת",
            "משמרת התחלה",
            "משמרת",
        ]

        for col in possible_cols:
            if col in row.index:
                minutes = time_to_minutes(row.get(col, ""))
                if minutes is not None:
                    return minutes

        return None

    def minutes_since_shift_start(shift_start_minutes):
        if shift_start_minutes is None:
            return None

        now_minutes = now_dt.hour * 60 + now_dt.minute

        # משמרת שהתחילה אתמול וחוצה חצות
        if now_minutes < shift_start_minutes:
            now_minutes += 24 * 60

        return now_minutes - shift_start_minutes

    rows = []

    if unassigned_df is None or unassigned_df.empty:
        return pd.DataFrame(columns=[
            "שם",
            "משמרת",
            "תפקיד",
            "דחיפות",
            "ציון",
            "סיבה",
            "סטטוס הפסקה",
            "דקות מתחילת משמרת",
        ])

    for _, row in unassigned_df.iterrows():
        emp_name = clean_value(row.get("שם", row.get("עובד", "")))

        if not emp_name:
            continue

        shift_label = clean_value(row.get("משמרת", ""))
        role = clean_value(row.get("תפקיד", row.get("תפקיד עיקרי", "")))

        break_info = break_log.get(emp_name, {})

        if not break_info:
            for key, value in break_log.items():
                if clean_value(key) == emp_name:
                    break_info = value
                    break

        has_break_start = bool(break_info.get("ts") or break_info.get("start"))
        has_break_end = bool(break_info.get("end_ts") or break_info.get("end"))

        if has_break_start and not has_break_end:
            rows.append({
                "שם": emp_name,
                "משמרת": shift_label,
                "תפקיד": role,
                "דחיפות": "🟢 כרגע בהפסקה",
                "ציון": 100,
                "סיבה": "העובד/ת נמצא/ת כרגע בהפסקה",
                "סטטוס הפסקה": "בהפסקה",
                "דקות מתחילת משמרת": None,
            })
            continue

        if has_break_start and has_break_end:
            continue

        shift_start_minutes = get_shift_start_minutes(row)
        worked_minutes = minutes_since_shift_start(shift_start_minutes)

        if worked_minutes is None:
            urgency = "⚪ ללא שעת התחלה"
            score = 0
            reason = "לא זוהתה שעת תחילת משמרת"
        elif worked_minutes >= 360:
            urgency = "🔴 חייבים לצאת להפסקה"
            score = 90
            reason = "עברו 6 שעות או יותר מתחילת המשמרת"
        elif worked_minutes >= 300:
            urgency = "🟠 ב־60 דקות הקרובות"
            score = 60
            reason = "עברו 5 שעות או יותר מתחילת המשמרת"
        else:
            urgency = "⚪ עדיין לא דחוף"
            score = 10
            reason = "עדיין לא הגיע זמן הפסקה"

        rows.append({
            "שם": emp_name,
            "משמרת": shift_label,
            "תפקיד": role,
            "דחיפות": urgency,
            "ציון": score,
            "סיבה": reason,
            "סטטוס הפסקה": "לא יצא/ה",
            "דקות מתחילת משמרת": worked_minutes,
        })

    result_df = pd.DataFrame(rows)

    if result_df.empty:
        return result_df

    result_df = result_df.sort_values(
        by=["ציון", "שם"],
        ascending=[False, True],
        kind="stable",
    )

    return result_df