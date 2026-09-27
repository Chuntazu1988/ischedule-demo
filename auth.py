"""Username/password login gate for iSchedule.

Credentials live in users.json (gitignored) — NOT .streamlit/secrets.toml,
because secrets.toml is read-once and not meant to be rewritten at runtime,
while this module needs to support self-service password changes (a new
employee account is created with must_change_password=True and is forced
through a one-time password-change screen right after first login).
Passwords are never stored in plain text — password_hash is a bcrypt hash.

Three roles (user rule 2026-07-26):
  "admin"   — the system owner. Full read/write access PLUS user management
              (create/view accounts) — the only role that can do that.
  "manager" — a shift manager. Full read/write access to everything EXCEPT
              user management — same dashboard as admin, just can't create
              new accounts.
  "viewer"  — an individual employee account. Sees ONLY their own personal
              screen (mark shift arrival + their own assigned tasks today),
              never the dashboard. Requires an "employee_name" field
              matching that person's "שם" in employees_clean.xlsx exactly —
              give each real employee their own account, don't share one.

is_admin() is intentionally broadened to mean "full dashboard access"
(admin OR manager) since that's what nearly every existing call site in
streamlit_app.py actually wants; is_super_admin() is the strict
admin-only check, used solely for gating the user-management panel.
"""

import json
import os

import bcrypt
import streamlit as st

USERS_FILE = "users.json"
SESSION_KEY = "auth_user"   # st.session_state[SESSION_KEY] = {"username","name","role","employee_name","must_change_password"}


def _load_users() -> dict:
    if not os.path.exists(USERS_FILE):
        return _seed_from_secrets()
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users(users: dict) -> None:
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _seed_from_secrets() -> dict:
    """First run only: migrate whatever's in .streamlit/secrets.toml (the
    original storage, before it needed to support runtime password changes)
    into users.json, so an already-working admin login keeps working."""
    try:
        users = {k: dict(v) for k, v in st.secrets["users"].items()}
    except Exception:
        users = {}
    if users:
        _save_users(users)
    return users


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def current_user() -> dict | None:
    """Returns {"username","name","role","employee_name","must_change_password"}
    for the logged-in user, or None."""
    return st.session_state.get(SESSION_KEY)


def is_admin() -> bool:
    """"Full dashboard access" — true for both admin and manager. See the
    module docstring for why this is broader than the name suggests."""
    user = current_user()
    return bool(user and user.get("role") in ("admin", "manager"))


# The account published for the public demo. Whatever role it was given in the deployment's
# secrets, it is never a super-admin: user and employee management edit shared state (and
# list accounts) on a server every visitor uses.
PUBLIC_DEMO_USERNAME = "demo"


def is_super_admin() -> bool:
    """Strict admin-only check — gates user and employee management."""
    user = current_user()
    if user and str(user.get("username", "")).strip().lower() == PUBLIC_DEMO_USERNAME:
        return False
    return bool(user and user.get("role") == "admin")


def is_employee_view() -> bool:
    user = current_user()
    return bool(user and user.get("role") == "viewer")


def _render_change_password_form(username: str) -> None:
    st.markdown(
        '<div dir="rtl" style="max-width:420px;margin:80px auto 0;padding:32px;'
        'background:var(--card);border:1px solid rgba(var(--acc-rgb),.35);border-radius:16px;">'
        '<h2 style="text-align:center;color:var(--ink);margin-bottom:8px;">🔑 יש להחליף סיסמה</h2>'
        '<p style="text-align:center;color:rgba(var(--ink-rgb),.74);font-size:14px;">'
        "זו ההתחברות הראשונה שלך — בחר/י סיסמה חדשה כדי להמשיך.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, col, _ = st.columns([1, 2, 1])
    with col:
        with st.form("change_pw_form"):
            new_pw = st.text_input("סיסמה חדשה", type="password")
            new_pw2 = st.text_input("אימות סיסמה חדשה", type="password")
            submitted = st.form_submit_button("שמור סיסמה חדשה", width="stretch")
        if submitted:
            if not new_pw or len(new_pw) < 6:
                st.error("הסיסמה חייבת להכיל לפחות 6 תווים.")
            elif new_pw != new_pw2:
                st.error("הסיסמאות אינן תואמות.")
            else:
                users = _load_users()
                if username in users:
                    users[username]["password_hash"] = hash_password(new_pw)
                    users[username]["must_change_password"] = False
                    _save_users(users)
                st.session_state[SESSION_KEY]["must_change_password"] = False
                st.success("✅ הסיסמה עודכנה.")
                st.rerun()
    st.stop()


def render_login_gate() -> None:
    """Blocks the rest of the app until a valid login (and, for a fresh
    account, a mandatory password change). Call once, right after
    st.set_page_config — before any other UI renders."""
    user = current_user()
    if user is not None:
        if user.get("must_change_password"):
            _render_change_password_form(user["username"])
        return

    users = _load_users()

    # Login card in the app's theme (colours are the theme CSS variables that
    # streamlit_app.py injects before calling this): radar + title + runway
    # lights on top, the form right under it as ONE card, gold login button.
    from app.styles import paper_planes_html, emblem_svg
    st.markdown(paper_planes_html(), unsafe_allow_html=True)
    with st.container(key="login_root"):
        if True:
            st.markdown(
                """<style>
                .st-key-login_root { position: fixed; inset: 0; z-index: 2; display: flex; flex-direction: column; align-items: center; justify-content: safe center; gap: 0; overflow-y: auto; padding: 5.5rem 0 1.5rem; box-sizing: border-box; }
                .st-key-login_root > * { width: min(460px, 92vw) !important; flex: none; }
                .login-card { direction: rtl; max-width: none; margin: 0; padding: 26px 28px 18px;
                    background: var(--card); border: 1px solid rgba(var(--acc-rgb),.35); border-bottom: none;
                    border-radius: 20px 20px 0 0; text-align: center; }
                .login-radar { width: 54px; height: 54px; margin: 0 auto 10px; border-radius: 50%;
                    border: 1px solid rgba(var(--acc-rgb),.6); position: relative; overflow: hidden; }
                .login-radar::before { content: ""; position: absolute; inset: 0;
                    background: conic-gradient(from 0deg, rgba(var(--acc-rgb),.6), transparent 25%);
                    animation: loginSweep 6s linear infinite; }
                @keyframes loginSweep { to { transform: rotate(360deg); } }
                .login-title { font-family: 'Chakra Petch','Rubik',sans-serif; font-size: 28px; font-weight: 700; color: var(--ink); }
                .login-sub { font-size: 14px; color: var(--acc-strong); margin-top: 2px; }
                .login-runway { height: 3px; margin-top: 16px; border-radius: 2px;
                    background-image: repeating-linear-gradient(90deg, rgba(var(--acc-rgb),.9) 0 22px, transparent 22px 50px);
                    background-size: 100px 3px; animation: loginLights 2.4s linear infinite; }
                @keyframes loginLights { to { background-position: -100px 0; } }
                [data-testid="stForm"]:has(input) { background: var(--card); border: 1px solid rgba(var(--acc-rgb),.35) !important;
                    border-top: none !important; border-radius: 0 0 20px 20px !important; margin-top: -1px; direction: rtl; }
                [data-testid="stForm"] [data-testid="stWidgetLabel"] { direction: rtl; width: 100%; justify-content: flex-start; }
                [data-testid="stForm"] input { text-align: right; direction: rtl; }
                
</style>
<div class="login-card">
<div class="login-emblem">""" + emblem_svg("lg") + """</div>
<div class="login-title">iSchedule</div>
<div class="login-sub">כניסה למערכת</div>
<div class="login-runway"></div>
</div>""",
                unsafe_allow_html=True,
            )
            with st.form("login_form"):
                username = st.text_input("שם משתמש")
                password = st.text_input("סיסמה", type="password")
                submitted = st.form_submit_button("התחבר", width="stretch", type="primary")

            if submitted:
                entry = users.get(username.strip())
                if not entry or not _verify_password(password, entry.get("password_hash", "")):
                    st.error("שם משתמש או סיסמה שגויים.")
                else:
                    st.session_state[SESSION_KEY] = {
                        "username": username.strip(),
                        "name": entry.get("name", username.strip()),
                        "role": entry.get("role", "viewer"),
                        "employee_name": entry.get("employee_name", ""),
                        "must_change_password": bool(entry.get("must_change_password", False)),
                    }
                    st.rerun()

    if not users:
        st.warning(
            "⚠️ לא הוגדרו משתמשים — ראה users.json.example / "
            "generate_employee_accounts.py."
        )
    st.stop()


def render_logout_control() -> None:
    """Small sidebar widget showing who's logged in + a logout button.
    Call after render_login_gate() succeeds."""
    user = current_user()
    if not user:
        return
    _role_label = {"admin": "מנהל מערכת", "manager": "מנהל משמרת", "viewer": "עובד/ת"}.get(
        user["role"], user["role"]
    )
    with st.sidebar:
        st.markdown(
            f'<div dir="rtl" style="font-size:13px;color:rgba(var(--ink-rgb),.74);padding:4px 0;">'
            f'מחובר/ת: <b>{user["name"]}</b> ({_role_label})'
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button("🛂 התנתק", width="stretch", key="logout_btn"):
            st.session_state.pop(SESSION_KEY, None)
            st.rerun()
