"""Admin-only user management panel — the only place new accounts get
created from the running app (in addition to the bulk
generate_employee_accounts.py script). Gated by auth.is_super_admin(): the
single "admin" role, per user rule 2026-07-26 ("admin — that's only me").
"""

import pandas as pd
import streamlit as st

import auth
from generate_employee_accounts import candidate_usernames, random_password

ROLE_LABELS = {"admin": "מנהל מערכת", "manager": "מנהל משמרת", "viewer": "עובד/ת"}
_REVERSE_ROLE_LABELS = {v: k for k, v in ROLE_LABELS.items()}


def render_user_management_panel(employees_df) -> None:
    st.markdown(
        "<h3 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);"
        "padding-bottom:8px;margin-bottom:16px;'>👤 ניהול משתמשים</h3>",
        unsafe_allow_html=True,
    )

    users = auth._load_users()

    with st.expander(f"משתמשים קיימים ({len(users)}) — עריכה", expanded=False):
        # Sort by the employee's PERSONAL (first) name — names in this system
        # are stored surname-first (e.g. "אבו רומאנה אליאן" = surname "אבו
        # רומאנה", first name "אליאן"), so the LAST word is the first name.
        # This is the table's permanent default sort (user rule 2026-07-26),
        # not a one-time click — recomputed fresh on every render.
        def _first_name_key(uname):
            _words = str(users[uname].get("name", "")).strip().split()
            return _words[-1] if _words else uname

        _sorted_usernames = sorted(users.keys(), key=_first_name_key)
        _rows = [
            {
                "שם משתמש": uname,
                "שם": users[uname].get("name", ""),
                "תפקיד": ROLE_LABELS.get(users[uname].get("role", ""), users[uname].get("role", "")),
            }
            for uname in _sorted_usernames
        ]
        _edit_df = pd.DataFrame(_rows)

        edited = st.data_editor(
            _edit_df,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            key="user_mgmt_editor",
            column_config={
                "שם משתמש": st.column_config.TextColumn("שם משתמש", width="medium"),
                "שם": st.column_config.TextColumn("שם", width="medium"),
                "תפקיד": st.column_config.SelectboxColumn(
                    "תפקיד", options=list(ROLE_LABELS.values()), width="small",
                ),
            },
        )

        if st.button("💾 שמור שינויים", key="save_user_edits"):
            new_users = {}
            _collision = False
            for i, row in edited.iterrows():
                orig_username = _sorted_usernames[i]
                entry = dict(users[orig_username])
                new_username = str(row["שם משתמש"]).strip()
                if not new_username:
                    st.error(f'שם משתמש ריק עבור "{entry.get("name", orig_username)}" — לא נשמר שינוי עבור/ה.')
                    new_username = orig_username
                if new_username in new_users:
                    st.error(f'שם המשתמש "{new_username}" כפול — יש לבחור שם ייחודי.')
                    _collision = True
                    continue
                entry["name"] = str(row["שם"]).strip() or entry.get("name", "")
                entry["role"] = _REVERSE_ROLE_LABELS.get(row["תפקיד"], entry.get("role", "viewer"))
                new_users[new_username] = entry

            if _collision:
                st.warning("לא נשמר — יש לתקן שמות משתמש כפולים ולנסות שוב.")
            else:
                auth._save_users(new_users)
                # Clear the data_editor's own cached widget state before
                # rerunning — otherwise st.data_editor reuses its previous
                # render's cached row order/values under this same key
                # instead of picking up the freshly re-sorted table (found
                # via real use 2026-07-26: saved changes displayed in a
                # stale/random order until a full page reload).
                st.session_state.pop("user_mgmt_editor", None)
                st.success("✅ השינויים נשמרו.")
                st.rerun()

        st.markdown("##### 🔄 איפוס סיסמה")
        st.caption(
            "לא ניתן להציג סיסמה קיימת (נשמרת מוצפנת) — רק ליצור סיסמה זמנית חדשה."
        )
        _reset_target = st.selectbox(
            "בחר/י משתמש/ת", options=_sorted_usernames,
            format_func=lambda u: f"{u} ({users[u].get('name', '')})",
            key="reset_pw_select",
        )
        if st.button("🔄 אפס סיסמה", key="reset_pw_btn"):
            new_pw = random_password()
            users[_reset_target]["password_hash"] = auth.hash_password(new_pw)
            users[_reset_target]["must_change_password"] = True
            auth._save_users(users)
            st.success(
                f'✅ סיסמה זמנית חדשה עבור "{_reset_target}" '
                f"(מוצג פעם אחת בלבד, אנא שמור/י):"
            )
            st.code(f"שם משתמש: {_reset_target}\nסיסמה זמנית: {new_pw}", language=None)
            st.caption("המשתמש/ת יידרש/תידרש להחליף סיסמה בהתחברות הבאה.")

    st.markdown("#### ➕ הוספת משתמש חדש")

    _name_col = "שם" if "שם" in employees_df.columns else None
    _employee_names = (
        sorted(employees_df[_name_col].dropna().astype(str).str.strip().unique().tolist())
        if _name_col else []
    )

    with st.form("add_user_form", clear_on_submit=True):
        new_display_name = st.text_input("שם להצגה")
        new_role = st.selectbox(
            "תפקיד",
            options=["viewer", "manager", "admin"],
            format_func=lambda r: ROLE_LABELS.get(r, r),
        )
        new_employee_name = None
        if new_role == "viewer":
            if _employee_names:
                new_employee_name = st.selectbox(
                    "שיוך לעובד/ת (לפי קובץ העובדים)", options=_employee_names,
                )
            else:
                new_employee_name = st.text_input("שם עובד/ת (כפי שמופיע בקובץ העובדים)")
        submitted = st.form_submit_button("צור משתמש", use_container_width=True, type="primary")

    if submitted:
        if not new_display_name.strip():
            st.error("יש להזין שם להצגה.")
        elif new_role == "viewer" and not (new_employee_name or "").strip():
            st.error("יש לבחור/להזין שם עובד/ת עבור תפקיד 'עובד/ת'.")
        else:
            base_for_username = new_employee_name if new_role == "viewer" else new_display_name
            cands = candidate_usernames(base_for_username)
            username = next((c for c in cands if c not in users), None)
            if username is None:
                base = cands[-1]
                i = 2
                while f"{base}{i}" in users:
                    i += 1
                username = f"{base}{i}"

            pw = random_password()
            users[username] = {
                "name": new_display_name.strip(),
                "password_hash": auth.hash_password(pw),
                "role": new_role,
                "employee_name": (new_employee_name or "").strip() if new_role == "viewer" else "",
                "must_change_password": True,
            }
            auth._save_users(users)

            st.success(
                f"✅ המשתמש נוצר — יש להעביר לעובד/ת את הפרטים הבאים "
                f"(מוצג פעם אחת בלבד, אנא שמור/י):"
            )
            st.code(f"שם משתמש: {username}\nסיסמה זמנית: {pw}", language=None)
            st.caption("המשתמש/ת יידרש/תידרש להחליף סיסמה בהתחברות הראשונה.")
