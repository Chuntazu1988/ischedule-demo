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

_BOOL_COLS = [
    "דייל", "ראש צוות", "מפקח TSA", "שומר TSA", "מתאם תורים",
    "חונך רצים", "מסמיך רצים", "טרייני רצ", "ילד עובדים",
    "מתדרכת", 'מנהל כר"צ', "דייל בטרייני", "פורשים",
]
_TEXT_COLS = ["מין", "מחלקה מקורית", "מנהל משמרת"]
_MENTOR_COL = "חונך דייל"

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


def _bool_col_config(label):
    return st.column_config.SelectboxColumn(label, options=["כן", "לא"], width="small")


def render_employee_admin_panel() -> None:
    st.markdown(
        "<h3 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);"
        "padding-bottom:8px;margin-bottom:16px;'>🧑‍✈️ ניהול עובדים והסמכות</h3>",
        unsafe_allow_html=True,
    )
    st.caption(
        "הרשימה כאן היא הבסיס הקבוע (מי קיים והסמכותיו) — קובץ הסידור היומי "
        "עדיין נטען כל יום בנפרד וקובע מי בפועל במשמרת ומתי."
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
    _active_open = st.session_state.get("_emp_active_expander_open", False)
    with st.expander(f"עובדים פעילים ({len(active_df)}) — עריכה", expanded=_active_open):
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
            use_container_width=True,
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

        st.markdown("##### 🚫 השבתת עובד/ת")
        st.caption("עובד/ת מושבת/ת נעלם/ת לגמרי מהרשימה הפעילה ומהשיבוץ — הנתונים לא נמחקים ואפשר להפעיל בחזרה.")
        if len(active_df):
            _deact_target = st.selectbox(
                "בחר/י עובד/ת", options=sorted(active_df["שם"].tolist()), key="deactivate_emp_select",
            )
            if st.button("🚫 השבת/י", key="deactivate_emp_btn"):
                edb.set_active(_deact_target, False)
                st.session_state["_emp_active_expander_open"] = True
                st.session_state["_emp_inactive_expander_open"] = True
                st.success(f'✅ "{_deact_target}" הושבת/ה.')
                st.rerun()

    inactive_df = edb.load_all_employees_df(include_inactive=True)
    inactive_df = inactive_df[inactive_df["active"] == 0]
    if len(inactive_df):
        _inactive_open = st.session_state.get("_emp_inactive_expander_open", False)
        with st.expander(f"עובדים מושבתים ({len(inactive_df)}) — הפעלה מחדש", expanded=_inactive_open):
            _reactivate_target = st.selectbox(
                "בחר/י עובד/ת להפעלה מחדש",
                options=sorted(inactive_df["שם"].tolist()),
                key="reactivate_emp_select",
            )
            if st.button("✅ הפעל/י מחדש", key="reactivate_emp_btn"):
                edb.set_active(_reactivate_target, True)
                st.session_state["_emp_active_expander_open"] = True
                st.session_state["_emp_inactive_expander_open"] = True
                st.success(f'✅ "{_reactivate_target}" הופעל/ה מחדש.')
                st.rerun()

    st.markdown("#### ➕ הוספת עובד/ת חדש/ה")
    st.caption(
        "עובד/ת חדש/ה מניחים כברירת מחדל שהוא/היא טרייני/ת המוצמד/ת לחונך/ת — "
        'לכן "דייל", "שומר TSA" ו"דייל בטרייני" מסומנים "כן" מראש. אפשר לשנות לפני השמירה.'
    )
    with st.form("add_employee_form", clear_on_submit=True):
        new_name = st.text_input("שם")
        new_mentor = st.text_input(
            "חונך/ת קבוע/ה (שם)",
            key="new_emp_mentor",
            help='החונך/ת הרשמי/ת הקבוע/ה של הטרייני. חונך זמני ליום בודד נקבע בסידור היומי עצמו ותמיד גובר על הערך הזה לאותו יום בלבד.',
        )
        _cols = st.columns(3)
        _bool_values = {}
        for i, c in enumerate(_BOOL_COLS):
            with _cols[i % 3]:
                _default_index = 1 if c in _NEW_TRAINEE_DEFAULT_YES else 0
                _bool_values[c] = st.selectbox(
                    c, options=["לא", "כן"], index=_default_index, key=f"new_emp_{c}",
                )
        _text_values = {}
        _tcols = st.columns(3)
        for i, c in enumerate(_TEXT_COLS):
            with _tcols[i % 3]:
                _text_values[c] = st.text_input(c, key=f"new_emp_{c}")
        submitted = st.form_submit_button("➕ הוסף/י עובד/ת", use_container_width=True, type="primary")

    if submitted:
        if not new_name.strip():
            st.error("יש להזין שם.")
        else:
            row = {
                "שם": new_name.strip(),
                _MENTOR_COL: new_mentor.strip(),
                **_bool_values,
                **_text_values,
            }
            edb.upsert_employee(row)
            st.success(f'✅ "{new_name.strip()}" נוסף/ה לרשימה.')
            st.rerun()

    st.markdown("#### 📥📤 יבוא / ייצוא מגובה Excel")
    _c1, _c2 = st.columns(2)
    with _c1:
        st.download_button(
            "📤 ייצוא כל הרשימה ל-Excel",
            data=edb.export_to_excel_bytes(include_inactive=True),
            file_name="עובדים_גיבוי.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with _c2:
        _import_file = st.file_uploader("📥 עדכון המוני מקובץ Excel", type=["xlsx"], key="employee_import_file")
        if _import_file is not None and st.button("ייבא ועדכן", key="employee_import_btn", use_container_width=True):
            n = edb.import_from_excel_bytes(_import_file.read())
            st.success(f"✅ יובאו/עודכנו {n} רשומות.")
            st.rerun()

    st.markdown("#### 🧑‍🎓 הוספת טריינים חדשים בקובץ מרוכז")
    st.caption(
        'קובץ Excel עם "שם"/"חניך" ו"חונך דייל"/"חונך" — או תמונה/צילום מסך של '
        "טבלת הצימודים. הקובץ רק *נבדק* — כלום לא נשמר עד שמאשרים במסך "
        "התצוגה המקדימה למטה. שם חדש נוסף עם הסמכות טרייני סטנדרטיות (דייל, "
        "שומר TSA, דייל בטרייני — כמו בהוספה בודדת); שם שכבר קיים — רק החונך "
        "שלו מתעדכן."
    )
    _trainees_file = st.file_uploader(
        "📥 קובץ טריינים — Excel או תמונה",
        type=["xlsx", "png", "jpg", "jpeg"],
        key="employee_import_trainees_file",
    )
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
            _preview_df, use_container_width=True, hide_index=True, key="trainee_import_editor",
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
        if st.button("✅ אישור — הוסף/י למאגר העובדים", key="trainee_import_confirm_btn", use_container_width=True):
            _mentor_arg = None if _mentor_col_pick == "(אין)" else _mentor_col_pick
            _new_n, _updated_n = edb.import_trainee_pairs_from_dataframe(
                _preview_edited, _name_col_pick, _mentor_arg
            )
            st.success(f"✅ נוספו {_new_n} טריינים חדשים, עודכן חונך עבור {_updated_n} קיימים.")
            for _k in ("_trainee_import_df", "_trainee_import_fp",
                       "_trainee_import_name_col", "_trainee_import_mentor_col"):
                st.session_state.pop(_k, None)
            st.rerun()
