"""Persistent employee roster/certification database — replaces the manual
"קובץ עובדים / הסמכות" Excel upload-every-time workflow with a SQLite table
editable in-app by a super-admin (see render_employee_admin_panel below).

Scope: this holds only the STATIC roster — who exists and their permanent
certifications (ר"צ, TSA, מתאם תורים, etc.), exactly the columns
employees_clean.xlsx used to carry. The per-DAY shift/terminal/availability
columns are still computed fresh on every build by apply_shift_map_to_
employees() from the daily roster file, which is genuinely different every
day and stays a file upload — only the "who exists" data moves in here.

File-persisted (like schedule_archive.py / session_checkpoint.py) since
this needs to be shared across sessions and survive restarts, not live in
one browser's st.session_state.
"""

import os
import sqlite3
from io import BytesIO

import pandas as pd

_DB_PATH = "employees.sqlite3"

# Same columns employees_clean.xlsx has always carried, minus "פעיל/לא פעיל"
# — that concept is now the `active` column instead of a text field, so a
# deactivated employee disappears from the active roster entirely rather
# than needing every caller to remember to filter on a text column (user
# rule 2026-09-15: "השבתה = היעלמות מוחלטת מהרשימה הפעילה").
CERT_COLUMNS = [
    "דייל", "ראש צוות", "מפקח TSA", "שומר TSA", "מתאם תורים",
    "חונך רצים", "מסמיך רצים", "טרייני רצ", "מין", "ילד עובדים",
    "מתדרכת", 'מנהל כר"צ', "דייל בטרייני", "פורשים", "מחלקה מקורית",
    "מנהל משמרת",
    # The trainee's PERMANENT/official mentor at hiring — not the same
    # concept as the daily roster's own "חונך דייל" note, which names a
    # one-off stand-in mentor for a single day and always wins for that
    # day (data_loader.py only overwrites this column when that day's
    # roster note actually names someone; otherwise this permanent
    # default just carries straight through). User rule 2026-09-15: a
    # trainee gets one fixed mentor at hire time, occasionally covered by
    # someone else for a single day, or reassigned permanently later by
    # editing this field again.
    "חונך דייל",
]
ALL_COLUMNS = ["שם"] + CERT_COLUMNS

# A brand-new trainee attendant always starts with this exact set — user
# rule 2026-09-15, same default the single add-employee form pre-checks.
# Shared here (not just in employee_management.py's UI layer) since the
# bulk trainee import below needs the identical rule.
TRAINEE_DEFAULT_CERTS = {"דייל": "כן", "שומר TSA": "כן", "דייל בטרייני": "כן"}


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    _cols_sql = ", ".join(f"{_q(c)} TEXT" for c in CERT_COLUMNS)
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS employees ('
        f'"שם" TEXT PRIMARY KEY, {_cols_sql}, active INTEGER NOT NULL DEFAULT 1)'
    )
    return conn


def _q(col: str) -> str:
    """Quote a SQL identifier, doubling embedded double-quotes (e.g. the
    literal '"' inside 'מנהל כר"צ') per standard SQL identifier escaping."""
    return '"' + col.replace('"', '""') + '"'


def _cols_quoted(cols) -> str:
    return ", ".join(_q(c) for c in cols)


_ALL_COLS_SQL = _cols_quoted(ALL_COLUMNS)


def _clean_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v).strip()


def _row_to_tuple(row: dict):
    name = _clean_cell(row.get("שם", ""))
    return (name,) + tuple(_clean_cell(row.get(c, "")) for c in CERT_COLUMNS)


def is_seeded() -> bool:
    if not os.path.exists(_DB_PATH):
        return False
    conn = _connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
        return n > 0
    finally:
        conn.close()


def seed_from_excel_path(xlsx_path: str) -> int:
    """One-time migration: load an existing employees_clean.xlsx into the
    DB. No-op (returns 0) if the DB already has rows — never overwrites
    live edits with a stale migration source on a later app start.

    Runs unconditionally at the top of every app load until the DB is
    seeded, so it must never crash the whole app just because the source
    file happens to be mid-write or open elsewhere (e.g. someone has it
    open in Excel right now) — best-effort, silently retried on the next
    rerun instead (found via real use 2026-09-15: a locked
    employees_clean.xlsx took the entire app down with a raw
    PermissionError traceback)."""
    if is_seeded() or not os.path.exists(xlsx_path):
        return 0
    try:
        df = pd.read_excel(xlsx_path)
    except (OSError, ValueError):
        return 0
    return _bulk_upsert(df)


def _bulk_upsert(df: pd.DataFrame) -> int:
    if "שם" not in df.columns:
        return 0
    conn = _connect()
    try:
        n = 0
        for _, row in df.iterrows():
            name = str(row.get("שם", "")).strip()
            if not name:
                continue
            active = 1
            _active_raw = str(row.get("פעיל/לא פעיל", "")).strip()
            if _active_raw == "לא":
                active = 0
            values = _row_to_tuple(row)
            _placeholders = ", ".join(["?"] * len(ALL_COLUMNS))
            _updates = ", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in CERT_COLUMNS)
            conn.execute(
                f'INSERT INTO employees ({_ALL_COLS_SQL}, active) '
                f'VALUES ({_placeholders}, ?) '
                f'ON CONFLICT("שם") DO UPDATE SET {_updates}, active=excluded.active',
                values + (active,),
            )
            n += 1
        conn.commit()
        return n
    finally:
        conn.close()


def load_active_employees_df() -> pd.DataFrame:
    """Returns the active roster in the same shape as the old
    employees_clean.xlsx (including a "פעיל/לא פעיל" column set to "כן"
    for every row here, since callers downstream — normalize_employees()
    etc. — expect that column to exist)."""
    conn = _connect()
    try:
        df = pd.read_sql_query(
            f'SELECT {_ALL_COLS_SQL} FROM employees WHERE active=1',
            conn,
        )
    finally:
        conn.close()
    df["פעיל/לא פעיל"] = "כן"
    return df


def load_all_employees_df(include_inactive: bool = True) -> pd.DataFrame:
    conn = _connect()
    try:
        _where = "" if include_inactive else "WHERE active=1"
        df = pd.read_sql_query(
            f'SELECT {_ALL_COLS_SQL}, active FROM employees {_where} '
            f'ORDER BY "שם"',
            conn,
        )
    finally:
        conn.close()
    return df


def upsert_employee(row: dict) -> None:
    """Insert a new employee or overwrite an existing one's certifications
    (matched by exact name). Does not touch `active` unless "active" is
    explicitly present in `row`."""
    name = str(row.get("שם", "")).strip()
    if not name:
        raise ValueError("שם עובד/ת ריק")
    conn = _connect()
    try:
        existing = conn.execute(
            'SELECT active FROM employees WHERE "שם"=?', (name,)
        ).fetchone()
        active = row.get("active", existing[0] if existing else 1)
        values = _row_to_tuple(row)
        _placeholders = ", ".join(["?"] * len(ALL_COLUMNS))
        _updates = ", ".join(f"{_q(c)}=excluded.{_q(c)}" for c in CERT_COLUMNS)
        conn.execute(
            f'INSERT INTO employees ({_ALL_COLS_SQL}, active) '
            f'VALUES ({_placeholders}, ?) '
            f'ON CONFLICT("שם") DO UPDATE SET {_updates}, active=excluded.active',
            values + (int(bool(active)),),
        )
        conn.commit()
    finally:
        conn.close()


def rename_employee(old_name: str, new_name: str) -> str:
    """Change an employee's name in place (certifications and active flag
    are untouched). Returns "" on success or no-op, otherwise a Hebrew
    error message and nothing changes — the name is the table's primary
    key, so an empty or already-taken name must never reach the UPDATE.

    Trainees' permanent-mentor field ("חונך דייל") is free text holding the
    mentor's name, so it follows the rename; leaving it would silently
    orphan every trainee paired with the renamed person."""
    old_name, new_name = str(old_name).strip(), str(new_name).strip()
    if old_name == new_name:
        return ""
    if not new_name:
        return f'"{old_name}": השם החדש ריק — השם לא שונה.'
    conn = _connect()
    try:
        if conn.execute('SELECT 1 FROM employees WHERE "שם"=?', (old_name,)).fetchone() is None:
            return f'"{old_name}": העובד/ת לא נמצא/ה במאגר — השם לא שונה.'
        if conn.execute('SELECT 1 FROM employees WHERE "שם"=?', (new_name,)).fetchone() is not None:
            return (f'"{new_name}" כבר קיים/ת במאגר — השם של "{old_name}" לא שונה. '
                    'לשני אנשים עם אותו שם יש להוסיף סיומת, למשל "(ילדת עובדים)".')
        conn.execute('UPDATE employees SET "שם"=? WHERE "שם"=?', (new_name, old_name))
        conn.execute(
            'UPDATE employees SET "חונך דייל"=? WHERE "חונך דייל"=?', (new_name, old_name)
        )
        conn.commit()
        return ""
    finally:
        conn.close()


def set_active(name: str, active: bool) -> None:
    conn = _connect()
    try:
        conn.execute('UPDATE employees SET active=? WHERE "שם"=?', (int(active), str(name).strip()))
        conn.commit()
    finally:
        conn.close()


def delete_employee(name: str) -> None:
    """Permanent removal — not the normal path (use set_active(False)
    instead); only for cleaning up a genuine data-entry mistake."""
    conn = _connect()
    try:
        conn.execute('DELETE FROM employees WHERE "שם"=?', (str(name).strip(),))
        conn.commit()
    finally:
        conn.close()


def import_from_excel_bytes(data: bytes) -> int:
    """Bulk import/update from an uploaded Excel file — same shape as the
    original employees_clean.xlsx. Upserts by name; existing employees not
    present in the file are left untouched (this is a merge, not a
    replace, so a partial update sheet can't accidentally deactivate
    everyone else)."""
    df = pd.read_excel(BytesIO(data))
    return _bulk_upsert(df)


def employee_exists(name: str) -> bool:
    conn = _connect()
    try:
        return conn.execute(
            'SELECT 1 FROM employees WHERE "שם"=?', (str(name).strip(),)
        ).fetchone() is not None
    finally:
        conn.close()


def import_trainees_from_excel_bytes(data: bytes) -> tuple[int, int]:
    """Bulk-add a batch of brand-new trainee attendants from a MINIMAL
    sheet — just a name column and a mentor column (extra columns, e.g. a
    trainee phone number, are ignored). User rule 2026-09-15: this is the
    batch equivalent of the single add-employee form's own trainee
    defaults — a NEW name gets the standard TRAINEE_DEFAULT_CERTS plus the
    given mentor.

    Accepts the real-world "צימודים של\"ן" pairing sheet's own headers
    directly — "חניך" for the trainee's name, "חונך" for the mentor's —
    alongside "שם"/"חונך דייל". No word-order handling needed for either:
    the roster's own name convention is already surname-first (see
    user_management.py's _first_name_key comment), matching that sheet's
    "חניך" column exactly; the "חונך" column there is first-name-first,
    but the mentor field is only ever matched downstream by word-SET (see
    data_loader.py's mentor resolution), so its word order doesn't affect
    correctness — just don't assume it matches "חניך"'s convention if this
    field is ever displayed back in that order-sensitive form.

    A name that ALREADY EXISTS only has its mentor field updated — every
    other certification is left exactly as it was. Never re-apply the
    trainee defaults to an existing row: a trainee who's since become a
    real ר"צ (or anyone else already in the system) must not get silently
    reset to "just a trainee" because their name reappears on a later
    mentor-update sheet.

    Returns (new_count, mentor_updated_count)."""
    df, name_col, mentor_col = parse_trainee_excel_to_dataframe(data)
    if name_col is None:
        return (0, 0)
    return import_trainee_pairs_from_dataframe(df, name_col, mentor_col)


def parse_trainee_excel_to_dataframe(
    data: bytes,
) -> tuple["pd.DataFrame", str | None, str | None]:
    """Parse-only (no DB writes) — used to show the admin a review/confirm
    table before committing, same as the OCR-photo path (user rule
    2026-09-15: "אני רוצה להעלות קובץ רק לבדיקה... שהשמות לא יתווספו
    אוטומטית"). Returns (df, detected_name_col, detected_mentor_col) —
    name_col is None if neither "שם" nor "חניך" is present."""
    df = pd.read_excel(BytesIO(data))
    name_col = next((c for c in ("שם", "חניך") if c in df.columns), None)
    mentor_col = next((c for c in ("חונך דייל", "חונך", "חונך/ת") if c in df.columns), None)
    return df, name_col, mentor_col


def import_trainee_pairs_from_dataframe(
    df: "pd.DataFrame", name_col: str, mentor_col: str | None
) -> tuple[int, int]:
    """The shared core behind import_trainees_from_excel_bytes — also used
    by the OCR-photo import path (employee_ocr.py), which builds a
    DataFrame from an admin-reviewed/corrected extraction instead of a
    real Excel file. See import_trainees_from_excel_bytes for the full
    behavior contract (new vs. existing name handling)."""
    new_n, updated_n = 0, 0
    for _, row in df.iterrows():
        name = _clean_cell(row.get(name_col, ""))
        if not name:
            continue
        mentor = _clean_cell(row.get(mentor_col, "")) if mentor_col else ""
        if employee_exists(name):
            if mentor:
                conn = _connect()
                try:
                    conn.execute(
                        'UPDATE employees SET "חונך דייל"=? WHERE "שם"=?', (mentor, name)
                    )
                    conn.commit()
                finally:
                    conn.close()
                updated_n += 1
        else:
            upsert_employee({"שם": name, "חונך דייל": mentor, **TRAINEE_DEFAULT_CERTS})
            new_n += 1
    return (new_n, updated_n)


def export_to_excel_bytes(include_inactive: bool = True) -> bytes:
    df = load_all_employees_df(include_inactive=include_inactive)
    df["פעיל/לא פעיל"] = df["active"].map(lambda a: "כן" if a else "לא")
    df = df.drop(columns=["active"])
    buf = BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


def db_fingerprint() -> str:
    """Cheap change-signal for the app's upload-fingerprint cache — a
    count + max-rowid pair changes on any insert/update, avoiding a full
    content hash on every rerun."""
    if not os.path.exists(_DB_PATH):
        return "empty"
    conn = _connect()
    try:
        row = conn.execute("SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM employees").fetchone()
        return f"{row[0]}:{row[1]}:{os.path.getmtime(_DB_PATH)}"
    finally:
        conn.close()
