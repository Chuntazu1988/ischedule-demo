"""Personal employee screen.

The ONLY thing a non-admin ("viewer" role — one account per real employee)
account can see: a shift-arrival mark button/status, and the tasks assigned
to that specific employee today. Reads the schedule from the shared
published_schedule.xlsx (see publish_state.py) — NOT the admin's in-progress
draft (gantt_data.xlsx gets overwritten on every build, including mid-edit) —
so employees only ever see a schedule an admin explicitly "שגר סידור"'d, and
st.session_state is per-browser-session anyway, so an employee's own session
never has anything built in it regardless (user rule 2026-07-30)."""

import streamlit as st

from arrivals import record_arrival, get_arrival_today
from publish_state import get_published_schedule
from utils.helpers import role_label_for, MALE_VALUES


def render_employee_view(employee_name: str, now) -> None:
    st.markdown(
        f'<div dir="rtl" style="max-width:700px;margin:40px auto 0;padding:28px 32px;'
        f'background:#0f172a;border:1px solid #1e293b;border-radius:16px;">'
        f'<h2 style="color:#fff;margin-bottom:4px;">שלום, {employee_name} 👋</h2>'
        f'<div style="color:#9ca3af;font-size:14px;">{now.strftime("%d/%m/%Y")}</div>'
        f"</div>",
        unsafe_allow_html=True,
    )

    _, col, _ = st.columns([1, 3, 1])
    with col:
        arrived_at = get_arrival_today(employee_name, now)
        if arrived_at:
            st.success(f"✅ סימנת הגעה למשמרת היום ב-{arrived_at}")
        else:
            if st.button("✅ סמן/ני הגעה למשמרת", use_container_width=True, type="primary"):
                t = record_arrival(employee_name, now)
                st.success(f"✅ הגעה נרשמה — {t}")
                st.rerun()

        st.markdown("---")
        st.markdown(
            '<div dir="rtl" style="font-size:18px;font-weight:700;color:#fff;margin:12px 0;">'
            "📋 המשימות שלי היום</div>",
            unsafe_allow_html=True,
        )

        sched = get_published_schedule()
        if sched is None:
            st.info("⏳ הסידור עוד לא הופץ.")
            return

        if "עובד" not in sched.columns:
            st.info("לא ניתן לטעון את הסידור כרגע.")
            return

        mine = sched[sched["עובד"].astype(str).str.strip() == employee_name.strip()]
        if mine.empty:
            st.info("לא נמצאו משימות משובצות עבורך בסידור הנוכחי.")
            return

        # Gender for the role titles below. The published schedule carries only
        # assignments, so fall back to the employees table the admin session
        # loaded; unknown gender keeps the existing (feminine) canonical label.
        _is_male = False
        try:
            _emps = st.session_state.get("employees_df")
            if _emps is not None and not _emps.empty:
                _gcol = next((c for c in _emps.columns
                              if str(c).strip() in {"מין", "gender", "זכר/נקבה", "מגדר"}), None)
                if _gcol:
                    _me = _emps[_emps["שם"].astype(str).str.strip() == employee_name.strip()]
                    if not _me.empty:
                        _is_male = str(_me.iloc[0].get(_gcol, "")).strip().upper() in MALE_VALUES
        except Exception:
            pass

        if "התחלה" in mine.columns:
            mine = mine.sort_values("התחלה")

        for _, row in mine.iterrows():
            _flight = str(row.get("טיסה", "")).strip()
            # The employee reads this about THEMSELVES, so the role title must
            # be in their own gender — ר"צ and טרייני stay as-is for everyone
            # (see role_label_for). The gender comes from the schedule's own
            # employees table when available; without it the neutral canonical
            # label is still shown rather than a wrong-gender one.
            _role = role_label_for(str(row.get("תפקיד בסיס", "")).strip(), _is_male)
            _start = str(row.get("התחלה", "")).strip()
            _end = str(row.get("סיום", "")).strip()
            st.markdown(
                f'<div dir="rtl" style="background:#111827;border:1px solid #1f2937;'
                f'border-radius:10px;padding:12px 16px;margin:6px 0;color:#e5e7eb;">'
                f"<b>{_start}-{_end}</b> &nbsp;|&nbsp; טיסה {_flight} &nbsp;|&nbsp; {_role}"
                f"</div>",
                unsafe_allow_html=True,
            )
