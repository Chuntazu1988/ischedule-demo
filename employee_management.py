"""Admin-only employee roster/certification management panel — replaces
the "קובץ עובדים / הסמכות" Excel upload-every-time workflow. Gated by
auth.is_super_admin(), same as render_user_management_panel in
user_management.py (a separate concept: THIS is the roster of real
employees and their certifications; that one is login accounts).
"""

import pandas as pd
import streamlit as st

import employee_db as edb
import employee_ocr
from app.styles import right_heading

_BOOL_COLS = [
    "דייל", "ראש צוות", "מפקח TSA", "שומר TSA", "מתאם תורים",
    "חונך רצים", "מסמיך רצים", "טרייני רצ", "ילד עובדים",
    "מתדרכת", 'מנהל כר"צ', "דייל בטרייני", "פורשים", "מנהל משמרת",
    "מתגבר שלוחה",
]
_TEXT_COLS = ["מין", "מחלקה מקורית"]
_MENTOR_COL = "חונך דייל"

# Fixed order + display label for the vertical certification checklist (user
# layout, 2026-09-22, amended same day) — shared by "add new" and "edit
# existing" below, in place of the old 3-per-row "כן"/"לא" dropdown grid.
# "דייל בטרייני" keeps its DB column name (scheduler.py reads it by that
# exact name) but is LABELED "טרייני" here, per the user's rename request.
# "מנהל משמרת" behaves exactly like the other certifications in the database
# (a plain "כן"/blank column — 28 of 395 real employees hold it) even though
# it used to sit in the free-text group here; moved so its own screen
# matches its real data shape. "מתגבר שלוחה" is a NEW permanent qualification
# (who MAY serve as the pier's stand-in #2 manager, e.g. TL#52 on the
# real 20.09 schedule) — distinct from the existing daily "תגבור שלוחה" roster
# flag ("is reinforcing TODAY"); no scheduling logic reads it yet, the user
# is marking who qualifies first. מתדרכת wasn't named in the user's list but
# is a real, actively-used certification ([[project-crew-chief-managers]]),
# kept editable, appended at the very end rather than silently dropped.
_CERT_ORDER = [
    ("דייל", "דייל"),
    ("שומר TSA", "שומר TSA"),
    ("מפקח TSA", "מפקח TSA"),
    ("מתאם תורים", "מתאם תורים"),
    ("ראש צוות", "ראש צוות"),
    ("טרייני רצ", 'טרייני ר"צ (קורס ר"צים)'),
    ("חונך רצים", 'חונך ר"צ'),
    ("מסמיך רצים", 'מסמיך ר"צ'),
    ("דייל בטרייני", "טרייני"),
    ("ילד עובדים", "ילד עובדים"),
    ("פורשים", "פורשים"),
    ("מנהל משמרת", "מנהל משמרת"),
    ('מנהל כר"צ', 'מנהל כר"צ'),
    ("מתגבר שלוחה", "מתגבר שלוחה"),
    ("מתדרכת", "מתדרכת"),
]
_DEPARTMENT_OPTIONS = ["(אין)", "טראפיק", "חוליה מיוחדת"]
# One-time on-disk value: existing rows said "חוליה", the requested dropdown
# option is the fuller "חוליה מיוחדת" — no code matches the exact string (only
# "is this non-empty", see app/scheduler.py's TSA-department checks), so
# normalizing display and storage together is safe.
_DEPARTMENT_LEGACY_MAP = {"חוליה": "חוליה מיוחדת"}


def _clean(v) -> str:
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


# Whole panel is wrapped in st.container(key=_PANEL_KEY) so every rule below
# is scoped to `.st-key-{_PANEL_KEY}` — Streamlit has no CSS scoping of its
# own (a <style> tag from st.markdown applies page-wide), and the rest of the
# app has its own hand-built RTL styling per screen that this must not touch.
_PANEL_KEY = "emp_admin_root"
_PANEL_RTL_CSS = f"""<style>
.st-key-{_PANEL_KEY} [data-testid="stMarkdownContainer"],
.st-key-{_PANEL_KEY} [data-testid="stCaptionContainer"] {{
    text-align: right !important;
    direction: rtl !important;
}}

/* Every widget sits in an stElementContainer that is, by default, only as
   wide as its content (confirmed via live DOM inspection) — a NARROW widget
   (a text input/select with an explicit width=, or a compact-by-design מין
   checkbox) then needs its CONTAINER pushed to the right side of the row;
   margin-left:auto is a PHYSICAL property, so it does this correctly
   regardless of direction/RTL settings (unlike text-align, which only
   affects the widget's own inner content, not its position in its row). A
   checkbox with width="stretch" (the cert checklist) already fills the row
   itself, so it doesn't need this — only the two narrow, deliberately
   compact מין checkboxes do, identified by their key. */
.st-key-{_PANEL_KEY} div[data-testid="stElementContainer"]:has(div[data-testid="stTextInput"]),
.st-key-{_PANEL_KEY} div[data-testid="stElementContainer"]:has(div[data-testid="stSelectbox"]) {{
    margin-left: auto !important;
    margin-right: 0 !important;
}}

.st-key-{_PANEL_KEY} div[data-testid="stCheckbox"],
.st-key-{_PANEL_KEY} div[data-testid="stTextInput"],
.st-key-{_PANEL_KEY} div[data-testid="stSelectbox"] {{
    direction: rtl !important;
}}
.st-key-{_PANEL_KEY} div[data-testid="stCheckbox"] label {{
    /* flex-end would mean the FAR end of the RTL reading order — i.e. the
       LEFT edge, the opposite of "right-aligned" (confirmed live: at a wide
       viewport every checkbox clustered near the left of a ~1450px row
       instead of hugging the right). flex-start is correct once
       direction:rtl is already set. */
    justify-content: flex-start !important;
}}
.st-key-{_PANEL_KEY} div[data-testid="stCheckbox"] label p {{ text-align: right !important; }}
.st-key-{_PANEL_KEY} div[data-testid="stTextInput"] label,
.st-key-{_PANEL_KEY} div[data-testid="stSelectbox"] label {{
    text-align: right !important;
    width: 100% !important;
}}
.st-key-{_PANEL_KEY} div[data-testid="stTextInput"] input {{ text-align: right !important; }}

/* Both cards: label / dashed action area of the SAME height / caption — the export
   button is drawn exactly like the import dropzone. */
.st-key-emp_export_box [data-testid="stDownloadButton"] button {{
    min-height: 74px !important;
    background: rgba(var(--ink-rgb),.035) !important;
    border: 2px dashed rgba(var(--acc-rgb),.5) !important;
    border-radius: 14px !important;
    font-weight: 700; font-size: 15px;
}}
.st-key-emp_export_box [data-testid="stDownloadButton"] button:hover {{
    background: rgba(var(--acc-rgb),.09) !important; border-color: var(--acc) !important; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] {{ min-height: 74px; }}
/* titles hug the right edge in BOTH cards; everything inside the dashed area and the
   captions are centred, so the two cards are mirror-symmetric */
.st-key-emp_import_box [data-testid="stWidgetLabel"] {{ width: 100% !important; justify-content: flex-start !important; direction: rtl; }}
.st-key-emp_import_box [data-testid="stWidgetLabel"] p, .st-key-{_PANEL_KEY} p.emp-card-label {{ text-align: right !important; width: 100%; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] {{ justify-content: center !important; align-items: center; gap: 18px; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzoneInstructions"] {{ text-align: center; flex: 0 0 auto !important; width: auto !important; margin: 0; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzoneInstructions"]::after {{ text-align: center; }}
.st-key-emp_export_box [data-testid="stDownloadButton"] {{ width: 100%; }}
.st-key-emp_export_box [data-testid="stDownloadButton"] button p {{ font-weight: 700 !important; font-size: 15px !important; }}
/* the import dropzone becomes ONE centred line that fills the dashed area — the same look
   as the export button (drag & drop still works; the whole area opens the file picker) */
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] {{ position: relative; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzoneInstructions"] {{ display: none !important; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] > span {{ position: absolute; inset: 0; display: block; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] button {{
    position: absolute; inset: 0; width: 100%; height: 100%; background: transparent !important; border: none !important;
    box-shadow: none !important; display: flex; align-items: center; justify-content: center; gap: 10px; cursor: pointer; }}
.st-key-emp_import_box [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {{
    content: "בחירת קובץ או גרירה לכאן"; font-size: 15px; font-weight: 700; }}
.st-key-emp_import_box [data-testid="stCaptionContainer"], .st-key-emp_export_box [data-testid="stCaptionContainer"],
.st-key-emp_import_box [data-testid="stCaptionContainer"] p, .st-key-emp_export_box [data-testid="stCaptionContainer"] p {{ text-align: center !important; }}
.st-key-emp_import_box [data-testid="stCaptionContainer"], .st-key-emp_export_box [data-testid="stCaptionContainer"] {{
    margin-top: 8px; font-size: 13px; }}
/* The import/export "cards" (emp_import_box / emp_export_box): same
   min-height and internal centering on both sides so an inherently short
   widget (the export button) and a taller one (the upload dropzone) still
   read as two cards of matching size, not a mismatched pair. */
.st-key-emp_import_box, .st-key-emp_export_box {{
    min-height: 160px;
    display: flex;
    flex-direction: column;
    justify-content: center;
}}
.st-key-{_PANEL_KEY} p.emp-card-label {{
    font-size: 0.875rem;
    color: rgba(var(--ink-rgb), 0.75);
    margin: 0 0 0.4rem 0;
    text-align: right;
    direction: rtl;
}}
</style>"""

_FIELD_WIDTH = 320   # px — proportional to the checklist, still fits a full name


# Moved to app/styles.py (2026-09-25) so every admin screen shares the same
# fix instead of each one rolling its own copy — kept as a local alias since
# this module's 6 call sites already use the short name.
_right_heading = right_heading


def _render_cert_form(prefix, defaults=None, mentor_default=""):
    """Vertical certification checklist in the fixed order above, right-
    aligned to match the rest of the (Hebrew) screen. Must be called OUTSIDE
    an st.form: the mentor field's visibility has to react live to the
    "טרייני" checkbox (user rule 2026-09-22 — form widgets don't rerun the
    page until submit, so this can't live inside one), and callers use a
    plain button instead of st.form_submit_button. מין's mutual exclusivity
    is still only enforced when the caller applies _finalize_cert_form.
    Returns {column: True/False, "_מין_זכר"/"_מין_נקבה": bool, "_mentor": str}.
    """
    defaults = defaults or {}
    values = {}
    mentor_value = st.session_state.get(f"{prefix}_mentor", mentor_default)
    for col, label in _CERT_ORDER:
        checked = _clean(defaults.get(col, "")) == "כן"
        values[col] = st.checkbox(label, value=checked, key=f"{prefix}_{col}", width="stretch")
        if col == "דייל בטרייני":
            # Shown directly under "טרייני", only while it's checked (user
            # rule 2026-09-22) — a temporary/one-day mentor is set in the
            # daily roster itself and always overrides this for that day.
            if values[col]:
                mentor_value = st.text_input(
                    "חונך/ת קבוע/ה (שם)", value=mentor_default, key=f"{prefix}_mentor", width=_FIELD_WIDTH,
                    help='החונך/ת הרשמי/ת הקבוע/ה של הטרייני. חונך זמני ליום בודד נקבע '
                         'בסידור היומי עצמו ותמיד גובר על הערך הזה לאותו יום בלבד.',
                )
            else:
                mentor_value = st.session_state.get(f"{prefix}_mentor", mentor_default)
    values["_mentor"] = mentor_value

    _right_heading("מין", level="p")
    _cur_sex = _clean(defaults.get("מין", ""))
    # A horizontal container with NATIVE physical right-alignment — no RTL/
    # flex ambiguity this time (see the checkbox justify-content comment
    # above): horizontal_alignment="right" packs children at the physical
    # right edge in normal (left-to-right) child order, so "נקבה" is added
    # BEFORE "זכר" to end up as the pair's LEFT member — "זכר" then sits
    # closest to the true right edge, reading first, right to left.
    with st.container(horizontal=True, horizontal_alignment="right", key=f"{prefix}_מין_row"):
        _female = st.checkbox("נקבה", value=_cur_sex == "נקבה", key=f"{prefix}_מין_נקבה")
        _male = st.checkbox("זכר", value=_cur_sex == "זכר", key=f"{prefix}_מין_זכר")
    values["_מין_זכר"], values["_מין_נקבה"] = _male, _female

    _cur_dept = _clean(defaults.get("מחלקה מקורית", ""))
    _cur_dept = _DEPARTMENT_LEGACY_MAP.get(_cur_dept, _cur_dept)
    _dept_index = _DEPARTMENT_OPTIONS.index(_cur_dept) if _cur_dept in _DEPARTMENT_OPTIONS else 0
    values["מחלקה מקורית"] = st.selectbox(
        "מחלקה מקורית", _DEPARTMENT_OPTIONS, index=_dept_index, key=f"{prefix}_מחלקה_מקורית", width=_FIELD_WIDTH,
        help="רק כאשר העובד/ת משמש/ת אך ורק כמפקח/ת TSA ושייך/ת לאחת המחלקות האלו (טראפיק או חוליה מיוחדת).",
    )
    return values


def _finalize_cert_form(values) -> tuple[dict, str]:
    """Turn _render_cert_form's raw widget values into DB-ready row fields.
    Returns (row_fields, error) — error is set (row_fields still usable for
    the rest of the row) when both מין checkboxes were ticked at once."""
    row = {col: ("כן" if values[col] else "לא") for col, _ in _CERT_ORDER}
    error = ""
    if values["_מין_זכר"] and values["_מין_נקבה"]:
        error = 'יש לבחור באפשרות אחת בלבד עבור "מין" — זכר או נקבה.'
        row["מין"] = ""
    elif values["_מין_זכר"]:
        row["מין"] = "זכר"
    elif values["_מין_נקבה"]:
        row["מין"] = "נקבה"
    else:
        row["מין"] = ""
    _dept = values.get("מחלקה מקורית", "")
    row["מחלקה מקורית"] = "" if _dept == "(אין)" else _dept
    return row, error

# A brand-new employee added through this panel is assumed to be a fresh
# trainee attendant paired with a mentor unless the admin says otherwise —
# user rule 2026-09-15: every new hire starts pre-checked "כן" on these
# three certifications (still editable before saving). Shared with the
# bulk trainee-import path in employee_db.py, so both stay in sync.
_NEW_TRAINEE_DEFAULT_YES = set(edb.TRAINEE_DEFAULT_CERTS)


def _sync_login_accounts(renamed) -> None:
    """A viewer login is tied to its employee by the EXACT name in
    employee_name (see auth.py) — after a rename it would point at nobody and
    the person would lose their personal screen. Follow the rename there too
    (the display "name" only when it was just the old employee name)."""
    try:
        import auth
        users = auth._load_users()
        changed = False
        for old, new in renamed:
            for entry in users.values():
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("employee_name", "")).strip() == old:
                    entry["employee_name"] = new
                    changed = True
                    if str(entry.get("name", "")).strip() == old:
                        entry["name"] = new
        if changed:
            auth._save_users(users)
    except Exception:
        pass    # accounts are a courtesy here; never block the rename itself


# ── Profile-card screen (2026-09-26) ─────────────────────────────────────────
_NEW_KEY = "__new__"
_CERT_LABEL = dict(_CERT_ORDER)
# The same 15 columns as _CERT_ORDER, grouped into three rows of pills.
_ROLE_GROUPS = [
    ("תפקידים", ["דייל", "שומר TSA", "מפקח TSA", "מתאם תורים", "דייל בטרייני", "מנהל משמרת"]),
    ("הסמכות ראש צוות", ["ראש צוות", "טרייני רצ", "חונך רצים", "מסמיך רצים"]),
    ("מאפיינים", ["ילד עובדים", "פורשים", 'מנהל כר"צ', "מתגבר שלוחה", "מתדרכת"]),
]
assert sorted(c for _, cols in _ROLE_GROUPS for c in cols) == sorted(c for c, _ in _CERT_ORDER)
# Same role→colour mapping as the dashboard legend (app/styles.py .dot-*),
# so a pill reads as the same role everywhere in the app.
_CERT_HUE = {
    # colour-blind-safe set (Okabe-Ito based), same as the schedule cards
    # theme variables (app/styles.py theme_vars_css) holding "r,g,b" triplets
    "דייל": "var(--r-agent)", "שומר TSA": "var(--r-guard)", "מפקח TSA": "var(--r-insp)",
    "מתאם תורים": "var(--r-queue)", "דייל בטרייני": "var(--r-train)", "מנהל משמרת": "var(--acc-rgb)",
    "ראש צוות": "var(--r-tl)", "טרייני רצ": "var(--r-tl)", "חונך רצים": "var(--r-tl)",
    "מסמיך רצים": "var(--r-tl)",
}
_FILTERS = {
    "all": "הכול",
    "tl": 'ר"צ',
    "tsa": "TSA",
    "trainee": "טריינים",
    "inactive": "מושבתים",
}


def _is_yes(row, col) -> bool:
    return _clean(row.get(col, "")) == "כן"


def _roles_summary(row) -> str:
    parts = []
    if _is_yes(row, "ראש צוות"):
        parts.append('ר"צ')
    if _is_yes(row, "מפקח TSA"):
        parts.append("מפקח TSA")
    elif _is_yes(row, "שומר TSA"):
        parts.append("TSA")
    if _is_yes(row, "דייל בטרייני") or _is_yes(row, "טרייני רצ"):
        parts.append("טרייני")
    if not parts and _is_yes(row, "דייל"):
        parts.append("דייל/ת")
    return " · ".join(parts)


def _initials(name: str) -> str:
    words = [w for w in str(name).split() if w]
    return "".join(w[0] for w in words[:2]) or "?"


def _pill_css() -> str:
    """Per-option colours for the role pills. st.pills has no per-option
    styling, so each option is targeted by its position inside its group's
    keyed container (the group's option order is fixed in _ROLE_GROUPS)."""
    rules = []
    for gi, (_, cols) in enumerate(_ROLE_GROUPS):
        for oi, col in enumerate(cols, start=1):
            rgb = _CERT_HUE.get(col, "var(--r-agent)")
            sel = f'[class*="st-key-emp_pills_{gi}_"] button:nth-of-type({oi})'
            rules.append(
                f'{sel}[kind="pillsActive"] {{ background: rgba({rgb},.18) !important; '
                f'border-color: rgb({rgb}) !important; color: var(--ink) !important; '
                f'box-shadow: 0 0 14px rgba({rgb},.25) !important; }}'
            )
    return "\n".join(rules)


def _render_roster_list(all_df) -> str:
    """Search + filter + scrollable list of people. Returns the selected name
    (or _NEW_KEY)."""
    if st.button("＋ הוספת עובד/ת", key="emp_new_btn", width="stretch"):
        st.session_state["emp_selected"] = _NEW_KEY
    _q = st.text_input("חיפוש", placeholder="חיפוש לפי שם…", key="emp_search",
                       label_visibility="collapsed").strip()
    _flt = st.segmented_control(
        "סינון", list(_FILTERS), default="all", required=True, key="emp_filter",
        format_func=lambda k: _FILTERS[k], label_visibility="collapsed",
    ) or "all"

    df = all_df
    if _flt == "inactive":
        df = df[df["active"] == 0]
    else:
        df = df[df["active"] == 1]
        if _flt == "tl":
            df = df[df["ראש צוות"].map(_clean) == "כן"]
        elif _flt == "tsa":
            df = df[(df["שומר TSA"].map(_clean) == "כן") | (df["מפקח TSA"].map(_clean) == "כן")]
        elif _flt == "trainee":
            df = df[(df["דייל בטרייני"].map(_clean) == "כן") | (df["טרייני רצ"].map(_clean) == "כן")]
    if _q:
        df = df[df["שם"].astype(str).str.contains(_q, regex=False)]
    df = df.sort_values("שם")

    _selected = st.session_state.get("emp_selected")
    if _selected is None:
        _selected = df["שם"].iloc[0] if len(df) else _NEW_KEY
        st.session_state["emp_selected"] = _selected

    st.markdown(f"<div class='emp-list-count'>{len(df)} עובדים</div>", unsafe_allow_html=True)
    _LIMIT = 80
    with st.container(height=520, key="emp_list"):
        for _i, row in enumerate(df.head(_LIMIT).to_dict("records")):
            _name = str(row["שם"])
            _sub = _roles_summary(row)
            _label = f"**{_name}**" + (f"  :gray[{_sub}]" if _sub else "")
            if st.button(_label, key=f"emp_pick_{_i}_{_name}", width="stretch",
                         type="primary" if _name == _selected else "secondary"):
                st.session_state["emp_selected"] = _name
                st.rerun()
        if len(df) > _LIMIT:
            st.caption(f"מוצגים {_LIMIT} ראשונים — הקלד/י שם כדי לצמצם.")
        if not len(df):
            st.caption("לא נמצאו עובדים.")
    return st.session_state.get("emp_selected", _NEW_KEY)


def _render_profile_form(prefix, defaults=None, mentor_default=""):
    """Pills version of _render_cert_form — same return shape, so
    _finalize_cert_form still turns it into DB fields."""
    defaults = defaults or {}
    selected = set()
    for gi, (title, cols) in enumerate(_ROLE_GROUPS):
        st.markdown(f"<div class='emp-group-title'>{title}</div>", unsafe_allow_html=True)
        with st.container(key=f"emp_pills_{gi}_{prefix}"):
            picked = st.pills(
                title, cols, selection_mode="multi",
                default=[c for c in cols if _is_yes(defaults, c)],
                format_func=lambda c: _CERT_LABEL[c],
                key=f"{prefix}_grp{gi}", label_visibility="collapsed",
            )
        selected |= set(picked or [])
    values = {c: (c in selected) for c, _ in _CERT_ORDER}

    _cur_sex = _clean(defaults.get("מין", ""))
    _cur_dept = _clean(defaults.get("מחלקה מקורית", ""))
    _cur_dept = _DEPARTMENT_LEGACY_MAP.get(_cur_dept, _cur_dept)
    _c_sex, _c_mentor, _c_dept = st.columns([1, 1.3, 1.2])
    with _c_sex:
        st.markdown("<div class='emp-group-title'>מין</div>", unsafe_allow_html=True)
        _sex = st.segmented_control(
            "מין", ["זכר", "נקבה"], default=_cur_sex if _cur_sex in ("זכר", "נקבה") else None,
            key=f"{prefix}_sex", label_visibility="collapsed",
        )
    with _c_mentor:
        st.markdown("<div class='emp-group-title'>חונך/ת קבוע/ה</div>", unsafe_allow_html=True)
        if values["דייל בטרייני"]:
            mentor_value = st.text_input(
                "חונך/ת קבוע/ה (שם)", value=mentor_default, key=f"{prefix}_mentor",
                label_visibility="collapsed", placeholder="שם החונך/ת",
                help='החונך/ת הרשמי/ת הקבוע/ה של הטרייני. חונך זמני ליום בודד נקבע '
                     'בסידור היומי עצמו ותמיד גובר על הערך הזה לאותו יום בלבד.',
            )
        else:
            mentor_value = st.session_state.get(f"{prefix}_mentor", mentor_default)
            st.markdown("<div class='emp-muted'>רק לטרייני</div>", unsafe_allow_html=True)
    with _c_dept:
        st.markdown("<div class='emp-group-title'>מחלקה מקורית</div>", unsafe_allow_html=True)
        values["מחלקה מקורית"] = st.selectbox(
            "מחלקה מקורית", _DEPARTMENT_OPTIONS,
            index=_DEPARTMENT_OPTIONS.index(_cur_dept) if _cur_dept in _DEPARTMENT_OPTIONS else 0,
            key=f"{prefix}_מחלקה_מקורית", label_visibility="collapsed",
            help='רק כאשר העובד/ת משמש/ת אך ורק כמפקח/ת TSA ושייך/ת לאחת המחלקות האלו.',
        )
    values["_mentor"] = mentor_value
    values["_מין_זכר"], values["_מין_נקבה"] = _sex == "זכר", _sex == "נקבה"
    return values


def _profile_header(name, subtitle, active, n_certs, is_new=False):
    _status = ("<span class='emp-chip emp-chip-new'>חדש/ה</span>" if is_new else
               "<span class='emp-chip emp-chip-on'>פעיל/ה</span>" if active else
               "<span class='emp-chip emp-chip-off'>מושבת/ת</span>")
    st.markdown(
        f"""<div class="emp-head">
  <div class="emp-avatar">{_initials(name) if not is_new else "＋"}</div>
  <div class="emp-head-main">
    <div class="emp-name">{name}</div>
    <div class="emp-sub">{_status}<span class="emp-sub-text">{subtitle}</span></div>
  </div>
  <div class="emp-stat"><b>{n_certs}</b><span>הסמכות</span></div>
</div>""",
        unsafe_allow_html=True,
    )


def _render_employee_card(row) -> None:
    name = str(row["שם"])
    active = int(row.get("active", 1)) == 1
    prefix = f"edit_{name}"
    with st.container(key="emp_profile"):
        _profile_header(name, _roles_summary(row), active,
                        sum(_is_yes(row, c) for c, _ in _CERT_ORDER))
        st.markdown("<div class='emp-group-title'>שם</div>", unsafe_allow_html=True)
        _new_name = st.text_input("שם", value=name, key=f"edit_emp_name_{name}",
                                  label_visibility="collapsed",
                                  help="שינוי השם כאן משנה אותו בכל המערכת.").strip()
        values = _render_profile_form(prefix, row, mentor_default=_clean(row.get(_MENTOR_COL, "")))

        st.markdown("<div class='emp-footer-line'></div>", unsafe_allow_html=True)
        _c_save, _c_toggle, _c_sp = st.columns([1.2, 1, 2])
        with _c_save:
            _save = st.button("שמור שינויים", type="primary", width="stretch",
                              key="detail_edit_submit")
        with _c_toggle:
            if active:
                _toggle = st.button("השבתה", width="stretch", key="emp_deactivate")
            else:
                _toggle = st.button("הפעלה מחדש", width="stretch", key="emp_reactivate")

    if _toggle:
        edb.set_active(name, not active)
        st.toast(f'"{name}" ' + ("הושבת/ה" if active else "הופעל/ה מחדש"), icon="✅")
        st.rerun()
    if _save:
        _row, _sex_error = _finalize_cert_form(values)
        if _sex_error:
            st.error(_sex_error)
            return
        _final_name = _new_name or name
        if _final_name != name:
            _err = edb.rename_employee(name, _final_name)
            if _err:
                st.error(_err)
                _final_name = name
            else:
                _sync_login_accounts([(name, _final_name)])
                st.session_state["emp_selected"] = _final_name
        _row["שם"] = _final_name
        _row[_MENTOR_COL] = values["_mentor"].strip()
        edb.upsert_employee(_row)
        st.toast(f'"{_final_name}" עודכן/ה', icon="✅")
        st.rerun()


def _render_new_employee_card() -> None:
    _defaults = {c: "כן" for c in _NEW_TRAINEE_DEFAULT_YES}
    with st.container(key="emp_profile"):
        _profile_header("עובד/ת חדש/ה", "טרייני/ת עם חונך כברירת מחדל — אפשר לשנות", True,
                        len(_defaults), is_new=True)
        st.markdown("<div class='emp-group-title'>שם</div>", unsafe_allow_html=True)
        new_name = st.text_input("שם", key="new_emp_name", label_visibility="collapsed",
                                 placeholder="שם מלא, בדיוק כמו בסידור היומי")
        values = _render_profile_form("new_emp", _defaults)
        st.markdown("<div class='emp-footer-line'></div>", unsafe_allow_html=True)
        _c_add, _c_sp = st.columns([1.2, 3])
        with _c_add:
            submitted = st.button("הוסף/י עובד/ת", type="primary", width="stretch",
                                  key="add_employee_submit")
    if submitted:
        if not new_name.strip():
            st.error("יש להזין שם.")
            return
        _row, _sex_error = _finalize_cert_form(values)
        if _sex_error:
            st.error(_sex_error)
            return
        _row["שם"] = new_name.strip()
        _row[_MENTOR_COL] = values["_mentor"].strip()
        edb.upsert_employee(_row)
        for _k in list(st.session_state):
            if _k.startswith("new_emp"):
                st.session_state.pop(_k, None)
        st.session_state["emp_selected"] = _row["שם"]
        st.toast(f'"{_row["שם"]}" נוסף/ה לרשימה', icon="✅")
        st.rerun()


_PROFILE_CSS = """<style>
.st-key-emp_profile {
    direction: rtl;
    background: linear-gradient(180deg, var(--card) 0%, var(--card-2) 100%);
    border: 1px solid rgba(var(--acc-rgb),.22);
    border-radius: 20px;
    padding: 22px 26px 20px;
    box-shadow: 0 16px 40px rgba(var(--shadow-rgb),.35), inset 0 1px 0 rgba(var(--ink-rgb),.04);
    animation: panelIn .22s ease-out both;
}
.emp-head { display: flex; align-items: center; gap: 18px; direction: rtl; margin-bottom: 6px; }
.emp-avatar {
    width: 68px; height: 68px; border-radius: 20px; flex-shrink: 0;
    background: linear-gradient(135deg, rgba(168,85,247,.5), rgba(var(--acc-rgb),.45));
    display: flex; align-items: center; justify-content: center;
    font-size: 25px; font-weight: 800; color: var(--ink);
}
.emp-head-main { flex-grow: 1; min-width: 0; }
.emp-name { font-size: 25px; font-weight: 800; color: var(--ink); line-height: 1.2; }
.emp-sub { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 13px; color: rgba(var(--ink-rgb),.74); }
.emp-chip { padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
.emp-chip-on  { background: rgba(var(--ok-rgb),.12); color: var(--ok-ink); }
.emp-chip-off { background: rgba(var(--bad-rgb),.12); color: var(--bad-ink); }
.emp-chip-new { background: rgba(var(--acc-rgb),.16); color: var(--acc-strong); }
.emp-stat {
    text-align: center; padding: 8px 16px; border-radius: 14px;
    background: rgba(var(--ink-rgb),.03); border: 1px solid rgba(var(--ink-rgb),.08);
}
.emp-stat b { display: block; font-size: 22px; color: var(--acc-strong); font-variant-numeric: tabular-nums; }
.emp-stat span { font-size: 11px; color: rgba(var(--ink-rgb),.74); }
.emp-group-title {
    direction: rtl; text-align: right; font-size: 12px; letter-spacing: .8px;
    color: var(--acc); font-weight: 700; margin: 14px 0 4px;
}
.emp-muted { direction: rtl; text-align: right; font-size: 13px; color: rgba(var(--ink-rgb),.74); padding-top: 8px; }
.emp-footer-line { border-top: 1px solid rgba(var(--ink-rgb),.08); margin: 18px 0 10px; }
.emp-list-count { direction: rtl; text-align: right; font-size: 12px; color: rgba(var(--ink-rgb),.74); margin: 2px 2px 4px; }

/* role pills */
[class*="st-key-emp_pills_"] [data-testid="stButtonGroup"],
[class*="st-key-emp_pills_"] [role="group"] { direction: rtl; justify-content: flex-start; gap: 8px; flex-wrap: wrap; }
[class*="st-key-emp_pills_"] button {
    min-height: 36px !important; padding: 0 16px !important; border-radius: 999px !important;
    background: rgba(var(--ink-rgb),.03) !important; border: 1px solid rgba(var(--ink-rgb),.12) !important;
    color: rgba(var(--ink-rgb),.74) !important; transition: all .15s !important;
}
[class*="st-key-emp_pills_"] button:hover { border-color: rgba(var(--ink-rgb),.3) !important; color: var(--ink) !important; }
[class*="st-key-emp_pills_"] button[kind="pillsActive"] {
    background: rgba(var(--r-agent),.16) !important; border-color: rgba(var(--r-agent),.8) !important; color: var(--ink) !important;
}
.st-key-emp_profile [data-testid="stButtonGroup"],
.st-key-emp_filter,
.st-key-emp_filter [data-testid="stButtonGroup"] { direction: rtl; }
.st-key-emp_search input { text-align: right !important; direction: rtl; }

/* roster list rows */
.st-key-emp_list { direction: rtl; }
.st-key-emp_list div[data-testid="stButton"] button {
    justify-content: flex-start !important; text-align: right !important;
    min-height: 44px !important; border-radius: 12px !important;
    background: rgba(var(--ink-rgb),.02) !important; border: 1px solid rgba(var(--ink-rgb),.06) !important;
    color: rgba(var(--ink-rgb),.9) !important; box-shadow: none !important;
}
.st-key-emp_list div[data-testid="stButton"] button:hover { border-color: rgba(var(--acc-rgb),.45) !important; }
.st-key-emp_list div[data-testid="stButton"] button[kind="primary"] {
    background: rgba(var(--acc-rgb),.12) !important; border-color: rgba(var(--acc-rgb),.55) !important;
}
.st-key-emp_list div[data-testid="stButton"] button p { text-align: right !important; width: 100%; }
.st-key-emp_new_btn div[data-testid="stButton"] button {
    border: 1px dashed rgba(var(--acc-rgb),.55) !important; background: transparent !important; color: var(--acc-strong) !important;
}
.st-key-emp_deactivate div[data-testid="stButton"] button {
    background: transparent !important; border: 1px solid rgba(var(--bad-rgb),.4) !important; color: var(--bad-ink) !important;
}
</style>"""


def _bool_col_config(label):
    return st.column_config.SelectboxColumn(label, options=["כן", "לא"], width="small")


def render_employee_admin_panel() -> None:
    # Everything below lives inside this keyed container so _PANEL_RTL_CSS
    # (a page-wide <style> tag — Streamlit has no built-in CSS scoping) only
    # ever touches this screen, never the rest of the app's own styling.
    with st.container(key=_PANEL_KEY):
        st.markdown(_PANEL_RTL_CSS, unsafe_allow_html=True)
        st.markdown(_PROFILE_CSS + "<style>" + _pill_css() + "</style>", unsafe_allow_html=True)
        st.markdown(
            "<h3 style='text-align:right;border-bottom:2px solid rgba(var(--ink-rgb),0.15);"
            "padding-bottom:8px;margin-bottom:16px;'>🧑‍✈️ ניהול עובדים והסמכות</h3>",
            unsafe_allow_html=True,
        )

        active_df = edb.load_all_employees_df(include_inactive=False)

        # An st.expander collapses back to its default on every rerun unless
        # told otherwise — a bare action button inside it (deactivate, save)
        # triggers st.rerun(), and the whole section would visibly vanish
        # right after use, looking like the deactivate option "turned into"
        # reactivate instead of just being collapsed (user rule 2026-09-15:
        # "שתי האופציות תמיד צריכות להיות זמינות"). Remember each expander's
        # own open/closed state across reruns, forcing it back open right
        # after any action taken inside it.
        # ── Roster list (right) + profile card (left) — user-approved design
        # 2026-09-26 (concept canvas "מסך עובד"): replaces the separate
        # add / edit / deactivate / reactivate sections and the 15-checkbox
        # column with one screen: pick a person on the right, edit them on
        # the left with role "pills" in the dashboard legend's colours.
        all_df = edb.load_all_employees_df(include_inactive=True)
        _col_profile, _col_list = st.columns([2.3, 1], gap="medium")
        with _col_list:
            _selected = _render_roster_list(all_df)
        with _col_profile:
            if _selected == _NEW_KEY or _selected not in set(all_df["שם"]):
                _render_new_employee_card()
            else:
                _render_employee_card(all_df[all_df["שם"] == _selected].iloc[0].to_dict())

        _active_open = st.session_state.get("_emp_active_expander_open", False)
        with st.expander(f"עריכה מהירה בטבלה — כל העובדים הפעילים ({len(active_df)})", expanded=_active_open):
            _display = active_df.drop(columns=["active"]).sort_values("שם").reset_index(drop=True)
            _col_config = {"שם": st.column_config.TextColumn("שם", width="medium")}
            for c in _BOOL_COLS:
                _col_config[c] = _bool_col_config(c)
            st.caption(
                "אפשר לשנות גם את השם: עורכים את התא ולוחצים ״שמור שינויים״. השם החדש חייב "
                "להיות זהה לשם שכתוב בסידור היומי (או שיירשם ב-data/name_aliases.json), "
                "אחרת העובד/ת לא יזוהה/תזוהה בסידור. חשבון התחברות מקושר ושם חונך אצל "
                "טריינים מתעדכנים אוטומטית."
            )

            edited = st.data_editor(
                _display,
                width="stretch",
                hide_index=True,
                num_rows="fixed",
                key="employee_mgmt_editor",
                column_config=_col_config,
            )

            if st.button("💾 שמור שינויים", key="save_employee_edits"):
                _errors, _renamed = [], []
                # `edited` keeps the row order of `_display` (num_rows="fixed"), so
                # the untouched name at the same position is the row's OLD name.
                for _i, row in edited.iterrows():
                    _data = row.to_dict()
                    _old_name = str(_display.at[_i, "שם"]).strip()
                    _new_name = str(_data.get("שם", "")).strip()
                    if _new_name != _old_name:
                        _err = edb.rename_employee(_old_name, _new_name)
                        if _err:
                            _errors.append(_err)
                            _data["שם"] = _old_name       # still save this row's other edits
                        else:
                            _renamed.append((_old_name, _new_name))
                            _data["שם"] = _new_name
                    edb.upsert_employee(_data)
                if _renamed:
                    _sync_login_accounts(_renamed)
                st.session_state.pop("employee_mgmt_editor", None)
                st.session_state["_emp_active_expander_open"] = True
                for _e in _errors:
                    st.error(_e)
                if _renamed:
                    st.toast("✅ שונו שמות: " + ", ".join(f"{o} ← {n}" for o, n in _renamed), icon="✅")
                if not _errors:
                    st.success("✅ השינויים נשמרו.")
                if _errors:
                    st.warning("שאר השינויים נשמרו; השמות שנדחו נשארו כפי שהיו.")
                else:
                    st.rerun()


        _right_heading("📥📤 ייבוא/ייצוא עובדים")
        # Two bordered "cards", same shape on both sides — label line, one
        # action element, one helper caption — so the import dropzone and
        # the export button read as a matching pair instead of two unrelated
        # widgets (user report 2026-09-23: a plain button next to a dropzone
        # "don't look like each other" even once evenly split into columns).
        _c1, _c2 = st.columns(2)
        with _c1:
            with st.container(border=True, key="emp_import_box"):
                _trainees_file = st.file_uploader(
                    "ייבוא שמות מקובץ/תמונה",
                    type=["xlsx", "png", "jpg", "jpeg"],
                    key="employee_import_trainees_file",
                )
                st.caption("קובץ Excel או תמונה — עמודת שמות ועמודת חונכים")
        with _c2:
            with st.container(border=True, key="emp_export_box"):
                st.markdown(
                    "<p class='emp-card-label'>ייצוא קובץ עובדים</p>",
                    unsafe_allow_html=True,
                )
                st.download_button(
                    "⬇️ הורדת קובץ Excel",
                    data=edb.export_to_excel_bytes(include_inactive=True),
                    file_name="עובדים_גיבוי.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width="stretch",
                )
                st.caption("קובץ אחד — כל העובדים, פעילים ומושבתים")
        if _trainees_file is not None:
            _file_fp = f"{_trainees_file.name}:{_trainees_file.size}"
            if st.session_state.get("_trainee_import_fp") != _file_fp:
                # A genuinely new/different file — drop any stale editor state
                # from a previous file so its edits never leak into this one's
                # row indices.
                st.session_state.pop("trainee_import_editor", None)
                _is_excel = _trainees_file.name.lower().endswith(".xlsx")
                try:
                    if _is_excel:
                        _df, _name_col, _mentor_col = edb.parse_trainee_excel_to_dataframe(
                            _trainees_file.read()
                        )
                        if _name_col is None:
                            raise RuntimeError('לא נמצאה עמודת "שם" או "חניך" בקובץ.')
                    else:
                        _df = employee_ocr.extract_table_from_image(_trainees_file.read())
                        _name_col = _df.columns[0] if len(_df.columns) else None
                        _mentor_col = _df.columns[1] if len(_df.columns) > 1 else None
                    st.session_state["_trainee_import_df"] = _df
                    st.session_state["_trainee_import_name_col"] = _name_col
                    st.session_state["_trainee_import_mentor_col"] = _mentor_col
                    st.session_state["_trainee_import_fp"] = _file_fp
                except RuntimeError as _exc:
                    st.error(str(_exc))
                    st.session_state.pop("_trainee_import_df", None)
                    st.session_state["_trainee_import_fp"] = _file_fp

        _preview_df = st.session_state.get("_trainee_import_df")
        if _preview_df is not None and not _preview_df.empty:
            st.caption(
                "תצוגה מקדימה — עריכת תא כאן משנה רק את הטבלה על המסך. שום עובד/ת "
                "לא נוסף/ת למאגר עד שלוחצים \"✅ אישור\" למטה."
            )
            _preview_edited = st.data_editor(
                _preview_df, width="stretch", hide_index=True, key="trainee_import_editor",
            )
            _col_options = list(_preview_edited.columns)
            _name_default = st.session_state.get("_trainee_import_name_col")
            _mentor_default = st.session_state.get("_trainee_import_mentor_col")
            _c1, _c2 = st.columns(2)
            with _c1:
                _name_col_pick = st.selectbox(
                    "איזו עמודה היא שם החניך?",
                    options=_col_options,
                    index=_col_options.index(_name_default) if _name_default in _col_options else 0,
                    key="trainee_import_name_col_pick",
                )
            with _c2:
                _mentor_options = ["(אין)"] + _col_options
                _mentor_index = (
                    _mentor_options.index(_mentor_default)
                    if _mentor_default in _col_options
                    else 0
                )
                _mentor_col_pick = st.selectbox(
                    "איזו עמודה היא שם החונך?",
                    options=_mentor_options,
                    index=_mentor_index,
                    key="trainee_import_mentor_col_pick",
                )

            # Editing a cell above only ever changes _preview_edited (the
            # in-memory table Streamlit hands back) — it does NOT touch the
            # employee DB. Only this explicit click does (user rule
            # 2026-09-15, correcting an earlier misreading: "בעת שינוי נתון,
            # הנתון נשמר בתוך התא ולא בתוך מאגר העובדים... הוסף כפתור אישור").
            if st.button(
                    "✅ אישור — הוסף/י למאגר העובדים", key="trainee_import_confirm_btn", width="stretch"):
                _mentor_arg = None if _mentor_col_pick == "(אין)" else _mentor_col_pick
                _new_n, _updated_n = edb.import_trainee_pairs_from_dataframe(
                    _preview_edited, _name_col_pick, _mentor_arg
                )
                st.success(f"✅ נוספו {_new_n} טריינים חדשים, עודכן חונך עבור {_updated_n} קיימים.")
                for _k in ("_trainee_import_df", "_trainee_import_fp",
                           "_trainee_import_name_col", "_trainee_import_mentor_col"):
                    st.session_state.pop(_k, None)
                st.rerun()
