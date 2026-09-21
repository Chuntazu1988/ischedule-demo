
import re
import io
import os
import time as _time_module
from datetime import datetime

import pandas as pd
# Pandas 3.x defaults `future.infer_string=True`, which makes EVERY newly
# created string Series/column (filters, .copy(), comparisons, list->Series)
# default to a pyarrow-backed StringDtype instead of plain object — and every
# .at[]/iterrows() scalar access on that dtype is far slower (found via real
# data 2026-07-24: build_schedule's own assignment loop alone measured 13.5s
# on 87 flights/377 employees, ~4x slower than the same data with this option
# off). Converting specific DataFrames to object dtype at a few entry points
# (_to_native_df, several .astype(object) calls in app/scheduler.py) only
# helps until the NEXT internal filter/copy recreates fresh Arrow-backed
# string data — which happens constantly inside the scheduling loop. Turning
# this off globally, once, at process start restores the old (fast) default
# for every string column created anywhere in the app for the rest of the
# session — the durable fix, not chasing every individual re-creation site.
pd.set_option("future.infer_string", False)
import streamlit as st
import streamlit.components.v1 as _components
import uuid
import json as _json
from app.display import (
    build_next_task_labels,
    build_output_table,
    build_workload,
    build_counter_continuity_rows,
    build_available_in_hall,
    build_unassigned_agents,
    render_flight_card_with_swap,
    to_excel_bytes,
    build_peak_analysis,
    render_peak_analysis,
)
# ============================================================
# DEV CLOCK - רק לבדיקות
# האתר מתחיל תמיד ב־10.05.2026 00:01
# ומתקדם עד 01:00, ואז מתאפס שוב ל־00:01
# לפני גרסה אמיתית לפרודקשן צריך להחזיר DEV_LOOP_CLOCK = False
# ============================================================

DEV_LOOP_CLOCK = False

DEV_CLOCK_START = pd.Timestamp("2026-05-10 00:01")
DEV_CLOCK_RESET_AT_MINUTE = 60        # 01:00
DEV_CLOCK_LOOP_MINUTES = 59           # 00:01 עד לפני 01:00


def app_now():
    if not DEV_LOOP_CLOCK:
        return pd.Timestamp.now()

    # שומרים את רגע תחילת הסשן בזמן אמיתי
    if "_dev_clock_real_start_ts" not in st.session_state:
        st.session_state["_dev_clock_real_start_ts"] = _time_module.time()

    real_elapsed_seconds = int(_time_module.time() - st.session_state["_dev_clock_real_start_ts"])

    # הלולאה היא 59 דקות:
    # 00:01 -> 00:59:59 ואז חזרה ל־00:01
    loop_seconds = DEV_CLOCK_LOOP_MINUTES * 60
    dev_elapsed_seconds = real_elapsed_seconds % loop_seconds

    return DEV_CLOCK_START + pd.Timedelta(seconds=dev_elapsed_seconds)


def app_now_ts():
    return int(app_now().timestamp())

def _is_local_run():
    """True when the app is opened on this machine (the developer), not on the published demo."""
    try:
        host = str(st.context.headers.get("host", "")).lower()
    except Exception:
        return False
    return host.startswith(("localhost", "127.0.0.1", "[::1]"))


if _is_local_run():
    st.warning("DEV SERVER")
# ── Force-reload local modules so code edits take effect without restarting ──
# Streamlit sometimes caches imported modules across reruns; this ensures that
# changes to scheduler.py / data_loader.py / helpers.py are always live.
import importlib as _il, sys as _sys
_LOCAL_PREFIXES = ("app.", "data.", "utils.")
for _mn in [k for k in list(_sys.modules) if any(k.startswith(p) for p in _LOCAL_PREFIXES)]:
    try:
        _il.reload(_sys.modules[_mn])
    except Exception:
        pass

# ── Local modules ─────────────────────────────────────────────────────────────
from app.styles import CSS, HERO_HTML
from utils.constants import (
    USA_TSA_DESTS,
    QUEUE_DESTS,
    TWO_TEAM_LEADS_DESTS,
    ROLE_COLUMNS,
)
from utils.helpers import (
    clean_text,
    safe_html,
    normalize_role_label,
    gender_role_label,
    role_label_for,
    MALE_VALUES,
    is_time_text,
    to_datetime_time,
    time_to_minutes,
    short_flight_number,
    flight_key,
    name_key,
    name_key_reversed,
    classify_shift,
    shift_length,
    break_label_for_employee,
    required_break,
    required_refresh,
    employee_shift_text,
    break_deadline_before_flight,
    preferred_break_window_by_shift,
)
from data.data_loader import (
    build_shift_map_from_excel,
    TWIN_ALT_SUFFIX,
    apply_shift_map_to_employees,
    stagger_transfer_breaks,
    apply_color_transfer_notes,
    merge_t1_shift_map,
    load_daily_schedule,
    normalize_employees,
    parse_fids_combined,
    flights_from_fids,
    fids_flt_key,
    fids_find_src,
    FIDS_COL_MAP,
    parse_shift_manager_home_labels,
    apply_shift_manager_home_labels,
)
from app.scheduler import (
    get_requirements,
    requirements_text,
    build_schedule,
    upgrade_teamleads,
    optimize_tl_continuity,
    fix_wasteful_gaps,
    pair_trainee_attendants,
    consolidate_tsa_inspectors_by_pier,
    backfill_remaining_gaps,
    protect_early_shift_preflight_breaks,
    enforce_trainee_pairing,
    boost_runner_floor_time,
    compact_idle_gaps,
    fill_idle_gaps,
    improve_night_continuity,
    avoid_fresh_start_assignments,
    reserve_dual_certified_for_tsa,
    get_terminal,
    is_within_shift,
    is_night_shift_for_return_rule,
    get_qualified_candidates_for_swap,
    get_extendable_candidates_for_swap,
    schedulable_employees,
    do_swap,
    force_assign_worker,
    role_start_time,
    role_end_time,
    auto_reassign_removed_employee,
    explain_missing_role,
    analyze_tl_peaks,
)

from app.display import (
    build_next_task_labels,
    build_output_table,
    build_counter_continuity_rows,
    build_workload,
    render_flight_card,
    render_flight_card_with_swap,
    build_available_in_hall,
    build_unassigned_agents,
    to_excel_bytes,
    to_departures_report_excel_bytes,
)

# =========================
# PAGE CONFIG
# =========================

st.set_page_config(
    page_title="iSchedule",
    page_icon="👩🏼‍🔧",
    layout="wide",
    initial_sidebar_state="expanded",
)

from auth import render_login_gate, render_logout_control, is_admin, is_super_admin, is_employee_view, current_user
from user_management import render_user_management_panel
render_login_gate()
render_logout_control()

# A non-admin ("viewer") account is one specific employee, not a read-only
# admin — they see ONLY their personal screen (mark arrival + their own
# tasks today), never the dashboard below (user rule 2026-07-26).
if is_employee_view():
    from employee_view import render_employee_view
    _emp_name = (current_user() or {}).get("employee_name", "")
    if not _emp_name:
        st.error("לחשבון זה לא משויך שם עובד/ת (employee_name) — פנה/י למנהל המערכת.")
        st.stop()
    render_employee_view(_emp_name, app_now())
    st.stop()

from arrivals import get_all_arrivals_today, clear_all_arrivals
import publish_state
import schedule_archive
import session_checkpoint
import employee_db
from employee_management import render_employee_admin_panel

# One-time migration: seed the employee DB from the legacy Excel file the
# first time the app runs with this feature — a no-op on every later start
# once the DB has rows, so live admin edits are never clobbered.
employee_db.seed_from_excel_path("employees_clean.xlsx")
# A fresh clone has no private roster: fall back to the anonymised demo employees so the
# "load demo data" button produces a real schedule instead of an empty one.
if not employee_db.is_seeded():
    import os as _os_seed
    employee_db.seed_from_excel_path(_os_seed.path.join(
        _os_seed.path.dirname(_os_seed.path.abspath(__file__)), "demo_data",
        "employee_certifications.xlsx"))

st.markdown(CSS, unsafe_allow_html=True)

# =========================
# LANDING PAGE (shown before files are uploaded)
# =========================

LANDING_PAGE = """
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&family=Heebo:wght@400;700;900&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:"Inter","Heebo",Arial,sans-serif;background:#04080f;overflow-x:hidden;}

#lp{
  min-height:100vh;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  padding:32px 24px 40px;position:relative;overflow:hidden;
  background:radial-gradient(ellipse 80% 60% at 50% 0%,#0a2540 0%,#04080f 70%);
}

#lp::before{
  content:"";position:absolute;inset:0;
  background-image:
    linear-gradient(rgba(0,201,190,.055) 1px,transparent 1px),
    linear-gradient(90deg,rgba(0,201,190,.055) 1px,transparent 1px);
  background-size:60px 60px;
  animation:gridDrift 22s linear infinite;pointer-events:none;
}
@keyframes gridDrift{0%{background-position:0 0;}100%{background-position:60px 60px;}}

.orb{position:absolute;border-radius:50%;filter:blur(90px);pointer-events:none;animation:orbFloat 12s ease-in-out infinite;}
.orb1{width:520px;height:520px;background:radial-gradient(circle,rgba(0,201,190,.16),transparent 70%);top:-160px;left:-110px;animation-duration:14s;}
.orb2{width:400px;height:400px;background:radial-gradient(circle,rgba(5,40,100,.28),transparent 70%);bottom:-90px;right:-90px;animation-duration:10s;animation-delay:-5s;}
.orb3{width:240px;height:240px;background:radial-gradient(circle,rgba(0,180,170,.14),transparent 70%);top:48%;left:66%;animation-duration:16s;animation-delay:-9s;}
@keyframes orbFloat{0%,100%{transform:translate(0,0);}50%{transform:translate(20px,-28px);}}

.lp-logo-wrap{
  position:relative;z-index:2;
  width:210px;height:210px;margin-bottom:8px;
  animation:logoIn .85s cubic-bezier(.2,1,.4,1) both, logoFloat 4s 1s ease-in-out infinite;
}
.lp-logo-wrap::before{
  content:"";
  position:absolute;inset:-12px;border-radius:50%;
  background:conic-gradient(from 0deg, rgba(0,201,190,0), rgba(0,201,190,.5) 40%, rgba(0,180,255,.4) 60%, rgba(0,201,190,0));
  animation:ringRotate 4s linear infinite;
  filter:blur(6px);z-index:-1;
}
.lp-logo-wrap::after{
  content:"";
  position:absolute;inset:-2px;border-radius:50%;
  background:conic-gradient(from 0deg, rgba(0,201,190,0), rgba(0,201,190,.9) 45%, rgba(0,201,190,0));
  animation:ringRotate 4s linear infinite;
  z-index:-1;
}
.lp-logo-wrap img{
  width:100%;height:100%;object-fit:contain;border-radius:50%;
  filter:drop-shadow(0 0 20px rgba(0,201,190,.3));
}
@keyframes logoIn{from{opacity:0;transform:translateY(-28px) scale(.9);}to{opacity:1;transform:translateY(0) scale(1);}}
@keyframes logoFloat{0%,100%{transform:translateY(0);}50%{transform:translateY(-10px);}}
@keyframes ringRotate{from{transform:rotate(0deg);}to{transform:rotate(360deg);}}

.lp-tag{
  position:relative;z-index:2;font-size:12px;font-weight:700;
  letter-spacing:3.5px;text-transform:uppercase;color:#00c9be;
  margin-bottom:30px;animation:fadeUp .7s .25s ease both;
}

.lp-grid{
  position:relative;z-index:2;display:grid;
  grid-template-columns:repeat(3,1fr);gap:12px;
  max-width:710px;width:100%;margin-bottom:34px;
  animation:fadeUp .7s .38s ease both;
}
.lp-card{
  background:rgba(0,201,190,.04);
  border:1px solid rgba(0,201,190,.16);
  border-radius:16px;padding:18px 13px 15px;
  text-align:center;direction:rtl;
  transition:transform .25s,background .25s,border-color .25s,box-shadow .25s;
  cursor:default;position:relative;overflow:hidden;
}
.lp-card-link{ cursor:pointer !important; }
.lp-card::after{
  content:"";position:absolute;inset:0;
  background:radial-gradient(circle at 50% 0%,rgba(0,201,190,.1),transparent 70%);
  opacity:0;transition:opacity .3s;
}
.lp-card:hover{transform:translateY(-7px);border-color:rgba(0,201,190,.42);box-shadow:0 10px 36px rgba(0,201,190,.14);}
.lp-card:hover::after{opacity:1;}
.lp-card-icon{font-size:24px;display:block;margin-bottom:9px;}
.lp-card-title{font-size:12.5px;font-weight:700;color:rgba(255,255,255,.9);}
.lp-card-desc{font-size:11px;color:rgba(255,255,255,.36);margin-top:5px;line-height:1.45;}

.lp-divider{
  position:relative;z-index:2;width:100%;max-width:500px;
  display:flex;align-items:center;gap:14px;margin-bottom:30px;
  animation:fadeUp .7s .48s ease both;
}
.lp-divider span{flex:1;height:1px;background:linear-gradient(90deg,transparent,rgba(0,201,190,.28),transparent);}
.lp-divider p{font-size:10.5px;color:rgba(0,201,190,.55);font-weight:700;letter-spacing:2.5px;white-space:nowrap;}

.lp-cta{
  position:relative;z-index:2;
  animation:fadeUp .7s .58s ease both;
  direction:rtl;text-align:center;font-size:13.5px;
  color:rgba(255,255,255,.3);font-weight:600;
}
.lp-cta span{
  display:inline-flex;align-items:center;gap:9px;
  background:rgba(0,201,190,.09);border:1px solid rgba(0,201,190,.22);
  border-radius:999px;padding:9px 22px;
  animation:ctaPulse 2.8s ease-in-out infinite;
}
@keyframes ctaPulse{0%,100%{box-shadow:0 0 0 0 rgba(0,201,190,0);}50%{box-shadow:0 0 0 9px rgba(0,201,190,.07);}}
.lp-arrow{display:inline-block;animation:arrowBounce 1.6s ease-in-out infinite;}
@keyframes arrowBounce{0%,100%{transform:translateX(0);}50%{transform:translateX(-7px);}}

@keyframes fadeUp{from{opacity:0;transform:translateY(20px);}to{opacity:1;transform:translateY(0);}}

.lp-footer{
  position:absolute;bottom:10px;font-size:9.5px;font-weight:700;
  letter-spacing:2.5px;color:rgba(255,255,255,.07);text-align:center;z-index:2;
}
</style>

<div id="lp">
  <div class="orb orb1"></div>
  <div class="orb orb2"></div>
  <div class="orb orb3"></div>

  <div class="lp-logo-wrap">
    <img src="data:image/jpeg;base64,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" alt="iSchedule" />
  </div>

  <div class="lp-tag">Airport Operations &middot; Smart Scheduling</div>

  <div class="lp-grid">
    <a href="?action=build" target="_parent" style="text-decoration:none;display:contents;"><div class="lp-card lp-card-link"><span class="lp-card-icon">🚀</span><p class="lp-card-title">שיבוץ אוטומטי</p><p class="lp-card-desc">הקצאת עובדים לטיסות לפי הכשרות ומשמרות</p></div></a>
    <a href="?action=gantt" target="_parent" style="text-decoration:none;display:contents;"><div class="lp-card lp-card-link"><span class="lp-card-icon">📅</span><p class="lp-card-title">גאנט עובדים</p><p class="lp-card-desc">ויזואליזציה של כל המשימות ביום</p></div></a>
    <div class="lp-card"><span class="lp-card-icon">🛡️</span><p class="lp-card-title">ניהול TSA</p><p class="lp-card-desc">מפקחים ושומרים לטיסות ארה"ב</p></div>
    <div class="lp-card"><span class="lp-card-icon">⏰</span><p class="lp-card-title">הפסקות חובה</p><p class="lp-card-desc">מעקב הפסקות לפי אורך משמרת</p></div>
    <div class="lp-card"><span class="lp-card-icon">🔄</span><p class="lp-card-title">החלפת עובדים</p><p class="lp-card-desc">גמישות בזמן אמת לשינוי שיבוצים</p></div>
    <div class="lp-card"><span class="lp-card-icon">📊</span><p class="lp-card-title">עומס עובדים</p><p class="lp-card-desc">ניתוח דקות עבודה ועומס כולל</p></div>
  </div>

  <div class="lp-divider"><span></span><p>READY FOR TAKEOFF</p><span></span></div>

  <div class="lp-cta">
    <span><span class="lp-arrow">&#8592;</span> העלי קובץ סידור + עובדים להתחלה</span>
  </div>

  <div class="lp-footer">LY OPS &nbsp;&middot;&nbsp; CHECK-IN &nbsp;&middot;&nbsp; GATES &nbsp;&middot;&nbsp; TSA &nbsp;&middot;&nbsp; BOARDING</div>
</div>
"""

# =========================
# UI
# =========================

# ── גאנט: רק הסתרת UI — הרנדור יקרה לאחר הגדרת הפונקציה ──────────────────
_goto_gantt_early = st.session_state.get("show_gantt_page", False)
if _goto_gantt_early and "schedule_df" in st.session_state:
    if DEV_LOOP_CLOCK:
        st.info(
            f"🧪 מצב בדיקות פעיל: שעון האתר רץ מ־00:01 עד 01:00 "
            f"ומתאפס אוטומטית. עכשיו באתר: {app_now().strftime('%d.%m.%Y %H:%M:%S')}"
        )

        if st.button("🔄 אפס שעון בדיקה ל־00:01"):
            st.session_state["_dev_clock_real_start_ts"] = _time_module.time()
            st.rerun()    
    st.markdown(
        """<style>
    section[data-testid="stSidebar"] { display:none !important; }
    header[data-testid="stHeader"]   { display:none !important; }
    footer                           { display:none !important; }
    #MainMenu                        { display:none !important; }
    [data-testid="stToolbar"]        { display:none !important; }
    [data-testid="stDecoration"]     { display:none !important; }
    .stApp                           { background:#04080f !important; }
    [data-testid="block-container"]  { padding:0 !important; max-width:100% !important; }
    [data-testid="stMain"]           { padding:0 !important; }
    </style>""",
        unsafe_allow_html=True,
    )

# Restore an interrupted in-progress session (browser/server restart without
# ever clicking "⭐ התחל סידור חדש") exactly once per fresh session — a
# checkpoint only exists at all when the previous session never reached that
# button, so this is safe to run unconditionally on a brand-new session.
if (
    "daily_file_obj" not in st.session_state
    and not st.session_state.get("_checkpoint_restore_attempted")
):
    st.session_state["_checkpoint_restore_attempted"] = True
    _cp_payload = session_checkpoint.load_checkpoint()
    if _cp_payload:
        session_checkpoint.restore_into_session_state(st.session_state, _cp_payload)
        st.toast("↩️ שוחזרה העבודה מהסשן הקודם.", icon="↩️")

# Checkpoint the in-progress session at the START of every rerun — this
# captures session_state exactly as the PREVIOUS rerun left it (Streamlit
# preserves session_state across reruns within a session), so it fires after
# every meaningful action (build/edit/manual save) regardless of which tab's
# render path happens to call st.stop() partway through this run. Doing this
# at the end of the script instead would silently miss every branch that
# exits early via st.stop() (several tabs do). Cheap and best-effort.
if st.session_state.get("schedule_df") is not None:
    session_checkpoint.save_checkpoint(st.session_state)

with st.sidebar:
    st.header("📂 העלאת קבצים")
    sidebar_daily = st.file_uploader(
        "קובץ סידור יומי", type=["xlsx"], key="sidebar_daily"
    )
    sidebar_t1 = st.file_uploader(
        "קובץ סידור טרמינל 1 (אופציונלי)", type=["xlsx"], key="sidebar_t1"
    )
    st.markdown("---")
    st.markdown("**📡 קובצי FIDS** (חובה — לוח הטיסות נבנה מהם)")
    sidebar_fids1 = st.file_uploader(
        "קובץ FIDS – יום נוכחי (חובה)", type=None, key="sidebar_fids1"
    )
    sidebar_fids2 = st.file_uploader(
        "קובץ FIDS – יום הבא / אחרי חצות (מומלץ)", type=None, key="sidebar_fids2"
    )
    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
    # ── הצגת קבצים טעונים כרגע ────────────────────────────────────────────
    _loaded_daily = st.session_state.get("_daily_file_name")
    _loaded_t1 = st.session_state.get("_t1_file_name")
    if _loaded_daily or _loaded_t1:
        st.markdown(
            '<div style="direction:rtl;font-size:11px;color:#888;margin-bottom:4px;">קבצים טעונים:</div>',
            unsafe_allow_html=True,
        )
        for _fname in filter(None, [_loaded_daily, _loaded_t1]):
            st.markdown(
                f'<div style="direction:rtl;font-size:11px;background:rgba(0,201,190,.08);'
                f"border-right:3px solid #00c9be;border-radius:4px;padding:3px 7px;margin-bottom:3px;"
                f'color:#0f6e56;font-weight:600;">📄 {_fname}</div>',
                unsafe_allow_html=True,
            )
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
    st.caption("לאחר העלאת הקבצים הדרושים יש ללחוץ על אשר וטען קבצים.")
    sidebar_confirm = st.button(
        "✅ אשר וטען קבצים", use_container_width=True, key="sidebar_confirm"
    )
    # The demo-data loader only exists where demo_data/ ships (the public demo snapshot).
    sidebar_demo = False
    if os.path.isdir(os.path.join(os.path.dirname(__file__), "demo_data")):
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        st.caption("🧪 נתוני דמו — סידור לדוגמה מנתונים מומצאים, לא נתוני עובדים אמיתיים.")
        sidebar_demo = st.button(
            "🧪 טען נתוני דמו", use_container_width=True, key="sidebar_demo",
        )

def _load_demo_data_into_session():
    """
    טוען את חמשת קובצי demo_data/ לתוך session_state, בדיוק כמו העלאה ידנית —
    כך שהמשך הזרימה (בניית הסידור) לא מבחין בין דמו לקבצים אמיתיים.
    משמש גם מכפתור הדמו בסיידבר וגם מזה שבטופס העלאת הקבצים הראשי, כי
    הסיידבר מוסתר כליל (CSS) עד שכל הקבצים הדרושים כבר טעונים — בדיוק
    הרגע שבו כפתור דמו נחוץ ביותר אינו נגיש דרכו (found via real testing
    2026-08-28: the sidebar button existed but was unreachable on first
    login, since section[data-testid="stSidebar"] is display:none until
    daily_file/employees_file/FIDS are all already set).
    """
    _demo_dir = os.path.join(os.path.dirname(__file__), "demo_data")
    _demo_files = {
        "daily": ("daily_roster_terminal3.xlsx", "daily_file_obj", "_daily_file_name"),
        "t1": ("daily_roster_terminal1.xlsx", "t1_file_obj", "_t1_file_name"),
        "emp": ("employee_certifications.xlsx", "employees_file_obj", "_emp_file_name"),
    }
    _demo_missing = [
        fn for fn, _, _ in _demo_files.values()
        if not os.path.exists(os.path.join(_demo_dir, fn))
    ] + [
        fn for fn in ("fids_today.html", "fids_tomorrow.html")
        if not os.path.exists(os.path.join(_demo_dir, fn))
    ]
    if _demo_missing:
        st.error("קובצי הדמו חסרים: " + ", ".join(_demo_missing))
        return
    for _fn, _obj_key, _name_key in _demo_files.values():
        with open(os.path.join(_demo_dir, _fn), "rb") as _f:
            _buf = io.BytesIO(_f.read())
        _buf.name = _fn
        st.session_state[_obj_key] = _buf
        st.session_state[_name_key] = _fn
    for _i, _fn in enumerate(("fids_today.html", "fids_tomorrow.html"), start=1):
        with open(os.path.join(_demo_dir, _fn), "rb") as _f:
            st.session_state[f"fids_file{_i}_bytes"] = _f.read()
        st.session_state[f"fids_file{_i}_name"] = _fn
    st.session_state.pop("fids_applied", None)
    st.session_state.pop("_fids_combined_raw", None)
    st.session_state["show_upload_form"] = False
    st.session_state["_is_demo_data"] = True


if sidebar_demo:
    _load_demo_data_into_session()
    st.rerun()

if sidebar_confirm:
    if (not sidebar_fids1 and not sidebar_fids2
            and not st.session_state.get("fids_file1_bytes")
            and not st.session_state.get("fids_file2_bytes")):
        st.sidebar.error("📡 קובץ FIDS הוא חובה — לוח הטיסות נבנה ממנו.")
    _is_refresh = "daily_file_obj" in st.session_state
    if sidebar_daily:
        st.session_state["daily_file_obj"] = sidebar_daily
        st.session_state["_daily_file_name"] = sidebar_daily.name
    if sidebar_t1:
        st.session_state["t1_file_obj"] = sidebar_t1
        st.session_state["_t1_file_name"] = sidebar_t1.name
    if sidebar_fids1:
        st.session_state["fids_file1_bytes"] = sidebar_fids1.read()
        st.session_state["fids_file1_name"] = sidebar_fids1.name
    else:
        st.session_state.pop("fids_file1_bytes", None)
        st.session_state.pop("fids_file1_name", None)
    if sidebar_fids2:
        st.session_state["fids_file2_bytes"] = sidebar_fids2.read()
        st.session_state["fids_file2_name"] = sidebar_fids2.name
    else:
        st.session_state.pop("fids_file2_bytes", None)
        st.session_state.pop("fids_file2_name", None)
    st.session_state.pop("fids_applied", None)
    st.session_state.pop("_fids_combined_raw", None)
    # סמן רענון רק אם כבר היו קבצים טעונים (לא טעינה ראשונה)
    if _is_refresh:
        st.session_state["_refresh_triggered"] = True
    st.session_state.pop("_refresh_diff", None)


# ── טיפול בפעולות מכפתורי עמוד הנחיתה ───────────────────────────────────
_lp_action = st.query_params.get("action", "")
if _lp_action:
    del st.query_params["action"]
    if _lp_action == "build":
        st.session_state["show_upload_form"] = True
    elif _lp_action == "gantt":
        st.session_state["show_gantt_page"] = True
    st.rerun()

# ── גרירה בגאנט ─────────────────────────────────────────────────────────────
_gantt_swap = st.query_params.get("gantt_swap", "")
if _gantt_swap:
    del st.query_params["gantt_swap"]
    _swap_msg = ""
    try:
        import urllib.parse as _uparse

        _parts = _gantt_swap.split(":", 1)
        _task_idx = int(_parts[0])
        _new_wrkr = _uparse.unquote(_parts[1])
        _sdf = st.session_state.get("schedule_df")
        _emp_snap = st.session_state.get("employees_snap")
        if _sdf is not None and _new_wrkr and "❌" not in _new_wrkr:
            _task_row = _sdf.loc[_task_idx]
            _t_start = str(_task_row.get("התחלה", ""))
            _t_end = str(_task_row.get("סיום", ""))
            _valid = True
            if _emp_snap is not None:
                _flight_num = str(_task_row.get("טיסה", "")).strip()
                _role_base = str(_task_row.get("תפקיד בסיס", "")).strip()
                _candidates = get_qualified_candidates_for_swap(
                    _sdf, _emp_snap, _flight_num, _role_base, _task_idx
                )
                if _new_wrkr not in _candidates:
                    _valid = False
                    # בדוק מדוע לא עובר ולידציה
                    _emp_r = _emp_snap[_emp_snap["שם"] == _new_wrkr]
                    if _emp_r.empty:
                        _reason = "לא נמצא בקובץ העובדים"
                    else:
                        from utils.helpers import classify_shift, is_within_shift, to_datetime_time, is_time_text

                        _er = _emp_r.iloc[0]
                        if is_time_text(_t_start) and is_time_text(_t_end):
                            try:
                                _ts2 = to_datetime_time(_t_start)
                                _te2 = to_datetime_time(_t_end)
                                if not is_within_shift(_er, _ts2, _te2):
                                    _reason = f"מחוץ לשעות המשמרת ({_er.get('תחילת משמרת','?')}–{_er.get('סוף משמרת','?')})"
                                else:
                                    _reason = "אין הסמכה לתפקיד זה או יש חפיפה"
                            except Exception:
                                _reason = "אין הסמכה / חפיפה"
                        else:
                            _reason = "אין הסמכה / חפיפה"
                    _swap_msg = f"⛔ לא ניתן לשבץ {_new_wrkr}: {_reason}"
            if _valid:
                # ── בדיקת רווח גדול בין משימות ──
                def _hm2m(s):
                    try:
                        p = str(s).split(":")
                        return int(p[0]) * 60 + int(p[1])
                    except:
                        return -1

                _new_tasks = _sdf[_sdf["עובד"].astype(str) == _new_wrkr].copy()
                _new_end = _hm2m(_t_end)
                _new_start = _hm2m(_t_start)
                _gap_warn = ""
                for _, _nt in _new_tasks.iterrows():
                    _nt_s = _hm2m(str(_nt.get("התחלה", "")))
                    _nt_e = _hm2m(str(_nt.get("סיום", "")))
                    if _nt_s > 0 and _new_end > 0 and _nt_s > _new_end:
                        _gap = _nt_s - _new_end
                        if _gap > 30:  # רווח > 30 דקות
                            _gap_warn = (
                                f"⚠️ רווח של {_gap} דק׳ בין המשימות של {_new_wrkr}"
                            )
                    if _nt_e > 0 and _new_start > 0 and _new_start > _nt_e:
                        _gap = _new_start - _nt_e
                        if _gap > 30:
                            _gap_warn = (
                                f"⚠️ רווח של {_gap} דק׳ בין המשימות של {_new_wrkr}"
                            )
                st.session_state["schedule_df"] = do_swap(
                    _sdf, _task_idx, _new_wrkr, "unassign"
                )
                _swap_msg = f"✅ שיבוץ עודכן: {_new_wrkr}" + (
                    f" | {_gap_warn}" if _gap_warn else ""
                )
    except Exception as _e:
        _swap_msg = f"שגיאה: {_e}"

    _is_ok = "✅" in _swap_msg
    _color = "#00c9be" if _is_ok else "#ef4444"
    if _swap_msg:
        st.session_state["_gantt_swap_msg"] = _swap_msg
    st.session_state["show_gantt_page"] = True
    st.rerun()

# ── localStorage polling bridge — detects swaps from gantt tab ──────────────
_components.html(
    """<script>
(function(){
  function poll(){
    try{
      var v=localStorage.getItem("gantt_swap_pending");
      if(v){
        localStorage.removeItem("gantt_swap_pending");
        var url=window.top.location.href.split("?")[0]+"?gantt_swap="+v;
        window.top.location.href=url;
        return;
      }
    }catch(e){}
    setTimeout(poll,600);
  }
  poll();
})();
</script>""",
    height=0,
)

daily_file = sidebar_daily or st.session_state.get("daily_file_obj")

# The "תאריך" column in "מי עובד היום" needs a real calendar date to anchor
# "yesterday"/"today"/"tomorrow evening" to — the app otherwise has no date
# concept at all (fully relative). Parsed from the daily roster file's own
# name (e.g. "סידור יומי שלישי 08.09.2026.xlsx" -> 08.09.2026), since that is
# the roster's actual day, not necessarily today's real-world date (user
# rule 2026-09-08: parse from the filename, not app_now()).
_BASE_DATE = None
if daily_file is not None:
    _dfn_m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(getattr(daily_file, "name", "")))
    if _dfn_m:
        try:
            _BASE_DATE = pd.Timestamp(
                year=int(_dfn_m.group(3)), month=int(_dfn_m.group(2)), day=int(_dfn_m.group(1))
            )
        except (ValueError, TypeError):
            _BASE_DATE = None
# קבצי FIDS הם חובה (הנחיית משתמש 2026-07-05) — לוח הטיסות נבנה מהם בלבד
_has_fids = bool(st.session_state.get("fids_file1_bytes")) or bool(
    st.session_state.get("fids_file2_bytes")
)

_goto_gantt = st.session_state.get("show_gantt_page", False)

if not daily_file or not _has_fids:
    import re as _re

    # Default True (not False) — skip the marketing landing page entirely and
    # land straight on the file-upload screen after login (user rule
    # 2026-07-26).
    show_upload = st.session_state.get("show_upload_form", True)
    _goto_gantt = st.session_state.get("show_gantt_page", False)

    # ── Zero-gap dark background ──
    st.markdown(
        """
    <style>
    /* ── Global font — avoid span to preserve Material Symbols icon font ── */
    html, body, p, div, label, h1, h2, h3, h4, button { font-family:'Heebo',sans-serif !important; }
    /* Restore Material Symbols font on icon spans inside buttons */
    button span[aria-hidden="true"],
    button span[class*="icon"] { font-family:'Material Symbols Rounded','Material Symbols Outlined',sans-serif !important; }
    section[data-testid="stSidebar"],
    header[data-testid="stHeader"],
    #MainMenu, footer                         { display:none !important; }
    html, body,
    .stApp,
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"],
    [data-testid="stMainBlockContainer"],
    [data-testid="stVerticalBlock"],
    .main .block-container,
    .block-container                          { background:#04080f !important;
                                                padding:0 !important; margin:0 !important;
                                                max-width:100% !important; gap:0 !important; }
    iframe                                    { display:block !important; border:none !important;
                                                margin:0 !important; padding:0 !important; }
    /* Style action buttons — color/font inherit to children naturally, no * rule needed */
    div[data-testid="stButton"] > button        { background:linear-gradient(120deg,#009e96 0%,#00c9be 100%) !important;
                                                color:#fff !important; font-size:13px !important;
                                                font-weight:800 !important;
                                                padding:10px 20px !important;
                                                min-height:40px !important; height:auto !important;
                                                border-radius:50px !important; border:none !important;
                                                box-shadow:0 6px 24px rgba(0,201,190,.4) !important;
                                                letter-spacing:.8px !important; }
    div[data-testid="stButton"] > button:hover  { box-shadow:0 10px 32px rgba(0,201,190,.6) !important;
                                                transform:translateY(-2px) !important; }
    /* Reset file uploader browse button to Streamlit default */
    div[data-testid^="stFileUploader"] div[data-testid="stButton"] > button
                                                { all:unset !important;
                                                display:inline-flex !important; align-items:center !important;
                                                padding:4px 16px !important; border-radius:4px !important;
                                                font-size:14px !important; font-weight:400 !important;
                                                letter-spacing:normal !important;
                                                cursor:pointer !important; border:1px solid rgba(250,250,250,.2) !important;
                                                color:rgba(250,250,250,.8) !important;
                                                background:transparent !important; }
    /* File uploaders */
    div[data-testid="stFileUploader"]         { background:rgba(0,201,190,.06) !important;
                                                border:1px solid rgba(0,201,190,.28) !important;
                                                border-radius:16px !important; padding:16px 20px !important; }
    div[data-testid="stFileUploader"] label p,
    div[data-testid="stFileUploader"] small   { color:rgba(255,255,255,.88) !important; }
    div[data-testid="stFileUploaderDropzone"] { background:rgba(0,201,190,.04) !important;
                                                border-color:rgba(0,201,190,.3) !important; }
    div[data-testid="stFileUploaderDropzoneInstructions"] small { color:rgba(255,255,255,.5) !important; }
    /* Tab bar — center labels */
    div[data-testid="stTabs"] > div[role="tablist"]             { justify-content:center !important; }
    </style>""",
        unsafe_allow_html=True,
    )

    if _goto_gantt and not show_upload:
        # קבצים לא טעונים — מבקשים טעינה קודם
        st.session_state["show_upload_form"] = True
        st.session_state["show_gantt_page"] = True  # שמור לאחר הטעינה
        st.rerun()

    if not show_upload and not _goto_gantt:
        # ── Hero iframe — logo + grid + divider, no button, compact ──
        _hero_css = """<style>
#lp {
  min-height: unset !important;
  padding: 22px 24px 20px !important;
}
.lp-logo-wrap { width:170px !important; height:170px !important; margin-bottom:5px !important; }
.lp-tag       { margin-bottom:18px !important; font-size:11.5px !important; }
.lp-grid      { margin-bottom:16px !important; gap:9px !important; max-width:660px !important; }
.lp-card      { padding:13px 11px 12px !important; }
.lp-card-icon { font-size:21px !important; margin-bottom:6px !important; }
.lp-divider   { margin-bottom:0 !important; }
.lp-cta, .lp-footer { display:none !important; }
</style>"""
        _components.html(
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
            "<body style='margin:0;padding:0;background:#04080f'>"
            + _hero_css
            + LANDING_PAGE
            + "</body></html>",
            height=590,
            scrolling=False,
        )

        # ── Native Streamlit button — blends with dark bg ──
        st.markdown(
            "<div style='height:48px;background:#04080f'></div>", unsafe_allow_html=True
        )
        _, col_btn, _ = st.columns([1, 1, 1])
        with col_btn:
            if st.button("✈️  Let's Fly", use_container_width=True, key="lf_btn"):
                st.session_state["show_upload_form"] = True
                st.rerun()
        st.markdown(
            "<div style='height:40px;background:#04080f'></div>", unsafe_allow_html=True
        )

    else:
        # ── Mini hero + uploaders ──
        if _goto_gantt:
            st.markdown(
                '<div style="direction:rtl;background:#0d1f30;border:1px solid rgba(0,201,190,.3);'
                'border-radius:12px;padding:14px 18px;margin:12px 0 8px;text-align:right;">'
                '<span style="font-size:15px;font-weight:900;color:#00c9be;">📅 גאנט עובדים</span>'
                '&nbsp;&nbsp;<span style="font-size:13px;color:rgba(200,220,240,.7);">— יש להזין קבצים תחילה</span>'
                "</div>",
                unsafe_allow_html=True,
            )
        _mini_css = """<style>
#lp {
  min-height: unset !important;
  padding: 16px 24px 14px !important;
}
.lp-logo-wrap { width:120px !important; height:120px !important; margin-bottom:4px !important; }
.lp-tag       { margin-bottom:8px !important; font-size:10.5px !important; }
.lp-grid, .lp-divider, .lp-cta, .lp-footer { display:none !important; }
</style>"""
        _components.html(
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
            "<body style='margin:0;padding:0;background:#04080f'>"
            + _mini_css
            + LANDING_PAGE
            + "</body></html>",
            height=185,
            scrolling=False,
        )

        _, col_up, _ = st.columns([1, 2, 1])
        with col_up:
            st.markdown(
                '<div style="text-align:center;color:rgba(0,201,190,.85);'
                "font-size:15px;font-weight:800;margin-bottom:14px;"
                'letter-spacing:1px;">📂 העלאת קבצים</div>',
                unsafe_allow_html=True,
            )
            main_daily = st.file_uploader(
                "📋 קובץ סידור יומי", type=["xlsx"], key="main_daily"
            )
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
            main_t1 = st.file_uploader(
                "🛄 קובץ סידור טרמינל 1 (אופציונלי)", type=["xlsx"], key="main_t1"
            )
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
            main_fids1 = st.file_uploader(
                "📡 קובץ FIDS – יום נוכחי (חובה)", type=None, key="main_fids1"
            )
            st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
            main_fids2 = st.file_uploader(
                "📡 קובץ FIDS – יום הבא / אחרי חצות (מומלץ)",
                type=None,
                key="main_fids2",
            )
            st.markdown("<div style='margin-top:14px'></div>", unsafe_allow_html=True)
            st.caption("לאחר העלאת הקבצים הדרושים יש ללחוץ על אשר וטען קבצים.")
            main_confirm = st.button(
                "✅ אשר וטען קבצים", use_container_width=True, key="main_confirm"
            )

            if main_confirm:
                if not main_fids1 and not main_fids2 and not st.session_state.get("fids_file1_bytes") and not st.session_state.get("fids_file2_bytes"):
                    st.error("📡 קובץ FIDS הוא חובה — לוח הטיסות נבנה ממנו. אנא העלי לפחות את קובץ היום הנוכחי.")
                if main_daily:
                    st.session_state["daily_file_obj"] = main_daily
                if main_t1:
                    st.session_state["t1_file_obj"] = main_t1
                    st.session_state["_t1_file_name"] = main_t1.name
                if main_fids1:
                    st.session_state["fids_file1_bytes"] = main_fids1.read()
                    st.session_state["fids_file1_name"] = main_fids1.name
                else:
                    st.session_state.pop("fids_file1_bytes", None)
                    st.session_state.pop("fids_file1_name", None)
                if main_fids2:
                    st.session_state["fids_file2_bytes"] = main_fids2.read()
                    st.session_state["fids_file2_name"] = main_fids2.name
                else:
                    st.session_state.pop("fids_file2_bytes", None)
                    st.session_state.pop("fids_file2_name", None)
                st.session_state.pop("fids_applied", None)
                st.session_state.pop("_fids_combined_raw", None)

            if os.path.isdir(os.path.join(os.path.dirname(__file__), "demo_data")):
                st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
                st.caption("🧪 נתוני דמו — סידור לדוגמה מנתונים מומצאים, לא נתוני עובדים אמיתיים.")
                if st.button("🧪 טען נתוני דמו", use_container_width=True, key="main_demo"):
                    _load_demo_data_into_session()
                    st.rerun()
    st.stop()

# Restore app chrome for the main app
st.markdown(HERO_HTML, unsafe_allow_html=True)

if "schedule_success_msg" in st.session_state:
    st.success(st.session_state["schedule_success_msg"])


# ── טעינת קבצים עם מטמון — פרסינג האקסלים רץ רק כשהקבצים משתנים ─────────────
# כל מעבר טאב מריץ את הסקריפט כולו; בלי המטמון הזה כל קליק פרסר מחדש את
# כל הגיליונות (כולל סריקות צבע ב-openpyxl) וגרם לאיטיות קשה בין המסכים.
# Per-employee columns a manual "מי עובד היום" edit can change.
_MANUAL_EDIT_COLS = (
    "תחילת משמרת", "סוף משמרת", "טרמינל", "מעבר טרמינל",
    "משמרת לילה הבאה", "משמרת קודמת", "שיוך משמרת",
    "זמינות", "זמינות טרמינל", "זמינות מלאה",
)


def _upload_fingerprint(include_db=True):
    import hashlib as _hl
    _parts = []
    for _tag, _f in (("d", daily_file), ("t", st.session_state.get("t1_file_obj"))):
        if _f is None:
            _parts.append(f"{_tag}:none")
            continue
        try:
            _f.seek(0)
            _b = _f.read()
            _f.seek(0)
            _parts.append(_tag + ":" + str(getattr(_f, "name", "?")) + ":" + str(len(_b)) + ":" + _hl.md5(_b).hexdigest()[:12])
        except Exception:
            _parts.append(_tag + ":" + str(getattr(_f, "name", "?")))
    if include_db:
        _parts.append("e:" + employee_db.db_fingerprint())
    return "|".join(_parts)


_dataload_fp = _upload_fingerprint()
if (st.session_state.get("_dataload_fp") == _dataload_fp
        and "_dataload_employees" in st.session_state):
    flights_df   = st.session_state["_dataload_flights"].copy()
    employees_df = st.session_state["_dataload_employees"].copy()
    shift_map    = st.session_state["_dataload_shift_map"]
else:
    try:
        flights_df = load_daily_schedule(daily_file)
        employees_df = employee_db.load_active_employees_df()
        employees_df = normalize_employees(employees_df)

        employees_df["תפקיד"] = "דייל"

        employees_df.loc[employees_df["ראש צוות"] == "כן", "תפקיד"] = 'ר"צ'

        employees_df.loc[employees_df["מפקח TSA"] == "כן", "תפקיד"] = "מפקח"

        employees_df.loc[employees_df["שומר TSA"] == "כן", "תפקיד"] = "שומר"

        employees_df.loc[employees_df["מתאם תורים"] == "כן", "תפקיד"] = "מתאם"

        shift_map = build_shift_map_from_excel(daily_file)
        daily_file.seek(0)
        # Daily-file color notes must be applied BEFORE merge_t1_shift_map so
        # its TR1 cross-check can drop color-linked transfers for workers who
        # never appear in the TR1 file (the pink note fill is reused for
        # unrelated blocks — see apply_color_transfer_notes/merge_t1_shift_map).
        apply_color_transfer_notes(daily_file, shift_map)
        daily_file.seek(0)

        # ── Terminal 1: merge the TR1 roster file when uploaded ─────────────────
        # T1 flights come from the T1 file's own flights report and are tagged
        # שלוחה="1" (their gates 30-40 also resolve to terminal "1" via FIDS).
        # T1 workers/windows merge into the shift map tagged terminal "1", and
        # color-linked transfer notes are applied per file (fill colors are local
        # to each workbook).
        _t1_file = st.session_state.get("t1_file_obj")
        if _t1_file is not None:
            try:
                _t1_file.seek(0)
                _t1_flights = load_daily_schedule(_t1_file)
                if _t1_flights is not None and not _t1_flights.empty:
                    _t1_flights["שלוחה"] = "1"
                    if "שלוחה" not in flights_df.columns:
                        flights_df["שלוחה"] = ""
                    # T1-file flights override same-number rows from the T3 report
                    _t1_keys = set(_t1_flights["טיסה"].apply(flight_key))
                    flights_df = flights_df[~flights_df["טיסה"].apply(flight_key).isin(_t1_keys)]
                    flights_df = pd.concat([flights_df, _t1_flights], ignore_index=True)
                _t1_file.seek(0)
                _t1_map = build_shift_map_from_excel(_t1_file, terminal="1")
                _t1_file.seek(0)
                apply_color_transfer_notes(_t1_file, _t1_map)
                merge_t1_shift_map(shift_map, _t1_map)
            except Exception as _t1_exc:
                st.warning(f"⚠️ קובץ סידור טרמינל 1 לא נטען: {_t1_exc}")
        daily_file.seek(0)

        employees_df = apply_shift_map_to_employees(employees_df, shift_map)
        # Terminal-transfer workers' pre-transfer break: stagger across the
        # group instead of leaving it to chance (user rule 2026-07-22) —
        # must run right after the shift/terminal fields above are set, and
        # before any scheduling so is_available's "חסימות" check picks it up.
        employees_df = stagger_transfer_breaks(employees_df)
        # A reinforcement worker's real home desk, from the separate "מנהלי
        # משמרות" sheet — feeds return_text_by_shift's "חזרה ל-X" instead
        # of the generic "חזרה לדלפקים" (user rule 2026-09-13).
        try:
            _home_labels = parse_shift_manager_home_labels(daily_file)
            daily_file.seek(0)
            employees_df = apply_shift_manager_home_labels(employees_df, _home_labels, shift_map)
        except Exception:
            pass

        # ── הוסף מנהלי משמרות שמגיעים לתגבר כר"צ ──────────────────────────────
        _existing_names = set(employees_df["שם"].astype(str).str.strip())
        # A manager listed in the managers sheet by a SHORTER name (usually just
        # a first name, e.g. "עידן"/"סטס"/"עמית") is the SAME person as their
        # full-name employees_clean row ("TL#16"/…) and must NOT be added
        # again as a backup — otherwise they show up TWICE in the missing-role
        # diagnostic (user 2026-07-20). The plain `_mname in _existing_names`
        # exact-match check misses this. Match the manager's name words against
        # ר"צ-qualified, floor-schedulable workers only (the backup adds them AS
        # a ר"צ, and concourse-reinforcement "תגבור שלוחה" workers are already
        # off the floor) — that narrows a bare first name enough to identify the
        # single real person; when it maps to EXACTLY ONE such worker, treat the
        # manager as that duplicate and skip. An ambiguous first name matching
        # two attendants is still added rather than wrongly merged.
        _rz_dedup_mask = employees_df["ראש צוות"].astype(str).str.strip() == "כן"
        if "תגבור שלוחה" in employees_df.columns:
            _rz_dedup_mask &= (employees_df["תגבור שלוחה"] != True)
        _existing_word_sets = [
            {name_key(_w) for _w in str(_en).split() if len(_w) > 1}
            for _en in employees_df.loc[_rz_dedup_mask, "שם"].astype(str).str.strip()
        ]
        _mgr_rows = []
        for _mk, _minfo in shift_map.items():
            _msheet = str(_minfo.get("sheet", "")).strip()
            if "מנהל" not in _msheet and "מנהלי משמרת" not in _msheet:
                continue
            _avail = _minfo.get("available_windows", [])
            if not _avail:
                continue
            _mname = str(_minfo.get("original", "")).strip()
            if not _mname:
                continue
            _mname_words = {name_key(_w) for _w in _mname.split() if len(_w) > 1}
            _subset_hits = sum(
                1 for _exw in _existing_word_sets
                if _mname_words and _mname_words <= _exw
            )
            if _mname in _existing_names or _subset_hits == 1:
                continue
            _existing_names.add(_mname)
            for (_ws, _we) in _avail:
                _row = {col: "" for col in employees_df.columns}
                _row["שם"] = _mname
                _row["תפקיד"] = 'ר"צ'
                _row['ראש צוות'] = "כן"
                _row["תחילת משמרת"] = _ws
                _row["סוף משמרת"] = _we
                _row["זמינות"] = f"{_ws}-{_we}"
                _row["_name_key"] = _mk
                _row["_is_manager_backup"] = True
                _row["in_daily_excel"] = 1    # added from the daily Excel's מנהלי משמרות sheet
                _row["upcoming_night"] = 0    # managers are always current-day workers
                _mgr_rows.append(_row)
        if _mgr_rows:
            employees_df = pd.concat(
                [employees_df, pd.DataFrame(_mgr_rows)], ignore_index=True
            )


        st.session_state["_dataload_fp"] = _dataload_fp
        st.session_state["_dataload_flights"] = flights_df.copy()
        st.session_state["_dataload_employees"] = employees_df.copy()
        st.session_state["_dataload_shift_map"] = shift_map
    except Exception as exc:
        st.error("לא הצלחתי לקרוא את הקבצים.")
        st.exception(exc)
        st.stop()

# ── זיהוי שינויים לאחר רענון קבצים ─────────────────────────────────────────
_refresh_triggered = st.session_state.pop("_refresh_triggered", False)
_prev_emp_df = st.session_state.get("_cached_employees_df")
_prev_flt_df = st.session_state.get("_cached_flights_df")

if _refresh_triggered and (_prev_emp_df is not None or _prev_flt_df is not None):
    _diff_lines = []

    # השוואת עובדים
    if (
        _prev_emp_df is not None
        and "שם" in _prev_emp_df.columns
        and "שם" in employees_df.columns
    ):
        _prev_names = set(_prev_emp_df["שם"].astype(str).str.strip())
        _curr_names = set(employees_df["שם"].astype(str).str.strip())
        _added_emp = _curr_names - _prev_names
        _removed_emp = _prev_names - _curr_names
        if _added_emp:
            _detail = "، ".join(sorted(_added_emp)[:4]) + (
                "..." if len(_added_emp) > 4 else ""
            )
            _diff_lines.append(("new", f"{len(_added_emp)} עובד/ת נוסף/ה", _detail))
        if _removed_emp:
            _detail = "، ".join(sorted(_removed_emp)[:4]) + (
                "..." if len(_removed_emp) > 4 else ""
            )
            _diff_lines.append(("del", f"{len(_removed_emp)} עובד/ת הוסר/ה", _detail))
        if not _added_emp and not _removed_emp:
            _diff_lines.append(("same", "עובדים – ללא שינוי", ""))

    # השוואת טיסות
    if (
        _prev_flt_df is not None
        and "טיסה" in _prev_flt_df.columns
        and "טיסה" in flights_df.columns
    ):
        _prev_flights = set(_prev_flt_df["טיסה"].astype(str).str.strip())
        _curr_flights = set(flights_df["טיסה"].astype(str).str.strip())
        _added_flt = _curr_flights - _prev_flights
        _removed_flt = _prev_flights - _curr_flights
        if _added_flt:
            _detail = "، ".join(sorted(_added_flt)[:4]) + (
                "..." if len(_added_flt) > 4 else ""
            )
            _diff_lines.append(("new", f"{len(_added_flt)} טיסה/ות נוספה/ו", _detail))
        if _removed_flt:
            _detail = "، ".join(sorted(_removed_flt)[:4]) + (
                "..." if len(_removed_flt) > 4 else ""
            )
            _diff_lines.append(("del", f"{len(_removed_flt)} טיסה/ות הוסרה/ו", _detail))
        if not _added_flt and not _removed_flt:
            _diff_lines.append(("same", "טיסות – ללא שינוי", ""))

    st.session_state["_refresh_diff"] = _diff_lines

# שמור cache של הנתונים הנוכחיים לשימוש ברענון הבא
st.session_state["_cached_employees_df"] = employees_df.copy()
st.session_state["_cached_flights_df"] = flights_df.copy()

# ── הצגת diff בסיידבר ──────────────────────────────────────────────────────
_diff_to_show = st.session_state.get("_refresh_diff")
if _diff_to_show:
    with st.sidebar:
        st.markdown("---")
        st.markdown("**🔄 שינויים לאחר רענון**")
        for _dtype, _label, _detail in _diff_to_show:
            _color = (
                "#15803d"
                if _dtype == "new"
                else ("#b91c1c" if _dtype == "del" else "#555")
            )
            _icon = "🟢" if _dtype == "new" else ("🔴" if _dtype == "del" else "✅")
            _border = (
                "#15803d"
                if _dtype == "new"
                else ("#b91c1c" if _dtype == "del" else "#ccc")
            )
            _bg = (
                "rgba(21,128,61,.07)"
                if _dtype == "new"
                else ("rgba(185,28,28,.07)" if _dtype == "del" else "rgba(0,0,0,.03)")
            )
            _detail_html = (
                ("<br><span style='font-size:10px;color:#888;'>" + _detail + "</span>")
                if _detail
                else ""
            )
            st.markdown(
                f'<div style="direction:rtl;font-size:12px;color:{_color};margin:3px 0;'
                f'background:{_bg};border-right:3px solid {_border};border-radius:4px;padding:4px 8px;">'
                f"{_icon} <strong>{_label}</strong>{_detail_html}"
                f"</div>",
                unsafe_allow_html=True,
            )
        if st.button("✕ סגור", key="close_refresh_diff", use_container_width=True):
            st.session_state.pop("_refresh_diff", None)
            st.rerun()
if "removed_employees" not in st.session_state:
    st.session_state["removed_employees"] = {}

# ── פילטר עובדים שהוסרו — חייב לרוץ לפני בניית שיבוץ ──────────────────────
_removed_early = set(st.session_state["removed_employees"].keys())
# עריכות ידניות של "מי עובד היום" (טרמינל / שעות / שעת מעבר). הטבלה מיישמת
# עריכה רק בזמן שהיא מוצגת (לשונית "מרכז בקרה"), ואילו כפתורי הבנייה נמצאים
# בלשונית אחרת — בלחיצה עליהם employees_df נטען מחדש מהקבצים בלי העריכות, וגם
# employees_snap נדרס בו (found via real 20.09.2026: four terminal swaps entered
# before the build never reached it). לכן העריכות נשמרות בנפרד ומוחלות כאן,
# בכל ריצה, על אותם קבצים בלבד.
_manual_store = st.session_state.get("_manual_emp_edits")
if _manual_store and _manual_store.get("fp") == _upload_fingerprint(include_db=False):
    for _mn, _mcols in _manual_store.get("rows", {}).items():
        _mm = employees_df["שם"] == _mn
        if _mm.any():
            for _mc, _mv in _mcols.items():
                if _mc not in employees_df.columns:
                    employees_df[_mc] = ""
                employees_df.loc[_mm, _mc] = _mv
_employees_df_all = employees_df.copy()  # שמור לפני הסינון — לשימוש ב-_render_who_works_today
# "מי עובד היום" נבנתה תמיד מ-_employees_df_all, שמקורו בקובץ שהועלה (קפוא,
# לא משתנה אחרי טעינה) — עריכות ידניות בטבלה נשמרות רק ל-employees_snap
# (ע"י כפתור השמירה, ובלייב ע"י בלוק הסנכרון למטה), עותק נפרד לגמרי שהטבלה
# עצמה לא קראה ממנו אף פעם. בפועל זה אומר שהטבלה תמיד מציגה את הנתונים
# המקוריים מחדש בכל rerun, ורק מנגנון המעקב הפנימי השברירי של st.data_editor
# (מבוסס מיקום שורה, hide_index=True) שמר עריכות שטרם נשמרו — וקורס ברגע
# שנעשית עריכה שנייה (באג שהמשתמשת דיווחה עליו 2026-09-08). התיקון: לשקף
# כל עריכה שכבר קיימת ב-employees_snap בחזרה על _employees_df_all, לפי שם,
# כדי שה-DataFrame שמוזן ל-data_editor בכל הרצה כבר יכיל את כל מה שנשמר
# עד כה — לא רק את המקור הקפוא.
if "employees_snap" in st.session_state:
    _snap_overlay = st.session_state["employees_snap"]
    _overlay_cols = [
        c for c in (
            "תחילת משמרת", "סוף משמרת", "טרמינל", "מעבר טרמינל",
            "משמרת לילה הבאה", "משמרת קודמת", "שיוך משמרת",
            "זמינות", "זמינות טרמינל", "זמינות מלאה",
        )
        if c in _snap_overlay.columns
    ]
    if _overlay_cols:
        _snap_by_name = _snap_overlay.set_index("שם")[_overlay_cols]
        _snap_by_name = _snap_by_name[~_snap_by_name.index.duplicated(keep="first")]
        _common = _employees_df_all["שם"].isin(_snap_by_name.index)
        for _c in _overlay_cols:
            if _c not in _employees_df_all.columns:
                _employees_df_all[_c] = ""
            _employees_df_all.loc[_common, _c] = (
                _employees_df_all.loc[_common, "שם"].map(_snap_by_name[_c])
            )
if _removed_early:
    employees_df = employees_df[~employees_df["שם"].isin(_removed_early)].copy()
# תגבור שלוחה — עובד/מנהל שהגיע לתגבר דלפק שלוחה ולא אמור לקבל טיסות ר"צ/רצפה.
# מסונן מהמאגר הניתן לשיבוץ, אך נשאר ב-_employees_df_all כך שהוא עדיין מוצג
# ברשימת "מי עובד היום" (הנחיית משתמש 2026-07-20).
if "תגבור שלוחה" in employees_df.columns:
    employees_df = employees_df[employees_df["תגבור שלוחה"] != True].copy()

@st.dialog("📋 סיכום הורדות ממשמרת", width="large")
def _removal_summary_dialog():
    """פופ-אפ סיכום עובדים שהוסרו ממשמרת."""
    _rem_emps = st.session_state.get("removed_employees", {})
    _rem_times = st.session_state.get("_removal_times", {})
    _rem_tasks = st.session_state.get("_removal_tasks", {})
    if not _rem_emps:
        st.info("אין עובדים שהוסרו ממשמרת.")
        return
    _html = (
        '<div dir="rtl" style="overflow-x:auto">'
        '<table style="width:100%;border-collapse:collapse;text-align:center;font-size:14px;">'
        '<thead><tr style="background:#1e1e2e;color:#ccc;">'
        '<th style="padding:10px 14px;border-bottom:2px solid #444;">שם עובד</th>'
        '<th style="padding:10px 14px;border-bottom:2px solid #444;">שעת הורדה</th>'
        '<th style="padding:10px 14px;border-bottom:2px solid #444;">סיבת הורדה</th>'
        '<th style="padding:10px 14px;border-bottom:2px solid #444;">טיסות שהיה משובץ</th>'
        '</tr></thead><tbody>'
    )
    for _i, (_sname, _sreason) in enumerate(_rem_emps.items()):
        _bg = "#16161f" if _i % 2 == 0 else "#1a1a28"
        _time_str = _rem_times.get(_sname, "—")
        _tl = _rem_tasks.get(_sname, [])
        _ts = " &nbsp;|&nbsp; ".join(_tl) if _tl else "—"
        _html += (
            f'<tr style="background:{_bg};">'
            f'<td style="padding:9px 14px;border-bottom:1px solid #333;">{safe_html(_sname)}</td>'
            f'<td style="padding:9px 14px;border-bottom:1px solid #333;">{_time_str}</td>'
            f'<td style="padding:9px 14px;border-bottom:1px solid #333;">{safe_html(str(_sreason))}</td>'
            f'<td style="padding:9px 14px;border-bottom:1px solid #333;">{_ts}</td>'
            f'</tr>'
        )
    _html += "</tbody></table></div>"
    st.markdown(_html, unsafe_allow_html=True)


def _render_who_works_today():
    """מציג את רשימת העובדים הרשומים להיום עם אפשרות הורדה ממשמרת."""

    # Filter: only employees in today's schedule — use UNFILTERED list so removed employees stay visible.
    # A literal "_name_key in shift_map" check misses anyone whose roster
    # spelling and employees-file spelling differ (e.g. a middle name present
    # on the roster only — real 22.07.2026 data: "worker#9" on the
    # roster vs. employees_clean's "TL#15" — or any of the other
    # spelling-variant cases apply_shift_map_to_employees' own get_entry()
    # already tolerates via fuzzy/subset name matching). That fuzzy match
    # already ran and, on a hit, set "in_daily_excel"=1 on the matched row —
    # use THAT flag instead of re-deriving membership with a cruder check,
    # so anyone the shift data itself was correctly applied to is also shown
    # here (found via user report 2026-09-08: such workers had correct shift
    # times internally but never appeared in "מי עובד היום" at all).
    if "in_daily_excel" in _employees_df_all.columns:
        _today_mask = pd.to_numeric(
            _employees_df_all["in_daily_excel"], errors="coerce"
        ).fillna(0) == 1
    else:
        _today_keys = set(shift_map.keys())
        _today_mask = _employees_df_all["_name_key"].isin(_today_keys)
    _today_employees = _employees_df_all[_today_mask].copy()

    # Sort order (user rule 2026-08-16): the table opens with the workers whose
    # night shift STARTED YESTERDAY and ends this morning — they are the ones on
    # the floor when the day begins. Then today's shifts from 02:00 onward, and
    # last tonight's upcoming-night workers, whose shift has not started yet.
    def _shift_group(row):
        _s = clean_text(str(row.get("תחילת משמרת", "")))
        if not is_time_text(_s):
            return 1
        _night = time_to_minutes(_s) >= 20 * 60
        try:
            _upcoming = int(row.get("upcoming_night", 0) or 0) == 1
        except (TypeError, ValueError):
            _upcoming = False
        if _night and _upcoming and not clean_text(str(row.get("משמרת לילה הבאה", ""))):
            return 2        # tonight's night shift — hasn't begun yet
        if _night:
            return 0        # yesterday's overnight — already on shift
        return 1

    # Sort: primary = start from 02:00, secondary = end time
    def _start_sort(s):
        s = str(s).strip()
        if not is_time_text(s):
            return 9999
        return (time_to_minutes(s) - 2 * 60) % (24 * 60)

    def _end_sort(s):
        s = str(s).strip()
        if not is_time_text(s):
            return 9999
        return (time_to_minutes(s) - 2 * 60) % (24 * 60)

    # דדופליקציה לפי שם — עובד עלול להופיע פעמיים אם _name_key כפול (קדמי + הפוך)
    _today_employees = _today_employees.drop_duplicates(subset=["שם"], keep="first")

    # ── עמודת טרמינל ─────────────────────────────────────────────────────────
    # מציגה היכן העובד/ת עובד/ת: 3, 1, או מעבר במהלך המשמרת (3+1 / 1+3).
    # ניתנת לעריכה ידנית — כך אפשר להעביר עובד/ת בין הטרמינלים כדי לכסות
    # חוסר (סיק), והבנייה הבאה תכבד את השינוי.
    _TERMINAL_OPTIONS = ["3", "1", "3+1", "1+3"]

    def _avail_windows_sorted(row, shift=None):
        """
        [(start_minutes, window_text, terminal), ...] בסדר יום תפעולי.

        shift=None → כל החלונות. shift=0/1 → רק חלונות המשמרת הזאת, לפי
        עמודת "שיוך משמרת" (0 = המשמרת הראשית, 1 = הלילה שמתחיל הערב).
        """
        _wins = [w.strip() for w in clean_text(str(row.get("זמינות", ""))).split(",") if w.strip()]
        _terms = [t.strip() for t in clean_text(str(row.get("זמינות טרמינל", ""))).split(",")]
        _shifts = [t.strip() for t in clean_text(str(row.get("שיוך משמרת", ""))).split(",")]
        _out = []
        for _i, _w in enumerate(_wins):
            if "-" not in _w:
                continue
            _s = _w.split("-", 1)[0].strip()
            if not is_time_text(_s):
                continue
            if shift is not None and _shifts and any(_shifts):
                _sh = _shifts[_i] if _i < len(_shifts) and _shifts[_i] else "0"
                if _sh != str(shift):
                    continue
            _t = _terms[_i] if _i < len(_terms) and _terms[_i] else "3"
            _out.append(((time_to_minutes(_s) - 3 * 60) % 1440, _w, "1" if _t == "1" else "3"))
        return sorted(_out)

    def _terminal_label_for(row, shift=None):
        """
        סדר העדיפויות (אושר מול נתוני 11.08):

        1. חלונות הזמינות, כשהם מכילים שני טרמינלים — אז העובד/ת מופיע/ה בשני
           הסידורים והחלונות עצמם מספרים את הכיוון. queue-coord#1/agent#34/agent#55/
           agent#58: 09:30-17:30 בטרמינל 1 ואז 14:00-17:30 בטרמינל 3 → 1+3.
        2. אחרת הערת המעבר, כי לרוב עובדי המעבר אין חלון זמינות לצד הראשון של
           המשמרת כלל — agent#22: כל חלונותיה בטרמינל 1, וההערה
           "המשך מש' בטרמינל 1 החל מ01:00" היא מה שמגלה שלפני כן היא בטרמינל 3.
        3. אחרת טרמינל הבסיס.
        """
        _seq = []
        for _, _w, _t in _avail_windows_sorted(row, shift):
            if not _seq or _seq[-1] != _t:
                _seq.append(_t)
        if len(_seq) >= 2 and _seq[0] != _seq[-1]:
            return f"{_seq[0]}+{_seq[-1]}"
        if len(_seq) >= 3:
            # יצא-וחזר באותו יום תפעולי (TL#41: ט3 הלילה → ט1 ב-01:00 →
            # ט3 שוב בבוקר, כי הבוקר שייך למשמרת של אתמול). "3+3" חסר משמעות,
            # אז ההערה קובעת את הכיוון.
            _tr_rt = clean_text(str(row.get("מעבר טרמינל", "")))
            if "@" in _tr_rt:
                _to_rt = "1" if _tr_rt.split("@", 1)[0].strip() == "1" else "3"
                return f'{"3" if _to_rt == "1" else "1"}+{_to_rt}'

        _tr = clean_text(str(row.get("מעבר טרמינל", "")))
        if "@" in _tr:
            _to = "1" if _tr.split("@", 1)[0].strip() == "1" else "3"
            # הערת המעבר שייכת למשמרת אחת בלבד. כשלעובד/ת יש שתי משמרות
            # (התיוג ב"שיוך משמרת"), אסור להחיל אותה על המשמרת שהחלונות שלה
            # אינם מגיעים לטרמינל היעד — TL#41 עובר לט1 רק בלילה שמתחיל
            # היום, ומשמרת אתמול שלו כולה ט3 (הערת המשתמשת 2026-08-21:
            # "בפועל מופיע שבשתי המשמרות הוא עובר לטרמינל 1").
            _tags = [t.strip() for t in clean_text(str(row.get("שיוך משמרת", ""))).split(",")]
            _two_shifts = "1" in _tags
            if not (shift is not None and _two_shifts and _to not in {t for _, _w, t in _avail_windows_sorted(row, shift)}):
                # הערה ללא שעה ("worker#5 -טרמינל 1") = כל המשמרת בטרמינל היעד,
                # לא מעבר באמצעה (restricted-agent#3: 22:00-01:30, כולה ט1).
                if not _tr.split("@", 1)[1].strip():
                    return _to
                return f'{"3" if _to == "1" else "1"}+{_to}'
        if _seq:
            return _seq[0]
        return "1" if clean_text(str(row.get("טרמינל", ""))) == "1" else "3"

    def _transfer_time_for(row, shift=None):
        """
        שעת המעבר של המשמרת הזאת, או '' אם אין בה מעבר.

        נגזרת מהתווית ולא מהחלונות: אצל רוב עובדי המעבר החלק הראשון של
        המשמרת כלל אינו מיוצג כחלון זמינות (TL#40: שני החלונות ט1, והצד
        של ט3 לפני 01:00 חסר), ולכן דרישה לשני טרמינלים בחלונות הסתירה את
        השעה בדיוק במקרים שבהם התווית כן הראתה 3+1.
        """
        _tr = clean_text(str(row.get("מעבר טרמינל", "")))
        if "@" not in _tr:
            return ""
        if shift is not None and "+" not in _terminal_label_for(row, shift):
            return ""
        return _tr.split("@", 1)[1].strip()

    def _apply_terminal_choice(frame, name, choice, at_time="", shift=0):
        """
        כתיבת בחירה ידנית חזרה לעמודות שהשיבוץ קורא (טרמינל / זמינות /
        זמינות טרמינל / מעבר טרמינל). מחזיר הודעת אזהרה או ''.

        עקיפה ידנית בונה את הזמינות מחדש מתוך שעות המשמרת: חלון אחד לכל
        המשמרת בטרמינל שנבחר, או שני חלונות סביב שעת המעבר. כך התוצאה צפויה
        וגם עובד/ת עם חלון יחיד יכול/ה לקבל מעבר.
        """
        _m = frame["שם"] == name
        if not _m.any():
            return ""
        _idx = frame.index[_m][0]
        _row = frame.loc[_idx]
        _ss = clean_text(str(_row.get("תחילת משמרת", "")))
        _se = clean_text(str(_row.get("סוף משמרת", "")))
        if not (is_time_text(_ss) and is_time_text(_se)):
            return f"{name}: אין שעות משמרת תקינות, לא ניתן לעדכן טרמינל."

        # החלונות של המשמרת האחרת נשארים כפי שהם — רק המשמרת שנערכה נבנית
        # מחדש. בלי זה עריכה בשורה אחת הייתה מוחקת את המשמרת השנייה.
        _all_w = [w.strip() for w in clean_text(str(_row.get("זמינות", ""))).split(",") if "-" in w]
        _all_t = [t.strip() for t in clean_text(str(_row.get("זמינות טרמינל", ""))).split(",")]
        _all_s = [t.strip() for t in clean_text(str(_row.get("שיוך משמרת", ""))).split(",")]
        _has_tags = bool(_all_s) and any(_all_s)
        _other = [
            (_w, _all_t[_i] if _i < len(_all_t) else "3", _all_s[_i] if _i < len(_all_s) else "0")
            for _i, _w in enumerate(_all_w)
            if _has_tags and (_all_s[_i] if _i < len(_all_s) else "0") != str(shift)
        ]

        def _commit(_new_wins, _new_terms, _base):
            _wins = [w for w, _, _ in _other] + _new_wins
            _terms = [t for _, t, _ in _other] + _new_terms
            _tags = [g for _, _, g in _other] + [str(shift)] * len(_new_wins)
            frame.at[_idx, "זמינות"] = ",".join(_wins)
            frame.at[_idx, "זמינות טרמינל"] = ",".join(_terms)
            if "שיוך משמרת" in frame.columns or _has_tags:
                frame.at[_idx, "שיוך משמרת"] = ",".join(_tags)
            if not shift:
                frame.at[_idx, "טרמינל"] = _base

        if "+" not in choice:
            _commit([f"{_ss}-{_se}"], [choice], choice)
            if "מעבר טרמינל" in frame.columns and not _other:
                frame.at[_idx, "מעבר טרמינל"] = ""
            return ""

        _first, _last = choice.split("+", 1)
        _at = clean_text(at_time) or _transfer_time_for(_row)
        if not is_time_text(_at):
            return (f"{name}: כדי לקבוע מעבר {choice} יש להזין שעת מעבר בעמודה "
                    f'"שעת מעבר" (פורמט HH:MM). הטרמינל לא שונה.')
        # שעת המעבר חייבת ליפול בתוך המשמרת
        _sm, _em, _am = time_to_minutes(_ss), time_to_minutes(_se), time_to_minutes(_at)
        _span = (_em - _sm) % 1440 or 1440
        if not (0 < (_am - _sm) % 1440 < _span):
            return (f"{name}: שעת המעבר {_at} אינה בתוך המשמרת {_ss}-{_se}. "
                    f"הטרמינל לא שונה.")
        _commit([f"{_ss}-{_at}", f"{_at}-{_se}"], [_first, _last], _first)
        if "מעבר טרמינל" in frame.columns:
            frame.at[_idx, "מעבר טרמינל"] = f"{_last}@{_at}"
        return ""

    _preview_cols = [
        c for c in ["שם", "תחילת משמרת", "סוף משמרת"] if c in _today_employees.columns
    ]
    # ── שורה אחת לכל משמרת ───────────────────────────────────────────────────
    # עובד/ת יכול/ה להחזיק שתי משמרות באותו יום תפעולי: משמרת לילה שהתחילה
    # אתמול ומסתיימת הבוקר, ובנוסף משמרת הלילה שמתחילה הערב (עמוד 1 מול הפס
    # התחתון). השתיים נראות זהות על השעון, ולכן העמודה "משמרת לילה הבאה"
    # מזהה את השנייה. כל שורה נושאת מפתח (שם, אינדקס משמרת) כדי שהעריכה תדע
    # לאיזו משמרת היא שייכת.
    _SECOND_SHIFT_COL = "משמרת לילה הבאה"
    _PREV_SHIFT_COL = "משמרת קודמת"

    def _shift_rows_for(row):
        """
        [(shift_index, start, end), ...] לפי סדר הזמן:
        -1 = משמרת אתמול שעדיין נמשכת הבוקר, 0 = המשמרת הראשית,
        1+ = משמרת הלילה שמתחילה הערב.
        """
        _out = []
        for _w in (w for w in clean_text(str(row.get(_PREV_SHIFT_COL, ""))).split(",") if "-" in w):
            _ws, _we = _w.split("-", 1)
            _out.append((-1, _ws.strip(), _we.strip()))
        _out.append((0, clean_text(str(row.get("תחילת משמרת", ""))),
                     clean_text(str(row.get("סוף משמרת", "")))))
        for _i, _w in enumerate(w for w in clean_text(str(row.get(_SECOND_SHIFT_COL, ""))).split(",") if "-" in w):
            _ws, _we = _w.split("-", 1)
            _out.append((_i + 1, _ws.strip(), _we.strip()))
        return _out

    _rows, _src_rows, _shift_idx = [], [], []
    for _, _er in _today_employees.iterrows():
        for _si, _ss_v, _se_v in _shift_rows_for(_er):
            _r2 = _er.copy()
            _r2["תחילת משמרת"] = _ss_v
            _r2["סוף משמרת"] = _se_v
            _rows.append({_c: _r2.get(_c, "") for _c in _preview_cols})
            _src_rows.append(_r2)
            _shift_idx.append(_si)

    _display_df = pd.DataFrame(_rows, columns=_preview_cols)
    _today_employees = pd.DataFrame(_src_rows).reset_index(drop=True)
    _display_df["_shift_i"] = _shift_idx
    _display_df["טרמינל"] = [
        _terminal_label_for(_r, _si)
        for (_, _r), _si in zip(_today_employees.iterrows(), _shift_idx)
    ]
    # תאריך תחילת המשמרת של השורה הזאת (לא "היום" הכללי) — עובד/ת עם שתי
    # משמרות באותו יום תפעולי (למשל 21:00-07:00 שהתחילה אתמול בערב, ובנוסף
    # משמרת ערב/לילה נפרדת שמתחילה היום) נראה זהה בטבלה בלי זה: שתי שורות
    # עם אותה שעה על השעון, אין דרך להבדיל ביניהן (בקשת המשתמשת 2026-09-08).
    # shift_i: -1 = משמרת אתמול (bottom-of-page-1), 0 = המשמרת הראשית (יכולה
    # גם היא להיות המשך של אתמול אם is_page1), 1+ = משמרת נוספת שמתחילה הערב.
    if _BASE_DATE is not None:
        def _shift_row_date(_r, _si):
            try:
                _is_p1 = int(_r.get("is_page1", 0) or 0) == 1
            except (TypeError, ValueError):
                _is_p1 = False
            if _si == -1 or (_si == 0 and _is_p1):
                return _BASE_DATE - pd.Timedelta(days=1)
            return _BASE_DATE

        _display_df.insert(
            _display_df.columns.get_loc("שם") + 1,
            "תאריך",
            [
                _shift_row_date(_r, _si).strftime("%d.%m")
                for (_, _r), _si in zip(_today_employees.iterrows(), _shift_idx)
            ],
        )
    # A multi-terminal row ("1+3") is one continuous shift split across two
    # terminals — "תחילת משמרת"/"סוף משמרת" must show the FULL span (earliest
    # start to latest end across the base entry AND every availability
    # window), not just the base entry's own start/end (found via real
    # 08.09.2026 data: queue-coord#1, base 09:30-17:30 T1 then T3 from 14:00,
    # showed only "14:00-17:30" — the base entry only ever covers ONE side).
    #
    # An earlier version of this fix took min/max straight off the
    # availability windows alone, which broke two other real ways a "+"
    # label can arise:
    #   - TL#41: base 21:00-06:00 (T3), but his ONLY windows are both
    #     tagged T1 (01:00-06:00 / 01:30-06:00) — the T3 half before the
    #     transfer isn't its own window at all (it's implied by the base
    #     entry + transfer note), so reading windows alone lost the real
    #     21:00 start entirely, showing 01:00/01:30 instead.
    #   - TL-trainee#5: 3 windows (21:00-00:30, 19:00-00:30, 01:00-07:00) whose
    #     own sort pivot (03:00) ranks 07:00 as "earlier" than 00:30 (it
    #     treats anything past 03:00 as a fresh new operational day) — so
    #     naively taking the max end by that pivot picked 00:30 over the
    #     true-latest 07:00.
    #
    # Fixed by anchoring every candidate (base entry + all windows) to the
    # base start's own clock position and walking each forward by whole
    # days until it lands within 12h of that anchor — no fixed pivot, so a
    # shift spanning any real number of hours in either direction from its
    # own start is handled correctly.
    if "תחילת משמרת" in _display_df.columns and "סוף משמרת" in _display_df.columns:
        for _i, ((_, _r), _si) in enumerate(zip(_today_employees.iterrows(), _shift_idx)):
            if "+" not in str(_display_df.at[_i, "טרמינל"]):
                continue
            _base_s = clean_text(str(_display_df.at[_i, "תחילת משמרת"]))
            _base_e = clean_text(str(_display_df.at[_i, "סוף משמרת"]))
            if not is_time_text(_base_s) or not is_time_text(_base_e):
                continue
            _anchor = time_to_minutes(_base_s)
            _wins = _avail_windows_sorted(_r, _si)
            _cands = [(_base_s, _base_e)] + [
                tuple(_w[1].split("-", 1)) for _w in _wins if "-" in _w[1]
            ]
            _best_s, _best_s_m = _base_s, _anchor
            _best_e, _best_e_m = _base_e, None
            for _cs, _ce in _cands:
                if not is_time_text(_cs) or not is_time_text(_ce):
                    continue
                _s_m = time_to_minutes(_cs)
                # snap to the occurrence nearest the anchor (whole-day steps)
                _s_m += round((_anchor - _s_m) / 1440) * 1440
                _e_m = time_to_minutes(_ce)
                if _e_m < _s_m:
                    _e_m += 1440
                if _s_m < _best_s_m:
                    _best_s, _best_s_m = _cs, _s_m
                if _best_e_m is None or _e_m > _best_e_m:
                    _best_e, _best_e_m = _ce, _e_m
            _display_df.at[_i, "תחילת משמרת"] = _best_s
            _display_df.at[_i, "סוף משמרת"] = _best_e
    _display_df["שעת מעבר"] = [
        _transfer_time_for(_r, _si)
        for (_, _r), _si in zip(_today_employees.iterrows(), _shift_idx)
    ]
    # החלפה ידנית: הקלדת שם עובד/ת אחר/ת מעבירה אליו/ה את כל המשימות של
    # השורה הזאת. נשאר ריק אחרי הביצוע כדי שאפשר יהיה להחליף שוב.
    _display_df["החלף ב־"] = ""
    if "תחילת משמרת" in _display_df.columns:
        if _BASE_DATE is not None:
            # With a real calendar date per row (see "תאריך" above), a plain
            # (date, clock-time) ascending sort IS the correct chronological
            # order — a page-1 night tail (dated yesterday) always sorts
            # before ANY of today's shifts regardless of its own clock time,
            # and a same-day evening shift naturally sorts after that day's
            # earlier ones, with no need for the old _grp/_start_sort pivot
            # heuristics below (which only ever approximated this using
            # clock time alone and had no way to tell "started yesterday
            # evening" apart from "starts this evening, same clock hour" —
            # found via real 08.09.2026 data: a fresh today-18:00 shift
            # sorted BEFORE a yesterday-21:00 night tail, even though the
            # night tail is chronologically earlier).
            def _mins(_v):
                _v = clean_text(str(_v))
                return time_to_minutes(_v) if is_time_text(_v) else 9999

            _display_df["_dt"] = [
                _shift_row_date(_r, _si).toordinal()
                for (_, _r), _si in zip(_today_employees.iterrows(), _display_df["_shift_i"])
            ]
            _display_df["_ss"] = _display_df["תחילת משמרת"].apply(_mins)
            _display_df["_se"] = (
                _display_df["סוף משמרת"].apply(_mins)
                if "סוף משמרת" in _display_df.columns
                else 9999
            )
            _display_df = _display_df.sort_values(["_dt", "_ss", "_se"]).drop(
                columns=["_dt", "_ss", "_se"]
            )
        else:
            _display_df["_grp"] = [
                0 if _si == -1 else (2 if _si else _shift_group(_r))
                for (_, _r), _si in zip(_today_employees.iterrows(), _display_df["_shift_i"])
            ]
            # Within yesterday's overnight group the clock order IS the shift order
            # (21:00 before 22:00 before 23:30), so it must not be pivoted at 02:00.
            _display_df["_ss"] = [
                time_to_minutes(clean_text(str(_v))) if _g == 0 and is_time_text(clean_text(str(_v)))
                else _start_sort(_v)
                for _v, _g in zip(_display_df["תחילת משמרת"], _display_df["_grp"])
            ]
            _display_df["_se"] = (
                _display_df["סוף משמרת"].apply(_end_sort)
                if "סוף משמרת" in _display_df.columns
                else 9999
            )
            _display_df = _display_df.sort_values(["_grp", "_ss", "_se"]).drop(
                columns=["_grp", "_ss", "_se"]
            )
    _row_shift_idx = list(_display_df["_shift_i"])
    _display_df = _display_df.drop(columns=["_shift_i"])
    _display_df = _display_df.reset_index(drop=True)

    # הגעה למשמרת: מסומנת ע"י העובד/ת עצמו/ה במסך האישי שלו/ה (auth.py role
    # "viewer" -> employee_view.py) ונשמרת בקובץ משותף (arrivals.py) — לא
    # ב-session_state, כי כל משתמש/ת מחובר/ת בסשן נפרד משלו/ה (הנחיית משתמש
    # 2026-07-26: להציג את מי שהגיע/ה כאן, בתוך "מי עובד היום", ולא בפאנל
    # נפרד).
    _today_arrivals = get_all_arrivals_today(app_now())
    _display_df.insert(
        0,
        "הגיע/ה למשמרת",
        _display_df["שם"].map(lambda n: f"✅ {_today_arrivals[n]}" if n in _today_arrivals else "—"),
    )

    # הורדה column: ➕ = active, reason text = removed
    _REMOVAL_OPTIONS = ["➕", "Sick", "סיק במשפחה", "סיק במשמרת", "כח אדם"]
    _display_df.insert(
        0,
        "הורדה ממשמרת",
        _display_df["שם"].map(
            lambda n: st.session_state["removed_employees"].get(n, "➕")
        ),
    )

    # st.data_editor, given a BRAND-NEW DataFrame object on every rerun (as
    # _display_df always is here — freshly rebuilt from source data, not a
    # stable cached object), does not reliably accumulate edits across
    # separate commits: after editing one cell and committing it (moving
    # focus away, which triggers a rerun), editing a SECOND cell makes the
    # FIRST one revert to whatever _display_df says right now — not what
    # was just typed. Confirmed 2026-09-08 on both a text column (תחילת
    # משמרת) and a selectbox column (הורדה ממשמרת), the latter despite
    # already round-tripping through session_state on every rerun before
    # today — ruling out "not persisted" as the sole cause and pointing at
    # the widget's own edit-tracking itself. Standard Streamlit fix: read
    # the widget's own recorded edits (st.session_state[key]["edited_rows"])
    # and reapply them onto the freshly-built data BEFORE re-rendering, so
    # what we feed the widget never disagrees with what it already knows it
    # recorded — it never has a "conflict" to silently resolve by dropping
    # the earlier edit.
    # Reading Streamlit's own st.session_state["shift_hours_editor"]
    # ["edited_rows"] alone (a plain reapply-what-it-currently-holds
    # approach) turned out NOT to be enough on its own: a user reported
    # setting "טרמינל" to "3+1" (accepted and shown), then typing "שעת
    # מעבר" — after which "טרמינל" itself reverted to its original value.
    # Two edits to the SAME row, on two DIFFERENT commits, can still lose
    # the earlier one even with reapplication, which means Streamlit's own
    # edited_rows bookkeeping is not reliably ACCUMULATING every edit
    # across separate commits in the first place — reapplying an already-
    # incomplete dict just reapplies the gap. Keep our OWN sticky
    # accumulator instead, merging (never replacing) every edited_rows
    # entry we ever see into it, keyed by (שם, shift_i) rather than raw row
    # POSITION — position is not stable across reruns whenever an edit
    # (e.g. "תחילת משמרת") can itself change the row's sort bucket.
    _sticky = st.session_state.setdefault("_shift_editor_sticky", {})
    _editor_prior = st.session_state.get("shift_hours_editor", {}) or {}
    _name_col_i = _display_df.columns.get_loc("שם")
    for _ridx, _changes in (_editor_prior.get("edited_rows") or {}).items():
        if 0 <= _ridx < len(_display_df):
            _rkey = (
                clean_text(str(_display_df.iat[_ridx, _name_col_i])),
                _row_shift_idx[_ridx] if _ridx < len(_row_shift_idx) else 0,
            )
            _sticky.setdefault(_rkey, {}).update(_changes)
    for _ridx in range(len(_display_df)):
        _rkey = (
            clean_text(str(_display_df.iat[_ridx, _name_col_i])),
            _row_shift_idx[_ridx] if _ridx < len(_row_shift_idx) else 0,
        )
        for _col, _val in _sticky.get(_rkey, {}).items():
            if _col in _display_df.columns:
                _display_df.iat[_ridx, _display_df.columns.get_loc(_col)] = _val

    _edited = st.data_editor(
        _display_df,
        use_container_width=True,
        num_rows="fixed",
        hide_index=True,
        key="shift_hours_editor",
        column_config={
            "הורדה ממשמרת": st.column_config.SelectboxColumn(
                "הורדה ממשמרת",
                options=_REMOVAL_OPTIONS,
                default="➕",
                width="small",
                help="לחץ להורדת עובד ממשמרת",
            ),
            "הגיע/ה למשמרת": st.column_config.TextColumn(
                "הגיע/ה למשמרת", disabled=True, width="small",
                help="מסומן ע\"י העובד/ת במסך האישי שלו/ה",
            ),
            "שם": st.column_config.TextColumn("שם", disabled=True, width="medium"),
            "תחילת משמרת": st.column_config.TextColumn(
                "תחילת משמרת", width="small", help="פורמט HH:MM"
            ),
            "סוף משמרת": st.column_config.TextColumn(
                "סוף משמרת", width="small", help="פורמט HH:MM"
            ),
            "טרמינל": st.column_config.SelectboxColumn(
                "טרמינל",
                options=_TERMINAL_OPTIONS,
                width="small",
                help='3 / 1 = כל המשמרת באותו טרמינל. 3+1 = מתחיל/ה ב-3 ועובר/ת ל-1 '
                     'במהלך המשמרת, 1+3 = להפך. ניתן לשנות ידנית כדי להעביר '
                     'עובד/ת בין הטרמינלים ולכסות חוסר.',
            ),
            "החלף ב־": st.column_config.TextColumn(
                "החלף ב־", width="medium",
                help="הקלד/י שם של עובד/ת אחר/ת כדי להעביר אליו/ה את כל המשימות "
                     "של השורה הזאת. השם חייב להופיע בקובץ העובדים.",
            ),
            "שעת מעבר": st.column_config.TextColumn(
                "שעת מעבר", width="small",
                help='שעת המעבר בין הטרמינלים (HH:MM) — רלוונטי רק ל-3+1 / 1+3. '
                     'ניתן לשנות ידנית; השעה חייבת ליפול בתוך המשמרת.',
            ),
        },
    )

    # Sync edits → removal state + shift times in employees_df AND
    # employees_snap. Writing only to the local employees_df (as this used
    # to) is thrown away at the top of the NEXT rerun (module-scope
    # employees_df is rebuilt fresh from the frozen upload snapshot every
    # time) — edits only ever appeared to persist via st.data_editor's own
    # fragile row-position-based tracking, which breaks the moment a SECOND
    # cell is edited (user-reported 2026-09-08: the first typed value
    # disappears as soon as a second edit is made, before any save click).
    # Writing straight into employees_snap here — the same session_state
    # copy the save button already writes to, and that the overlay above
    # now reads back into _employees_df_all on every render — makes every
    # edit durable immediately, independent of the widget's own bookkeeping.
    _snap_live = st.session_state.get("employees_snap")
    if _edited is not None:
        _new_removed = {}
        _term_warnings = []
        _swaps = []          # (מוחלף, מחליף, אינדקס משמרת)
        for (_, _r), _si in zip(_edited.iterrows(), _row_shift_idx):
            _swap_to = clean_text(str(_r.get("החלף ב־", "")))
            if _swap_to:
                _swaps.append((clean_text(str(_r["שם"])), _swap_to, _si))
            _n = _r["שם"]
            _reason = str(_r.get("הורדה ממשמרת", "➕")).strip()
            if _reason and _reason != "➕":
                _new_removed[_n] = _reason
            _m = employees_df["שם"] == _n
            _m_snap = (_snap_live["שם"] == _n) if _snap_live is not None else None
            if _m.any() and _si:
                # שורת משמרת נוספת (אתמול או הלילה הבא): שעותיה נשמרות
                # בעמודה הייעודית לה ולא דורסות את המשמרת הראשית.
                _ns, _ne = str(_r.get("תחילת משמרת", "")).strip(), str(_r.get("סוף משמרת", "")).strip()
                _col_for_row = _PREV_SHIFT_COL if _si == -1 else _SECOND_SHIFT_COL
                if is_time_text(_ns) and is_time_text(_ne):
                    if _col_for_row not in employees_df.columns:
                        employees_df[_col_for_row] = ""
                    employees_df.loc[_m, _col_for_row] = f"{_ns}-{_ne}"
                    if _m_snap is not None and _m_snap.any():
                        if _col_for_row not in _snap_live.columns:
                            _snap_live[_col_for_row] = ""
                        _snap_live.loc[_m_snap, _col_for_row] = f"{_ns}-{_ne}"
                _erow2 = {**employees_df.loc[employees_df.index[_m][0]].to_dict(),
                          "תחילת משמרת": _ns, "סוף משמרת": _ne}
                _tchoice2 = str(_r.get("טרמינל", "")).strip()
                _tat2 = clean_text(str(_r.get("שעת מעבר", "")))
                if _tchoice2 in _TERMINAL_OPTIONS and (
                        _tchoice2 != _terminal_label_for(_erow2, _si)
                        or _tat2 != _transfer_time_for(_erow2, _si)):
                    # _apply_terminal_choice בונה את חלונות המשמרת משעותיה,
                    # ולכן צריך להציב זמנית את שעות המשמרת השנייה ולהחזיר
                    # אחר כך את שעות המשמרת הראשית ללא שינוי.
                    _idx_m = employees_df.index[_m][0]
                    _keep_ss = employees_df.at[_idx_m, "תחילת משמרת"]
                    _keep_se = employees_df.at[_idx_m, "סוף משמרת"]
                    employees_df.at[_idx_m, "תחילת משמרת"] = _ns
                    employees_df.at[_idx_m, "סוף משמרת"] = _ne
                    _w2 = _apply_terminal_choice(employees_df, _n, _tchoice2, _tat2, _si)
                    employees_df.at[_idx_m, "תחילת משמרת"] = _keep_ss
                    employees_df.at[_idx_m, "סוף משמרת"] = _keep_se
                    if _m_snap is not None and _m_snap.any():
                        _idx_ms = _snap_live.index[_m_snap][0]
                        _keep_ss2 = _snap_live.at[_idx_ms, "תחילת משמרת"]
                        _keep_se2 = _snap_live.at[_idx_ms, "סוף משמרת"]
                        _snap_live.at[_idx_ms, "תחילת משמרת"] = _ns
                        _snap_live.at[_idx_ms, "סוף משמרת"] = _ne
                        _apply_terminal_choice(_snap_live, _n, _tchoice2, _tat2, _si)
                        _snap_live.at[_idx_ms, "תחילת משמרת"] = _keep_ss2
                        _snap_live.at[_idx_ms, "סוף משמרת"] = _keep_se2
                    if _w2:
                        _term_warnings.append(_w2)
            elif _m.any():
                employees_df.loc[_m, "תחילת משמרת"] = _r.get("תחילת משמרת", "")
                employees_df.loc[_m, "סוף משמרת"] = _r.get("סוף משמרת", "")
                if _m_snap is not None and _m_snap.any():
                    _snap_live.loc[_m_snap, "תחילת משמרת"] = _r.get("תחילת משמרת", "")
                    _snap_live.loc[_m_snap, "סוף משמרת"] = _r.get("סוף משמרת", "")
                _tchoice = str(_r.get("טרמינל", "")).strip()
                _tat = clean_text(str(_r.get("שעת מעבר", "")))
                if _tchoice in _TERMINAL_OPTIONS:
                    _erow = employees_df.loc[employees_df.index[_m][0]]
                    if (_tchoice != _terminal_label_for(_erow, 0)
                            or _tat != _transfer_time_for(_erow, 0)):
                        _w = _apply_terminal_choice(employees_df, _n, _tchoice, _tat, 0)
                        if _m_snap is not None and _m_snap.any():
                            _apply_terminal_choice(_snap_live, _n, _tchoice, _tat, 0)
                        if _w:
                            _term_warnings.append(_w)
        # Persist what the user actually touched (terminal / hours / transfer
        # time) — see the "_manual_emp_edits" note where it is applied. Only
        # rows in the sticky accumulator count as touched: the loop above also
        # rewrites every row's hours from the display values on each render,
        # and that must not be mistaken for an edit.
        _touched = {
            _tn for (_tn, _tsi), _tch in _sticky.items()
            if any(_c in _tch for _c in ("טרמינל", "שעת מעבר", "תחילת משמרת", "סוף משמרת"))
        }
        if _touched:
            _fp_now = _upload_fingerprint(include_db=False)
            _store = st.session_state.get("_manual_emp_edits") or {}
            if _store.get("fp") != _fp_now:
                _store = {"fp": _fp_now, "rows": {}}
            for _tn in _touched:
                _tm = employees_df["שם"] == _tn
                if _tm.any():
                    _trow = employees_df.loc[_tm].iloc[0]
                    _store["rows"][_tn] = {
                        _c: _trow[_c] for _c in _MANUAL_EDIT_COLS if _c in employees_df.columns
                    }
            st.session_state["_manual_emp_edits"] = _store
        for _w in _term_warnings:
            st.warning(_w)

        # ── החלפה ידנית של עובד/ת ────────────────────────────────────────────
        # הקלדת שם בעמודה "החלף ב־" מעבירה למי שהוקלד/ה את כל המשימות של אותה
        # שורה. השם נבדק מול קובץ העובדים; אם הוא לא מזוהה — לא משנים כלום.
        if _swaps:
            _sched_sw = st.session_state.get("schedule_df")
            _all_names = {clean_text(str(_n)) for _n in employees_df["שם"].astype(str)}

            def _resolve_name(_typed):
                if _typed in _all_names:
                    return _typed
                _words = set(_typed.split())
                _hits = [_n for _n in _all_names
                         if _words and (_words <= set(_n.split()) or set(_n.split()) <= _words)]
                return _hits[0] if len(_hits) == 1 else None

            _did = 0
            for _old_n, _typed, _si in _swaps:
                _new_n = _resolve_name(_typed)
                if _new_n is None:
                    st.warning(f'"{_typed}" לא נמצא/ה בקובץ העובדים — לא בוצעה החלפה.')
                    continue
                if _new_n == _old_n:
                    continue
                if _sched_sw is None or _sched_sw.empty:
                    st.warning("אין סידור בנוי — אין מה להחליף.")
                    break
                _mask_sw = _sched_sw["עובד"].astype(str).str.strip() == _old_n
                if not _mask_sw.any():
                    st.warning(f"ל{_old_n} אין שיבוצים בסידור — לא בוצעה החלפה.")
                    continue
                # ── בדיקות לפני ההחלפה ───────────────────────────────────
                # ההחלפה מתבצעת בכל מקרה (עקיפה ידנית מכוונת), אבל כל בעיה
                # מוצגת במפורש כדי שההחלטה תהיה מודעת.
                _issues = []
                _emp_rows_sw = employees_df[employees_df["שם"].astype(str).str.strip() == _new_n]
                _erow_sw = _emp_rows_sw.iloc[0] if len(_emp_rows_sw) else None
                _ROLE_COL_SW = {
                    "ראש צוות": "ראש צוות", "דייל": "דייל", "מתאם תורים": "מתאם תורים",
                    "מפקח TSA": "מפקח TSA", "שומר TSA": "שומר TSA", "טרייני רצ": "טרייני רצ",
                }
                _moving = _sched_sw[_mask_sw]
                _others_sw = _sched_sw[
                    (_sched_sw["עובד"].astype(str).str.strip() == _new_n)
                ]

                def _mn_sw(_v):
                    try:
                        _h, _m = str(_v).split(":")[:2]
                        return int(_h) * 60 + int(_m)
                    except Exception:
                        return None

                for _, _mt in _moving.iterrows():
                    _fl_sw = str(_mt.get("טיסה", "")).strip()
                    _role_sw = normalize_role_label(str(_mt.get("תפקיד בסיס", "")))
                    _col_sw = _ROLE_COL_SW.get(_role_sw)
                    # הסמכה
                    if (_erow_sw is not None and _col_sw
                            and clean_text(str(_erow_sw.get(_col_sw, ""))) != "כן"):
                        _issues.append(f"{_fl_sw}: אינו/ה מוסמך/ת לתפקיד {_role_sw}")
                    # משמרת
                    _st_sw = to_datetime_time(_mt.get("התחלה", ""))
                    _en_sw = to_datetime_time(_mt.get("סיום", ""))
                    if _erow_sw is not None and _st_sw is not None and _en_sw is not None:
                        try:
                            if not is_within_shift(
                                _erow_sw, _st_sw, _en_sw,
                                task_terminal=get_terminal(clean_text(str(_mt.get("_gate", "")))),
                            ):
                                _issues.append(
                                    f'{_fl_sw}: מחוץ למשמרת ({_mt.get("התחלה")}-{_mt.get("סיום")})'
                                )
                        except Exception:
                            pass
                    # התנגשות עם משימה קיימת של המחליף/ה
                    _a_sw, _b_sw = _mn_sw(_mt.get("התחלה")), _mn_sw(_mt.get("סיום"))
                    if _a_sw is not None and _b_sw is not None:
                        for _, _ot in _others_sw.iterrows():
                            _c_sw, _d_sw = _mn_sw(_ot.get("התחלה")), _mn_sw(_ot.get("סיום"))
                            if _c_sw is None or _d_sw is None:
                                continue
                            if not (_b_sw <= _c_sw or _a_sw >= _d_sw):
                                _o_fl_sw = str(_ot.get("טיסה", "")).strip()
                                if _o_fl_sw == _fl_sw:
                                    _issues.append(
                                        f'{_fl_sw}: כבר משובץ/ת בטיסה זו כ'
                                        f'{normalize_role_label(str(_ot.get("תפקיד בסיס", "")))}'
                                    )
                                else:
                                    _issues.append(
                                        f'{_fl_sw}: מתנגש/ת עם {_o_fl_sw} '
                                        f'({_ot.get("התחלה")}-{_ot.get("סיום")})'
                                    )
                                break

                _sched_sw.loc[_mask_sw, "עובד"] = _new_n
                if "סיבה" in _sched_sw.columns:
                    _sched_sw.loc[_mask_sw, "סיבה"] = f"הוחלף/ה ידנית ({_old_n})"
                _did += int(_mask_sw.sum())
                st.success(f"✅ {_new_n} החליף/ה את {_old_n} ב-{int(_mask_sw.sum())} משימות.")
                if _issues:
                    _hdr = "⚠️ ההחלפה בוצעה, אבל שימו לב (" + _new_n + "):"
                    _body = chr(10).join("• " + _i for _i in dict.fromkeys(_issues))
                    st.warning(_hdr + chr(10) + _body)
            if _did:
                st.session_state["schedule_df"] = _sched_sw
                (st.session_state["labeled_df"], st.session_state["workload_df"],
                 st.session_state["continuity_df"], st.session_state["output_df"]) =                     recompute_from_schedule(
                        _sched_sw,
                        st.session_state.get("flights_snap", flights_editor_df),
                        st.session_state.get("employees_snap", employees_df),
                    )
                st.rerun()
        _prev_removed = set(st.session_state.get("removed_employees", {}).keys())
        _now_removed  = set(_new_removed.keys())
        _sched_now    = st.session_state.get("schedule_df", pd.DataFrame())
        _rem_times    = st.session_state.setdefault("_removal_times", {})
        _rem_tasks    = st.session_state.setdefault("_removal_tasks", {})
        _now_str      = __import__("datetime").datetime.now().strftime("%H:%M")
        # רשום זמן וטיסות לכל עובד שאין לו עדיין (חדש או backfill מסשן קודם)
        for _nn in _now_removed:
            if _nn not in _rem_times:
                _rem_times[_nn] = _now_str
            if _nn not in _rem_tasks:
                _task_list = []
                if not _sched_now.empty:
                    _assigned = _sched_now[_sched_now["עובד"].astype(str) == _nn]
                    for _, _sr in _assigned.iterrows():
                        _f  = str(_sr.get("טיסה", "")).replace("LY", "").strip()
                        _ro = normalize_role_label(str(_sr.get("תפקיד בסיס", "")))
                        _ts = str(_sr.get("התחלה", ""))
                        _te = str(_sr.get("סיום", ""))
                        _task_list.append(f"{_f} ({_ro} {_ts}–{_te})")
                _rem_tasks[_nn] = _task_list
        # נקה נתוני עובדים שכבר לא מוסרים
        for _gone in _prev_removed - _now_removed:
            _rem_times.pop(_gone, None)
            _rem_tasks.pop(_gone, None)
        st.session_state["removed_employees"] = _new_removed

    # ── Save button (always available) ────────────────────────────────────────
    if st.button(
        "💾 שמור שעות משמרת", use_container_width=True, key="save_shift_hours"
    ):
        if _edited is not None and "employees_snap" in st.session_state:
            for (_, _r), _si in zip(_edited.iterrows(), _row_shift_idx):
                _n = _r["שם"]
                _m = st.session_state["employees_snap"]["שם"] == _n
                if _m.any() and _si:
                    _ns, _ne = str(_r.get("תחילת משמרת", "")).strip(), str(_r.get("סוף משמרת", "")).strip()
                    _col_for_row = _PREV_SHIFT_COL if _si == -1 else _SECOND_SHIFT_COL
                    if is_time_text(_ns) and is_time_text(_ne):
                        if _col_for_row not in st.session_state["employees_snap"].columns:
                            st.session_state["employees_snap"][_col_for_row] = ""
                        st.session_state["employees_snap"].loc[_m, _col_for_row] = f"{_ns}-{_ne}"
                elif _m.any():
                    st.session_state["employees_snap"].loc[_m, "תחילת משמרת"] = _r.get(
                        "תחילת משמרת", ""
                    )
                    st.session_state["employees_snap"].loc[_m, "סוף משמרת"] = _r.get(
                        "סוף משמרת", ""
                    )
                    _tc = str(_r.get("טרמינל", "")).strip()
                    if _tc in _TERMINAL_OPTIONS:
                        _apply_terminal_choice(
                            st.session_state["employees_snap"], _n, _tc,
                            clean_text(str(_r.get("שעת מעבר", ""))),
                        )
        # הכל כבר committed ל-employees_snap (גם חי, בכל rerun) — הצטברות
        # השורות הזמנית (_shift_editor_sticky) סיימה את תפקידה; לא לגרור
        # אותה קדימה, כדי שבנייה מחדש של השיבוץ לא "תזכור" עריכות ישנות
        # שכבר נשמרו ועלולות להתאים בטעות לעובד/ת אחר/ת באותו שם.
        st.session_state["_shift_editor_sticky"] = {}
        st.success("✅ שעות המשמרת והטרמינלים נשמרו בהצלחה")

    # ── Removed employees section ─────────────────────────────────────────────
    _removed_names = set(st.session_state["removed_employees"].keys())
    if _removed_names:
        st.markdown("---")

        if "schedule_df" in st.session_state:
            _sched = st.session_state["schedule_df"]
        if st.session_state.get("output_df") is not None:
            try:
                _workload = build_workload(
                    st.session_state.get("output_df", pd.DataFrame()),
                    st.session_state.get("employees_snap", pd.DataFrame()),
                )
                excel_bytes = to_excel_bytes(
                    st.session_state.get("output_df", pd.DataFrame()),
                    _workload,
                    _sched,
                )
                st.download_button(
                    label="📥 הורד דוח שיבוץ טיסות",
                    data=excel_bytes,
                    file_name="flight_assignments.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            except Exception:
                pass
        # ── Reassignment UI: per removed employee per task ────────────────
        _any_tasks = False
        if "_sched" not in dir():
            _sched = pd.DataFrame()
        for _rname in sorted(_removed_names):
            _tasks = _sched[_sched["עובד"].astype(str) == _rname].copy() if not _sched.empty else pd.DataFrame()
            if _tasks.empty:
                continue
            _any_tasks = True

            st.markdown(
                f'<div style="background:#fff3f3;border-right:4px solid #e74c3c;'
                f"border-radius:6px;padding:8px 12px;margin:8px 0 4px 0;"
                f'font-weight:700;direction:rtl;">✈️ {safe_html(_rname)}</div>',
                unsafe_allow_html=True,
            )

            for _tidx, _trow in _tasks.iterrows():
                _flight = str(_trow.get("טיסה", "")).replace("LY", "").strip()
                _role_base = str(_trow.get("תפקיד בסיס", ""))
                _role = normalize_role_label(_role_base)
                _t_start = str(_trow.get("התחלה", ""))
                _t_end = str(_trow.get("סיום", ""))

                _candidates = get_qualified_candidates_for_swap(
                    _sched, employees_df, str(_trow.get("טיסה", "")), _role_base, _tidx
                )
                _opts = ["— ללא החלפה (יישאר חוסר) —"] + (_candidates or [])

                _ci, _cs = st.columns([2, 3])
                _ci.markdown(
                    f'<div style="direction:rtl;padding-top:7px;font-size:13px;">'
                    f"<b>{safe_html(_flight)}</b> | {safe_html(_role)} | {safe_html(_t_start)}–{safe_html(_t_end)}</div>",
                    unsafe_allow_html=True,
                )
                with _cs:
                    st.selectbox(
                        "",
                        _opts,
                        key=f"repl_{_tidx}",
                        label_visibility="collapsed",
                    )

        if not _any_tasks:
            # אין טיסות לשיבוץ מחדש — עדכן snap אם צריך
            _snap = st.session_state.get("employees_snap", pd.DataFrame())
            _snap_names = set(_snap["שם"].astype(str).tolist()) if "שם" in _snap.columns else set()
            if any(n in _snap_names for n in _removed_names):
                st.session_state["employees_snap"] = employees_df.copy()
                st.rerun()
            st.success("✅ השיבוץ מעודכן — כל משימות העובדים שהוסרו כוסו.")
            if st.button("📋 סיכום הורדות", use_container_width=True, key="show_removal_summary_notask"):
                _removal_summary_dialog()
            return

        # ── Helper: compute auto-reassign and store as preview ─────────────
        def _run_auto_reassign(excluded_workers=None):
            _break_log = st.session_state.get("break_log", {})
            _new_sched = _sched.copy()
            for _rname in _removed_names:
                _new_sched = auto_reassign_removed_employee(
                    _new_sched, employees_df, _rname, _break_log,
                    excluded_workers=excluded_workers,
                )
            # Build changes dict
            _changes = {}
            for _idx in _sched.index:
                if _idx not in _new_sched.index:
                    continue
                _old_w = str(_sched.at[_idx, "עובד"]).strip()
                _new_w = str(_new_sched.at[_idx, "עובד"]).strip()
                if _old_w != _new_w:
                    _changes[_idx] = {
                        "old": _old_w,
                        "new": _new_w,
                        "flight": str(_sched.at[_idx, "טיסה"]).replace("LY", "").strip(),
                        "role": normalize_role_label(str(_sched.at[_idx, "תפקיד בסיס"]).strip()),
                        "start": str(_sched.at[_idx, "התחלה"]).strip(),
                        "end": str(_sched.at[_idx, "סיום"]).strip(),
                    }
            st.session_state["_ar_preview"] = _new_sched
            st.session_state["_ar_changes"] = _changes

        # ── Handle retry request (triggered from outside the expander) ──────
        if st.session_state.pop("_ar_retry_needed", False):
            _run_auto_reassign(excluded_workers=st.session_state.get("_ar_excluded"))

        # ── Action buttons (hidden while preview is pending) ───────────────
        if "_ar_preview" not in st.session_state:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            _col_auto, _col1, _col2, _col3 = st.columns(4)

            with _col_auto:
                if st.button(
                    "🤖 שיבוץ אוטומטי",
                    use_container_width=True,
                    type="primary",
                    key="do_auto_reassign",
                ):
                    st.session_state.pop("_ar_excluded", None)
                    _run_auto_reassign()
                    st.rerun()

            with _col1:
                if st.button(
                    "🔄 שיבוץ ידני",
                    use_container_width=True,
                    key="do_reassign",
                ):
                    _new_sched = _sched.copy()
                    for _rname in _removed_names:
                        _tasks = _new_sched[_new_sched["עובד"].astype(str) == _rname]
                        for _tidx, _ in _tasks.iterrows():
                            _choice = st.session_state.get(f"repl_{_tidx}", "")
                            if _choice and "ללא החלפה" not in _choice:
                                _new_sched.at[_tidx, "עובד"] = _choice
                            else:
                                _new_sched.at[_tidx, "עובד"] = "❌ (חסר)"
                    st.session_state["schedule_df"] = _new_sched
                    st.session_state["employees_snap"] = employees_df.copy()
                    for _k in ["_ar_highlighted", "labeled_df", "workload_df", "continuity_df", "output_df"]:
                        st.session_state.pop(_k, None)
                    st.rerun()

            with _col2:
                if st.button(
                    "💾 שמור ללא שיבוץ",
                    use_container_width=True,
                    key="save_no_reassign",
                ):
                    _new_sched = _sched.copy()
                    for _rname in _removed_names:
                        _rmask = _new_sched["עובד"].astype(str) == _rname
                        if _rmask.any():
                            _new_sched.loc[_rmask, "עובד"] = "❌ (חסר)"
                    st.session_state["schedule_df"] = _new_sched
                    st.session_state["employees_snap"] = employees_df.copy()
                    for _k in ["_ar_highlighted", "labeled_df", "workload_df", "continuity_df", "output_df"]:
                        st.session_state.pop(_k, None)
                    st.rerun()

            with _col3:
                if st.button(
                    "📋 סיכום הורדות", use_container_width=True, key="show_removal_summary_btn"
                ):
                    _removal_summary_dialog()

_mc1, _mc2, _mc3 = st.columns([1, 1, 1])
with _mc2:
    st.markdown(
        '<div style="text-align:center;font-size:16px;font-weight:700;margin-bottom:4px;">🛫 טיסות מהסידור היומי</div>',
        unsafe_allow_html=True,
    )
    st.metric("", len(flights_df))

# ── בניית טבלת הטיסות ──────────────────────────────────────────────────────
# אם saved_flight_edits קיים — הוא ה-DataFrame המלא (כולל שורות שנוספו ידנית)
# עמודות ההכשרה נשארות בלוח — טיסת הכשרת טרייני ר"צ מסומנת ידנית בעורך
# (ה-FIDS לא יודע עליה); "דייל בטרייני" נגזר אוטומטית מהסידור היומי ("טרייני - שם").
_base_from_excel = flights_df.copy()
if "שלוחה" not in _base_from_excel.columns:
    _base_from_excel["שלוחה"] = ""
if "טרייני רצ" not in _base_from_excel.columns:
    _base_from_excel["טרייני רצ"] = "לא"
if "סוג הכשרה" not in _base_from_excel.columns:
    _base_from_excel["סוג הכשרה"] = ""

if "saved_flight_edits" in st.session_state:
    _saved = st.session_state["saved_flight_edits"].copy()
    _saved = _saved.drop(columns=["טרייני רצ"], errors="ignore")
    for _c in _base_from_excel.columns:
        if _c not in _saved.columns:
            _saved[_c] = ""
    # Use ALL of _saved's columns (already a superset of _base_from_excel's,
    # per the loop above) — NOT just the ones _base_from_excel itself has.
    # Restricting to _base_from_excel.columns silently dropped columns that
    # only exist on the FIDS-sourced board, like "ETD": the daily Excel's
    # own flight sheet never has an ETD column, so on every rerun AFTER the
    # first FIDS-apply (e.g. the one triggered by clicking "בנה שיבוץ"),
    # this reload step wiped ETD from flights_editor_df even though it was
    # sitting correctly in st.session_state["saved_flight_edits"] the whole
    # time (found via real data 2026-07-25: ETD visible right after FIDS
    # upload, gone the moment "בנה שיבוץ" was clicked — the build itself was
    # never the culprit, this reload was).
    flights_editor_df = _saved.copy()
else:
    flights_editor_df = _base_from_excel.copy()

# ── Apply a pending manual gate update (from the "אבחון תפקידים חסרים"
# panel's gate-entry field) as EARLY as possible — before the flights
# data_editor or any other widget renders and could interfere with
# saved_flight_edits. The panel itself only sets this flag + calls
# st.rerun(); the actual mutation + rebuild happen here and right after
# _run_build_schedule is defined below (found via real data 2026-07-13:
# doing the mutation+rebuild deep inside the diagnostic expander, after
# the flights form/data_editor had already rendered earlier in the same
# script run, silently failed to take effect).
_pending_gate = st.session_state.pop("_pending_gate_update", None)
if _pending_gate:
    _pg_key = flight_key(str(_pending_gate.get("fnum", "")))
    _pg_gate = clean_text(str(_pending_gate.get("gate", ""))).upper()
    if _pg_key and _pg_gate:
        if "גייט" not in flights_editor_df.columns:
            flights_editor_df["גייט"] = ""
        _pg_mask = flights_editor_df["טיסה"].apply(lambda v: flight_key(str(v))) == _pg_key
        flights_editor_df.loc[_pg_mask, "גייט"] = _pg_gate
        st.session_state["saved_flight_edits"] = flights_editor_df.copy()
        st.session_state["_pending_gate_rebuild"] = True
        st.session_state["_pending_gate_success"] = f"✅ שער {_pg_gate} עודכן לטיסה {_pending_gate.get('fnum','')} — הסידור נבנה מחדש."

# Apply workday sort (03:00 = start of day, night flights go to the end)
def _workday_key(col):
    def _m(t):
        try:
            h, m = str(t).strip().split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 9999
    return col.apply(lambda t: (_m(t) - 180) % 1440)

_sort_col = "בורדינג" if "בורדינג" in flights_editor_df.columns else "המראה"
if _sort_col in flights_editor_df.columns:
    try:
        flights_editor_df = flights_editor_df.sort_values(
            by=_sort_col, key=_workday_key
        ).reset_index(drop=True)
    except Exception:
        pass

if "_source_order" not in flights_editor_df.columns:
    flights_editor_df["_source_order"] = range(len(flights_editor_df))


def _hm_to_min(t):
    """Convert HH:MM string to minutes. Returns None on failure."""
    try:
        h, m = str(t).strip().split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None

def _min_to_hm(minutes):
    """Convert minutes (0-1439) back to HH:MM string."""
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"

def _apply_etd_to_flights(df):
    """Flag rows where ETD differs from המראה — does NOT touch בורדינג/המראה
    themselves. The displayed המראה always stays the flight's ORIGINAL
    scheduled departure; ETD is shown separately in its own column (user
    rule 2026-07-25: show both times, don't overwrite המראה with ETD).
    Scheduling still treats the flight as departing per ETD when present —
    see _effective_flights_for_schedule, applied only at build time on a
    throwaway copy, never persisted back into this editor's data."""
    if "ETD" not in df.columns:
        return df
    if "_etd_changed" not in df.columns:
        df = df.copy()
        df["_etd_changed"] = False
    for idx in df.index:
        etd_raw = str(df.at[idx, "ETD"]).strip()
        if not etd_raw or etd_raw.lower() in {"nan", "none", ""}:
            df.at[idx, "_etd_changed"] = False
            continue
        m = re.search(r"\d{1,2}:\d{2}", etd_raw)
        if not m:
            df.at[idx, "_etd_changed"] = False
            continue
        etd_str = m.group()
        dep_str = str(df.at[idx, "המראה"]).strip()
        df.at[idx, "_etd_changed"] = etd_str != dep_str
    return df


def _effective_flights_for_schedule(df):
    """Return a COPY of df with בורדינג/המראה shifted to the ETD time for any
    row where ETD differs — used ONLY right before build_schedule, never
    persisted back to flights_editor_df/the display table. The flight is
    scheduled as if it departs at ETD; the editor keeps showing the original
    scheduled המראה plus ETD separately (user rule 2026-07-25)."""
    if "ETD" not in df.columns:
        return df
    df = df.copy()
    for idx in df.index:
        etd_raw = str(df.at[idx, "ETD"]).strip()
        if not etd_raw or etd_raw.lower() in {"nan", "none", ""}:
            continue
        m = re.search(r"\d{1,2}:\d{2}", etd_raw)
        if not m:
            continue
        etd_str = m.group()
        dep_str = str(df.at[idx, "המראה"]).strip()
        if etd_str == dep_str:
            continue
        board_str = str(df.at[idx, "בורדינג"]).strip()
        etd_m   = _hm_to_min(etd_str)
        dep_m   = _hm_to_min(dep_str)
        board_m = _hm_to_min(board_str)
        if etd_m is None:
            continue
        if dep_m is not None and board_m is not None:
            offset = (dep_m - board_m) % 1440   # boarding-to-departure gap
            new_board = _min_to_hm(etd_m - offset)
        else:
            new_board = board_str  # can't compute — keep original
        df.at[idx, "המראה"]   = etd_str
        df.at[idx, "בורדינג"] = new_board
    return df


def _build_display_df(df):
    """Merge columns for compact display in the flights editor."""
    d = pd.DataFrame(index=df.index)
    changed = df.get("_etd_changed", pd.Series(False, index=df.index))
    # Flight number: prefix changed rows with ⚡
    flight_label = df["טיסה"].astype(str).str.strip()
    flight_label = flight_label.where(~changed, "⚡ " + flight_label)
    # Terminal-1 flights get a "(T1)" marker on the destination line (user
    # rule 2026-07-25) — determined from the flight's own gate, same
    # classification used everywhere else (get_terminal on גייט/שלוחה).
    _t1_gate = df.get("גייט", pd.Series("", index=df.index)).astype(str)
    _t1_shloucha = df.get("שלוחה", pd.Series("", index=df.index)).astype(str)
    _is_t1 = pd.Series(
        [
            get_terminal(clean_text(_g) or clean_text(_s)) == "1"
            for _g, _s in zip(_t1_gate, _t1_shloucha)
        ],
        index=df.index,
    )
    _dest = df["יעד"].astype(str).str.strip()
    _dest = _dest.where(~_is_t1, _dest + " (T1)")
    d["טיסה / יעד"] = flight_label + "\n" + _dest
    # Show ONLY the flight's original scheduled departure (המראה) here — not
    # merged with בורדינג, and NEVER overwritten by ETD (user rule
    # 2026-07-25: both times must stay visible/independent; ETD gets its own
    # column below, and only affects scheduling — see
    # _effective_flights_for_schedule).
    d["המראה"] = df["המראה"].astype(str).str.strip()
    d["ETD"] = (
        df["ETD"].astype(str).str.strip().replace("nan", "")
        if "ETD" in df.columns
        else ""
    )
    d["מטוס / רישוי"] = (
        df["סוג מטוס"].astype(str).str.strip()
        + "\n"
        + df["רישוי"].astype(str).str.strip()
    )
    d["גייט"] = df["גייט"].astype(str).str.strip().replace("nan", "")
    d["נוסעים"] = df["נוסעים"].astype(str).str.strip().replace("nan", "")
    return d


def _parse_display_to_original(edited_display, base_df):
    """Parse the merged display dataframe back to the original column format.

    Matches rows by FLIGHT NUMBER (parsed from the non-editable "טיסה / יעד"
    column) rather than by row position — flights_editor_df gets re-sorted by
    boarding time on every rerun (see _sort_col above), and st.data_editor's
    widget state ("flights_editor" key) is keyed to the dataframe's row
    index/position at render time. If the sort order shifts between an edit
    and the save click (e.g. a fresh FIDS auto-apply changes some OTHER
    flight's boarding time and reorders the table), a positional/`.values`
    assignment silently applies the edit to whatever flight now sits in that
    row slot instead of the one actually edited. Found via real data
    2026-07-25: editing LY015's gate to "C7" (to consolidate it onto the same
    TSA pier as LY005/LY025) instead landed on LY001/LY017's gates, while
    LY015 itself kept its original unedited FIDS gate — TSA consolidation
    then correctly left LY015 on its own pier, which looked like a scheduling
    bug but was actually this save-path misalignment.
    """
    flight_num = (
        edited_display["טיסה / יעד"].astype(str).str.split("\n", n=1).str[0]
        .str.replace("⚡ ", "", regex=False).str.strip()
    )
    base_flight_num = base_df["טיסה"].astype(str).str.strip()

    result = base_df.copy()
    mat_reg = (
        edited_display["מטוס / רישוי"].astype(str).str.split("\n", n=1, expand=True)
    )
    edited = pd.DataFrame({
        "_fn": flight_num.values,
        "סוג מטוס": mat_reg[0].str.strip().values if 0 in mat_reg.columns else "",
        "רישוי": mat_reg[1].str.strip().values if 1 in mat_reg.columns else "",
        "גייט": edited_display["גייט"].values,
        "נוסעים": edited_display["נוסעים"].values,
        "ETD": edited_display["ETD"].astype(str).str.strip().replace("nan", "").values,
    }).drop_duplicates(subset="_fn", keep="last").set_index("_fn")

    _matched = base_flight_num.isin(edited.index)
    for col in ("סוג מטוס", "רישוי", "גייט", "נוסעים", "ETD"):
        result.loc[_matched, col] = base_flight_num[_matched].map(edited[col]).values
    return result


# ── ensure ETD column exists ──────────────────────────────────────────────────
if "ETD" not in flights_editor_df.columns:
    flights_editor_df["ETD"] = ""


# ── FIDS auto-apply (two files: current day + next day) ──────────────────────
def _apply_fids_files(fids_file_objects, base_df):
    """Merge FIDS data from one or two file objects into base_df. Returns (updated_df, filled_count)."""
    base = base_df.copy()
    base["_fk"] = base["טיסה"].apply(fids_flt_key)
    filled = 0

    combined = parse_fids_combined(fids_file_objects)
    if combined is None:
        return base.drop(columns=["_fk"]), 0

    for target_col, aliases in FIDS_COL_MAP.items():
        if target_col not in base.columns:
            continue
        src = fids_find_src(combined.columns, aliases)
        if not src:
            continue
        for _, fr in combined.iterrows():
            raw_val = str(fr.get(src, "")).strip()
            if not raw_val or raw_val.lower() in {"nan", "none", "&nbsp"}:
                continue
            if target_col == "ETD":
                m = re.search(r"\d{1,2}:\d{2}", raw_val)
                val = m.group() if m else ""
            else:
                val = clean_text(raw_val)
            if not val:
                continue
            mask = base["_fk"] == str(fr["_fk"])
            if mask.any():
                empty_mask = mask & (
                    base[target_col]
                    .astype(str)
                    .str.strip()
                    .isin(["", "nan", "None", "NaN"])
                )
                if empty_mask.any():
                    base.loc[empty_mask, target_col] = val
                    filled += int(empty_mask.sum())

    # זיהוי ETD מה-FIDS: ערך עם E בסוף (למשל "1016:30E") = שעה משוערת עתידית.
    # נכתב לעמודת ETD הנפרדת — לא מחליף את "המראה" המוצגת (user rule
    # 2026-07-25: showing both times, המראה never overwritten). ערך ללא E =
    # ההמראה בפועל שכבר קרתה — לא רלוונטי כ-ETD.
    _etd_src = fids_find_src(
        combined.columns, ["actual departure", "etd", "etdl", "etdz", "new departure"]
    )
    if _etd_src and "המראה" in base.columns:
        if "ETD" not in base.columns:
            base["ETD"] = ""
        for _, fr in combined.iterrows():
            _raw_etd = str(fr.get(_etd_src, "")).strip()
            if not _raw_etd or _raw_etd.lower() in {"nan", "none", "&nbsp"}:
                continue
            # בדיקת סיומת E (שעה משוערת עתידית)
            _has_e = bool(
                re.search(
                    r"\d{1,2}:\d{2}\s*E\s*$",
                    re.sub(r"\s+", " ", _raw_etd),
                    re.IGNORECASE,
                )
            )
            if not _has_e:
                continue  # המריאה בפועל — לא רלוונטי כ-ETD
            _m = re.search(r"\d{1,2}:\d{2}", _raw_etd)
            if not _m:
                continue
            _etd_val = _m.group()
            mask = base["_fk"] == str(fr["_fk"])
            if mask.any():
                base.loc[mask, "ETD"] = _etd_val

    base = base.drop(columns=["_fk"])
    return base, filled


_fids1_bytes = st.session_state.get("fids_file1_bytes")
_fids2_bytes = st.session_state.get("fids_file2_bytes")
_fids1_name = st.session_state.get("fids_file1_name", "fids.xlsx")
_fids2_name = st.session_state.get("fids_file2_name", "fids.xlsx")


class _NamedBytesIO(io.BytesIO):
    def __init__(self, data, name):
        super().__init__(data)
        self.name = name


_fids1 = _NamedBytesIO(_fids1_bytes, _fids1_name) if _fids1_bytes else None
_fids2 = _NamedBytesIO(_fids2_bytes, _fids2_name) if _fids2_bytes else None
if (_fids1 or _fids2) and not st.session_state.get("fids_applied"):
    if "ETD" not in flights_editor_df.columns:
        flights_editor_df["ETD"] = ""
    if _fids1: _fids1.seek(0)
    if _fids2: _fids2.seek(0)
    # לוח הטיסות נבנה מה-FIDS בלבד (הנחיית משתמש 2026-07-05) — לשונית
    # "דוח שיבוץ טיסות - המראות" בקובץ היומי אינה מקור הטיסות יותר.
    _fids_combined = parse_fids_combined([_fids1, _fids2])
    _fids_board = None
    if _fids_combined is not None:
        _fids_board = flights_from_fids(_fids_combined)
    if _fids_board is not None and not _fids_board.empty:
        for _c in flights_editor_df.columns:
            if _c not in _fids_board.columns:
                _fids_board[_c] = ""
        flights_editor_df = _fids_board[
            [c for c in flights_editor_df.columns if c in _fids_board.columns]
        ].copy()
    else:
        # FIDS ריק/לא נקרא — נפילה חזרה להעשרת הלוח הקיים מהקובץ היומי
        if _fids1: _fids1.seek(0)
        if _fids2: _fids2.seek(0)
        flights_editor_df, _fids_count = _apply_fids_files(
            [_fids1, _fids2], flights_editor_df
        )
    st.session_state["saved_flight_edits"] = flights_editor_df.copy()
    st.session_state["fids_applied"] = True

display_df = _build_display_df(flights_editor_df)
display_df.index = range(1, len(display_df) + 1)

# NOT wrapped in st.form: a form batches every widget interaction (including
# cell edits) until submit, but st.data_editor only flushes an IN-PROGRESS
# cell edit to its widget state on blur/Enter/Tab — if that flush and the
# submit-button click land in the same batched submission, the edit can lose
# the race and get missed, which is exactly why saving needed 3-4 clicks to
# "catch" all pending edits, and only ever committed one cell at a time (user
# 2026-07-24). Outside a form, each cell edit triggers its own immediate
# rerun and is committed to st.session_state["flights_editor"] BEFORE the
# save button is ever clicked in a later, separate rerun — no race. This
# table isn't wired to any expensive recompute on every rerun (the schedule
# build only runs on its own explicit button), so the extra reruns from
# editing are cheap.
_flights_editable = is_admin()
edited_display = st.data_editor(
    display_df,
    use_container_width=True,
    num_rows="fixed",
    column_config={
        "טיסה / יעד": st.column_config.TextColumn("✈️ טיסה / יעד", disabled=True),
        "המראה": st.column_config.TextColumn("🕒 המראה", disabled=True),
        "ETD": st.column_config.TextColumn("⏱️ ETD", disabled=not _flights_editable),
        "מטוס / רישוי": st.column_config.TextColumn("🛩️ מטוס / רישוי", disabled=not _flights_editable),
        "גייט": st.column_config.TextColumn("🚪 גייט", disabled=not _flights_editable),
        "נוסעים": st.column_config.TextColumn("👥 נוסעים", disabled=not _flights_editable),
    },
    key="flights_editor",
)
if _flights_editable:
    col_save, col_clear = st.columns([3, 1])
    with col_save:
        save_clicked = st.button("💾 שמור נתונים", use_container_width=True)
    with col_clear:
        clear_clicked = st.button("🗑️ נקה", use_container_width=True)
else:
    save_clicked = clear_clicked = False
    st.caption("👁️ צפייה בלבד — אין הרשאת עריכה.")

if save_clicked:
    parsed_back = _parse_display_to_original(edited_display, flights_editor_df)
    # Carry over existing _etd_changed flags from the base dataframe
    if "_etd_changed" in flights_editor_df.columns:
        parsed_back["_etd_changed"] = flights_editor_df["_etd_changed"].values
    # Flag rows where ETD differs from המראה — does not touch בורדינג/המראה
    parsed_back = _apply_etd_to_flights(parsed_back)
    # Re-sort by בורדינג using the same workday key
    _sort_col_save = "בורדינג" if "בורדינג" in parsed_back.columns else "המראה"
    if _sort_col_save in parsed_back.columns:
        try:
            parsed_back = parsed_back.sort_values(
                by=_sort_col_save, key=_workday_key
            ).reset_index(drop=True)
        except Exception:
            pass
    st.session_state["saved_flight_edits"] = parsed_back
    _n_changed = int(parsed_back.get("_etd_changed", pd.Series(False)).sum())
    if _n_changed:
        st.success(f"הנתונים נשמרו ✅ — {_n_changed} טיסות עודכנו לפי ETD ⚡")
    else:
        st.success("הנתונים נשמרו ✅")

if clear_clicked:
    st.session_state.pop("saved_flight_edits", None)
    st.session_state.pop("fids_applied", None)
    st.session_state.pop("_fids_combined_raw", None)
    st.rerun()


def _to_native_df(df):
    """Convert Arrow-backed / pandas-extension string columns to object dtype
    for fast iteration.

    Streamlit serialises DataFrames as Arrow when storing in session_state;
    every iterrows()/.at[] access on such a column triggers expensive boxing.
    Converting once at the entry point eliminates that overhead.

    The isinstance(..., pd.ArrowDtype) check alone stopped catching anything
    once pandas was upgraded to a version defaulting `future.infer_string` to
    True (pandas 3.x): plain string columns then get `pandas.StringDtype`
    (pyarrow-backed), a DIFFERENT class from `pd.ArrowDtype` — so this helper
    silently converted NOTHING anymore, and the whole pipeline (build_schedule,
    every post-pass, build_next_task_labels/workload/continuity/output_table)
    kept paying the slow per-cell Arrow access on every run. Use the generic
    is_extension_array_dtype check instead, which catches BOTH dtype families
    (found via real data 2026-07-24: a build that should take ~5-6s was taking
    32.8s end-to-end).
    """
    if df is None or df.empty:
        return df
    try:
        import pandas as pd
        arrow_cols = [
            c for c in df.columns
            if pd.api.types.is_extension_array_dtype(df[c].dtype)
        ]
        if arrow_cols:
            return df.astype({c: object for c in arrow_cols})
    except Exception:
        pass
    return df


def recompute_from_schedule(schedule_df, flights_df, employees_df):

    schedule_df  = _to_native_df(schedule_df)
    flights_df   = _to_native_df(flights_df)
    employees_df = _to_native_df(employees_df)

    labeled_df = build_next_task_labels(
        schedule_df, employees_df,
        built_until_minutes=st.session_state.get("_segment_built_until"),
    )
    workload_df = build_workload(labeled_df, employees_df)
    continuity_df = build_counter_continuity_rows(labeled_df, employees_df)
    output_df = build_output_table(flights_df, labeled_df, employees_df)

    return labeled_df, workload_df, continuity_df, output_df


# ── תאריך ושעה נוכחיים (ישראל) ───────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo as _ZI
except ImportError:
    import importlib
    _ZI = importlib.import_module("backports.zoneinfo").ZoneInfo

_IL_TZ_APP = _ZI("Asia/Jerusalem")
_now_il = __import__("datetime").datetime.now(tz=_IL_TZ_APP)
_DAY_HE = {
    "Monday": "יום שני",
    "Tuesday": "יום שלישי",
    "Wednesday": "יום רביעי",
    "Thursday": "יום חמישי",
    "Friday": "יום שישי",
    "Saturday": "שבת",
    "Sunday": "יום ראשון",
}
_day_he = _DAY_HE.get(_now_il.strftime("%A"), _now_il.strftime("%A"))

st.markdown(
    f'<div style="direction:rtl;text-align:center;background:linear-gradient(90deg,#eef5ff,#f0fdf4);'
    f"border:1px solid #c7d9f5;border-radius:10px;padding:7px 16px;margin-bottom:10px;"
    f'font-size:13.5px;color:#1e3a5f;font-weight:600;">'
    f'📅 {_day_he}, {_now_il.strftime("%d/%m/%Y")}'
    f"&nbsp;&nbsp;|&nbsp;&nbsp;"
    f'🕐 שעה נוכחית (ישראל): <strong style="font-size:15px;">{_now_il.strftime("%H:%M")}</strong>'
    f"</div>",
    unsafe_allow_html=True,
)


def render_worker_gantt(schedule_df):

    df = schedule_df.copy()

    df = df[
        (df["עובד"].astype(str).str.strip() != "")
        & (~df["עובד"].astype(str).str.contains("❌", na=False))
        & (df["התחלה"].astype(str).str.strip() != "")
        & (df["סיום"].astype(str).str.strip() != "")
    ].copy()

    if df.empty:
        st.info("אין נתונים להצגה בגאנט.")
        return

    df["start_dt"] = pd.to_datetime(df["התחלה"], format="%H:%M", errors="coerce")
    df["end_dt"] = pd.to_datetime(df["סיום"], format="%H:%M", errors="coerce")

    df = df.dropna(subset=["start_dt", "end_dt"])
    df = df.sort_values(["עובד", "start_dt"])

    role_colors = {
        "ראש צוות": "#8e24aa",
        "דייל": "#1e88e5",
        "מפקח TSA": "#d32f2f",
        "שומר TSA": "#2e7d32",
        "מתאם תורים": "#f9a825",
        "טרייני רצ": "#fb8c00",
        "טרייני ר״צ": "#fb8c00",
    }

    workers = df["עובד"].dropna().unique().tolist()

    st.markdown("### 📊 גאנט עובדים")

    for worker in workers:
        worker_df = df[df["עובד"] == worker]

        st.markdown(
            f"""
            <div style="
                direction:rtl;
                background:#111827;
                border:1px solid #334155;
                border-radius:14px;
                padding:10px 14px;
                margin-top:14px;
                font-weight:800;
                color:white;
            ">
                👤 {worker}
            </div>
            """,
            unsafe_allow_html=True,
        )

        cols = st.columns(len(worker_df))

        for col, (_, row) in zip(cols, worker_df.iterrows()):
            role = str(row.get("תפקיד בסיס", ""))
            color = role_colors.get(role, "#64748b")

            with col:
                st.markdown(
                    f"""
                    <div style="
                        direction:rtl;
                        background:{color};
                        color:white;
                        border-radius:12px;
                        padding:10px;
                        margin-top:6px;
                        min-height:88px;
                        box-shadow:0 3px 10px rgba(0,0,0,0.25);
                        font-size:13px;
                    ">
                        <b>✈️ {row.get("טיסה", "")} {row.get("יעד", "")}</b><br>
                        🧩 {role}<br>
                        ⏰ {row.get("התחלה", "")}-{row.get("סיום", "")}<br>
                        🛫 {row.get("גייט", "")}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


# ── Build button ──────────────────────────────────────────────────────────────
col_btn_auto, col_btn_seg, col_btn_hours, col_btn_manual = st.columns(4)

# Column marking a row that belongs to an EARLIER segment of the same
# operational day (see the נייט/דיי/אפטר builds). Such rows are seeded into the
# build as pre_assignments and restored verbatim afterwards, so a later team's
# build continues the schedule instead of re-deciding it.
LOCKED_COL = "_נעול"


def _restore_locked_segment(schedule_df, locked_df, emps_df):
    """
    Pin previously-built segments back to exactly what the earlier team built.

    The post-passes optimise across the WHOLE schedule and will happily move a
    worker who is already published in an earlier segment. Rather than teach
    each of the thirteen passes about locking, the locked flights are simply
    restored afterwards — and any new-segment assignment that this restore
    turns into a double-booking is dropped and re-filled.
    """
    import pandas as _pd
    from app.scheduler import backfill_remaining_gaps

    # Pin per (flight, role), not per flight: on the נייט segment's "extension"
    # flights only the ר"צ / מפקח slots are locked and the דיי team fills the
    # rest of the same flight, so dropping whole flights would delete its work.
    # Every (flight, role) the earlier team decided is replaced WHOLESALE, not
    # one row per locked row: build_schedule both seeds the locked rows and
    # emits the flight's own requirement rows when that flight is in this
    # build's flight set (the "extension" flights), so a one-for-one claim left
    # the freshly-built twin behind — LY5141 ended up with two ר"צ, TL#28
    # (locked) and TL#38 (rebuilt). Roles the earlier team did NOT decide
    # are untouched, which is what lets the דיי team fill the rest of an
    # extension flight.
    _locked_keys = {
        (clean_text(str(_lr.get("טיסה", ""))), clean_text(str(_lr.get("תפקיד בסיס", ""))))
        for _, _lr in locked_df.iterrows()
    }
    _claimed = [
        _i for _i in schedule_df.index
        if (clean_text(str(schedule_df.at[_i, "טיסה"])),
            clean_text(str(schedule_df.at[_i, "תפקיד בסיס"]))) in _locked_keys
    ]
    _kept = schedule_df.drop(index=_claimed).copy()

    def _m(v):
        try:
            _h, _mm = str(v).split(":")[:2]
            return int(_h) * 60 + int(_mm)
        except Exception:
            return None

    # Time spans the locked rows occupy, per worker.
    _busy = {}
    for _, _r in locked_df.iterrows():
        _s, _e = _m(_r.get("התחלה")), _m(_r.get("סיום"))
        if _s is None or _e is None:
            continue
        _busy.setdefault(clean_text(str(_r.get("עובד", ""))), []).append((_s, _e))

    _drop = []
    for _i in _kept.index:
        _name = clean_text(str(_kept.at[_i, "עובד"]))
        if _name not in _busy or "❌" in _name:
            continue
        _s, _e = _m(_kept.at[_i, "התחלה"]), _m(_kept.at[_i, "סיום"])
        if _s is None or _e is None:
            continue
        if any(not (_e <= _bs or _s >= _be) for _bs, _be in _busy[_name]):
            _drop.append(_i)
    for _i in _drop:
        _kept.at[_i, "עובד"] = f'❌ חסר {_kept.at[_i, "תפקיד בסיס"]}'

    # NOTE: no de-duplication by (flight, role, start, end) here. Two required
    # slots of the same role on one flight share identical role windows (two
    # דיילים both 19:40-20:30), so such a key cannot tell a redundant twin from
    # a genuine second slot — an earlier attempt at this silently deleted
    # LY5133's second, unfilled דייל. The wholesale claim above is what keeps
    # the seeded-plus-rebuilt duplication from arising in the first place.
    out = _pd.concat([_kept, locked_df], ignore_index=True)
    # Always: a slot the earlier team could not fill is carried forward as-is,
    # but THIS team's people may well be able to staff it — and their flights
    # aren't in this build's flight set, so only a backfill over the combined
    # schedule can reach it.
    if _drop or out["עובד"].astype(str).str.contains("❌", na=False).any():
        out = backfill_remaining_gaps(out, emps_df)
    return out


def _run_build_schedule(flights_df, emps_df, locked_df=None):
    """
    locked_df: rows from previously-built segments. They are seeded so their
    workers count as busy, and pinned back to their original values after the
    post-passes — a later segment may never move work the previous team already
    published.
    """
    import time as _time
    start_time = _time.time()
    # Normalize BEFORE build_schedule + every post-pass, not just at the end
    # (recompute_from_schedule) — emps_df/flights_df come straight out of
    # st.session_state, which Streamlit serializes with a slow pyarrow-backed
    # string dtype; every .at[]/iterrows() in build_schedule and all the
    # post-passes below was paying that cost on EVERY call otherwise (found
    # via real data 2026-07-24: build still took ~25s after fixing
    # _to_native_df alone, since that fix only normalized the DISPLAY step
    # that runs afterward, not the scheduling step itself).
    flights_df = _to_native_df(flights_df)
    emps_df = _to_native_df(emps_df)
    # Schedule the flight as if it departs at ETD when one is present (typed
    # manually or pulled from FIDS — either way it takes precedence over the
    # displayed departure once non-empty; user rule 2026-07-25). This copy is
    # used for build_schedule and everything derived from it below (labeled_
    # df/workload_df/flights_snap) so the schedule and its display line up —
    # but it is NEVER written back to flights_editor_df/saved_flight_edits,
    # so the editable flights table above keeps showing the ORIGINAL
    # scheduled המראה untouched, with ETD visible separately.
    flights_df = _effective_flights_for_schedule(flights_df)
    # Every pass below schedules from this frame; emps_df itself stays intact
    # for the display and the snapshot, so the swap UI can still offer
    # duty-blocked workers as an approval-required option.
    _sched_emps = schedulable_employees(emps_df)
    _pre = None
    if locked_df is not None and not locked_df.empty:
        _pre = _to_native_df(locked_df).copy()
        _pre[LOCKED_COL] = True
    schedule_df = build_schedule(flights_df, _sched_emps, pre_assignments=_pre)
    # Reserve dual-certified (ר"צ+TSA) workers for uncovered inspector slots
    # BEFORE upgrade_teamleads, so any ר"צ slot it vacates can be back-filled.
    schedule_df = reserve_dual_certified_for_tsa(schedule_df, _sched_emps)
    schedule_df = upgrade_teamleads(schedule_df, _sched_emps)
    schedule_df = optimize_tl_continuity(schedule_df, _sched_emps)
    schedule_df = fix_wasteful_gaps(schedule_df, _sched_emps)
    schedule_df = pair_trainee_attendants(schedule_df, _sched_emps)
    schedule_df = consolidate_tsa_inspectors_by_pier(schedule_df, _sched_emps)
    schedule_df = backfill_remaining_gaps(schedule_df, _sched_emps)
    schedule_df = protect_early_shift_preflight_breaks(schedule_df, _sched_emps)
    # LAST: re-assert the trainee/mentor pairing invariant — the passes
    # above can move either side of a pair independently.
    schedule_df = improve_night_continuity(schedule_df, _sched_emps)
    schedule_df = avoid_fresh_start_assignments(schedule_df, _sched_emps)
    schedule_df = fill_idle_gaps(schedule_df, _sched_emps)
    schedule_df = enforce_trainee_pairing(schedule_df, _sched_emps)
    # ר"צים get priority over plain attendants for floor work, then idle time
    # between a worker's flights is traded/handed away (user rules 2026-09-20).
    # After enforce_trainee_pairing (both passes skip trainee flights) and
    # before the final fix_wasteful_gaps cleanup.
    schedule_df = boost_runner_floor_time(schedule_df, _sched_emps)
    schedule_df = compact_idle_gaps(schedule_df, _sched_emps)
    # Re-run wasteful-gap cleanup AFTER every pass that can still reassign a
    # flight (pair_trainee_attendants / backfill_remaining_gaps /
    # enforce_trainee_pairing all hand vacated slots to a substitute without
    # checking whether doing so strands THEM with a purposeless gap). The
    # first fix_wasteful_gaps call above only sees the schedule as it stood
    # right after optimize_tl_continuity — found via real 12.07.2026 data:
    # agent#67 (03:30-11:00) had a clean day until enforce_trainee_pairing
    # backfilled LY323 (08:25-09:30) onto her at the very end, isolated by a
    # ~2h25m gap after her break with nothing to bridge it — she'd have gone
    # back to the counters and back down again for one flight ("טרטור").
    schedule_df = fix_wasteful_gaps(schedule_df, _sched_emps)

    if _pre is not None:
        schedule_df = _restore_locked_segment(schedule_df, _pre, _sched_emps)

    # Pre-compute display data so the next render is instant (avoids 5-7s re-compute on re-render)
    labeled_df, workload_df, continuity_df, output_df = recompute_from_schedule(
        schedule_df, flights_df, emps_df
    )
    st.session_state["labeled_df"]    = labeled_df
    st.session_state["workload_df"]   = workload_df
    st.session_state["continuity_df"] = continuity_df
    st.session_state["output_df"]     = output_df

    elapsed = round(_time.time() - start_time, 1)
    st.session_state["build_seconds"] = elapsed
    st.session_state["schedule_df"] = schedule_df.copy()
    try:
        schedule_df.to_excel("gantt_data.xlsx", index=False)
    except Exception:
        pass  # file may be open in Excel; session state is already updated
    # Every successful build gets a fresh id — this is how the draft/published
    # banner (see publish_state.py) tells whether the schedule on screen is
    # the one employees currently see, or unpublished changes since then.
    import uuid
    st.session_state["_build_id"] = uuid.uuid4().hex
    st.session_state["flights_snap"] = flights_df.copy()
    st.session_state["employees_snap"] = emps_df.copy()
    # A fresh build means the "מי עובד היום" edit accumulator's saved-by-
    # (name, shift_i) entries could now wrongly match a different person's
    # row in the new schedule — start clean.
    st.session_state["_shift_editor_sticky"] = {}
    st.session_state["show_time_form"] = False
    st.session_state["show_results"] = True
    st.session_state.pop("_ar_highlighted", None)
    st.session_state.pop("_transfer_delay_highlighted", None)
    # shift_map_ref (roster metadata: trainee/mentor pairing, sick flags,
    # terminal transfers...) and everything derived from it are cached with
    # "if not in session_state" guards (~line 2778-2786) so repeated widget
    # interactions don't re-parse the roster Excel on every rerun — but that
    # means a schedule REBUILD never picked up a re-uploaded roster file or a
    # code change to build_shift_map_from_excel itself. Found via real data
    # 2026-07-19: the trainee-mentor pairing fix (entry["mentor"]) was
    # correct in isolation but never appeared live — shift_map_ref had been
    # cached from before the fix and a rebuild never invalidated it.
    st.session_state.pop("shift_map_ref", None)
    st.session_state.pop("available_df", None)
    st.session_state.pop("unassigned_df", None)

# Consume the pending manual-gate rebuild flag (set earlier, right after
# flights_editor_df was updated with the new gate) — this is the earliest
# point _run_build_schedule is defined, well before the flights data_editor
# or the diagnostic panel render, so the fresh schedule is what this whole
# run displays everywhere.
# An extra st.rerun() here forces one more clean, fully-fresh top-to-bottom
# pass with the pending flags already cleared — found via real use 2026-07-13:
# without it, the FIRST click sometimes only reloaded the page without the
# rebuild visibly taking effect, and a SECOND click was needed to actually
# see it. The rebuild itself already happened by this point; this rerun only
# guarantees every widget below renders from the settled post-rebuild state.
if st.session_state.pop("_pending_gate_rebuild", False):
    _run_build_schedule(flights_editor_df, employees_df)
    _pg_msg = st.session_state.pop("_pending_gate_success", "")
    if _pg_msg:
        st.toast(_pg_msg, icon="✅")
    st.rerun()

with col_btn_auto:
    if not is_admin():
        st.caption("👁️ צפייה בלבד — אין הרשאה לבנות שיבוץ.")
    elif st.button("🚀 בנה שיבוץ לכל היום", use_container_width=True):
        try:
            _status = st.empty()
            _status.info("⏳ בונה שיבוץ... נא להמתין")
            st.session_state.pop("schedule_hours_label", None)  # סידור מלא — ללא באנר שעות
            # בנייה מלאה מאפסת את מצב הבנייה לפי משמרות
            st.session_state.pop("_segments_built", None)
            st.session_state.pop("_segment_built_until", None)
            _run_build_schedule(flights_editor_df, employees_df)
            _status.empty()
            st.rerun()
        except Exception as exc:
            st.error("הייתה שגיאה בבניית השיבוץ.")
            st.exception(exc)

with col_btn_seg:
    if is_admin() and st.button("🌙 בנה לפי משמרת", use_container_width=True,
                 help="הסידור נבנה שלוש פעמים ביום — נייט, דיי ואפטר. כל בנייה ממשיכה את הקודמת."):
        st.session_state["show_segment_form"] = True
        st.rerun()

with col_btn_hours:
    if is_admin() and st.button("🕐 בנה שיבוץ לפי שעות", use_container_width=True,
                 help="בחר טווח שעות ובנה שיבוץ רק לטיסות שהמראתן בטווח"):
        st.session_state["show_time_form"] = True
        st.rerun()

@st.dialog("⚠️ שים לב!")
def _confirm_new_schedule_dialog():
    st.markdown(
        '<div dir="rtl" style="font-size:17px;font-weight:700;color:#f5c542;'
        'text-align:center;padding:10px 0 18px;">'
        '⚠️ לחיצה על כפתור זה תאפס את כל הנתונים הקיימים.</div>',
        unsafe_allow_html=True,
    )
    _c_confirm, _c_cancel = st.columns(2)
    with _c_confirm:
        if st.button("✅ כן, אפס והתחל סידור חדש", use_container_width=True, type="primary"):
            _now = app_now()
            _sched_for_archive = st.session_state.get("schedule_df")
            _report_bytes = None
            if _sched_for_archive is not None and not _sched_for_archive.empty:
                try:
                    _report_bytes = to_departures_report_excel_bytes(
                        flights_editor_df,
                        st.session_state.get("labeled_df", _sched_for_archive),
                        employees_df,
                        terminal_view="both",
                    )
                except Exception:
                    _report_bytes = None
            # גרסה נפרדת לכל אחד משלושת סידורי היום (נייט/דיי/אפטר), כך
            # שההיסטוריה שומרת את כל השלושה ולא רק את המצב הסופי.
            _seg_versions = []
            for _sk in ("night", "day", "after"):
                _snap = st.session_state.get("_segment_snapshots", {}).get(_sk)
                if not _snap:
                    continue
                try:
                    _sf = flights_editor_df
                    if _snap.get("flights"):
                        _sw = set(_snap["flights"])
                        _sf = _sf[_sf["טיסה"].apply(lambda v: clean_text(str(v)) in _sw)]
                    _sb = to_departures_report_excel_bytes(
                        _sf, _snap["labeled_df"], employees_df,
                        terminal_view="both",
                    )
                except Exception:
                    _sb = None
                if _sb:
                    _seg_versions.append({
                        "key": _sk, "label": _snap["label"],
                        "range": _snap.get("range", ""), "bytes": _sb,
                    })
            schedule_archive.archive_schedule(
                _report_bytes, _now,
                segments=_seg_versions,
                archived_by=(current_user() or {}).get("username", ""),
                rows=int(len(_sched_for_archive)) if _sched_for_archive is not None else 0,
                removal_summary={
                    "removed": st.session_state.get("removed_employees", {}),
                    "times": st.session_state.get("_removal_times", {}),
                    "tasks": st.session_state.get("_removal_tasks", {}),
                },
            )
            publish_state.clear_published()
            clear_all_arrivals()
            for _k in [
                "schedule_df", "flights_snap", "employees_snap", "labeled_df",
                "workload_df", "continuity_df", "output_df", "build_seconds",
                "schedule_hours_label", "_build_id", "saved_flight_edits",
                "fids_applied", "fids_file1_bytes", "fids_file1_name",
                "fids_file2_bytes", "fids_file2_name", "daily_file_obj",
                "employees_file_obj", "t1_file_obj", "_t1_file_name",
                "show_results", "show_gantt_page", "active_main_tab",
                "removed_employees", "_removal_times", "_removal_tasks",
                "_segments_built", "_segment_built_until", "show_segment_form",
                "_segment_snapshots", "_segment_current", "_view_segment",
                "_manual_emp_edits",
            ]:
                st.session_state.pop(_k, None)
            session_checkpoint.clear_checkpoint()
            st.session_state["show_upload_form"] = True
            st.toast("✅ הסידור הקודם נשמר בלשונית \"היסטוריה\" — אפשר להתחיל סידור חדש.", icon="⭐")
            st.rerun()
    with _c_cancel:
        if st.button("ביטול", use_container_width=True):
            st.rerun()


with col_btn_manual:
    if is_admin() and st.button("⭐ התחל סידור חדש", use_container_width=True,
                 help="שומר עותק של הסידור הנוכחי (כולל סיכום ההורדות ממשמרת ונתוני ההחתמות) בלשונית \"היסטוריה\" ומתחיל סידור חדש מאפס"):
        _confirm_new_schedule_dialog()

# ── בנייה לפי משמרת (נייט / דיי / אפטר) ───────────────────────────────────────
# הסידור נבנה שלוש פעמים ביום ע"י שלושה צוותים. כל בנייה ממשיכה את הקודמת:
# הטיסות שכבר שובצו ננעלות ומועברות כ-pre_assignments, כך שעובד שכבר בטיסות
# ממשיך מהמקום שבו עצר ולא משובץ מחדש.
_SEGMENT_META = {
    "night": ("נייט", "🌙"),
    "day":   ("דיי",  "☀️"),
    "after": ("אפטר", "🌃"),
}

if st.session_state.get("show_segment_form", False):
    st.markdown(
        '<div style="direction:rtl;background:#f0f4ff;border:1px solid #c0d0f0;'
        'border-radius:12px;padding:16px 18px;margin-top:8px;">',
        unsafe_allow_html=True,
    )
    st.markdown("**🌙 בניית סידור לפי משמרת**")
    try:
        from app.scheduler import compute_segment_boundaries

        _segs = compute_segment_boundaries(
            _effective_flights_for_schedule(_to_native_df(flights_editor_df)),
            _to_native_df(employees_df),
        )
        _done = set(st.session_state.get("_segments_built", []))
        _cols = st.columns(3)
        for _col, _key in zip(_cols, ("night", "day", "after")):
            _seg = _segs.get(_key)
            _he, _icon = _SEGMENT_META[_key]
            with _col:
                if not _seg:
                    st.caption(f"{_icon} {_he} — אין טיסות")
                    continue
                _fm, _tm = _seg["from_minutes"], _seg["to_minutes"]
                st.markdown(
                    f'<div dir="rtl" style="font-size:13px;line-height:1.7;">'
                    f'<b>{_icon} {_he}</b><br>'
                    f'{_seg["first_flight"]} '
                    f'<span dir="ltr">{_fm // 60:02d}:{_fm % 60:02d}</span> ← '
                    f'{_seg["last_flight"]} '
                    f'<span dir="ltr">{_tm // 60:02d}:{_tm % 60:02d}</span><br>'
                    f'{len(_seg["flights"])} טיסות'
                    + (' · <b style="color:#2e7d32;">נבנה ✓</b>' if _key in _done else '')
                    + '</div>',
                    unsafe_allow_html=True,
                )
                if st.button(f"בנה {_he}", use_container_width=True, key=f"seg_go_{_key}"):
                    try:
                        _status = st.empty()
                        _status.info(f"⏳ בונה את סידור ה{_he}... נא להמתין")
                        _wanted = set(_seg["flights"])
                        # נייט ממשיך לשבץ ר"צ/מפקח גם על טיסות שאחרי הגבול,
                        # כל עוד המשמרת שלהם מכסה אותן — שאר התפקידים בטיסות
                        # האלה נשארים לצוות הדיי.
                        _ext = set(_seg.get("extension", []))
                        _ext_roles = set(_seg.get("extension_roles", []))
                        _seg_flights = flights_editor_df[
                            flights_editor_df["טיסה"].apply(
                                lambda v: clean_text(str(v)) in (_wanted | _ext)
                            )
                        ].copy()
                        # כל מה שכבר נבנה בסבבים קודמים — ננעל ומועבר כבסיס.
                        _locked = None
                        _prev = st.session_state.get("schedule_df")
                        if _prev is not None and not _prev.empty and _done:
                            _prev_flights = set()
                            for _k2 in _done:
                                _s2 = _segs.get(_k2)
                                if _s2:
                                    _prev_flights |= set(_s2["flights"])
                            _pf = _prev["טיסה"].astype(str).str.strip()
                            _mask = _pf.isin(_prev_flights - _wanted)
                            # טיסות ההרחבה של הנייט יושבות בתוך הקטע של הדיי,
                            # ולכן החיסור למעלה מוחק בדיוק את מה שהנייט שיבץ
                            # עליהן. יש לנעול את שורות ההרחבה שלהן בנפרד.
                            _nseg = _segs.get("night") or {}
                            _ext_prev = set(_nseg.get("extension", [])) if "night" in _done else set()
                            if _ext_prev & _wanted:
                                # רק שיבוצים ממשיים — משבצת הרחבה שנשארה חסרה
                                # שייכת לקטע הזה, ונעילתה הייתה מונעת ממנו
                                # לאייש אותה לעולם.
                                _mask |= (
                                    _pf.isin(_ext_prev & _wanted)
                                    & _prev["תפקיד בסיס"].astype(str).str.strip().isin(
                                        set(_nseg.get("extension_roles", []))
                                    )
                                    & ~_prev["עובד"].astype(str).str.contains("❌", na=False)
                                )
                            if _mask.any():
                                _locked = _prev[_mask].copy()
                        # הגבול שנבנה עד כה — משמש לצ'יפ "המשך יבוא" בזרימת
                        # העבודה. נקבע לפני הבנייה, כי recompute_from_schedule
                        # רץ בתוך _run_build_schedule וקורא אותו משם.
                        st.session_state["_segment_built_until"] = (
                            None if _key == "after" else _seg["to_minutes"]
                        )
                        _run_build_schedule(_seg_flights, employees_df, locked_df=_locked)
                        if _ext:
                            # השאר מטיסות ההרחבה רק את תפקידי ההרחבה
                            _sd = st.session_state["schedule_df"]
                            _in_ext = _sd["טיסה"].astype(str).str.strip().isin(_ext)
                            _is_ext_role = _sd["תפקיד בסיס"].astype(str).str.strip().isin(_ext_roles)
                            st.session_state["schedule_df"] = _sd[~_in_ext | _is_ext_role].copy()
                            (st.session_state["labeled_df"], st.session_state["workload_df"],
                             st.session_state["continuity_df"], st.session_state["output_df"]) = \
                                recompute_from_schedule(
                                    st.session_state["schedule_df"],
                                    st.session_state["flights_snap"],
                                    st.session_state["employees_snap"],
                                )
                        st.session_state["_segments_built"] = sorted(_done | {_key})
                        # שמור עותק של הסידור כפי שנבנה בקטע הזה — כך משמרת
                        # מאוחרת יותר יכולה לחזור ולראות את הסידור של הקודמת,
                        # וכל שלושת הסידורים נשמרים להיסטוריה בסוף היום.
                        st.session_state.setdefault("_segment_snapshots", {})[_key] = {
                            "label": _he,
                            "icon": _icon,
                            "output_df": st.session_state["output_df"].copy(),
                            "schedule_df": st.session_state["schedule_df"].copy(),
                            "labeled_df": st.session_state["labeled_df"].copy(),
                            "range": f"{_seg['first_flight']}–{_seg['last_flight']}",
                            "flights": sorted(_wanted | _ext),
                        }
                        st.session_state["_segment_current"] = _key
                        st.session_state.pop("_view_segment", None)
                        st.session_state["schedule_hours_label"] = (
                            f"{_he}: {_seg['first_flight']}–{_seg['last_flight']}"
                        )
                        _status.empty()
                        st.session_state["schedule_success_msg"] = (
                            f"✅ סידור ה{_he} נבנה בהצלחה תוך "
                            f"{st.session_state.get('build_seconds', 0)} שניות"
                        )
                        st.session_state["show_segment_form"] = False
                        st.rerun()
                    except Exception as _exc:
                        st.error("שגיאה בבניית הסידור.")
                        st.exception(_exc)
    except Exception as _exc:
        st.error("לא ניתן לחשב את גבולות המשמרות.")
        st.exception(_exc)

    if st.button("✖ סגור", key="seg_cancel"):
        st.session_state["show_segment_form"] = False
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ── טופס זמנים ────────────────────────────────────────────────────────────────
if st.session_state.get("show_time_form", False):
    st.markdown(
        '<div style="direction:rtl;background:#f0f4ff;border:1px solid #c0d0f0;'
        'border-radius:12px;padding:16px 18px;margin-top:8px;">',
        unsafe_allow_html=True,
    )
    st.markdown("**🕐 בחר טווח שעות לשיבוץ**", unsafe_allow_html=False)
    tf_col1, tf_col2 = st.columns(2)
    with tf_col1:
        time_from = st.text_input(
            "משעה", value="06:00", placeholder="HH:MM", key="tf_from"
        )
    with tf_col2:
        time_to = st.text_input(
            "עד שעה", value="23:59", placeholder="HH:MM", key="tf_to"
        )

    tf_go, tf_cancel = st.columns(2)
    with tf_go:
        if st.button(
            "✅ בנה שיבוץ אוטומטי בטווח זה", use_container_width=True, key="tf_confirm"
        ):
            try:
                from datetime import datetime as _dt

                def _hm(s):
                    # גמיש: מקבל "13:30", "1330", "13.30", "9" וכו'; מחזיר None אם לא תקין
                    t = clean_text(str(s)).replace(".", ":").replace(" ", "")
                    if ":" not in t and t.isdigit():
                        t = (t[:-2] + ":" + t[-2:]) if len(t) >= 3 else (t + ":00")
                    try:
                        return _dt.strptime(t, "%H:%M")
                    except ValueError:
                        return None

                t_from = _hm(time_from)
                t_to = _hm(time_to)

                if t_from is None or t_to is None:
                    st.warning("⚠️ פורמט שעה לא תקין — הזן שעה כמו 13:30 (או 1330).")
                else:
                    # סנן רק טיסות שהמראה שלהן בטווח הזמן
                    filtered_flights = flights_editor_df[
                        flights_editor_df["המראה"].apply(
                            lambda v: is_time_text(str(v))
                            and _hm(str(v)) is not None
                            and t_from <= _hm(str(v)) <= t_to
                        )
                    ].copy()

                    if filtered_flights.empty:
                        st.warning("לא נמצאו טיסות בטווח הזמן שנבחר.")
                    else:
                        status_box = st.empty()
                        status_box.info("⏳ בונה שיבוץ אוטומטי... נא להמתין")

                        # שמור את טווח השעות להצגה בבאנר מעל הסידור
                        st.session_state["schedule_hours_label"] = (
                            f"{t_from.strftime('%H:%M')}-{t_to.strftime('%H:%M')}"
                        )
                        # השתמש באותו נתיב בנייה כמו "בנה שיבוץ לכל היום" — כך
                        # labeled_df/workload_df וכו' מחושבים מחדש מהטיסות המסוננות
                        # (אחרת התצוגה נשארת עם הסידור המלא הישן מה-cache).
                        _run_build_schedule(filtered_flights, employees_df)
                        status_box.empty()

                        st.session_state["schedule_success_msg"] = (
                            f"✅ הסידור נבנה בהצלחה תוך "
                            f"{st.session_state.get('build_seconds', 0)} שניות"
                        )
                        st.rerun()
            except Exception as exc:
                st.error("שגיאה בבניית השיבוץ.")
                st.exception(exc)
    with tf_cancel:
        if st.button("✖ ביטול", use_container_width=True, key="tf_cancel"):
            st.session_state["show_time_form"] = False
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ── Tab navigation — defined here (not inside the "schedule built" gate
# below) so the control-center and user-management tabs are reachable
# immediately after login/upload, before any schedule exists. The other
# tabs (סידור עבודה, זרימת עבודה, לא משובצים, פנויים) need schedule_df and
# only appear once a schedule has actually been built (user rule
# 2026-07-26: "לאחר יצירת השיבוץ, יראו את כל הלשוניות במלואן").
TAB_SCHEDULE = "schedule"
TAB_DASHBOARD = "dashboard"
TAB_UNASSIGNED = "unassigned"
TAB_AVAILABLE = "available"
TAB_WORKFLOW = "workflow"
TAB_USERS = "users"
TAB_EMPLOYEES = "employees"
TAB_ARCHIVE = "archive"

FULL_TAB_LABELS = {
    TAB_SCHEDULE: "🛠️ סידור עבודה",
    TAB_WORKFLOW: "📋 זרימת עבודה",
    TAB_DASHBOARD: "⏱️ מרכז בקרה",
    TAB_UNASSIGNED: "🚨 לא משובצים / הפסקות",
    TAB_AVAILABLE: "🟡 פנויים באולם",
    TAB_USERS: "👤 ניהול משתמשים",
    TAB_EMPLOYEES: "🧑‍✈️ ניהול עובדים",
    TAB_ARCHIVE: "🗂️ היסטוריה",
}


def _render_archive_tab():
    st.markdown(
        "<h3 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);"
        "padding-bottom:8px;margin-bottom:16px;'>🗂️ היסטוריה</h3>",
        unsafe_allow_html=True,
    )
    entries = schedule_archive.list_archived_schedules()
    if not entries:
        st.info("אין עדיין סידורים בהיסטוריה — סידור נשמר כאן אוטומטית בכל לחיצה על \"⭐ התחל סידור חדש\".")
        return
    for entry in entries:
        _cols = st.columns([3, 2, 2, 2, 1])
        with _cols[0]:
            _by = entry.get("archived_by") or "—"
            st.markdown(
                f'<div dir="rtl" style="padding-top:8px;font-weight:700;">📅 {entry["date"]}'
                f' <span style="color:#9ca3af;font-weight:400;">— נשמר בשעה {entry["archived_at"]}'
                f' ע"י {_by}</span></div>',
                unsafe_allow_html=True,
            )
        with _cols[1]:
            _removed_n = entry.get("removed_count", 0)
            st.markdown(
                f'<div dir="rtl" style="padding-top:8px;color:#9ca3af;">{entry["rows"]} שורות שיבוץ'
                + (f' &nbsp;|&nbsp; {_removed_n} הורדות ממשמרת' if _removed_n else '')
                + '</div>',
                unsafe_allow_html=True,
            )
        with _cols[2]:
            _sched_bytes = schedule_archive.load_archived_schedule_bytes(entry["id"])
            if _sched_bytes is not None:
                st.download_button(
                    "⬇️ קובץ שיבוץ", data=_sched_bytes,
                    file_name=f"דוח שיבוץ טיסות - {entry['date']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, key=f"dl_archive_sched_{entry['id']}",
                )
        with _cols[3]:
            _removal_bytes = schedule_archive.load_archived_removal_bytes(entry["id"])
            if _removal_bytes is not None:
                st.download_button(
                    "⬇️ ירידות ממשמרת", data=_removal_bytes,
                    file_name=f"ירידות ממשמרת - {entry['date']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True, key=f"dl_archive_removals_{entry['id']}",
                )
        with _cols[4]:
            if is_super_admin():
                if st.button("🗑️ מחק", use_container_width=True, key=f"del_archive_{entry['id']}"):
                    st.session_state["_confirm_delete_archive_id"] = entry["id"]
                    st.rerun()
        # שלוש גרסאות היום — נייט / דיי / אפטר, כפי שכל משמרת בנתה אותן
        _segs_hist = entry.get("segments") or []
        if _segs_hist:
            st.markdown(
                '<div dir="rtl" style="color:#9ca3af;font-size:13px;padding:2px 0 4px;">'
                'גרסאות המשמרות של אותו יום:</div>',
                unsafe_allow_html=True,
            )
            _sc = st.columns(len(_segs_hist))
            for _c, _sg in zip(_sc, _segs_hist):
                with _c:
                    _sb = schedule_archive.load_archived_segment_bytes(entry["id"], _sg.get("key", ""))
                    if _sb is not None:
                        st.download_button(
                            f'⬇️ סידור {_sg.get("label", "")}'
                            + (f'  ({_sg["range"]})' if _sg.get("range") else ""),
                            data=_sb,
                            file_name=f'סידור {_sg.get("label","")} - {entry["date"]}.xlsx',
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key=f'dl_archive_seg_{entry["id"]}_{_sg.get("key","")}',
                        )

        if st.session_state.get("_confirm_delete_archive_id") == entry["id"]:
            st.warning(f'⚠️ למחוק לצמיתות את הסידור מתאריך {entry["date"]} ({entry["archived_at"]})? לא ניתן לשחזר.')
            _c_del_yes, _c_del_no = st.columns(2)
            with _c_del_yes:
                if st.button("כן, מחק לצמיתות", use_container_width=True, type="primary", key=f"del_confirm_{entry['id']}"):
                    schedule_archive.delete_archived_schedule(entry["id"])
                    st.session_state.pop("_confirm_delete_archive_id", None)
                    st.rerun()
            with _c_del_no:
                if st.button("ביטול", use_container_width=True, key=f"del_cancel_{entry['id']}"):
                    st.session_state.pop("_confirm_delete_archive_id", None)
                    st.rerun()
        st.markdown("<div style='border-bottom:1px solid rgba(255,255,255,0.08);margin:6px 0;'></div>", unsafe_allow_html=True)


if "schedule_df" not in st.session_state:
    _pre_tab_labels = {
        TAB_DASHBOARD: FULL_TAB_LABELS[TAB_DASHBOARD],
        TAB_ARCHIVE: FULL_TAB_LABELS[TAB_ARCHIVE],
        TAB_USERS: FULL_TAB_LABELS[TAB_USERS],
        TAB_EMPLOYEES: FULL_TAB_LABELS[TAB_EMPLOYEES],
    }
    if st.session_state.get("active_main_tab") not in _pre_tab_labels:
        st.session_state["active_main_tab"] = TAB_DASHBOARD
    active_main_tab = st.session_state["active_main_tab"]

    _nav_cols_pre = st.columns(len(_pre_tab_labels))
    for _ci, (_tk, _tlabel) in enumerate(reversed(list(_pre_tab_labels.items()))):
        with _nav_cols_pre[_ci]:
            _is_active = (active_main_tab == _tk)
            if st.button(
                _tlabel, use_container_width=True, key=f"nav_tab_pre_{_tk}",
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["active_main_tab"] = _tk
                st.rerun()
    active_main_tab = st.session_state["active_main_tab"]

    if active_main_tab == TAB_USERS:
        if is_super_admin():
            render_user_management_panel(employees_df)
        else:
            st.info("אין הרשאה לניהול משתמשים.")
    elif active_main_tab == TAB_EMPLOYEES:
        if is_super_admin():
            render_employee_admin_panel()
        else:
            st.info("אין הרשאה לניהול עובדים.")
    elif active_main_tab == TAB_ARCHIVE:
        _render_archive_tab()
    else:
        st.markdown(
            "<h3 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);"
            "padding-bottom:8px;margin-bottom:16px;'>⏱️ מרכז בקרה</h3>",
            unsafe_allow_html=True,
        )
        with st.expander("🕒 מי עובד היום?"):
            _render_who_works_today()

# — Display
if "schedule_df" in st.session_state:
    if "build_seconds" in st.session_state:
        st.markdown(
            f"""
            <div style="
                background:#0f3d2e;
                color:white;
                padding:14px;
                border-radius:12px;
                text-align:center;
                font-size:18px;
                font-weight:600;
                margin-bottom:15px;
            ">
                ⏱️ זמן הפקת הסידור: {st.session_state['build_seconds']} שניות 🎉
            </div>
            """,
            unsafe_allow_html=True,
        )
    if st.session_state.get("schedule_hours_label"):
        st.markdown(
            f"""
            <div style="
                background:#1e3a5f;
                color:white;
                padding:12px;
                border-radius:12px;
                text-align:center;
                font-size:17px;
                font-weight:600;
                margin-bottom:15px;
                direction:rtl;
            ">
                🕐 סידור מוצג לשעות <span dir="ltr" style="unicode-bidi:isolate;">{st.session_state['schedule_hours_label']}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    live_schedule = st.session_state["schedule_df"]
    live_flights = st.session_state["flights_snap"]
    live_employees = st.session_state["employees_snap"]

    import time

    display_start = time.time()
    if "labeled_df" not in st.session_state:
        labeled_df, workload_df, continuity_df, output_df = recompute_from_schedule(
            live_schedule,
            live_flights,
            live_employees,
        )

        st.session_state["labeled_df"] = labeled_df
        st.session_state["workload_df"] = workload_df
        st.session_state["continuity_df"] = continuity_df
        st.session_state["output_df"] = output_df
    else:
        labeled_df = st.session_state["labeled_df"]
        workload_df = st.session_state["workload_df"]
        continuity_df = st.session_state["continuity_df"]
        output_df = st.session_state["output_df"]

    display_elapsed = round(time.time() - display_start, 1)
    if "תפקיד" in live_employees.columns and "שם" in live_employees.columns:
        role_map = dict(
            zip(
                live_employees["שם"].astype(str).str.strip(),
                live_employees["תפקיד"].astype(str).str.strip(),
            )
        )

    worker_col = "עובד" if "עובד" in output_df.columns else "שם"

    if worker_col in output_df.columns:
        output_df["תפקיד"] = (
            output_df[worker_col].astype(str).str.strip().map(role_map).fillna("דייל")
        )

    missing = live_schedule[
        live_schedule["עובד"].astype(str).str.contains("❌", na=False)
    ]

    def classify_employee_for_scheduling(row):
        sheet = str(row.get("לשונית", "")).strip()
        section = str(row.get("כותרת", "")).strip()
        role = str(row.get("תפקיד", "")).strip()
        note = str(row.get("הערה", "")).strip()

        has_tsa_supervisor = str(row.get("מפקח TSA", "")).strip() in [
            "כן",
            "TRUE",
            "True",
            "1",
        ]
        has_teamlead = str(row.get("רצ", "")).strip() in ["כן", "TRUE", "True", "1"]

        # לא נספרים בכלל
        if "מרכז שירות כבודה" in sheet:
            return "מרכז שירות כבודה"

        if "מלווי נוסעים" in sheet:
            return "מלווי נוסעים"

        # מנהלי משמרת
        if "מנהלי משמרת" in sheet:
            if "תגבור" in note or "ראש צוות" in note or "שלן" in note or "של״ן" in note:
                return "תגבור מנהל משמרת"
            return "מנהל משמרת"

        # של״ן בידוק חולייה
        if "של" in sheet and (
            "בידוק חול" in section or "חוליה" in section or "חולייה" in section
        ):
            if has_tsa_supervisor:
                return "בידוק חולייה - מפקח TSA"
            return "בידוק חולייה"

        # מתדרכת
        if "מתדרכת" in role:
            if has_teamlead:
                return "מתדרכת גיבוי ר״צ"
            return "מתדרכת"

        # עובדים רגילים לשיבוץ
        if "דלפקי" in sheet or "סיירת" in sheet or "של" in sheet:
            return "פעיל לשיבוץ"

        return "לא מסווג"

    def _build_full_shift_map_ref():
        """build_shift_map_from_excel(daily_file) alone omits every Terminal-1
        worker — T1 comes from a SEPARATE uploaded file and is only merged
        into the map used for actual scheduling (see the merge_t1_shift_map
        call near the top of this script), never into this display-only
        shift_map_ref. Panels that read shift_map_ref (e.g. the "פירוט חישובי
        דדליין" urgent-break table) showed "—" for every T1 worker's shift
        hours even though they're correctly scheduled (found via real data
        2026-07-31: TL#3/agent#40/agent#19/TL-trainee#3, all T1)."""
        ref = build_shift_map_from_excel(daily_file)
        _t1f = st.session_state.get("t1_file_obj")
        if _t1f is not None:
            try:
                _t1f.seek(0)
                _t1_ref = build_shift_map_from_excel(_t1f, terminal="1")
                _t1f.seek(0)
                merge_t1_shift_map(ref, _t1_ref)
            except Exception:
                pass
        return ref

    def missing_severity(row):
        role = str(row.get("תפקיד בסיס", ""))
        worker = str(row.get("עובד", ""))

        if "ראש צוות" in role:
            return "🔴 קריטי"
        if "דייל" in role:
            return "🟠 גבוה"
        if "TSA" in role:
            return "🟠 גבוה"
        if "טרייני" in role:
            return "🟡 בינוני"
        return "🟡 בינוני"

    if "schedule_df" in st.session_state:
        live_schedule = st.session_state["schedule_df"]
        live_flights = st.session_state["flights_snap"]
        live_employees = st.session_state["employees_snap"]
        shift_map_ref = _build_full_shift_map_ref()
        shift_sheet_by_name = {}

        for name, info in shift_map_ref.items():
            clean_name = str(info.get("original", name)).strip()
            sheet = str(info.get("sheet", "")).strip()
            if clean_name:
                shift_sheet_by_name[name] = sheet

        employees_class_df = live_employees.copy()
        employees_class_df["sheet"] = (
            employees_class_df["_name_key"]
            .astype(str)
            .str.strip()
            .map(shift_sheet_by_name)
            .fillna("")
        )
        active_sheets = [
            'של"ן',
            "של״ן",
            "שלן",
            'דלפקי ש"ש',
            "דלפקי ש״ש",
        ]
        excluded_sheets = [
            "מנהלי משמרת",
            "מרכז שירות כבודה",
            "מלווי נוסעים",
        ]

    _ss_sheets = ['דלפקי ש"ש', 'דלפקי ש״ש', 'דלפקי שש']

    active_schedulable_count = employees_class_df[
        employees_class_df["sheet"].isin(active_sheets)
    ]["_name_key"].nunique()
    _excluded_df = employees_class_df[employees_class_df["sheet"].isin(excluded_sheets)]
    excluded_count = _excluded_df["_name_key"].nunique()
    _excluded_names = sorted(
        _excluded_df.drop_duplicates("_name_key")["שם"].astype(str).tolist()
    )
    excluded_help = "\n".join(_excluded_names) if _excluded_names else "אין"

    shalom_count = employees_class_df[
        employees_class_df["sheet"].isin(_ss_sheets)
    ]["_name_key"].nunique()

    # ספור בידוק חוליה ומתדרכות מתוך blocked_roles ב-shift_map_ref
    # דדופליקציה לפי original name (כי כל עובד רשום פעמיים: שם רגיל + הפוך)
    hulya_count = 0
    trainer_backup_count = 0
    _seen_hulya_orig = set()
    _seen_trainer_orig = set()
    _trainer_names = []
    for _nm_key, _info in shift_map_ref.items():
        _orig = str(_info.get("original", _nm_key)).strip()
        _blocked_labels = _info.get("blocked_roles", [])
        for _lbl in _blocked_labels:
            _lbl_l = str(_lbl).lower()
            if "בידוק" in _lbl_l and _orig not in _seen_hulya_orig:
                hulya_count += 1
                _seen_hulya_orig.add(_orig)
            if "מתדרכ" in _lbl_l and _orig not in _seen_trainer_orig:
                trainer_backup_count += 1
                _seen_trainer_orig.add(_orig)
                _trainer_names.append(_orig)

    # ספור מנהלים שמגיעים לתגבר כר"צ (available_windows בלשונית מנהלי משמרת)
    mgr_backup_rz_count = 0
    _seen_mgr_orig = set()
    _mgr_names = []
    for _mk, _minfo in shift_map_ref.items():
        if "מנהל" not in str(_minfo.get("sheet", "")):
            continue
        _orig = str(_minfo.get("original", _mk)).strip()
        if _minfo.get("available_windows") and _orig not in _seen_mgr_orig:
            mgr_backup_rz_count += 1
            _seen_mgr_orig.add(_orig)
            _avail_str = ", ".join(
                f"{ws}–{we}" for ws, we in _minfo["available_windows"]
            )
            _mgr_names.append(f"{_orig} ({_avail_str})")

    import time

    display_start = time.time()
    labeled_df = st.session_state["labeled_df"]
    workload_df = st.session_state["workload_df"]
    continuity_df = st.session_state["continuity_df"]
    output_df = st.session_state["output_df"]

    # Viewing an EARLIER segment of the same day (the "סידור נייט" / "סידור דיי"
    # buttons in the work-schedule tab) — swap in that segment's snapshot.
    _view_seg = st.session_state.get("_view_segment")
    _seg_snaps = st.session_state.get("_segment_snapshots", {})
    if _view_seg and _view_seg in _seg_snaps:
        output_df = _seg_snaps[_view_seg]["output_df"]
        labeled_df = _seg_snaps[_view_seg]["labeled_df"]

    display_elapsed = round(time.time() - display_start, 1)

    missing = continuity_df.copy()
    missing["חומרה"] = missing.apply(missing_severity, axis=1)
    display_df = output_df.copy()

    # מיון טיסות: ציר 02:00 — טיסות לילה (00:xx, 01:xx) מוצגות בסוף
    _DISP_PIVOT = 2 * 60
    def _disp_dep_key(val):
        dep = str(val).split("(")[0].strip()
        try:
            h, m = dep.split(":")[:2]
            return (int(h) * 60 + int(m) - _DISP_PIVOT) % 1440
        except Exception:
            return 9999
    def _is_midnight_crossing(val):
        """True when boarding (in parentheses) is before midnight but departure is after."""
        s = str(val)
        try:
            dep_part   = s.split("(")[0].strip()
            board_part = s.split("(")[1].rstrip(")").strip()
            dh, dm = dep_part.split(":")[:2]
            bh, bm = board_part.split(":")[:2]
            dep_min   = int(dh) * 60 + int(dm)
            board_min = int(bh) * 60 + int(bm)
            return board_min > dep_min  # boarding numerically later → crosses midnight
        except Exception:
            return False
    if "זמנים" in display_df.columns:
        display_df = display_df.sort_values(
            by="זמנים", key=lambda col: col.apply(_disp_dep_key)
        ).reset_index(drop=True)
        display_df["_midnight_crossing"] = display_df["זמנים"].apply(_is_midnight_crossing)

    if "available_df" not in st.session_state:
        st.session_state["available_df"] = build_available_in_hall(
            live_schedule,
            live_employees,
            live_flights,
        )

    if "shift_map_ref" not in st.session_state:
        st.session_state["shift_map_ref"] = _build_full_shift_map_ref()

    if "unassigned_df" not in st.session_state:
        st.session_state["unassigned_df"] = build_unassigned_agents(
            live_schedule,
            live_employees,
            st.session_state["shift_map_ref"],
        )
    available_df = st.session_state["available_df"]
    shift_map_ref = st.session_state["shift_map_ref"]
    unassigned_df = st.session_state["unassigned_df"]
    total_flights = (
        live_flights["טיסה"].nunique()
        if "טיסה" in live_flights.columns
        else len(live_flights)
    )

    missing_real = missing[
        missing.astype(str).apply(
            lambda row: row.str.contains("❌", na=False).any(), axis=1
        )
    ].copy()

    assigned_workers = live_schedule["עובד"].dropna().astype(str).str.strip()

    assigned_workers = assigned_workers[~assigned_workers.str.contains("❌", na=False)]

    total_assigned = assigned_workers.nunique()

    total_unassigned = len(unassigned_df)
    total_available = available_df["עובד"].nunique() if not available_df.empty else 0
    total_missing = (
        output_df.astype(str)
        .apply(lambda row: row.str.contains("❌", na=False).any(), axis=1)
        .sum()
    )

    # TAB_* constants / FULL_TAB_LABELS are defined once, above, before this
    # "schedule built" gate — reused here so the nav bar includes every tab
    # (including ניהול משתמשים) once a schedule exists.
    TAB_LABELS = FULL_TAB_LABELS

    if "active_main_tab" not in st.session_state:
        st.session_state["active_main_tab"] = TAB_SCHEDULE
    active_main_tab = st.session_state["active_main_tab"]

    _nav_cols = st.columns(len(TAB_LABELS))
    for _ci, (_tk, _tlabel) in enumerate(reversed(list(TAB_LABELS.items()))):
        with _nav_cols[_ci]:
            _is_active = (active_main_tab == _tk)
            if st.button(
                _tlabel,
                use_container_width=True,
                key=f"nav_tab_{_tk}",
                type="primary" if _is_active else "secondary",
            ):
                st.session_state["active_main_tab"] = _tk
                st.rerun()
    active_main_tab = st.session_state["active_main_tab"]

    # ── מי עובד היום — מוצג מתחת לטאבים בכל מסך ──────────────────────────
    st.markdown(
        """<style>
        [data-testid="stExpander"] {
            background: rgba(255,255,255,0.04) !important;
            border: 1px solid rgba(255,255,255,0.1) !important;
            border-radius: 10px !important;
        }
        [data-testid="stExpander"] summary {
            color: rgba(255,255,255,0.85) !important;
            font-weight: 600 !important;
            direction: rtl !important;
        }
        [data-testid="stExpander"] > div { direction: rtl; }
        </style>""",
        unsafe_allow_html=True,
    )
    if active_main_tab == TAB_USERS:
        if is_super_admin():
            render_user_management_panel(employees_df)
        else:
            st.info("אין הרשאה לניהול משתמשים.")

    if active_main_tab == TAB_EMPLOYEES:
        if is_super_admin():
            render_employee_admin_panel()
        else:
            st.info("אין הרשאה לניהול עובדים.")

    if active_main_tab == TAB_ARCHIVE:
        _render_archive_tab()

    if active_main_tab == TAB_DASHBOARD:
        st.markdown(
            "<h3 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);padding-bottom:8px;margin-bottom:16px;'>⏱️ מרכז בקרה</h3>",
            unsafe_allow_html=True,
        )

        with st.expander("🕒 מי עובד היום?"):
            _render_who_works_today()

        # ── Auto-assign preview panel ──────────────────────────────────────
        _ar_preview_outer = st.session_state.get("_ar_preview")
        if _ar_preview_outer is not None:
            _ar_changes_outer = st.session_state.get("_ar_changes", {})
            st.markdown(
                '<div style="background:#fffbe6;border:2px solid #f5a623;border-radius:12px;'
                'padding:16px 18px;margin:10px 0 12px 0;direction:rtl;">',
                unsafe_allow_html=True,
            )
            st.markdown("### 🤖 הצעת שיבוץ אוטומטי — ממתינה לאישור")
            if _ar_changes_outer:
                _rows_outer = []
                for _idx, _ch in _ar_changes_outer.items():
                    _old_label = _ch["old"] if "❌" not in _ch["old"] else "—"
                    _new_label = _ch["new"] if "❌" not in _ch["new"] else "❌ לא נמצא מחליף"
                    _rows_outer.append({
                        "טיסה": _ch["flight"],
                        "תפקיד": _ch["role"],
                        "זמן": f"{_ch['start']}–{_ch['end']}",
                        "עובד ישן": _old_label,
                        "עובד חדש ✨": _new_label,
                    })
                st.dataframe(pd.DataFrame(_rows_outer), use_container_width=True, hide_index=True)
            else:
                st.info("לא נמצאו שינויים — כל הטיסות כבר מכוסות.")
            st.markdown("</div>", unsafe_allow_html=True)

            _ob1, _ob2, _ob3 = st.columns(3)
            with _ob1:
                if st.button("✅ אישור — אמץ שיבוץ", use_container_width=True, type="primary", key="ar_confirm"):
                    _highlighted = set(st.session_state["_ar_changes"].keys())
                    st.session_state["schedule_df"] = st.session_state["_ar_preview"]
                    st.session_state["employees_snap"] = employees_df.copy()
                    st.session_state["_ar_highlighted"] = _highlighted
                    for _k in ["_ar_preview", "_ar_changes", "_ar_excluded",
                               "labeled_df", "workload_df", "continuity_df", "output_df"]:
                        st.session_state.pop(_k, None)
                    st.rerun()
            with _ob2:
                if st.button("🔄 נסה שוב — עובד אחר", use_container_width=True, key="ar_retry"):
                    _excl = {k: set(v) for k, v in st.session_state.get("_ar_excluded", {}).items()}
                    for _idx, _ch in _ar_changes_outer.items():
                        if "❌" not in _ch["new"]:
                            _excl.setdefault(_idx, set()).add(_ch["new"])
                    st.session_state["_ar_excluded"] = _excl
                    st.session_state["_ar_retry_needed"] = True
                    for _k in ["_ar_preview", "_ar_changes"]:
                        st.session_state.pop(_k, None)
                    st.rerun()
            with _ob3:
                if st.button("✕ ביטול", use_container_width=True, key="ar_cancel"):
                    for _k in ["_ar_preview", "_ar_changes", "_ar_excluded"]:
                        st.session_state.pop(_k, None)
                    st.rerun()

        # חשב כרגע באולם
        _lounge_roles = {"דייל", "ראש צוות", "מתאם תורים", 'ר"צ'}
        _now_min_db = app_now().hour * 60 + app_now().minute

        _flight_times = {}
        for _, _fr in live_flights.iterrows():
            _fnum = str(_fr.get("טיסה", "")).strip()
            try:
                _bh, _bm2 = str(_fr.get("בורדינג", "")).split(":")[:2]
                _bm = int(_bh) * 60 + int(_bm2)
            except Exception:
                continue
            if _fnum:
                _flight_times[_fnum] = _bm

        _in_lounge_count = 0
        for _, _row in live_schedule.iterrows():
            _worker = str(_row.get("עובד", "")).strip()
            if not _worker or "❌" in _worker:
                continue
            if str(_row.get("תפקיד בסיס", "")).strip() not in _lounge_roles:
                continue
            try:
                _sh, _sm = str(_row.get("התחלה", "")).split(":")[:2]
                _eh, _em = str(_row.get("סיום", "")).split(":")[:2]
                _start_m = int(_sh) * 60 + int(_sm)
                _end_m   = int(_eh) * 60 + int(_em)
            except Exception:
                continue
            if not (_start_m <= _now_min_db <= _end_m):
                continue
            _bm = _flight_times.get(str(_row.get("טיסה", "")).strip())
            if _bm is None or _now_min_db < _bm:
                continue
            _in_lounge_count += 1

        _on_break_now = sum(
            1 for b in st.session_state.get("break_log", {}).values()
            if b.get("start") and not b.get("end") and b.get("active", True)
        )

        _removed_count = len(st.session_state.get("removed_employees", {}))

        c1, c2, c3 = st.columns(3)
        c1.metric("☕ בהפסקה", _on_break_now)
        c2.metric("🚫 הורדו ממשמרת", _removed_count)
        c3.metric("🚪 כרגע באולם", _in_lounge_count)

        st.markdown(
            "<h4 style='text-align:right;border-bottom:2px solid rgba(255,255,255,0.15);padding-bottom:8px;margin:24px 0 16px;'>👥 סיווג עובדים לשיבוץ</h4>",
            unsafe_allow_html=True,
        )

        e1, e2, e3, e4, e5, e6 = st.columns(6)
        e1.metric("פעילים לשיבוץ", active_schedulable_count)
        e2.metric("בידוק חוליה", hulya_count)

        with e3:
            st.metric("מתדרכות גיבוי ר״צ", trainer_backup_count)
            if trainer_backup_count:
                with st.popover("👁 רשימה", use_container_width=True):
                    st.markdown("**מתדרכות גיבוי ר״צ:**")
                    for _n in sorted(_trainer_names):
                        st.markdown(f"• {_n}")

        with e4:
            st.metric('מנהלים כר"צ', mgr_backup_rz_count)
            if mgr_backup_rz_count:
                with st.popover("👁 רשימה", use_container_width=True):
                    st.markdown('**מנהלים לתגבור ר"צ:**')
                    for _n in sorted(_mgr_names):
                        st.markdown(f"• {_n}")

        e5.metric("שירות שלום", shalom_count)

        with e6:
            st.metric("לא נספרים", excluded_count)
            if excluded_count:
                with st.popover("👁 רשימה", use_container_width=True):
                    st.markdown("**לא נספרים בשיבוץ:**")
                    for _n in _excluded_names:
                        st.markdown(f"• {_n}")

        st.markdown("<hr><h4 style='text-align:right'>📊 ניתוח פיקים</h4>", unsafe_allow_html=True)
        _peak_data = build_peak_analysis(live_flights)
        render_peak_analysis(_peak_data)

        st.stop()

    from datetime import datetime

    def time_to_min(value):
        try:
            t = pd.to_datetime(str(value), errors="coerce")
            if pd.isna(t):
                return None
            return t.hour * 60 + t.minute
        except Exception:
            return None

    now = app_now()
    now_min = now.hour * 60 + now.minute

    active_tasks_now = live_schedule.copy()

    active_tasks_now["start_min"] = active_tasks_now["התחלה"].apply(time_to_min)
    active_tasks_now["end_min"] = active_tasks_now["סיום"].apply(time_to_min)

    active_tasks_now = active_tasks_now[
        active_tasks_now["start_min"].notna()
        & active_tasks_now["end_min"].notna()
        & (active_tasks_now["start_min"] <= now_min)
        & (active_tasks_now["end_min"] >= now_min)
        & (~active_tasks_now["עובד"].astype(str).str.contains("❌", na=False))
    ]

    workers_on_flights_now = active_tasks_now["עובד"].nunique()

    if "break_log" not in st.session_state:
        st.session_state["break_log"] = {}

    workers_on_break_now = sum(
        1
        for b in st.session_state["break_log"].values()
        if b.get("start") and not b.get("end")
    )

    workers_available_now = max(total_available - workers_on_break_now, 0)

    workers_should_be_counters_now = max(
        total_assigned - workers_on_flights_now - workers_on_break_now, 0
    )

    # ── Tab: לוח מבצעים ──────────────────────────────────────────────────
    if active_main_tab == TAB_SCHEDULE:
        # Draft-vs-published banner (user rule 2026-07-30): employees never
        # see a schedule until an admin explicitly "שגר סידור"s it — this
        # tells the admin/manager whether what's on screen right now has
        # actually been sent out yet.
        if publish_state.is_published(st.session_state.get("_build_id")):
            _pub_at = publish_state.get_published_at() or ""
            st.markdown(
                f'<div style="direction:rtl;background:#0f3d2e;border:1px solid #1c6b4a;'
                f'border-radius:10px;padding:10px 16px;margin-bottom:10px;text-align:center;'
                f'font-weight:700;color:#7ee8b8;">✅ הסידור מפורסם ועדכני — עובדים רואים אותו'
                f' (שוגר ב-{_pub_at})</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div style="direction:rtl;background:#3d2e0f;border:1px solid #6b5a1c;'
                'border-radius:10px;padding:10px 16px;margin-bottom:10px;text-align:center;'
                'font-weight:700;color:#f0d27e;">⚠️ הסידור עדיין בטיוטה — לא הופץ לעובדים.'
                ' לחצו על "שגר סידור" בתחתית העמוד כדי לפרסם אותו.</div>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div style="text-align:right;direction:rtl;font-weight:700;'
            'font-size:14px;color:#aaa;padding:2px 0 0 0;">🔎 חיפוש לפי טיסה / יעד / עובד</div>',
            unsafe_allow_html=True,
        )
        search = st.text_input("חיפוש", label_visibility="collapsed")
        _txt_col, _chk_col = st.columns([20, 1])
        with _txt_col:
            st.markdown(
                '<div style="text-align:right;direction:rtl;font-size:14px;font-weight:700;'
                'color:#aaa;padding-top:6px;">הצג רק טיסות עם חוסר</div>',
                unsafe_allow_html=True,
            )
        with _chk_col:
            only_missing = st.checkbox("", key="only_missing_chk", label_visibility="collapsed")
        # ── מסנן טרמינל — הפרדה ברורה בין סידור T3 לסידור T1 ─────────────────
        if "טרמינל" in display_df.columns and (display_df["טרמינל"] == "1").any():
            _term_view = st.radio(
                "תצוגת טרמינל",
                ["הכל", "טרמינל 3", "טרמינל 1"],
                horizontal=True,
                key="schedule_term_filter",
                label_visibility="collapsed",
            )
            if _term_view == "טרמינל 3":
                display_df = display_df[display_df["טרמינל"] != "1"]
            elif _term_view == "טרמינל 1":
                display_df = display_df[display_df["טרמינל"] == "1"]
        # ── כותרת המשמרת + מעבר לסידורים הקודמים של אותו יום ─────────────────
        # מופיעה רק כשהסידור נבנה לפי משמרות (נייט/דיי/אפטר).
        _cur_seg = st.session_state.get("_segment_current")
        _snaps_ui = st.session_state.get("_segment_snapshots", {})
        if _cur_seg and _cur_seg in _snaps_ui:
            _shown_seg = st.session_state.get("_view_segment") or _cur_seg
            _shown = _snaps_ui.get(_shown_seg, _snaps_ui[_cur_seg])
            _is_past = _shown_seg != _cur_seg
            st.markdown(
                '<div dir="rtl" style="text-align:center;font-size:22px;font-weight:800;'
                f'padding:14px 0 4px;color:{"#f0d27e" if _is_past else "inherit"};">'
                f'{_shown["icon"]} סידור משמרת {_shown["label"]}'
                + (' <span style="font-size:14px;font-weight:600;">(סידור קודם — לצפייה בלבד)</span>'
                   if _is_past else '')
                + '</div>'
                f'<div dir="rtl" style="text-align:center;font-size:13px;color:#888;'
                f'padding-bottom:10px;">{_shown["range"]}</div>',
                unsafe_allow_html=True,
            )
            # כפתור לכל סידור קודם שנבנה היום, ועוד אחד לחזרה לסידור הנוכחי
            _prev_keys = [k for k in ("night", "day", "after")
                          if k in _snaps_ui and k != _cur_seg]
            if _prev_keys:
                _btn_cols = st.columns(len(_prev_keys) + (1 if _is_past else 0))
                for _bc, _pk in zip(_btn_cols, _prev_keys):
                    with _bc:
                        if st.button(f'סידור {_snaps_ui[_pk]["label"]}',
                                     key=f"view_seg_{_pk}", use_container_width=True,
                                     disabled=(_shown_seg == _pk)):
                            st.session_state["_view_segment"] = _pk
                            st.rerun()
                if _is_past:
                    with _btn_cols[-1]:
                        if st.button(f'↩ חזרה לסידור {_snaps_ui[_cur_seg]["label"]}',
                                     key="view_seg_back", use_container_width=True,
                                     type="primary"):
                            st.session_state.pop("_view_segment", None)
                            st.rerun()

        if only_missing:
            display_df = display_df[
                display_df.astype(str).apply(
                    lambda row: row.str.contains("❌", na=False).any(), axis=1
                )
            ]
        if search:
            mask = display_df.astype(str).apply(
                lambda row: row.str.contains(search, case=False, na=False).any(), axis=1
            )
            display_df = display_df[mask]

            # When filtering by employee name, re-sort by THAT employee's actual
            # role start time so her assignments appear in true chronological order
            # (e.g. night flight at 22:30 comes before morning flight at 03:00).
            _live_sched = st.session_state.get("schedule_df", pd.DataFrame())
            if not _live_sched.empty and "עובד" in _live_sched.columns and "התחלה" in _live_sched.columns:
                _emp_starts = (
                    _live_sched[
                        _live_sched["עובד"].astype(str).str.contains(search, case=False, na=False)
                    ]
                    .groupby("טיסה")["התחלה"]
                    .min()
                    .reset_index()
                    .rename(columns={"התחלה": "_emp_start"})
                )
                if not _emp_starts.empty:
                    # Normalise flight key for merging
                    _emp_starts["_fk"] = _emp_starts["טיסה"].astype(str).str.replace(" ", "").str.upper()
                    # Find which column in display_df holds the flight number
                    _flt_col = next(
                        (c for c in display_df.columns if "טיסה" in str(c) or "flight" in str(c).lower()),
                        display_df.columns[0] if not display_df.empty else None
                    )
                    if _flt_col:
                        _disp_fk = display_df[_flt_col].astype(str).str.replace(" ", "").str.upper()
                        _start_map = dict(zip(_emp_starts["_fk"], _emp_starts["_emp_start"]))

                        def _emp_sort_key(fk):
                            s = _start_map.get(fk, "99:99")
                            try:
                                h, m = str(s).split(":")[:2]
                                t = int(h) * 60 + int(m)
                                # Pivot at 20:00 so night starts (22:xx, 23:xx, 00:xx)
                                # sort before early-morning (03:xx, 04:xx, 05:xx).
                                return (t - 20 * 60) % 1440
                            except Exception:
                                return 9999

                        _sort_keys = _disp_fk.apply(_emp_sort_key).values
                        display_df = display_df.iloc[_sort_keys.argsort()]
        _highlighted_tasks = st.session_state.get("_ar_highlighted", set())
        _transfer_delay_tasks = st.session_state.get("_transfer_delay_highlighted", set())
        _gap_sep_shown = False   # separator before midnight-crossing block already shown
        for _, row in display_df.iterrows():
            _cur_crossing = bool(row.get("_midnight_crossing", False))
            # Insert separator just before the first midnight-crossing flight
            if _cur_crossing and not _gap_sep_shown:
                st.markdown(
                    '<div dir="rtl" style="text-align:center;padding:10px 0;margin:8px 0;'
                    'border-top:2px dashed #666;border-bottom:2px dashed #666;'
                    'color:#888;font-size:13px;letter-spacing:1px;">'
                    '🌙 טיסות לילה — פעילות מתחילה לפני חצות</div>',
                    unsafe_allow_html=True,
                )
                _gap_sep_shown = True
            render_flight_card_with_swap(
                row,
                st.session_state["schedule_df"],
                st.session_state["employees_snap"],
                highlighted_tasks=_highlighted_tasks,
                transfer_delay_tasks=_transfer_delay_tasks,
            )

        # ── ניתוח פיקי ראשי צוות ──────────────────────────────────────────
        # Staffing-planning tool: finds the day's ר"צ demand peaks using the
        # same anchor/block algorithm as the an existing internal reference
        # tool (user rule 2026-09-05 — ground truth already in
        # production; see analyze_tl_peaks/_tl_reference_peaks), labeled
        # מקדים/(plain)/משני exactly like that tool's report. Reports, per
        # peak: how many flights need a ר"צ (one each, no reuse credit), how
        # many distinct real ר"צ the built schedule actually used, and
        # whether it actually covered the peak (user rule 2026-09-04: "כמה
        # טיסות יש בכל פיק וכמה ראשי צוות דרושים... כמה רצים זמינים").
        _tl_sched = st.session_state.get("schedule_df", pd.DataFrame())
        if not _tl_sched.empty:
            _tl_peaks = analyze_tl_peaks(_tl_sched)
            if _tl_peaks:
                with st.expander(f"📊 ניתוח פיקי ראשי צוות ({len(_tl_peaks)} פיקים)", expanded=False):
                    st.caption(
                        "התוויות (למשל \"פיק בוקר מקדים/בוקר/בוקר משני\") מקובצות לפי יום וחלק-יום "
                        "(בוקר/יום/צהריים/אחה\"צ/ערב/לילה) — הפיק הגדול ביותר בקבוצה (כולל בונוס "
                        "לטיסות BKK/HKT שדורשות 2 ר\"צ) מקבל את השם הפשוט, פיקים לפניו \"מקדים\", "
                        "פיקים אחריו \"משני\". "
                        "\"דרושים\" = מספר משבצות הזמן שצריך למלא, לא מספר אנשים שונים — "
                        "אותו ר\"צ יכול למלא כמה משבצות ברצף אם הן לא חופפות בזמן. "
                        "\"זמינים\" = כמה ראשי צוות שונים הסידור בפועל שיבץ לפיק הזה. "
                        "\"כיסוי בפועל\" מבוסס על הסידור שכבר נבנה, לא על חישוב עצמאי."
                    )
                    for _peak in _tl_peaks:
                        # "יש מספיק?" is read directly off the ACTUAL schedule
                        # built for this peak (the ❌-unfilled ר"צ slots), not
                        # off available_rc — a quick check showed a naive
                        # first-fit match against available_rc over-predicts
                        # shortages (flagged 4 unfillable flights in one peak
                        # where the real scheduler, doing proper candidate
                        # selection incl. legitimate back-to-back reuse, left
                        # only 1 actually unfilled).
                        _coverage = (
                            f"⚠️ **חסר** — {_peak['unfilled']} מתוך {_peak['n_flights']} לא מאוישות"
                            if _peak["unfilled"] else "✅ **מכוסה במלואו**"
                        )
                        st.markdown(
                            f"**{_peak['label']}** — {_peak['n_flights']} טיסות · "
                            f"דרושים **{_peak['required_rc']}** ראשי צוות · "
                            f"זמינים **{_peak['available_rc']}** · {_coverage}"
                        )

        # ── אבחון טיסות עם תפקידים חסרים ─────────────────────────────────────
        _sched_diag = st.session_state.get("schedule_df", pd.DataFrame())
        _missing_flights = _sched_diag[
            _sched_diag["עובד"].astype(str).str.contains("❌", na=False)
        ]["טיסה"].unique().tolist() if not _sched_diag.empty else []

        # Chronological order for the missing-roles diagnostic. Raw departure
        # time puts the after-midnight NIGHT flights (LY027/LY083/LY001/LY005,
        # boarding 22:xx-00:xx on 20.07 and departing 00:30-01:05 on 21.07) at
        # the TOP because 00:30 < 06:00 numerically — but they belong at the
        # BOTTOM chronologically (they are the tail of the night shift). Pivot
        # at 02:00 (the operational-day start = the 02:00 shift): any flight
        # whose earliest task boards BEFORE 02:00 is night-shift tail and sorts
        # LAST; everything boarding 02:00+ sorts by boarding time (user
        # 2026-07-20).
        if _missing_flights and not _sched_diag.empty:
            def _mf_chrono_key(_fnum):
                _ft = _sched_diag[_sched_diag["טיסה"].astype(str) == str(_fnum)]
                _starts = [
                    time_to_minutes(clean_text(str(_x)))
                    for _x in _ft["התחלה"].tolist()
                    if is_time_text(clean_text(str(_x)))
                ]
                if not _starts:
                    return 10 ** 6
                return (min(_starts) - 2 * 60) % 1440
            _missing_flights = sorted(_missing_flights, key=_mf_chrono_key)

        if _missing_flights:
            with st.expander(f"🔍 אבחון תפקידים חסרים ({len(_missing_flights)} טיסות)", expanded=False):
                _diag_flights_df = st.session_state.get("flights_snap", pd.DataFrame())
                _diag_emps_df    = st.session_state.get("employees_snap", pd.DataFrame())
                _diag_sched_list = _sched_diag.to_dict("records")

                for _fnum in _missing_flights:
                    _missing_roles_for_flight = _sched_diag[
                        (_sched_diag["טיסה"].astype(str) == str(_fnum)) &
                        (_sched_diag["עובד"].astype(str).str.contains("❌", na=False))
                    ]["תפקיד בסיס"].unique().tolist()

                    _flight_row = _diag_flights_df[
                        _diag_flights_df["טיסה"].astype(str).str.strip() == str(_fnum).strip()
                    ]
                    if _flight_row.empty:
                        continue
                    _flight_dict = _flight_row.iloc[0].to_dict()

                    st.markdown(f"### ✈️ טיסה {_fnum} ← {_flight_dict.get('יעד','')} ({_flight_dict.get('המראה','')})")

                    # ── USA flight with no gate yet → explain the shortage is
                    # due to missing gate info, and offer manual gate entry.
                    # A TSA inspector is stationed at a specific pier/checkpoint,
                    # which is derived from the gate — with no gate the pier is
                    # unknown, so the inspector slot can't be resolved. FIDS often
                    # leaves late-night US flights gate-less until closer to
                    # departure (user rule 2026-07-13).
                    _diag_dest = clean_text(str(_flight_dict.get("יעד", "")))
                    _diag_gate = clean_text(str(_flight_dict.get("גייט", "")))
                    if not _diag_gate or _diag_gate.lower() == "nan":
                        _diag_gate = clean_text(str(_flight_dict.get("שלוחה", "")))
                    _diag_gate_missing = (not _diag_gate) or _diag_gate.lower() == "nan"
                    if (_diag_dest in USA_TSA_DESTS and _diag_gate_missing
                            and "מפקח TSA" in _missing_roles_for_flight):
                        # Plain, short, single-clause sentences — a longer sentence
                        # mixing an em-dash / bold markdown near the mid-sentence
                        # "TSA" (LTR) token got visually scrambled by the browser's
                        # bidi algorithm (same class of bug as the terminal-transfer
                        # label fix: see [[project_terminal1]] "never mix an arrow/
                        # symbol character with adjacent Hebrew+LTR tokens").
                        st.info(
                            "ℹ️ לטיסה זו לארצות הברית עדיין לא הוזן שער יציאה. "
                            "בלי שער אי אפשר לדעת לאיזו שלוחה לשבץ מפקח TSA, "
                            "וזו הסיבה לחוסר בשיבוץ. ניתן להזין את השער ידנית כאן. "
                            "לאחר ההזנה הסידור ייבנה מחדש והמידע יתעדכן בכל מקום "
                            "רלוונטי באתר."
                        )
                        _gk = f"manual_gate_input_{_fnum}"
                        _gc1, _gc2 = st.columns([3, 1])
                        with _gc1:
                            st.text_input(
                                "שער יציאה (למשל C8 / D6):",
                                key=_gk,
                                placeholder="הזן שער...",
                            )
                        with _gc2:
                            st.markdown("<div style='padding-top:28px'></div>",
                                        unsafe_allow_html=True)
                            if st.button("✅ עדכן שער ובנה מחדש",
                                         key=f"apply_gate_{_fnum}",
                                         use_container_width=True):
                                _g_in = clean_text(st.session_state.get(_gk, "")).upper()
                                if not _g_in:
                                    st.warning("יש להזין ערך שער תקין.")
                                else:
                                    # Don't mutate flights_editor_df / rebuild here —
                                    # the flights data_editor and other widgets
                                    # already rendered earlier in THIS script run,
                                    # so a late in-place mutation here doesn't
                                    # reliably reach them (found via real data
                                    # 2026-07-13: the gate update silently didn't
                                    # take effect). Instead, stash the request and
                                    # rerun — the top of the script applies it
                                    # (right after flights_editor_df is built) and
                                    # rebuilds (right after _run_build_schedule is
                                    # defined), both well before anything renders.
                                    st.session_state["_pending_gate_update"] = {
                                        "fnum": str(_fnum), "gate": _g_in,
                                    }
                                    st.rerun()

                    for _missing_role in _missing_roles_for_flight:
                        st.markdown(f"**❌ חסר: {_missing_role}**")
                        # Build assignments list up to (but not including) this missing slot
                        _prior = [t for t in _diag_sched_list
                                  if t.get("טיסה") != str(_fnum) or "❌" not in str(t.get("עובד",""))]
                        _diag_results = explain_missing_role(
                            _flight_dict, _missing_role, _diag_emps_df, _prior
                        )
                        _diag_data = []
                        for _d in _diag_results:
                            _diag_data.append({"עובד": _d["name"], "סיבה לדחייה": _d["reason"]})
                        if _diag_data:
                            st.dataframe(
                                pd.DataFrame(_diag_data),
                                use_container_width=True,
                                hide_index=True,
                            )

                        # ── "הכרח שיבוץ" (force-assign) — user 2026-07-20 ──────
                        # A manual override: for a candidate who is qualified,
                        # within shift, and at the right terminal but currently
                        # BUSY on another flight (reason starts "כבר משובץ") — or
                        # genuinely free ("✅ זמין") — offer a button that pulls
                        # them onto THIS slot and reshuffles their conflicting
                        # tasks (best-effort back-fill; a shortage may just move).
                        # NOT offered for "לא מכסה"/"הפרדת טרמינלים" candidates —
                        # those are hard shift/terminal constraints, not a
                        # priority-order choice.
                        _force_targets = [
                            _d for _d in _diag_results
                            if str(_d.get("reason", "")).startswith("כבר משובץ")
                            or "זמין" in str(_d.get("reason", ""))
                        ]
                        _fslot_mask = (
                            (_sched_diag["טיסה"].astype(str) == str(_fnum)) &
                            (_sched_diag["תפקיד בסיס"].astype(str) == str(_missing_role)) &
                            (_sched_diag["עובד"].astype(str).str.contains("❌", na=False))
                        )
                        _fslot_idx = _sched_diag[_fslot_mask].index
                        if _force_targets and len(_fslot_idx):
                            st.caption(
                                "🔧 הכרח שיבוץ — משבץ את העובד/ת לטיסה זו גם אם הוא/היא "
                                "משובצ/ת כרגע במקום אחר; שאר משימותיו/ה יעודכנו בהתאם:"
                            )
                            for _ft in _force_targets:
                                _fc1, _fc2, _fc3 = st.columns([4, 1, 1])
                                with _fc1:
                                    st.markdown(
                                        f"<div style='direction:rtl;padding-top:7px;font-size:13px;'>"
                                        f"<b>{safe_html(_ft['name'])}</b> "
                                        f"<span style='color:#888;'>— {safe_html(_ft['reason'])}</span></div>",
                                        unsafe_allow_html=True,
                                    )
                                with _fc2:
                                    if st.button(
                                        "הכרח שיבוץ",
                                        key=f"force_{_fnum}_{_missing_role}_{_ft['name']}",
                                        use_container_width=True,
                                    ):
                                        st.session_state["schedule_df"] = force_assign_worker(
                                            _sched_diag, _fslot_idx[0], _ft["name"], _diag_emps_df,
                                            keep_prior=True,
                                        )
                                        for _k in ["labeled_df", "workload_df",
                                                   "continuity_df", "output_df",
                                                   "_ar_highlighted"]:
                                            st.session_state.pop(_k, None)
                                        st.success(
                                            f"✅ {_ft['name']} שובצ/ה בהכרח ל-{_missing_role} "
                                            f"בטיסה {_fnum}. שאר משימותיו/ה עודכנו."
                                        )
                                        st.rerun()
                                with _fc3:
                                    # "שבץ במקום" — move the worker here, freeing
                                    # ALL their overlapping tasks (no late arrival):
                                    # e.g. pull them off an earlier דייל slot to
                                    # cover this ר"צ. User rule 2026-07-22.
                                    if st.button(
                                        "שבץ במקום",
                                        key=f"move_{_fnum}_{_missing_role}_{_ft['name']}",
                                        use_container_width=True,
                                    ):
                                        st.session_state["schedule_df"] = force_assign_worker(
                                            _sched_diag, _fslot_idx[0], _ft["name"], _diag_emps_df,
                                            keep_prior=False,
                                        )
                                        for _k in ["labeled_df", "workload_df",
                                                   "continuity_df", "output_df",
                                                   "_ar_highlighted"]:
                                            st.session_state.pop(_k, None)
                                        st.success(
                                            f"✅ {_ft['name']} הועבר/ה ל-{_missing_role} "
                                            f"בטיסה {_fnum} במקום השיבוץ הקודם. "
                                            f"שאר משימותיו/ה עודכנו."
                                        )
                                        st.rerun()

                        # ── "הכרח שיבוץ" גם למי שנדחה על "הפרדת טרמינלים" —
                        # LAST RESORT בלבד (user rule 2026-07-22: "רק במידה ואין
                        # שום אופציה אחרת"). מוצג רק כש-_force_targets (למעלה)
                        # ריק — אין אף מועמד/ת "כבר משובץ"/"זמין". העובד/ת
                        # משובץ/ת לטיסה זו למרות שהוא/היא נמצא/ת בטרמינל האחר
                        # לפי מיפוי הזמינות שלו/ה כרגע — כלומר זה מעכב את מעבר/ה
                        # לטרמינל השני, לא דוחה טיסה קונקרטית אחרת (force_assign_
                        # worker לא מוצא כאן משימה חופפת אמיתית לפנות, כי מדובר
                        # רק בחלון-זמינות תיאורטי, לא שיבוץ בפועל).
                        if not _force_targets and len(_fslot_idx):
                            _term_targets = [
                                _d for _d in _diag_results
                                if "הפרדת טרמינלים" in str(_d.get("reason", ""))
                            ]
                            if _term_targets:
                                st.caption(
                                    "⚠️ מוצא אחרון — אין אף מועמד/ת זמינ/ה או במקום אחר; "
                                    "הכפתור הבא ישבץ עובד/ת שנמצא/ת כרגע בטרמינל האחר, "
                                    "ובכך יעכב את מעבר/ה בין הטרמינלים:"
                                )
                                for _tt in _term_targets:
                                    _tc1, _tc2 = st.columns([4, 1])
                                    with _tc1:
                                        st.markdown(
                                            f"<div style='direction:rtl;padding-top:7px;font-size:13px;'>"
                                            f"<b>{safe_html(_tt['name'])}</b> "
                                            f"<span style='color:#888;'>— {safe_html(_tt['reason'])}</span></div>",
                                            unsafe_allow_html=True,
                                        )
                                    with _tc2:
                                        if st.button(
                                            "הכרח שיבוץ",
                                            key=f"force_term_{_fnum}_{_missing_role}_{_tt['name']}",
                                            use_container_width=True,
                                        ):
                                            st.session_state["schedule_df"] = force_assign_worker(
                                                _sched_diag, _fslot_idx[0], _tt["name"], _diag_emps_df,
                                                keep_prior=True,
                                                reason_tag="הכרח שיבוץ - מעכב מעבר בין טרמינלים",
                                            )
                                            _tdh = set(st.session_state.get("_transfer_delay_highlighted", set()))
                                            _tdh.add(_fslot_idx[0])
                                            st.session_state["_transfer_delay_highlighted"] = _tdh
                                            for _k in ["labeled_df", "workload_df",
                                                       "continuity_df", "output_df",
                                                       "_ar_highlighted"]:
                                                st.session_state.pop(_k, None)
                                            st.success(
                                                f"✅ {_tt['name']} שובצ/ה בהכרח ל-{_missing_role} "
                                                f"בטיסה {_fnum} (מוצא אחרון) — מעבר/ה בין "
                                                f"הטרמינלים יתעכב בהתאם."
                                            )
                                            st.rerun()

                        # ── "הכרח שיבוץ" גם למי שנדחה על "משמרת לא מכסה" אבל יש
                        # לו/ה מעבר-טרמינל מתוכנן (user rule 2026-07-25, מוצג
                        # תמיד — גם כשקיימים מועמדי-_force_targets אחרים, לא
                        # רק כמוצא אחרון: "יש להציג אותה באופציה"). שונה
                        # מהקטגוריה הקודמת: כאן העובד/ת לא ב"טרמינל הלא נכון"
                        # — היא בטרמינל הנכון, אבל חלון הזמינות שלה נחתך מוקדם
                        # מדי בגלל חוצץ ההפסקה+מעבר שנשמר לפני המעבר בפועל (ר'
                        # project_terminal1.md — "T1->T3 transfer buffer").
                        # מציעים לה את הטיסה הזו במחיר עיכוב המעבר לטרמינל
                        # השני (במקום לצאת בזמן להפסקה+מעבר, היא ממשיכה לעבוד
                        # ומגיעה מאוחר יותר). מזוהה ע"י: יש לעובד/ת שדה "מעבר
                        # טרמינל" לא ריק ב-employees_df (כלומר היא בעלת מעבר
                        # מתוכנן היום), והסיבה שהוצגה היא "משמרת ... לא מכסה"
                        # (לא הפרדת טרמינלים — היא כבר בטרמינל הנכון).
                        if len(_fslot_idx):
                            _transfer_names = set()
                            if "מעבר טרמינל" in _diag_emps_df.columns:
                                _tr_col = _diag_emps_df["מעבר טרמינל"].astype(str).str.strip()
                                _transfer_names = set(
                                    _diag_emps_df.loc[_tr_col.ne(""), "שם"].astype(str).str.strip()
                                )
                            _shift_short_targets = [
                                _d for _d in _diag_results
                                if str(_d.get("reason", "")).startswith("משמרת")
                                and str(_d.get("name", "")).strip() in _transfer_names
                            ]
                            if _shift_short_targets:
                                st.caption(
                                    "⚠️ מוצא אחרון — העובד/ת בטרמינל הנכון אך חלון "
                                    "הזמינות שלה קצר מדי בגלל חוצץ המעבר לטרמינל השני; "
                                    "הכפתור הבא ישבץ אותה כאן ויעכב את המעבר שלה:"
                                )
                                for _st_t in _shift_short_targets:
                                    _sc1, _sc2 = st.columns([4, 1])
                                    with _sc1:
                                        st.markdown(
                                            f"<div style='direction:rtl;padding-top:7px;font-size:13px;'>"
                                            f"<b>{safe_html(_st_t['name'])}</b> "
                                            f"<span style='color:#888;'>— {safe_html(_st_t['reason'])}</span></div>",
                                            unsafe_allow_html=True,
                                        )
                                    with _sc2:
                                        if st.button(
                                            "הכרח שיבוץ",
                                            key=f"force_shortshift_{_fnum}_{_missing_role}_{_st_t['name']}",
                                            use_container_width=True,
                                        ):
                                            st.session_state["schedule_df"] = force_assign_worker(
                                                _sched_diag, _fslot_idx[0], _st_t["name"], _diag_emps_df,
                                                keep_prior=True,
                                                reason_tag="הכרח שיבוץ - מעכב מעבר בין טרמינלים",
                                            )
                                            _tdh = set(st.session_state.get("_transfer_delay_highlighted", set()))
                                            _tdh.add(_fslot_idx[0])
                                            st.session_state["_transfer_delay_highlighted"] = _tdh
                                            for _k in ["labeled_df", "workload_df",
                                                       "continuity_df", "output_df",
                                                       "_ar_highlighted"]:
                                                st.session_state.pop(_k, None)
                                            st.success(
                                                f"✅ {_st_t['name']} שובצ/ה בהכרח ל-{_missing_role} "
                                                f"בטיסה {_fnum} (מוצא אחרון) — מעבר/ה בין "
                                                f"הטרמינלים יתעכב בהתאם."
                                            )
                                            st.rerun()

                        # Near-miss candidates: qualified, conflict-free workers
                        # whose shift ends just short (≤30 min) of the task end —
                        # assignable only if they agree to extend. Uses the
                        # conflict-safe get_extendable_candidates_for_swap (NOT
                        # the raw "❓" diagnostic reasons, which don't check for
                        # a scheduling conflict and could offer a double-booked
                        # worker). Offered here because the normal swap flow
                        # hard-excludes anyone outside their shift hours (user
                        # rule 2026-07-13: "יש לשאול את המפקחים... האם הם מוכנים
                        # למשוך את משמרתם... הצג אותם כאופציה לשיבוץ אבל ציין כי
                        # זה תלוי באישור העובד").
                        _slot_mask_nm = (
                            (_sched_diag["טיסה"].astype(str) == str(_fnum)) &
                            (_sched_diag["תפקיד בסיס"].astype(str) == str(_missing_role)) &
                            (_sched_diag["עובד"].astype(str).str.contains("❌", na=False))
                        )
                        _slot_idx_nm = _sched_diag[_slot_mask_nm].index
                        _near_miss = (
                            get_extendable_candidates_for_swap(
                                _sched_diag, _diag_emps_df, str(_fnum),
                                str(_missing_role), _slot_idx_nm[0]
                            ) if len(_slot_idx_nm) else []
                        )
                        if _near_miss:
                            _nm_labels = [
                                f"{n}  (משובצ/ת ל{d} — נדרש אישור)" if d
                                else f"{n}  (פער {g} דק׳ מעבר לסיום המשמרת)"
                                for (n, g, d) in _near_miss
                            ]
                            st.caption(
                                "⏱️ שיבוץ בכפוף לאישור — עובד/ת שצריכ/ה להישאר "
                                "מעבר לסיום המשמרת, או שמשובצ/ת לתפקיד אחר "
                                "שיש למשוך אותו/ה ממנו:"
                            )
                            _nm_c1, _nm_c2 = st.columns([3, 1])
                            _nm_key = f"nm_pick_{_fnum}_{_missing_role}"
                            with _nm_c1:
                                _nm_choice_label = st.selectbox(
                                    "", _nm_labels, key=_nm_key,
                                    label_visibility="collapsed",
                                )
                            with _nm_c2:
                                if st.button(
                                    "שבץ בכפוף לאישור",
                                    key=f"nm_assign_{_fnum}_{_missing_role}",
                                    use_container_width=True,
                                ):
                                    _nm_choice = _near_miss[_nm_labels.index(_nm_choice_label)][0]
                                    if len(_slot_idx_nm):
                                        st.session_state["schedule_df"] = do_swap(
                                            _sched_diag, _slot_idx_nm[0], _nm_choice, "unassign"
                                        )
                                        for _k in ["labeled_df", "workload_df",
                                                   "continuity_df", "output_df",
                                                   "_ar_highlighted"]:
                                            st.session_state.pop(_k, None)
                                        st.success(
                                            f"✅ {_nm_choice} שובצ/ה ל-{_missing_role} בטיסה {_fnum} "
                                            f"— יש לוודא אישור להארכת המשמרת בפועל."
                                        )
                                        st.rerun()
                    st.markdown("---")

        # ── Tab: פנויים באולם ─────────────────────────────────────────────────
    if active_main_tab == TAB_AVAILABLE:
        st.subheader("🟡 עובדים פנויים באולם היציאה")
        available_df = build_available_in_hall(
            live_schedule, live_employees, live_flights
        )
        if available_df.empty:
            st.success("אין עובדים פנויים כרגע באולם היציאה 🎉")
        else:
            total_free = available_df["עובד"].nunique()
            long_gaps = available_df[available_df["פנות (דק׳)"] >= 60]["עובד"].nunique()
            sc1, sc2 = st.columns(2)
            sc1.metric("עובדים פנויים באולם", total_free)
            sc2.metric("מתוכם פנויים שעה+", long_gaps)
            st.markdown("---")
            roles = ["הכל"] + sorted(
                available_df["תפקיד עיקרי"].dropna().unique().tolist()
            )
            selected_role = st.selectbox(
                "סנן לפי תפקיד:", roles, key="avail_role_filter"
            )
            filtered = (
                available_df
                if selected_role == "הכל"
                else available_df[available_df["תפקיד עיקרי"] == selected_role]
            )
            for _, r in filtered.iterrows():
                gap_color = "#fff3cd" if r["פנות (דק׳)"] < 60 else "#d4edda"
                gap_border = "#ffc107" if r["פנות (דק׳)"] < 60 else "#28a745"
                st.markdown(
                    f'<div style="direction:rtl;background:{gap_color};border-right:5px solid {gap_border};'
                    f'border-radius:10px;padding:10px 14px;margin-bottom:8px;font-size:14px;">'
                    f'<strong>{safe_html(r["עובד"])}</strong> · {safe_html(r["תפקיד עיקרי"])} · משמרת: {safe_html(r["משמרת"])}<br>'
                    f'🕒 פנוי: <strong>{safe_html(r["פנוי מ"])} – {safe_html(r["פנוי עד"])}</strong>'
                    f' ({r["פנות (דק׳)"]} דק׳) · הבא: {safe_html(r["משימה הבאה"])}<br>'
                    f'<span style="color:#555;font-size:12px">{safe_html(r["הערה"])}</span></div>',
                    unsafe_allow_html=True,
                )

    ## — Tab: זרימת עבודה ─────────────────────────────────────────────────────
    if active_main_tab == TAB_WORKFLOW:
        _wf_labeled_outer = st.session_state.get("labeled_df", pd.DataFrame())
        if _wf_labeled_outer.empty:
            st.info("יש לבנות סידור עבודה תחילה כדי לצפות בזרימת העבודה.")
        else:
            if "wf_briefing" not in st.session_state:
                st.session_state["wf_briefing"] = {}

            @st.fragment
            def _render_wf_tab():
                _wf_labeled = st.session_state.get("labeled_df", pd.DataFrame())
                _wf_emps    = st.session_state.get("employees_snap", pd.DataFrame())

                st.markdown("<h3 style='text-align:right'>📋 זרימת עבודה לפי עובד</h3>",
                            unsafe_allow_html=True)

                # Hover tooltip for (ט) trainee-attendant name tags — shows which
                # veteran דייל they're paired with, or that they're unpaired
                # (user rule 2026-07-17). Injected once for the whole tab.
                st.markdown(
                    '<style>'
                    '.wf-trainee-tag{position:relative;cursor:help;'
                    'border-bottom:1px dotted #888;}'
                    '.wf-trainee-tag .wf-tooltip-box{visibility:hidden;opacity:0;'
                    'transition:opacity .15s;position:absolute;z-index:50;'
                    'bottom:125%;right:0;background:#1e293b;color:#e0e0e0;'
                    'border:1px solid #00c9be;border-radius:8px;padding:6px 10px;'
                    'font-size:12px;white-space:nowrap;'
                    'box-shadow:0 4px 12px rgba(0,0,0,.4);}'
                    '.wf-trainee-tag:hover .wf-tooltip-box{visibility:visible;opacity:1;}'
                    '</style>',
                    unsafe_allow_html=True,
                )

                # ── Alerts ────────────────────────────────────────────────────
                from datetime import datetime as _wf_now_dt
                _wf_now_min = _wf_now_dt.now().hour * 60 + _wf_now_dt.now().minute
                for _an, _ai in list(st.session_state["wf_briefing"].items()):
                    if _ai.get("state") == 1:
                        try:
                            _fh2, _fm2 = str(_ai.get("first_start", "00:00")).split(":")
                            _ft_m2 = int(_fh2) * 60 + int(_fm2)
                            _diff2 = (_ft_m2 - _wf_now_min) % 1440
                            if 0 <= _diff2 <= 15:
                                st.error(
                                    f"⚠️  {_an} — {_ai.get('first_task','')} "
                                    f"יוצאת בעוד {_diff2} דקות! יש לשלוח לאולם!"
                                )
                        except Exception:
                            pass

                # ── Search ────────────────────────────────────────────────────
                st.markdown("""
                <style>
                div[data-testid="stTextInput"]:has(input[aria-label="🔍 חפש עובד"]) {
                    direction:rtl; text-align:right; }
                div[data-testid="stTextInput"]:has(input[aria-label="🔍 חפש עובד"]) label {
                    text-align:right; width:100%; }
                div[data-testid="stTextInput"]:has(input[aria-label="🔍 חפש עובד"]) input {
                    direction:rtl; text-align:right; }
                </style>""", unsafe_allow_html=True)
                _wf_col_clear, _wf_col_search = st.columns([1, 10])
                with _wf_col_search:
                    _wf_search = st.text_input("🔍 חפש עובד", key="wf_search",
                                                placeholder="הקלד שם עובד לסינון...")
                with _wf_col_clear:
                    st.markdown("<div style='padding-top:28px'>", unsafe_allow_html=True)
                    st.button("נקה", key="wf_clear_search",
                              help="נקה חיפוש — הצג את כל העובדים",
                              on_click=lambda: st.session_state.update({"wf_search": ""}),
                              use_container_width=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                # ── סינון לפי תפקיד ──────────────────────────────────────────
                _WF_ROLE_FILTERS = {
                    "הכל": None,
                    "מפקח TSA": "מפקח TSA",
                    "ראש צוות": "ראש צוות",
                    "דייל": "דייל",
                    'מתדרכת כר"צ': "מתדרכת",
                    'מנהל כר"צ': 'מנהל כר"צ',
                }
                _wf_role_col_clear, _wf_role_col = st.columns([1, 10])
                with _wf_role_col:
                    _wf_role_choice = st.radio(
                        "סינון לפי תפקיד",
                        list(_WF_ROLE_FILTERS.keys()),
                        horizontal=True,
                        key="wf_role_filter",
                        label_visibility="collapsed",
                    )
                with _wf_role_col_clear:
                    st.button("נקה סינון", key="wf_clear_role_filter",
                              help="הצג את כלל העובדים",
                              on_click=lambda: st.session_state.update({"wf_role_filter": "הכל"}),
                              use_container_width=True)

                # שמות העובדים העומדים בסינון התפקיד הנבחר (לפי עמודת ההסמכה)
                _wf_role_names = None
                _wf_role_col_name = _WF_ROLE_FILTERS.get(_wf_role_choice)
                if _wf_role_col_name and not _wf_emps.empty and _wf_role_col_name in _wf_emps.columns:
                    _wf_role_names = set(
                        clean_text(str(n)) for n in _wf_emps.loc[
                            _wf_emps[_wf_role_col_name].astype(str).str.strip() == "כן", "שם"
                        ]
                    )

                # ── מסך נפרד לזרימת העבודה בטרמינל 1 ─────────────────────────
                # שיוך עובד לטרמינל נקבע לפי המשימות שלו בפועל (טרמינל הגייט).
                # עובד מעבר (T3→T1) מופיע בשני המסכים — כל הזרימה שלו רלוונטית
                # לשני מנהלי המשמרת.
                def _hm(t):
                    try:
                        h, m = str(t).strip().split(":")
                        return int(h) * 60 + int(m)
                    except Exception:
                        return 9999

                _wf_term_of_task = {}
                _wf_t1_names, _wf_t3_names = set(), set()
                _wf_t1_spans = []          # חלונות עבודה פעילים בטרמינל 1
                for _tri, _tr_ in _wf_labeled.iterrows():
                    _wn_ = clean_text(str(_tr_.get("עובד", "")))
                    if not _wn_ or "❌" in _wn_:
                        continue
                    _tt_ = get_terminal(clean_text(str(_tr_.get("_gate", ""))))
                    if _tt_ == "1":
                        _wf_t1_names.add(_wn_)
                        _s1_ = _hm(str(_tr_.get("התחלה", "")))
                        _e1_ = _hm(str(_tr_.get("סיום", "")))
                        if 0 <= _s1_ < 1440 and 0 <= _e1_ < 1440:   # _hm=9999 בכישלון
                            _wf_t1_spans.append((_s1_, _e1_))
                    else:
                        _wf_t3_names.add(_wn_)

                # מעל כמה דקות רצופות בלי טיסה פעילה כבר לא שווה להישאר באולם
                _T1_IDLE_TOLERANCE = 30

                def _t1_active_between(_from_m, _to_m, _skip_name=""):
                    """
                    האם ההמתנה מכוסה בטיסות טרמינל 1 פעילות שאפשר לעזור בהן?

                    לא מספיק שיש חפיפה כלשהי — צריך שלא ייווצר קטע ריק ארוך.
                    אצל TL#5 ההמתנה 13:00-15:25 חופפת טיסה שמסתיימת
                    13:45, ואחריה שעה וחצי שבהן אין כלום; שם עדיף להחזיר
                    לדלפקים ולרדת שוב (הנחיית משתמשת 2026-08-21).
                    """
                    _len = (_to_m - _from_m) % 1440
                    if _len <= 0:
                        return False
                    # כיסוי החלון ע"י טיסות ט1, בקואורדינטות יחסיות לתחילתו
                    _cov = []
                    for _a_, _b_ in _wf_t1_spans:
                        _ra = (_a_ - _from_m) % 1440
                        _rb = _ra + ((_b_ - _a_) % 1440)
                        _ra, _rb = max(0, _ra), min(_len, _rb)
                        if _rb > _ra:
                            _cov.append((_ra, _rb))
                    _cov.sort()
                    _idle, _cur = 0, 0
                    for _ra, _rb in _cov:
                        if _ra > _cur:
                            _idle = max(_idle, _ra - _cur)
                        _cur = max(_cur, _rb)
                    _idle = max(_idle, _len - _cur)
                    return _idle <= _T1_IDLE_TOLERANCE

                _wf_term_names = None
                if _wf_t1_names:
                    _wf_term_choice = st.radio(
                        "תצוגת טרמינל — זרימת עבודה",
                        ["הכל", "טרמינל 3", "טרמינל 1"],
                        horizontal=True,
                        key="wf_term_filter",
                        label_visibility="collapsed",
                    )
                    if _wf_term_choice == "טרמינל 1":
                        _wf_term_names = _wf_t1_names
                        st.markdown(
                            '<div style="direction:rtl;text-align:right;font-weight:800;'
                            'color:#e8930c;font-size:15px;padding:2px 0 6px;">'
                            '🛄 זרימת עבודה — טרמינל 1</div>',
                            unsafe_allow_html=True,
                        )
                    elif _wf_term_choice == "טרמינל 3":
                        _wf_term_names = _wf_t3_names

                # ── Helpers ───────────────────────────────────────────────────
                # Short chip labels, per gender. ר"צ and טרייני ר"צ have a
                # single form for everyone (user rules 2026-08-06: "ראש צוות זה
                # לא ראשת צוות, יש תמיד להשתמש במונח ר\"צ" / "אין כזה דבר
                # טריינית"); the rest follow the worker's מין column.
                _ROLE_SHORT = {
                    "ראש צוות": ('ר"צ', 'ר"צ'),
                    "טרייני רצ": ('טרייני ר"צ', 'טרייני ר"צ'),
                    "דייל": ("דייל", "דיילת"),
                    "מתאם תורים": ("מ.ת", "מ.ת"),
                    "מפקח TSA": ("מפקח", "מפקחת"),
                    "שומר TSA": ("שומר", "שומרת"),
                }
                _wf_gender_col = next(
                    (c for c in _wf_emps.columns
                     if clean_text(c) in {"מין", "gender", "זכר/נקבה", "מגדר"}),
                    None) if not _wf_emps.empty else None
                _wf_is_male = {}
                if _wf_gender_col:
                    for _, _gr in _wf_emps.iterrows():
                        _wf_is_male[clean_text(str(_gr.get("שם", "")))] = (
                            clean_text(str(_gr.get(_wf_gender_col, ""))).upper() in MALE_VALUES
                        )

                def _short_role(r, worker=""):
                    _pair = _ROLE_SHORT.get(str(r).strip())
                    if not _pair:
                        return str(r).strip()[:5]
                    return _pair[0] if _wf_is_male.get(clean_text(str(worker)), False) else _pair[1]

                # ── Shift maps ────────────────────────────────────────────────
                _wf_shift_map = {}
                _se_col = next((c for c in ([] if _wf_emps.empty else _wf_emps.columns)
                                if "סוף" in str(c)), "")
                _wf_shift_end_map = {}
                # Multi-window availability — a worker can genuinely be rostered for
                # TWO separate shifts the same day (e.g. finishes an overnight shift,
                # goes home, returns later for a distinct evening/night shift). זמינות
                # holds all such windows comma-separated; used below to (a) show every
                # real shift range next to the name instead of just the primary one,
                # and (b) sort that worker's chips in plain chronological order rather
                # than the single-continuous-shift pivot, which wraps early tasks from
                # a SECOND shift to the wrong end of the row.
                _wf_avail_map = {}
                # Full (pre-terminal-split) range per זמינות window, same index
                # order as _wf_avail_map — a terminal transfer cuts one shift
                # into a source-terminal window + a destination-terminal window
                # (e.g. "21:00-23:45" + "01:00-07:00"); both entries here hold
                # the FULL original "21:00-07:00" span. Used only for the
                # workflow-tab DISPLAY/grouping so a transferring worker shows
                # ONE row with their real full shift (user rule 2026-07-09:
                # "אם לעובד יש מעבר טרמינלים יש את שעות המשמרת המלאות שלו ולא
                # רק את השעות שבו הוא נמצא בטרמינל הספציפי") — task-to-row
                # matching still uses the precise per-terminal windows.
                _wf_full_avail_map = {}
                if not _wf_emps.empty and "זמינות מלאה" in _wf_emps.columns:
                    for _, _wr in _wf_emps.iterrows():
                        _wn = clean_text(str(_wr.get("שם", "")))
                        _avf = str(_wr.get("זמינות מלאה", "")).strip()
                        _full_windows = [w.strip() for w in _avf.split(",") if w.strip() and "-" in w]
                        if _full_windows:
                            _wf_full_avail_map[_wn] = _full_windows
                # For a worker with exactly ONE זמינות window (typically the
                # restricted "01:30-{end}" tail of a completed overnight shift, see
                # data_loader.py), THAT window — not the nominal (full, pre-
                # restriction) שmirat start — is what actually governs which
                # flights they can work today. e.g. בן דב אורנה's nominal shift is
                # "22:00" (sorts near the bottom), but she can only actually work
                # from 01:30 onward — she should sort near other early-morning
                # workers, not near tonight's night-shift workers.
                _wf_single_avail_sort_m = {}
                if not _wf_emps.empty and "זמינות" in _wf_emps.columns:
                    for _, _wr in _wf_emps.iterrows():
                        _wn = clean_text(str(_wr.get("שם", "")))
                        _av = str(_wr.get("זמינות", "")).strip()
                        _windows = [w.strip() for w in _av.split(",") if w.strip() and "-" in w]
                        if _windows:
                            _wf_avail_map[_wn] = _windows
                            if len(_windows) == 1:
                                _parsed_single = _windows[0].split("-", 1)
                                if len(_parsed_single) == 2:
                                    _wf_single_avail_sort_m[_wn] = _hm(_parsed_single[0].strip())
                if not _wf_emps.empty and "תחילת משמרת" in _wf_emps.columns:
                    for _, _wr in _wf_emps.iterrows():
                        _wn = clean_text(str(_wr.get("שם", "")))
                        _wf_shift_map[_wn] = _hm(str(_wr.get("תחילת משמרת", "")))
                        if _se_col:
                            _wf_shift_end_map[_wn] = str(_wr.get(_se_col, "")).strip()

                # Terminal-transfer indicator ("1@02:00" → base terminal 3,
                # transfers to terminal 1 at 02:00) — shown next to the shift
                # hours so a mid-shift terminal split is visible at a glance,
                # not just inferred from the per-flight ת1/ת3 badges.
                _wf_transfer_map = {}
                _wf_transfer_raw = {}   # name -> (to_terminal, at_HHMM, required_break_minutes)
                if not _wf_emps.empty and "מעבר טרמינל" in _wf_emps.columns and "טרמינל" in _wf_emps.columns:
                    for _, _wr in _wf_emps.iterrows():
                        _wn = clean_text(str(_wr.get("שם", "")))
                        _tm = re.match(r"^(\d)@(\d{1,2}:\d{2})$", clean_text(str(_wr.get("מעבר טרמינל", ""))))
                        if _tm:
                            _base_term = clean_text(str(_wr.get("טרמינל", ""))) or "3"
                            # Plain Hebrew wording ("עובר מטרמינל X לטרמינל Y") instead
                            # of an arrow symbol — mixing "→" with Hebrew letter+digit
                            # tokens (ט3/ט1) got visually scrambled by the browser's
                            # bidi algorithm (found via real rendering: showed reversed/
                            # jumbled). This mirrors the already-working mid-shift
                            # transfer chip text ("הפסקה ומעבר לטרמינל 1 (הגעה 02:00)").
                            _wf_transfer_map[_wn] = (
                                f"עובר מטרמינל {_base_term} לטרמינל {_tm.group(1)} "
                                f"בשעה {_tm.group(2)}"
                            )
                            _rb_wf = required_break({
                                "תחילת משמרת": str(_wr.get("תחילת משמרת", "")),
                                "סוף משמרת": str(_wr.get(_se_col, "")) if _se_col else "",
                            }) or 45
                            _wf_transfer_raw[_wn] = (_tm.group(1), _tm.group(2), _rb_wf)

                # Category tags shown after the worker's name: "(ט)" = new/trainee
                # flight attendant attached to a veteran attendant ("דייל בטרייני");
                # "(ס)" = ילד עובדים / פורש (restricted-pool worker) — mirrors the
                # existing per-flight markers in app/display.py's build_output_table.
                _wf_category_map = {}
                if not _wf_emps.empty:
                    for _, _wr in _wf_emps.iterrows():
                        _wn = clean_text(str(_wr.get("שם", "")))
                        if not _wn:
                            continue

                        def _wf_yn(col):
                            return clean_text(str(_wr.get(col, ""))) == "כן"

                        # "דייל בטרייני" (new attendant paired with a veteran) is a
                        # DISTINCT concept from "טרייני רצ" (team-leader trainee,
                        # see TL_TRAINEE_RE in data_loader.py) — a worker can carry
                        # both flags (e.g. set independently in the employees
                        # sheet), but (ט) must only mark the former. Found via real
                        # data: טל רח, a TL trainee (LY353/LY237 shown as "ר"צ
                        # טרייני"), wrongly showed "(ט)".
                        if _wf_yn("דייל בטרייני") and not _wf_yn("טרייני רצ"):
                            _wf_category_map[_wn] = "ט"
                        elif _wf_yn("ילד עובדים") or _wf_yn("ילדי עובדים") or _wf_yn("פורשים"):
                            _wf_category_map[_wn] = "ס"

                # Mentor pairing lookup for (ט) trainee attendants. Authoritative
                # source (user rule 2026-07-19): "אפשר לדעת למי מוצמד הטרייני
                # דייל, מכיוון שתמיד יופיע תחתיו בסידור" — a trainee's row is
                # always placed directly BELOW their veteran's row in the SAME
                # column of the original roster sheet. data_loader.py's
                # build_shift_map_from_excel now captures this as entry["mentor"]
                # while parsing (the last real worker name seen in that column
                # before the trainee's own row). Previously this was INFERRED from
                # shared flight+time+role in the scheduled output, which broke
                # when a flight number recurred multiple times a day with
                # different staff (found via real data: trainee-agent#4's tooltip
                # showed an unrelated worker sharing the LY117 number at a
                # different time, not her real mentor TL#13) — the roster
                # row-order is the actual ground truth, not the schedule.
                _wf_pairing_map = {}
                _wf_shift_map_ref = st.session_state.get("shift_map_ref", {})
                for _tn, _tcat in _wf_category_map.items():
                    if _tcat != "ט":
                        continue
                    _mentor_entry = _wf_shift_map_ref.get(name_key(_tn), {})
                    _wf_pairing_map[_tn] = _mentor_entry.get("mentor")  # None if genuinely unpaired

                # The restricted "01:30-{end}" window (page-1 night workers, see
                # data_loader.py) is only the PORTION relevant to today's roster —
                # the worker's real shift started the evening before. For display,
                # show the FULL nominal shift (already held in _wf_shift_map /
                # _wf_shift_end_map, which are never truncated) plus a "(started
                # yesterday)" note using the daily file's own date when parseable.
                _wf_yesterday_str = ""
                try:
                    _dfn = str(getattr(daily_file, "name", "") or "")
                    _dm = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", _dfn)
                    if _dm:
                        _sched_dt = datetime(int(_dm.group(3)), int(_dm.group(2)), int(_dm.group(1)))
                        _wf_yesterday_str = (_sched_dt - __import__("datetime").timedelta(days=1)).strftime("%d.%m")
                except Exception:
                    pass

                # ── Group tasks by employee — SPLIT into separate rows when a ──
                # worker has two genuinely separate shifts the same day, so each
                # shows on its own row positioned by its OWN shift's start time
                # instead of being blended into one confusing row.
                def _parse_hm_range(w):
                    try:
                        s, e = w.split("-")
                        s, e = s.strip(), e.strip()
                        return _hm(s), _hm(e), s, e
                    except Exception:
                        return None

                def _in_window(t_m, ws_m, we_m):
                    we_n = we_m if we_m > ws_m else we_m + 1440
                    t_n = t_m if t_m >= ws_m else t_m + 1440
                    return ws_m <= t_n <= we_n

                _wf_groups = {}
                _wf_group_name = {}     # row id -> display name (no suffix)
                _wf_group_window = {}   # row id -> (start_str, end_str) — only set for split rows
                _wf_group_sort_m = {}   # row id -> that row's own shift-start minute — only for split rows
                for _, _wr in _wf_labeled.iterrows():
                    _we = str(_wr.get("עובד", "")).strip()
                    if not _we or "❌" in _we:
                        continue
                    if _wf_search and _wf_search.lower() not in _we.lower():
                        continue
                    if _wf_role_names is not None and clean_text(_we) not in _wf_role_names:
                        continue
                    if _wf_term_names is not None and clean_text(_we) not in _wf_term_names:
                        continue
                    _windows = _wf_avail_map.get(clean_text(_we), [])
                    if len(_windows) >= 2:
                        _t_m = _hm(str(_wr.get("התחלה", "")))
                        _wi_match = 0
                        _parsed_match = None
                        for _wi, _wstr in enumerate(_windows):
                            _parsed = _parse_hm_range(_wstr)
                            if _parsed and _in_window(_t_m, _parsed[0], _parsed[1]):
                                _wi_match = _wi
                                _parsed_match = _parsed
                                break
                        # If this window is one half of a terminal transfer, use
                        # its FULL pre-split range for grouping/display — both
                        # halves of the same transfer share the same full range,
                        # so they merge into ONE row instead of two.
                        _full_windows = _wf_full_avail_map.get(clean_text(_we), [])
                        _display_parsed = _parsed_match
                        _group_token = str(_wi_match)
                        if _full_windows and _wi_match < len(_full_windows):
                            _full_parsed = _parse_hm_range(_full_windows[_wi_match])
                            if _full_parsed:
                                _display_parsed = _full_parsed
                                _group_token = _full_windows[_wi_match]
                        # A TERMINAL TRANSFER is one continuous shift that
                        # happens to move between terminals, so the whole day
                        # belongs on ONE row (user rule 2026-08-06: "במידה
                        # ולעובד יש פיצול בין טרמינלים יש להציג את כל זרימת
                        # העבודה בשורה אחת... רק אם מדובר בשתי משמרות נפרדות
                        # שקורות באותו יום יש לפצל"). The pre-existing
                        # "share the full pre-split range" trick only works
                        # when both halves carry the SAME full range, and they
                        # often don't — TL#4's read
                        # "21:00-07:00,01:00-07:00", two different strings, so
                        # her evening flights sat on a separate row from her
                        # Terminal-1 morning ones. Force one token instead.
                        if clean_text(_we) in _wf_transfer_raw:
                            _group_token = "מעבר"
                            _first_full = _parse_hm_range(_full_windows[0]) if _full_windows else None
                            _display_parsed = _first_full or _parsed_match
                        _rid = f"{_we}␟{_group_token}"
                        if _rid not in _wf_groups:
                            _wf_groups[_rid] = []
                            _wf_group_name[_rid] = _we
                            if _display_parsed:
                                _wf_group_window[_rid] = (_display_parsed[2], _display_parsed[3])
                                _wf_group_sort_m[_rid] = _display_parsed[0]
                        _wf_groups[_rid].append(_wr)
                    else:
                        if _we not in _wf_groups:
                            _wf_groups[_we] = []
                            _wf_group_name[_we] = _we
                        _wf_groups[_we].append(_wr)

                def _emp_shift_key(row_id):
                    if row_id in _wf_group_sort_m:
                        return _wf_group_sort_m[row_id]
                    _nm = clean_text(_wf_group_name.get(row_id, row_id))
                    if _nm in _wf_single_avail_sort_m:
                        return _wf_single_avail_sort_m[_nm]
                    return _wf_shift_map.get(_nm, 9999)

                # Names actually working today, for the trainee-mentor tooltip
                # below: a mentor listed in the roster's "mentor" field may
                # themselves be off today (sick etc.) — user rule 2026-07-19:
                # "במידה והחונך יורד מהמשמרת (sick וכו..), יש לציין שזה טרייני
                # ללא חונך" — such a trainee must show as unpaired, not show
                # the name of someone who isn't actually on shift.
                # Compare by name_key (order-independent) — the roster "mentor"
                # field stores the name as written there (e.g. "TL#13")
                # while the schedule may display it reversed ("בן TL#13"),
                # so a plain string-membership check wrongly reported the mentor
                # as absent. name_key normalises word order + spacing.
                _wf_scheduled_name_keys = {name_key(clean_text(_n)) for _n in _wf_group_name.values()}

                # ── CSS ───────────────────────────────────────────────────────
                st.markdown("""
                <style>
                .wf-chips { display:flex; align-items:center; gap:5px; flex-wrap:wrap;
                            direction:rtl; justify-content:flex-start; padding:2px 0; }
                .wf-sep   { color:#444; font-size:13px; flex-shrink:0; }
                .wf-chip  { position:relative; border-radius:7px; padding:4px 10px;
                            font-size:12px; line-height:1.5; white-space:nowrap;
                            flex-shrink:0; direction:rtl; text-align:right; }
                .wf-time  { direction:ltr; display:inline-block; font-size:10px;
                            color:#aaa; unicode-bidi:embed; }
                .wf-flight   { background:#0d2340; border:1px solid #1a4a80; color:#7bb8f5; }
                .wf-flight.wf-t1 { background:#3a2405; border:1px solid #e8930c; color:#f5b942; }
                .wf-t1-tag   { font-size:9px; font-weight:800; color:#e8930c; margin-left:3px; }
                .wf-break    { background:#2a1a00; border:1px solid #7a4500; color:#f5a623; }
                .wf-gate     { background:#0a1f2a; border:1px solid #0e5a7a; color:#4ac8f5; }
                .wf-hall     { background:#2a1f0a; border:1px solid #8a6a1a; color:#e8c05a; }
                .wf-refresh  { background:#1a0a2a; border:1px solid #5a2a8a; color:#c084fc; }
                .wf-counters { background:#1a1a00; border:1px solid #5a5a00; color:#d4d44a; }
                .wf-end      { background:#0a1f0a; border:1px solid #1a5a1a; color:#6ee77a; }
                .wf-late     { background:#2a0a0a; border:1px solid #8a2a2a; color:#f56a6a; }
                .wf-single   { font-size:14px; color:#f59e42; flex-shrink:0; }
                .wf-badge { position:absolute; top:-7px; right:-7px;
                            border-radius:10px; font-size:9px; font-weight:bold;
                            min-width:16px; height:16px; padding:0 3px;
                            display:inline-flex; align-items:center;
                            justify-content:center; z-index:10; line-height:1; }
                .wf-b1 { background:#f59e0b; color:#000; }
                .wf-b2 { background:#22c55e; color:#000; }
                div[data-testid="column"]:first-child button,
                div[data-testid="column"]:nth-child(2) button {
                    font-size:13px; font-weight:bold; padding:6px 4px;
                    border-radius:8px; min-height:38px; }
                /* כפתור נקה חיפוש */
                button[data-testid="baseButton-secondary"][kind="secondary"]:has(+ div),
                div[data-testid="stButton"]:has(button[data-testid*="wf_clear"]) button {
                    font-size:11px; padding:3px 6px; min-height:0;
                    opacity:0.7; border-radius:6px; }
                </style>""", unsafe_allow_html=True)

                SEP = '<span class="wf-sep">◂</span>'

                # ── One row per employee ──────────────────────────────────────
                for _row_id in sorted(_wf_groups.keys(), key=_emp_shift_key):
                    tasks = _wf_groups[_row_id]
                    _emp_name = _wf_group_name.get(_row_id, _row_id)

                    # Collect any shift-label notes (started-yesterday, terminal
                    # transfer) as plain phrases and combine them into ONE
                    # parenthetical at the end, instead of each appending its
                    # own "(...)" — two adjacent parenthetical groups read as
                    # cluttered/confusing, especially once line-wrap is involved
                    # (user: "זה עדיין קצת מבולגן עם כל הסוגריים").
                    _shift_notes = []
                    if _row_id in _wf_group_window:
                        # This row is one half of a genuinely dual-shift worker —
                        # sort/pivot using THIS row's own window, not the overall
                        # nominal shift field (which belongs to the OTHER half).
                        _ws_s, _se_v = _wf_group_window[_row_id]
                        _sh_m = _hm(_ws_s)
                        _pivot = (_sh_m - 30) % 1440
                        tasks.sort(key=lambda r: (_hm(str(r.get("התחלה", ""))) - _pivot) % 1440)
                        _nominal_sh_m = _wf_shift_map.get(clean_text(_emp_name), -1)
                        # This row is the RESTRICTED early-morning tail of a page-1
                        # night shift when its OWN display window starts early
                        # (before 08:00) while the worker's NOMINAL שmirat start is
                        # a late-evening/night hour (≥17:00) — i.e. the row's start
                        # time is an artifact of the page-1/transfer restriction,
                        # not the worker's real start. Checking the literal "01:30"
                        # marker alone missed a worker whose restricted window ALSO
                        # has a terminal transfer, which shifts the row's own start
                        # to the transfer time (e.g. "02:00") instead of "01:30" —
                        # found via real data: trainee-agent#11 showed "02:00–06:00" with
                        # no indication her real shift is "22:00–06:00".
                        if 0 <= _sh_m < 8 * 60 and _nominal_sh_m >= 17 * 60:
                            # This is the RESTRICTED tail-end portion of a page-1
                            # night shift — "01:30" is only relevant to today's
                            # roster. Show the worker's FULL nominal shift instead
                            # (never truncated) plus a note that it started the
                            # evening before.
                            _full_sh_m = _nominal_sh_m
                            _full_se_v = _wf_shift_end_map.get(clean_text(_emp_name), "")
                            if _full_sh_m >= 0:
                                _shift_str = f"{_full_sh_m//60:02d}:{_full_sh_m%60:02d}–{_full_se_v}"
                                _shift_notes.append(f"החל ב-{_wf_yesterday_str}" if _wf_yesterday_str
                                                     else "החל אתמול")
                            else:
                                _shift_str = f"{_ws_s}–{_se_v}"
                        else:
                            _shift_str = f"{_ws_s}–{_se_v}"
                    else:
                        _sh_m = _wf_shift_map.get(clean_text(_emp_name), -1)
                        _se_v = _wf_shift_end_map.get(clean_text(_emp_name), "")
                        _pivot = (_wf_shift_map.get(clean_text(_emp_name), 20 * 60) - 30) % 1440
                        tasks.sort(key=lambda r: (_hm(str(r.get("התחלה", ""))) - _pivot) % 1440)
                        _shift_str = (f"{_sh_m//60:02d}:{_sh_m%60:02d}–{_se_v}"
                                      if _sh_m >= 0 else "")
                        # Same "(started yesterday)" annotation as the split-row
                        # branch above, for a worker whose ONLY זמינות window is
                        # the restricted "01:30-{end}" night-tail (no dual-shift
                        # split needed since they have just one window) — e.g.
                        # TL#44: single window, no second shift, but still a
                        # page-1 night worker whose shift started the evening
                        # before. Previously only dual-shift (split-row) workers
                        # got this note; single-window night-tail workers showed
                        # a plain "22:00–08:30" with no indication it started
                        # the day before.
                        # Robust to a terminal transfer shifting the window's own
                        # start away from the literal "01:30" marker (e.g. "02:00")
                        # — same generalization as the split-row branch above.
                        _single_windows = _wf_avail_map.get(clean_text(_emp_name), [])
                        _single_start_m = _hm(_single_windows[0].split("-", 1)[0]) if _single_windows else -1
                        if (len(_single_windows) == 1 and 0 <= _single_start_m < 8 * 60
                                and _sh_m >= 17 * 60):
                            _shift_notes.append(f"החל ב-{_wf_yesterday_str}" if _wf_yesterday_str
                                                 else "החל אתמול")

                    # Show the terminal split in the displayed shift hours for a
                    # worker with a mid-shift terminal transfer — not just via
                    # the per-flight ת1/ת3 badge, which only shows up once tasks
                    # are already listed. User: "אם המשמרת מתחלקת לשני טרמינלים
                    # נפרדים... יש לציין את החלוקה בשעות המשמרת המוצגות."
                    _transfer_note = _wf_transfer_map.get(clean_text(_emp_name), "")
                    if _transfer_note:
                        _shift_notes.append(_transfer_note)

                    # Combine all notes into ONE parenthetical, comma-separated,
                    # instead of each note appending its own "(...)" — avoids the
                    # cluttered look of multiple adjacent parenthetical groups
                    # (user: "זה עדיין קצת מבולגן עם כל הסוגריים").
                    if _shift_str and _shift_notes:
                        _shift_str += f" ({', '.join(_shift_notes)})"

                    _binfo  = st.session_state["wf_briefing"].get(_row_id, {})
                    _bstate = _binfo.get("state", 0)

                    # Pre-flight break chip for 02:xx shifts
                    # If shift starts before 03:00 and first task starts ≥ 90 min
                    # after shift start, show a break chip BEFORE the first flight.
                    # Only if no break already appears inside the first task's continuation.
                    # NAMING COLLISION GUARD: a night-shift worker's SPLIT row uses
                    # the artificially restricted "01:30" window start as _sh_m (see
                    # the "_ws_s == '01:30'" branch above) — which is < 3*60 and would
                    # otherwise wrongly match this 02:xx-shift rule. A real night-shift
                    # worker's break is ALREADY assumed taken hours earlier (their
                    # actual shift started at 22:00+, not 01:30) and must never show
                    # a pre-flight break here — found via real data: agent#30
                    # (22:00-07:00, split row "01:30-07:00") was getting a spurious
                    # "☕ הפסקה" chip before her first flight (LY549 03:05) for
                    # exactly this reason.
                    _is_night_tail_row = _row_id in _wf_group_window and _wf_group_window[_row_id][0] == "01:30"
                    _pre_break_chip = ""
                    _suppress_break_mentions = False

                    # Dedicated break-before-transfer chip: ANY worker with a
                    # terminal transfer must ALWAYS show the break they take
                    # before arriving at the second terminal — never silently
                    # assumed, even when every visible task starts after the
                    # transfer time. Takes priority over the generic 02:xx
                    # pre-break check below (which only coincidentally fired for
                    # some transfer workers depending on their display window,
                    # not consistently — found via real data: agent#21, TL#19 showed no break chip at all despite having the same
                    # transfer as other workers who happened to show one).
                    _transfer_break_after_i = None
                    _transfer_raw = _wf_transfer_raw.get(clean_text(_emp_name))
                    if tasks and _transfer_raw:
                        _t_to, _t_at, _t_rb = _transfer_raw
                        _t_at_m = _hm(_t_at)
                        # "at" is the ARRIVAL time at the destination terminal —
                        # need to also subtract the ~15-min travel/shuttle time on
                        # top of the break duration, not just the break itself.
                        # User: "העובדים... צריכים להיות שם כבר בשעה 02:00 שהם
                        # אחרי הפסקה, אבל יש לקחת בחשבון רבע שעה הגעה לטרמינל 1.
                        # לכן ההפסקה שלהם כבר ב-01:00" (45 break + 15 travel = 60,
                        # so the break itself starts a full hour before arrival).
                        _t_travel_m = 15
                        _t_b_start_m = (_t_at_m - _t_rb - _t_travel_m) % 1440
                        # The break cannot start while the worker is still on a
                        # flight at the SOURCE terminal. Push it to the end of
                        # the last such task and recompute the arrival — the
                        # transfer is then genuinely late, and the chip says so
                        # (user rule 2026-08-06: "צ'יפ ההפסקה צריך להיות אחרי
                        # טיסות הלילה של טרמינל 3 ולהתאים בשעות לשעת סיום טיסת
                        # הלילה... יש לציין בצ'יפ כי המעבר מתעכב בגלל הטיסה" —
                        # TL#4: LY027 runs to 00:30 while the chip
                        # claimed a 00:00-01:00 break, straight through it).
                        # "Latest" must be measured as DISTANCE BACK FROM THE
                        # TRANSFER, not by raw clock value — a flight ending
                        # 00:30 is later than one ending 23:05 but sorts lower
                        # numerically, so a raw comparison picks the wrong task
                        # (TL#4: LY087 23:05 beat LY027 00:30).
                        _t_last_src_end = -1
                        _t_last_src_i = -1
                        _t_best_delta = None
                        for _ti_t, _t_task in enumerate(tasks):
                            _t_term_task = get_terminal(clean_text(str(_t_task.get("_gate", ""))))
                            if _t_term_task == _t_to:
                                continue          # already at the destination
                            _t_e = _hm(str(_t_task.get("סיום", "")))
                            if not (0 <= _t_e < 1440):
                                continue
                            _t_delta = (_t_at_m - _t_e) % 1440
                            if _t_delta == 0 or _t_delta > 12 * 60:
                                continue          # ends after the transfer time
                            if _t_best_delta is None or _t_delta < _t_best_delta:
                                _t_best_delta, _t_last_src_end, _t_last_src_i = _t_delta, _t_e, _ti_t
                        _t_delayed = False
                        if _t_last_src_end >= 0 and ((_t_last_src_end - _t_b_start_m) % 1440) < 12 * 60 \
                                and _t_last_src_end != _t_b_start_m:
                            _t_b_start_m = _t_last_src_end
                            _t_delayed = True
                        _t_b_end_m = (_t_b_start_m + _t_rb) % 1440
                        _t_arrive_m = (_t_b_end_m + _t_travel_m) % 1440
                        _t_arrive = f'{_t_arrive_m//60:02d}:{_t_arrive_m%60:02d}' if _t_delayed else _t_at
                        _pre_break_chip = (
                            f'<span class="wf-chip wf-break">☕ הפסקה '
                            f'<span class="wf-time">{_t_b_start_m//60:02d}:{_t_b_start_m%60:02d}–'
                            f'{_t_b_end_m//60:02d}:{_t_b_end_m%60:02d}</span> '
                            f'לפני המעבר לטרמינל {_t_to} (הגעה {_t_arrive}'
                            + (' — מתעכב עקב הטיסה)' if _t_delayed else ')')
                            + '</span>' + SEP
                        )
                        # When the break had to wait for a source-terminal
                        # flight, the chip belongs AFTER that flight, not at the
                        # head of the row.
                        _transfer_break_after_i = _t_last_src_i if _t_delayed else None
                        _suppress_break_mentions = True

                    # A genuine night shift (start ≥20:30, crosses midnight, end
                    # ≥04:00) is UNCONDITIONALLY assumed to have already taken
                    # its break during counter-closing time before the first
                    # early-morning flight — never shown explicitly (HARD RULE,
                    # user-confirmed 2026-07-09: "אין צורך להציג את משבצת
                    # ההפסקה לפני טיסות לפנות הבוקר של משמרות הלילה"). The
                    # last-resort mechanism below can't tell "silently already
                    # taken" apart from "nowhere else to put it" — for a plain
                    # 6-10h night shift, break_state is forced to stage=2 by
                    # app/display.py's blanket rule, so "המשך אזורי" NEVER
                    # mentions הפסקה, and _break_already_placed below always
                    # reads False — wrongly triggering a pre-flight chip for
                    # EVERY night-shift worker with tightly-packed flights
                    # (found via real 12.07 data: TL-trainee#4, TL#25,
                    # agent#43, agent#25, agent#35, agent#15 — all
                    # genuine night shifts, all got a spurious "הפסקה לפני
                    # הירידה לטיסות" chip). Exclude genuine night shifts here;
                    # their רענון (if entitled, ≥10h shifts) is already handled
                    # by the separate mid-shift break-slot mechanism.
                    _is_genuine_night_shift = is_night_shift_for_return_rule({
                        "תחילת משמרת": f"{_sh_m//60:02d}:{_sh_m%60:02d}" if _sh_m >= 0 else "",
                        "סוף משמרת": _se_v,
                    })
                    # 02:00-08:30/09:30 family: the default proactive break for
                    # this shift is a FIXED 03:30 (not shown as its own chip —
                    # standard proactive-break treatment). But if the worker's
                    # first flight starts before that slot would even finish,
                    # a flight is genuinely blocking the normal 03:30 break —
                    # take it right before THAT flight instead and show it
                    # explicitly (user rule 2026-08-02: previously this exact
                    # squeeze — first flight only 15-25 min into the 03:00-05:00
                    # preferred window, e.g. 03:10-03:25 — fell through with NO
                    # visible pre-flight marker at all, since the generic ≥90-
                    # min-gap chip below doesn't fire until there's a full 90
                    # min between shift start and the first flight; found via
                    # real data: TL#17/agent#32/agent#41/דז'אנשווילי
                    # לינוי/agent#14, all 02:00-09:30 with a 70-85 min gap).
                    if (not _pre_break_chip and tasks and _sh_m >= 0
                            and not _is_night_tail_row and not _is_genuine_night_shift
                            and 1 * 60 + 30 <= _sh_m <= 2 * 60 + 30
                            and _se_v and is_time_text(_se_v)
                            and 8 * 60 <= _hm(_se_v) <= 10 * 60):
                        _ft0_m = _hm(str(tasks[0].get("התחלה", "")))
                        _brk_already0 = any(
                            "הפסקה" in str(t.get("המשך אזורי", "")) or "רענון" in str(t.get("המשך אזורי", ""))
                            for t in tasks
                        )
                        if not _brk_already0:
                            _pb0_emp = {"תחילת משמרת": f"{_sh_m//60:02d}:{_sh_m%60:02d}", "סוף משמרת": _se_v}
                            _rb0 = required_break(_pb0_emp) or 45
                            # 15-min walk-to-gate buffer AFTER the break ends,
                            # on top of the break's own duration — the break
                            # itself must finish 15 min before the flight, not
                            # run right up to it (user rule 2026-08-05).
                            _walk0 = 15
                            if _ft0_m < (3 * 60 + 30 + _rb0 + _walk0):
                                _pb0_end_m = _ft0_m - _walk0
                                _pb0_start_m = _pb0_end_m - _rb0
                                # The break must never START before 02:45 (user
                                # rule 2026-08-05, relaxed from the earlier
                                # 03:00 floor) — e.g. 02:15 is unrealistically
                                # early, barely 15 min into the shift. If the
                                # flight is too close for a full break + the
                                # 15-min walk to fit starting there, don't show
                                # this chip at all — protect_early_shift_
                                # preflight_breaks (the scheduler post-pass
                                # that actually reassigns the flight to a
                                # night-shift/02:00-11:00 worker) is the real
                                # fix for that case, not a geometrically-
                                # impossible chip.
                                if _pb0_start_m >= 2 * 60 + 45:
                                    _pre_break_chip = (
                                        f'<span class="wf-chip wf-break">☕ הפסקה '
                                        f'<span class="wf-time">{_pb0_start_m//60:02d}:{_pb0_start_m%60:02d}–'
                                        f'{_pb0_end_m//60:02d}:{_pb0_end_m%60:02d}</span> '
                                        f'לפני טיסה</span>' + SEP
                                    )

                    if (not _pre_break_chip and tasks and _sh_m >= 0
                            and not _is_night_tail_row and not _is_genuine_night_shift):
                        _first_task_start_m = _hm(str(tasks[0].get("התחלה", "")))
                        _pre_gap_min = (_first_task_start_m - _sh_m) % 1440
                        # Check ALL tasks (not just the first) for an existing
                        # break/רענון mention — the scheduler may place the
                        # worker's real break mid-shift, between two LATER
                        # tasks, rather than before the first one. Checking
                        # only tasks[0]'s own continuation field missed that
                        # and showed a redundant generic pre-break chip on top
                        # of the genuine mid-sequence one (found via real
                        # data: בן TL#13, 02:00 shift, break correctly
                        # placed after LY221 but a second "☕ הפסקה" chip still
                        # got prepended before her first flight LY2521).
                        _break_already_placed = any(
                            "הפסקה" in str(t.get("המשך אזורי", ""))
                            or "רענון" in str(t.get("המשך אזורי", ""))
                            for t in tasks
                        )
                        # A pre-flight chip is only warranted as a genuine LAST
                        # RESORT — i.e. no adequate gap exists ANYWHERE else in
                        # the shift, neither between two of the worker's tasks
                        # nor after their last task before shift end. A large
                        # pre-gap alone doesn't mean the break must happen there;
                        # it may just as well fit later with no need to flag it
                        # at all (user: "אין צורך להציג הפסקה לפני טיסה... רק אם
                        # הטיסה ממש קרובה לתחילת המשמרת ולא תהיה שעה אחרת, או
                        # שיהיה מאוחר מדי לתוך המשמרת"). NOT limited to 02:xx
                        # shifts — app/display.py's own "no adequate gap" logic
                        # (_preflight_note) already detects this same last-resort
                        # case for ALL shift types, but only writes it to "טקסט
                        # עובד" (the per-flight table), never to "המשך אזורי"
                        # (what the workflow tab reads) — so the workflow tab
                        # showed NOTHING, or a misleadingly-late end-of-shift
                        # chip, for e.g. 10:00-16:00 workers whose tasks have no
                        # gap ≥45 min anywhere (TL#23, עדן גריצי'י: only
                        # 5-10 min between flights, 30 min left after the last
                        # one — found via real data).
                        _pb_emp_dict = {
                            "תחילת משמרת": f"{_sh_m//60:02d}:{_sh_m%60:02d}",
                            "סוף משמרת": _se_v,
                        }
                        # Shifts under 6h are only entitled to a short רענון (or
                        # nothing at all) — required_break() alone returns 0 for
                        # those, and blindly falling back to "or 45" would wrongly
                        # assume a full break requirement that doesn't exist.
                        _pb_break_label = break_label_for_employee(_pb_emp_dict)
                        _req_break_dur = (
                            (required_break(_pb_emp_dict) or required_refresh(_pb_emp_dict) or 20)
                            if _pb_break_label else 0
                        )
                        _no_other_gap = bool(_pb_break_label)
                        _prev_end_m = None
                        for _t in tasks:
                            _t_s = _hm(str(_t.get("התחלה", "")))
                            _t_e = _hm(str(_t.get("סיום", "")))
                            if _prev_end_m is not None and (_t_s - _prev_end_m) % 1440 >= _req_break_dur:
                                _no_other_gap = False
                                break
                            _prev_end_m = _t_e
                        if _no_other_gap and _prev_end_m is not None and _se_v:
                            _se_m = _hm(_se_v)
                            if (_se_m - _prev_end_m) % 1440 >= _req_break_dur:
                                _no_other_gap = False
                        if _pre_gap_min >= 90 and not _break_already_placed and _no_other_gap:
                            # Break ends 15 min BEFORE the flight — the walk down
                            # to the gate hall comes on top of the full break
                            # duration (user rule 2026-08-06: a break ending at
                            # flight start is really only ~30 usable minutes).
                            _pb_end_m = (_first_task_start_m - 15) % 1440
                            _pb_start_m = (_pb_end_m - _req_break_dur) % 1440
                            _pb_label = "רענון" if _pb_break_label == "רענון" else "הפסקה"
                            _pre_break_chip = (
                                f'<span class="wf-chip wf-break">☕ {_pb_label} '
                                f'<span class="wf-time">{_pb_start_m//60:02d}:{_pb_start_m%60:02d}–'
                                f'{_pb_end_m//60:02d}:{_pb_end_m%60:02d}</span> '
                                f'לפני הירידה לטיסות</span>' + SEP
                            )

                    # Proactive midday break for employees with a single very-late task.
                    # e.g. shift 11:30-21:00 with only one flight at 19:xx — the break
                    # should be scheduled around 14:00-16:00 (after afternoon check-in peak),
                    # NOT after the flight at 20:xx+.
                    # User rule 2026-07-15: "במידה ומדובר בהפסקה יזומה, אין צורך
                    # להזכיר זאת במסך זרימת העבודה" — a proactive break is never
                    # shown as its own chip in the workflow tab (unlike a genuine
                    # pre-flight break, which IS shown — see `_pre_break_chip`'s
                    # "לפני הירידה לטיסות" branches, untouched). _proactive_break_chip
                    # itself stays "" (nothing rendered); _proactive_break_active
                    # keeps the "a break was already accounted for" signal alive so
                    # the per-task loop below still doesn't ALSO show a stray
                    # "הפסקה"/"חזרה" mention for this task.
                    _proactive_break_chip = ""
                    _proactive_break_active = False
                    _last_cont_check = str(tasks[-1].get("המשך אזורי", "")).strip() if tasks else ""
                    _has_break_entitlement = "הפסקה" in _last_cont_check
                    if (tasks and _sh_m >= 0 and len(tasks) == 1
                            and _has_break_entitlement and _bstate == 0):
                        _only_task_start_m = _hm(str(tasks[0].get("התחלה", "")))
                        # Late evening: task after 17:00, shift starts before 15:00
                        if _only_task_start_m >= 17 * 60 and _sh_m < 15 * 60:
                            _proactive_break_active = True
                        # Midday shift (10:00-12:30 start): single task after 14:00
                        # → proactive break 12:30-13:30 (after morning check-in peak)
                        elif 10 * 60 <= _sh_m <= 12 * 60 + 30 and _only_task_start_m >= 14 * 60:
                            _proactive_break_active = True

                    # REMOVED (2026-07-09): a generalized "proactive break window"
                    # chip used to fire here for any 2+-task worker whose entire
                    # shift-appropriate preferred window was idle before a LATE
                    # first task (e.g. agent#53, first flight 09:10 in a
                    # 03:30-12:30 shift). User reverted this: "אין צורך להציג את
                    # המשבצת הפסקה יזומה בזרימת העבודה. יש להציג את ההפסקה לפני
                    # הטיסה רק אם הטיסה בשלב מוקדם יחסית של המשמרת" — a pre-flight
                    # break should only ever be shown when the flight itself is
                    # relatively EARLY in the shift (see the `_pre_break_chip`
                    # 02:xx-shift check above, and the single-task
                    # `_proactive_break_chip` cases below) — never as a recommended
                    # window preceding a LATE first flight. Do not re-add a
                    # "show the ideal break window" mechanism for late-first-task
                    # workers without this rule in mind.
                    #
                    # BUT (2026-07-15): a DIFFERENT, narrower case than the reverted
                    # one above — a worker (single OR multi-task) whose shift type is
                    # one of a small set explicitly named by the user, where the
                    # break either wasn't shown at all (single task / no other gap —
                    # would otherwise fall to the generic `_pre_break_chip` "jam
                    # before flight" default above) OR was placed by the scheduler in
                    # a real but LATE between-task gap (a "הפסקה"/"רענון" mention
                    # exists on some task, unlike the reverted mechanism which fired
                    # for ANY late-first-task worker with no such distinction) — in
                    # both cases the shift type's own preferred window would have fit
                    # cleanly before the worker's FIRST task. This mirrors the
                    # "existing gap is real but too late" fix already applied in
                    # app/display.py's _preflight_note mechanism — but the workflow
                    # tab renders its OWN chips from "המשך אזורי" text matching (see
                    # chips loop below), completely independent of display.py, so
                    # that fix alone never reached here; it also runs UNCONDITIONALLY
                    # (not gated on `not _pre_break_chip`) so it can override the
                    # generic block's jam-before-flight default for these shift types
                    # too. Found via real data 2026-07-15: 02:00-09:30 (originally
                    # window override 06:30-08:00; superseded 2026-07-19 to use the
                    # shift's own natural 03:00-05:00 window instead, unconditionally
                    # overriding any already-shown late break -- see _is_0200_short_w
                    # below — agent#3, agent#50, agent#31, agent#39); 08:00-17:00
                    # (-> 10:00-12:00 — agent#51, agent#1, trainee-agent#3,
                    # agent#53, agent#44, agent#28); 09:30-17:30 (-> 11:30-13:30 —
                    # סימן agent#10, trainee-agent#9, agent#12, agent#6, agent#36,
                    # agent#48); 11:00-19:00 (-> 13:00-15:00 — restricted-agent#7). Reuses the
                    # existing _suppress_break_mentions mechanism (already used above
                    # for the transfer-break chip) to hide the late chip in the main
                    # per-task loop.
                    if tasks and _sh_m >= 0 and not _proactive_break_chip and not _proactive_break_active and not _transfer_raw:
                        _wemp = {"תחילת משמרת": f"{_sh_m//60:02d}:{_sh_m%60:02d}", "סוף משמרת": _se_v}
                        _se_m_w = _hm(_se_v) if _se_v else None
                        _wfirst_start_m = _hm(str(tasks[0].get("התחלה", "")))
                        _wpref_s, _wpref_e = preferred_break_window_by_shift(_wemp)
                        _is_0200_short_w = (
                            _se_m_w is not None and 1 * 60 + 30 <= _sh_m <= 2 * 60 + 30
                            and 8 * 60 <= _se_m_w <= 10 * 60
                        )
                        _proactive_shift_windows = {
                            ("10:00", "12:00"): "הפסקה יזומה",
                            ("11:30", "13:30"): "הפסקה יזומה",
                            ("13:00", "15:00"): "הפסקה יזומה",
                            ("18:00", "19:00"): "הפסקה יזומה",
                            # 13:00-00:30 shift (helpers.py's preferred_break_window_by_shift,
                            # ~line 501-502): a long shift whose only flight is late at
                            # night — the break belongs early (16:00-17:30), same
                            # "proactive, not pre-flight" treatment as every other window
                            # here. Was wrongly left as plain "הפסקה", routing it into the
                            # "show a chip" branch below instead of being suppressed
                            # (user-reported 2026-08-02: עידן לביסקי/agent#5/agent#64/עדן גוליית יעקב, all 13:00-00:30 with one late flight,
                            # showed a spurious "הפסקה 16:00-17:30 לפני הירידה לטיסות" chip).
                            ("16:00", "17:30"): "הפסקה יזומה",
                        }
                        if _is_0200_short_w:
                            # 2026-07-19: matches preferred_break_window_by_shift's
                            # own ("03:00","05:00") window for this shift (helpers.py
                            # line ~444) -- the OLD "06:30-08:00 late-flight override"
                            # here was stale, predating the display.py broadening that
                            # made pre-flight placement unconditional for this whole
                            # shift type regardless of role or an existing (late)
                            # break already computed by the scheduler. "הפסקה" here
                            # is just the default (squeeze case); it's overridden to
                            # "הפסקה יזומה" below when there's room to spare.
                            _w_s, _w_e, _w_label = "03:00", "05:00", "הפסקה"
                        elif _wpref_s and _wpref_e and (_wpref_s, _wpref_e) in _proactive_shift_windows:
                            _w_s, _w_e = _wpref_s, _wpref_e
                            _w_label = _proactive_shift_windows[(_wpref_s, _wpref_e)]
                        else:
                            _w_s = _w_e = _w_label = None
                        if _w_s and _wfirst_start_m is not None:
                            _w_s_m = _hm(_w_s)
                            _w_e_m = _hm(_w_e)
                            _w_req_dur = required_break(_wemp) or required_refresh(_wemp) or 45
                            _w_break_label = break_label_for_employee(_wemp)
                            _w_break_shown = any(
                                "הפסקה" in str(t.get("המשך אזורי", "")) or "רענון" in str(t.get("המשך אזורי", ""))
                                for t in tasks
                            )
                            if _is_0200_short_w:
                                # The 03:00-05:00 window can END AFTER the first
                                # flight starts (e.g. flight at 04:10) -- unlike the
                                # other windows below, whose whole span is checked to
                                # close before the first task. Mirrors display.py's
                                # _preflight_note logic (2026-07-19 update): "room to
                                # spare" isn't measured against the window's own end
                                # — it's measured as 60+ min of genuine idle time
                                # after a break taken at the window's OPENING. If
                                # that idle time exists, this is a proactive break
                                # (worker returns to counters, goes down later) —
                                # "הפסקה יזומה", never shown as its own chip (user
                                # 2026-07-19: "במידה ומדובר בהפסקה יזומה, אין צורך
                                # להציג אותה בתזרים העבודה"). Otherwise it's a
                                # genuine squeeze (flight close enough that this is
                                # the only opportunity) — "לפני הירידה לטיסות".
                                # +15 min walk-to-gate after the break, on top of
                                # its full duration (user rule 2026-08-06: a break
                                # ending exactly at flight start really gives only
                                # ~30 usable minutes — agent#47, chip 03:25-04:10
                                # before an 04:10 flight). Feasibility is anchored
                                # to the worker's ACTUAL shift start (02:00), and
                                # the squeeze may not start before 02:45 — both
                                # mirror display.py's _preflight_note logic.
                                _w_anchor_m = min(_w_s_m, _sh_m) if _sh_m >= 0 else _w_s_m
                                _w_fits = _wfirst_start_m >= _w_anchor_m + _w_req_dur + 15
                                if _w_fits:
                                    if (_wfirst_start_m - (_w_s_m + _w_req_dur + 15)) >= 60:
                                        _w_disp_s_m, _w_disp_e_m = _w_s_m, _w_s_m + _w_req_dur
                                        _w_label = "הפסקה יזומה"
                                    else:
                                        _w_disp_e_m = _wfirst_start_m - 15
                                        _w_disp_s_m = _w_disp_e_m - _w_req_dur
                                        if _w_disp_s_m < 2 * 60 + 45:
                                            _w_fits = False
                            else:
                                _w_fits = _w_e_m + _w_req_dur <= _wfirst_start_m
                                _w_disp_s_m, _w_disp_e_m = _w_s_m, _w_e_m
                            # For the 02:00-09:30 short shift, "already shown
                            # elsewhere" must NOT block the override: the scheduler
                            # may have placed a real but too-late (between-flights)
                            # break, which is exactly the case that needs
                            # overriding -- mirrors display.py's unconditional
                            # `_existing_too_late` special-case for this one window.
                            if (_w_break_label and (not _w_break_shown or _is_0200_short_w)
                                    and _w_fits):
                                if _w_label == "הפסקה יזומה":
                                    # User rule 2026-07-15: a proactive break is
                                    # never shown as its own chip in the workflow
                                    # tab — only the suppression (no stray break
                                    # mention elsewhere) is kept.
                                    _proactive_break_active = True
                                    # A late-single-flight 02:00-09:30 worker
                                    # (e.g. agent#38 LY285 08:15, agent#11
                                    # LY323 08:25 — both handed a lone late flight
                                    # by fix_wasteful_gaps) already had the generic
                                    # `_pre_break_chip` mechanism jam a "07:30-08:15
                                    # לפני הירידה לטיסות" chip before this block ran.
                                    # That squeeze is exactly what this proactive
                                    # branch overrides — the flight is late in the
                                    # shift, so the break belongs early (03:00, the
                                    # shift's recommended window) and is suppressed,
                                    # NOT jammed against the flight. Clear the stale
                                    # jam chip (user 2026-07-19). Found via real data.
                                    _pre_break_chip = ""
                                else:
                                    _w_disp_s = f"{_w_disp_s_m//60:02d}:{_w_disp_s_m%60:02d}"
                                    _w_disp_e = f"{_w_disp_e_m//60:02d}:{_w_disp_e_m%60:02d}"
                                    # Same LTR-isolation fix as _br_win below —
                                    # without it the browser's bidi algorithm
                                    # can visually swap start/end around the
                                    # en-dash inside this RTL chip text.
                                    _pre_break_chip = (
                                        f'<span class="wf-chip wf-break">☕ הפסקה '
                                        f'<span class="wf-time">{_w_disp_s}–{_w_disp_e}</span> '
                                        f'לפני הירידה לטיסות</span>' + SEP
                                    )
                                _suppress_break_mentions = True

                    chips = []
                    for _ti, task in enumerate(tasks):
                        _flt  = str(task.get("טיסה", "")).strip()
                        _role = _short_role(str(task.get("תפקיד בסיס", task.get("תפקיד", ""))), _emp_name)
                        _ts   = str(task.get("התחלה", "")).strip()
                        _te   = str(task.get("סיום",  "")).strip()
                        _is_first = (_ti == 0)
                        if _bstate == 1:
                            _badge = ('<span class="wf-badge wf-b1">✓</span>'
                                      if _is_first else
                                      '<span class="wf-badge wf-b2">✓✓</span>')
                        elif _bstate >= 2:
                            _badge = '<span class="wf-badge wf-b2">✓✓</span>'
                        else:
                            _badge = ""
                        _time_html = (
                            f' <span class="wf-time">{safe_html(_ts)}–{safe_html(_te)}</span>'
                            if _ts and _te else ""
                        )
                        # הבחנה ויזואלית ברורה בין טיסות טרמינל 1 לטרמינל 3 בתצוגה
                        # המשולבת "הכל" — צ'יפ כתום עם תג T1 במקום הכחול הרגיל.
                        _task_term = get_terminal(clean_text(str(task.get("_gate", ""))))
                        _is_t1_task = _task_term == "1"
                        _t1_class = " wf-t1" if _is_t1_task else ""
                        _t1_tag = ' <span class="wf-t1-tag">T1</span>' if _is_t1_task else ""
                        chips.append(
                            f'<span class="wf-chip wf-flight{_t1_class}">{_badge}'
                            f'<b>{safe_html(_flt)}</b> '
                            f'<span style="font-size:11px">{safe_html(_role)}</span>'
                            f'{_time_html}{_t1_tag}</span>'
                        )
                        # "הגעה באיחור" — forced assignment onto this flight while
                        # the worker is kept on a prior overlapping flight (user
                        # rule 2026-07-22). Shown right after the flight chip.
                        if str(task.get("הגעה באיחור", "")).strip() in ("True", "כן", "1"):
                            chips.append('<span class="wf-chip wf-late">🕐 הגעה באיחור</span>')
                        if _transfer_break_after_i == _ti and _pre_break_chip:
                            # break + terminal transfer, shown where it really
                            # happens: right after the last source-terminal
                            # flight (see _transfer_break_after_i above)
                            chips += [SEP, _pre_break_chip.rstrip(SEP)]
                            _pre_break_chip = ""
                        _cont    = str(task.get("המשך אזורי", "")).strip()
                        _is_last = (_ti == len(tasks) - 1)
                        if not _is_last:
                            if _suppress_break_mentions:
                                # The proactive-window chip already covers this
                                # worker's break/רענון (shown earlier, before
                                # their first flight) — don't ALSO show the
                                # misleadingly-late one the scheduler placed
                                # between two flights.
                                pass
                            elif "הפסקה" in _cont or "רענון" in _cont:
                                # Show the actual break window, not a bare
                                # label: it starts when this task ends and runs
                                # for the worker's required duration (user rule
                                # 2026-08-06, TL#15 — a night ר"צ who flies
                                # the night block, breaks and comes back down
                                # needs the real times on the chip: "תהיה
                                # ההפסקה עד 01:50 ואז תרד שוב לטיסה").
                                _br_is_refresh = "רענון" in _cont and "הפסקה" not in _cont
                                _br_emp = {
                                    "תחילת משמרת": f"{_sh_m//60:02d}:{_sh_m%60:02d}" if _sh_m >= 0 else "",
                                    "סוף משמרת": _se_v or "",
                                }
                                _br_dur = ((required_refresh(_br_emp) or 20) if _br_is_refresh
                                           else (required_break(_br_emp) or 45))
                                _br_s_m = _hm(_te)
                                _br_win = ""
                                if 0 <= _br_s_m < 1440:
                                    _br_e_m = (_br_s_m + _br_dur) % 1440
                                    # Without an explicit LTR span, the browser's
                                    # bidi algorithm can visually swap the two
                                    # numbers around the en-dash inside this
                                    # RTL-flowing chip text — found via real
                                    # 04.09 data: "רענון 05:10–04:50" displayed
                                    # for a 04:50–05:10 window (start/end were
                                    # correct in the underlying string; only the
                                    # rendering was reversed). Same fix already
                                    # used for the flight-chip time (.wf-time).
                                    _br_win = (
                                        ' <span class="wf-time">'
                                        f'{_br_s_m//60:02d}:{_br_s_m%60:02d}–'
                                        f'{_br_e_m//60:02d}:{_br_e_m%60:02d}</span>'
                                    )
                                # A ר"צ with nothing schedulable in the gap stays
                                # airside helping in the hall instead of going
                                # back to the counters (TL#41).
                                if "עזרה באולם" in _cont:
                                    _tail_txt = " ועזרה באולם"
                                elif "חזרה הביתה" in _cont:
                                    _tail_txt = " וחזרה הביתה"
                                elif "חזרה" in _cont:
                                    _tail_txt = " וחזרה לדלפקים"
                                else:
                                    _tail_txt = ""
                                if _br_is_refresh:
                                    chips += [SEP, f'<span class="wf-chip wf-refresh">🔄 רענון{_br_win}{_tail_txt}</span>']
                                else:
                                    chips += [SEP, f'<span class="wf-chip wf-break">☕ הפסקה{_br_win}{_tail_txt}</span>']
                                # Went back to the counters mid-shift → show
                                # when they come back DOWN for the next flight
                                # (user rule 2026-08-06, TL#15: after the
                                # ordinary "הפסקה וחזרה" chip, show the renewed
                                # descent time and then the rest of her
                                # flights). 15 min before it starts, same
                                # walk-to-gate allowance as everywhere else.
                                if "חזרה" in _cont and "הביתה" not in _cont:
                                    _nx_m = _hm(str(tasks[_ti + 1].get("התחלה", "")))
                                    if 0 <= _nx_m < 1440:
                                        _dn_m = (_nx_m - 15) % 1440
                                        chips += [SEP, f'<span class="wf-chip wf-gate">🚶 ירידה לטיסה '
                                                       f'{_dn_m//60:02d}:{_dn_m%60:02d}</span>']
                            elif "עזרה באולם" in _cont:
                                chips += [SEP, '<span class="wf-chip wf-hall">🛟 עזרה באולם</span>']
                            elif clean_text(_emp_name) in _wf_t1_names:
                                # Terminal 1: any wait over 20 min between two
                                # tasks is time the worker spends helping in the
                                # hall, and the workflow must say so before the
                                # next task (user rule 2026-08-06: "במידה ויש
                                # רווח של יותר מ-20 דקות בין משימות יש לציין
                                # בזרימת העבודה עזרה ואז את המשימה הבאה").
                                # Only when no break/רענון chip already covers
                                # that stretch — those are shown above.
                                # Only for a plain "המשך ל..." wait. When the
                                # continuation already says something about the
                                # stretch — a break, a return to the counters, a
                                # terminal transfer — that text governs, and the
                                # gap is not hall time: without this guard a
                                # 15-HOUR split between a Terminal-1 morning
                                # block and the night flights (TL#4
                                # 920 min, TL#32 980) was labelled "עזרה".
                                _hlp_e = _hm(_te)
                                _hlp_s = _hm(str(tasks[_ti + 1].get("התחלה", "")))
                                _hlp_quiet = not any(
                                    _w in _cont for _w in ("הפסקה", "רענון", "חזרה", "מעבר", "עזרה")
                                )
                                if _hlp_quiet and 0 <= _hlp_e < 1440 and 0 <= _hlp_s < 1440                                         and ((_hlp_s - _hlp_e) % 1440) > 20:
                                    # "עזרה" רק כשיש באמת במה לעזור — טיסת
                                    # טרמינל 1 פעילה בתוך חלון ההמתנה. אחרת
                                    # ההליכה בין השערים לדלפקים בטרמינל 1 קצרה,
                                    # ועדיף להחזיר לדלפקים ולהוריד שוב לטיסה
                                    # (הנחיית משתמשת 2026-08-21, על TL#5 וTL#14).
                                    if _t1_active_between(_hlp_e, _hlp_s, _emp_name):
                                        chips += [SEP, '<span class="wf-chip wf-hall">🛟 עזרה</span>']
                                    else:
                                        chips += [
                                            SEP,
                                            '<span class="wf-chip wf-hall">↩ חזרה לדלפקים</span>',
                                            SEP,
                                            '<span class="wf-chip wf-gate">🚶 ירידה לטיסה '
                                            f'{((_hlp_s - 15) % 1440) // 60:02d}:'
                                            f'{((_hlp_s - 15) % 1440) % 60:02d}</span>',
                                        ]
                            chips.append(SEP)

                    _last_cont = str(tasks[-1].get("המשך אזורי", "")).strip() if tasks else ""
                    _single_task = len(tasks) == 1
                    _star = " *" if _single_task else ""
                    if _proactive_break_chip or _pre_break_chip or _proactive_break_active:
                        # A break was already accounted for BEFORE the flights
                        # (either the 02:xx pre-break, the generalized
                        # proactive-window break — silently, per the 2026-07-15
                        # rule, or shown, for a genuine pre-flight break — or the
                        # dedicated break-before-transfer chip) — the last task
                        # should only show plain return, not a second
                        # break/רענון mention.
                        if "חזרה" in _last_cont_check:
                            chips += [SEP, f'<span class="wf-chip wf-counters">חזרה לדלפקים{_star}</span>']
                    else:
                        _last_return_label = (
                            "חזרה הביתה" if "חזרה הביתה" in _last_cont
                            else "חזרה לדלפקים" if "חזרה" in _last_cont
                            else "עזרה באולם" if "עזרה באולם" in _last_cont
                            else ""
                        )
                        if "הפסקה" in _last_cont and _last_return_label:
                            chips += [SEP, f'<span class="wf-chip wf-break">☕ הפסקה ו{_last_return_label}{_star}</span>']
                        elif "הפסקה" in _last_cont:
                            chips += [SEP, '<span class="wf-chip wf-break">☕ הפסקה</span>']
                        elif "רענון" in _last_cont and _last_return_label:
                            # Same combined treatment as הפסקה — a רענון on the
                            # worker's LAST task must still show whether they
                            # return to the counters or go home afterward
                            # (found via real data: agent#35 showed a lone
                            # "רענון" chip with no indication of what happens
                            # after it).
                            chips += [SEP, f'<span class="wf-chip wf-refresh">🔄 רענון ו{_last_return_label}{_star}</span>']
                        elif "רענון" in _last_cont:
                            chips += [SEP, '<span class="wf-chip wf-refresh">🔄 רענון</span>']
                        elif _last_return_label:
                            # Terminal 1: when the day ends with a plain return
                            # to the counters (not going home), the worker helps
                            # in the hall until then — "עזרה וחזרה" (user rule
                            # 2026-08-06: "במידה וזה רק חזרה לדלפקים ולא לעוד
                            # משימה, לכתוב עזרה וחזרה").
                            if (clean_text(_emp_name) in _wf_t1_names
                                    and _last_return_label == "חזרה לדלפקים"):
                                chips += [SEP, f'<span class="wf-chip wf-hall">🛟 עזרה ו{_last_return_label}{_star}</span>']
                            else:
                                chips += [SEP, f'<span class="wf-chip wf-counters">{_last_return_label}{_star}</span>']
                        elif _last_cont:
                            chips += [SEP, f'<span class="wf-chip wf-end">{safe_html(_last_cont[:30])}</span>']

                    # Prepend pre-flight break for 02:xx shifts
                    if _pre_break_chip:
                        chips = [_pre_break_chip] + chips
                    # Prepend proactive midday break for single very-late task
                    if _proactive_break_chip:
                        chips = [_proactive_break_chip] + chips
                    if tasks and not _pre_break_chip and not _proactive_break_chip:
                        # No pre-flight break is shown for this worker, so nothing
                        # tells the shift manager WHEN to send them down to the
                        # gate. Show the walk-to-gate time on its own chip: the
                        # worker must leave the counters 15 min before their first
                        # task starts (user rule 2026-08-06 — the same 15-min walk
                        # that a pre-flight break chip already accounts for by
                        # ending 15 min early, made explicit for everyone else).
                        _gate_first_m = _hm(str(tasks[0].get("התחלה", "")))
                        if 0 <= _gate_first_m < 1440:  # _hm returns 9999 on failure
                            _gate_m = (_gate_first_m - 15) % 1440
                            chips = [
                                f'<span class="wf-chip wf-gate">🚶 ירידה לטיסה '
                                f'{_gate_m//60:02d}:{_gate_m%60:02d}</span>' + SEP
                            ] + chips

                    _emp_cat_tag = _wf_category_map.get(clean_text(_emp_name), "")
                    if _emp_cat_tag == "ט":
                        # Hover tooltip showing which veteran דייל this trainee is
                        # paired with (or an explicit "not paired" note) — user
                        # rule 2026-07-17. Names are escaped individually; the
                        # surrounding span/tooltip markup is NOT escaped (it's
                        # static HTML we control, not user data).
                        _wf_partner = _wf_pairing_map.get(clean_text(_emp_name))
                        # _wf_scheduled_name_keys (name_key-normalised, order-
                        # independent) is what the comment above actually calls
                        # for — _wf_scheduled_names is a plain clean_text set and
                        # stayed order-SENSITIVE, so a mentor whose roster name is
                        # word-order-reversed from their schedule name (e.g. "TL#13" vs "בן TL#13") was still wrongly reported as
                        # off-shift (found via real data 2026-08-02: worker#7
                        # סיום's mentor showed "טרייני ללא חונך" despite being
                        # genuinely scheduled that day).
                        if _wf_partner and name_key(clean_text(_wf_partner)) not in _wf_scheduled_name_keys:
                            # The named mentor isn't actually working today (sick
                            # etc.) — the trainee has effectively no mentor on
                            # shift, even though the roster paired them on paper.
                            _wf_partner = None
                            # "טרייני" has no feminine form; the mentor noun does
                            # (חונך/חונכת) and follows the MENTOR's gender, while
                            # "מוצמד/מוצמדת" follows the trainee's.
                            _wf_tooltip_text = "טרייני ללא חונך"
                        else:
                            _wf_tr_male = _wf_is_male.get(clean_text(_emp_name), False)
                            _wf_att = "מוצמד" if _wf_tr_male else "מוצמדת"
                            _wf_tooltip_text = (
                                f"{_wf_att} ל: {_wf_partner}" if _wf_partner
                                else f"לא {_wf_att} לאף עובד"
                            )
                        _emp_name_html = (
                            f'{safe_html(_emp_name)} '
                            f'<span class="wf-trainee-tag">(ט)'
                            f'<span class="wf-tooltip-box">{safe_html(_wf_tooltip_text)}</span>'
                            f'</span>'
                        )
                    elif _emp_cat_tag:
                        _emp_name_html = safe_html(f"{_emp_name} ({_emp_cat_tag})")
                    else:
                        _emp_name_html = safe_html(_emp_name)

                    _c_btn, _c_cancel, _c_chips, _c_name = st.columns([1, 1, 12, 3])
                    with _c_name:
                        st.markdown(
                            f'<div style="text-align:right;font-weight:bold;font-size:16px;'
                            f'color:#e0e0e0;line-height:1.5">{_emp_name_html}</div>'
                            + (f'<div style="text-align:right;font-size:13px;color:#aaa;'
                               f'direction:ltr;unicode-bidi:embed">{safe_html(_shift_str)}</div>'
                               if _shift_str else ""),
                            unsafe_allow_html=True,
                        )
                    with _c_chips:
                        st.markdown(f'<div class="wf-chips">{"".join(chips)}</div>',
                                    unsafe_allow_html=True)
                    # ── Callbacks — fire before fragment rerun (one-click) ──
                    # Keyed by _row_id (not _emp_name) so a dual-shift worker's two
                    # split rows track briefing state independently.
                    def _cb_brief(en=_row_id, tsk=tasks):
                        _ft0 = tsk[0] if tsk else {}
                        st.session_state["wf_briefing"][en] = {
                            "state": 1,
                            "first_task":  str(_ft0.get("טיסה", "")).strip(),
                            "first_start": str(_ft0.get("התחלה", "")).strip(),
                        }
                    def _cb_send(en=_row_id):
                        st.session_state["wf_briefing"][en]["state"] = 2
                    def _cb_cancel1(en=_row_id):
                        st.session_state["wf_briefing"].pop(en, None)
                    def _cb_cancel2(en=_row_id):
                        st.session_state["wf_briefing"][en]["state"] = 1

                    with _c_btn:
                        if _bstate == 0:
                            st.button("○", key=f"wf_brief_{_row_id}",
                                      help=f"סמן בריפינג — {_emp_name} קיבל/ה משימות",
                                      on_click=_cb_brief,
                                      use_container_width=True)
                        elif _bstate == 1:
                            st.button("✓✓", key=f"wf_send_{_row_id}",
                                      help=f"שלח לאולם — {_emp_name} יורד/ת לשער",
                                      type="primary",
                                      on_click=_cb_send,
                                      use_container_width=True)
                        else:
                            st.markdown(
                                '<div style="text-align:center;color:#22c55e;'
                                'font-size:18px;font-weight:bold;padding-top:4px">✓✓</div>',
                                unsafe_allow_html=True,
                            )
                    with _c_cancel:
                        if _bstate == 1:
                            st.button("בטל 1/2", key=f"wf_cancel1_{_row_id}",
                                      help="בטל בריפינג — חזור למצב ראשוני",
                                      on_click=_cb_cancel1,
                                      use_container_width=True)
                        elif _bstate == 2:
                            st.button("בטל 2/2", key=f"wf_cancel2_{_row_id}",
                                      help="בטל הורדה — חזור למצב בריפינג",
                                      on_click=_cb_cancel2,
                                      use_container_width=True)

                    st.markdown('<hr style="margin:2px 0;border-color:#1e1e1e">',
                                unsafe_allow_html=True)

                if not _wf_groups:
                    st.info("לא נמצאו עובדים מתאימים.")

            _render_wf_tab()

    ## — Tab: לא משובצים / הפסקות
    if active_main_tab == TAB_UNASSIGNED:
        st.markdown("""
            <style>
                [data-testid="stAppViewContainer"] .block-container { direction: rtl; text-align: right; }
                [data-testid="stMarkdownContainer"] p { text-align: right; }
                [data-testid="stDataFrame"] { direction: rtl; }
                div[data-testid="column"] { direction: rtl; text-align: right; }
            </style>
        """, unsafe_allow_html=True)

        INNER_UNASSIGNED_WORKERS = "👥 עובדים לא משובצים"
        INNER_BREAKS = "🚨 ניהול הפסקות"

        active_breaks = []
        due_breaks = []
        late_breaks = []

        st.markdown("<h3 style='text-align:right'>🚨 לא משובצים / הפסקות</h3>", unsafe_allow_html=True)
        

        if "unassigned_inner_view" not in st.session_state:
            st.session_state["unassigned_inner_view"] = INNER_UNASSIGNED_WORKERS

        unassigned_inner_view = st.radio(
            "בחרי תצוגה",
            [INNER_UNASSIGNED_WORKERS, INNER_BREAKS],
            horizontal=True,
            key="unassigned_inner_view",
        )

        def clear_unassigned_search():
            st.session_state["unassigned_search"] = ""

        def _time_to_minutes(time_text):
            import re

            time_text = str(time_text).strip()
            match = re.search(r"(\d{1,2})[:.](\d{2})", time_text)

            if not match:
                return None

            hour = int(match.group(1))
            minute = int(match.group(2))

            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return None

            return hour * 60 + minute

        def _row_shift_start_minutes(row):
            possible_cols = [
                "שעת התחלה",
                "תחילת משמרת",
                "משמרת התחלה",
                "משמרת",
            ]

            for col in possible_cols:
                if col in row.index:
                    value = row.get(col, "")
                    minutes = _time_to_minutes(value)

                    if minutes is not None:
                        return minutes

            return None

        def _is_in_time_range(value_minutes, start_minutes, end_minutes):
            if value_minutes is None:
                return False

            # אם בחרנו אותה שעה, מציגים את כל היום
            if start_minutes == end_minutes:
                return True

            # טווח רגיל, למשל 06:00 עד 14:00
            if start_minutes < end_minutes:
                return start_minutes <= value_minutes < end_minutes

            # טווח שחוצה חצות, למשל 22:00 עד 06:00
            return value_minutes >= start_minutes or value_minutes < end_minutes

        # חישוב רשימות הפסקה, ללא הצגה למסך בתוך הלולאה
        if unassigned_df is None:
            unassigned_df = pd.DataFrame()

        for _, r in unassigned_df.iterrows():
            try:
                emp_name = clean_text(r.get("שם", r.get("עובד", "")))
                shift_label = str(r.get("משמרת", ""))

                if not emp_name:
                    continue

                break_info = st.session_state.get("break_log", {}).get(emp_name, {})

                on_break = bool(break_info.get("ts")) and not bool(break_info.get("end_ts"))
                break_done = bool(break_info.get("ts")) and bool(break_info.get("end_ts"))

                if on_break:
                    active_breaks.append(emp_name)
                    continue

                if break_done:
                    continue

                shift_start = shift_label.split("-")[0].strip()
                h, m = shift_start.split(":")
                shift_start_min = int(h) * 60 + int(m)

                now_min = datetime.now().hour * 60 + datetime.now().minute
                if now_min < shift_start_min:
                    now_min += 1440

                worked_minutes = now_min - shift_start_min

                if worked_minutes >= 360:
                    late_breaks.append(emp_name)
                elif worked_minutes >= 300:
                    due_breaks.append(emp_name)

            except Exception:
                pass

                # מסך עובדים לא משובצים בלבד
                if "עובדים לא משובצים" in str(unassigned_inner_view):
                    st.markdown("### 👥 עובדים לא משובצים")


                    debug_rows = []

                    for name in [
                        "unassigned_df",
                        "available_df",
                        "live_schedule",
                        "live_employees",
                        "employees_df",
                    ]:
                        if name in locals():
                            obj = locals()[name]
                            if obj is None:
                                debug_rows.append({"שם משתנה": name, "מצב": "None", "שורות": ""})
                            elif hasattr(obj, "shape"):
                                debug_rows.append({"שם משתנה": name, "מצב": "DataFrame", "שורות": obj.shape[0]})
                            else:
                                debug_rows.append({"שם משתנה": name, "מצב": type(obj).__name__, "שורות": ""})
                        else:
                            debug_rows.append({"שם משתנה": name, "מצב": "לא קיים", "שורות": ""})

                    st.dataframe(pd.DataFrame(debug_rows), use_container_width=True, hide_index=True)
                    
                    def clear_unassigned_search():
                        st.session_state["unassigned_search"] = ""

                    search_col, clear_col = st.columns([4, 1])

                    with search_col:
                        unassigned_search = st.text_input(
                            "🔎 חיפוש עובד",
                            placeholder="הקלידי שם עובד / תפקיד / משמרת",
                            key="unassigned_search",
                        ).strip()

                    with clear_col:
                        st.markdown("<br>", unsafe_allow_html=True)
                        st.button(
                            "נקה חיפוש",
                            key="clear_unassigned_search",
                            use_container_width=True,
                            on_click=clear_unassigned_search,
                        )

                unassigned_view_df = pd.DataFrame()

                # ניסיון 1: לקחת מהמשתנה הרגיל
                if "unassigned_df" in locals() and unassigned_df is not None and not unassigned_df.empty:
                    unassigned_view_df = unassigned_df.copy()

                # ניסיון 2: לקחת מה-session_state
                elif (
                    "unassigned_df" in st.session_state
                    and st.session_state["unassigned_df"] is not None
                    and not st.session_state["unassigned_df"].empty
                ):
                    unassigned_view_df = st.session_state["unassigned_df"].copy()

                # ניסיון 3: fallback למסך עובדים זמינים, אם קיים
                elif "available_df" in locals() and available_df is not None and not available_df.empty:
                    unassigned_view_df = available_df.copy()

                    if unassigned_search and not unassigned_view_df.empty:
                        search_mask = unassigned_view_df.astype(str).apply(
                            lambda row: row.str.contains(
                                unassigned_search,
                                case=False,
                                na=False,
                            ).any(),
                            axis=1,
                        )
                        unassigned_view_df = unassigned_view_df[search_mask].copy()

                    if unassigned_view_df.empty:
                        st.info("אין כרגע עובדים לא משובצים להצגה.")
                    else:
                        display_cols = [
                            c for c in [
                                "שם",
                                "משמרת",
                                "תפקיד",
                                "תפקיד עיקרי",
                                "הערה",
                                "סיבה",
                            ]
                            if c in unassigned_view_df.columns
                        ]

                        if display_cols:
                            unassigned_view_df = unassigned_view_df[display_cols].copy()

                        st.caption(f"סה״כ עובדים לא משובצים להצגה: {len(unassigned_view_df)}")

                        st.dataframe(
                            unassigned_view_df,
                            use_container_width=True,
                            hide_index=True,
                        )

                    st.stop()
            # מסך ניהול הפסקות בלבד
                if unassigned_inner_view == INNER_BREAKS:

                    # כרטיסי דחיפות יוצגו אחרי בניית break_priority_df

                    # הכרטיסים יוצגו אחרי בניית break_priority_rows,
                    # כדי שהמספרים יתאימו למה שמופיע בטבלה.
                    def _shift_to_minutes_from_text(shift_label):
                        shift_label = str(shift_label).strip()

                        if "-" not in shift_label:
                            return None, None

                        start_txt, end_txt = shift_label.split("-", 1)

                        try:
                            sh, sm = start_txt.strip().split(":")[:2]
                            eh, em = end_txt.strip().split(":")[:2]

                            start_min = int(sh) * 60 + int(sm)
                            end_min = int(eh) * 60 + int(em)

                            return start_min, end_min
                        except Exception:
                            return None, None

                    def _is_now_inside_shift_label(shift_label, now_dt):
                        start_min, end_min = _shift_to_minutes_from_text(shift_label)

                        if start_min is None or end_min is None:
                            return True

                        now_min = now_dt.hour * 60 + now_dt.minute

                        # משמרת רגילה, למשל 10:00-18:00
                        if end_min >= start_min:
                            return start_min <= now_min <= end_min

                        # משמרת שחוצה חצות, למשל 20:30-04:00
                        return now_min >= start_min or now_min <= end_min

                    def _is_night_shift_label_20_to_04(shift_label):
                        start_min, end_min = _shift_to_minutes_from_text(shift_label)

                        if start_min is None or end_min is None:
                            return False

                        # משמרת לילה: מתחילה מ־20:00 והלאה,
                        # חוצה חצות, ומסתיימת ב־04:00 או מאוחר יותר.
                        return (
                            start_min >= 20 * 60
                            and end_min < start_min
                            and end_min >= 4 * 60
                        )

                    def employee_shift_label(emp_name, unassigned_df):
                        emp_name = clean_text(emp_name)

                        if unassigned_df is None or unassigned_df.empty:
                            return ""

                        if "שם" not in unassigned_df.columns or "משמרת" not in unassigned_df.columns:
                            return ""

                        rows = unassigned_df[
                            unassigned_df["שם"].astype(str).map(clean_text) == emp_name
                        ]

                        if rows.empty:
                            return ""

                        return clean_text(rows.iloc[0].get("משמרת", ""))

                    break_priority_rows = []

            # Skip UI rendering for all but the last employee in the loop
            if _ != unassigned_df.index[-1]:
                continue

            def bp_get_shift_label(emp_name):
                emp_name = clean_text(emp_name)
                shift_label = ""

                try:
                    if (
                        unassigned_df is not None
                        and not unassigned_df.empty
                        and "שם" in unassigned_df.columns
                        and "משמרת" in unassigned_df.columns
                    ):
                        emp_rows = unassigned_df[
                            unassigned_df["שם"].astype(str).map(clean_text) == emp_name
                        ]

                        if not emp_rows.empty:
                            shift_label = clean_text(emp_rows.iloc[0].get("משמרת", ""))
                except Exception:
                    shift_label = ""

                return shift_label


            def bp_parse_shift_minutes(shift_label):
                try:
                    if "-" not in str(shift_label):
                        return None, None

                    start_txt, end_txt = str(shift_label).split("-", 1)

                    sh, sm = start_txt.strip().split(":")[:2]
                    eh, em = end_txt.strip().split(":")[:2]

                    start_min = int(sh) * 60 + int(sm)
                    end_min = int(eh) * 60 + int(em)

                    return start_min, end_min
                except Exception:
                    return None, None


            def bp_is_now_inside_shift(shift_label):
                start_min, end_min = bp_parse_shift_minutes(shift_label)

                if start_min is None or end_min is None:
                    return True

                now_dt = app_now()
                now_min = now_dt.hour * 60 + now_dt.minute

                # משמרת רגילה, למשל 10:00-18:00
                if end_min >= start_min:
                    return start_min <= now_min <= end_min

                # משמרת שחוצה חצות, למשל 20:30-04:00
                return now_min >= start_min or now_min <= end_min


            def bp_is_night_or_late_evening_shift(shift_label):
                start_min, end_min = bp_parse_shift_minutes(shift_label)

                if start_min is None or end_min is None:
                    return False

                # עובד לילה רגיל:
                # מתחיל מ־20:00 והלאה, חוצה חצות, ומסיים ב־04:00 או מאוחר יותר
                is_night_shift = (
                    start_min >= 20 * 60
                    and end_min < start_min
                    and end_min >= 4 * 60
                )

                # ערב־לילה קצר, למשל 19:00-01:30
                # גם אותו לא נציג כ"חייב לצאת להפסקה" באמצע הלילה
                is_late_evening_shift = (
                    start_min >= 19 * 60
                    and end_min < start_min
                    and end_min <= 2 * 60
                )

                return is_night_shift or is_late_evening_shift

            break_priority_rows = []

            for emp_name in sorted(set(late_breaks)):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    continue

                shift_label = bp_get_shift_label(emp_name)

                # לא להציג עובד לפני תחילת משמרת
                if shift_label and not bp_is_now_inside_shift(shift_label):
                    continue

                # לא להציג עובדי לילה / ערב־לילה כדחופים להפסקה לפני או במהלך טיסות לילה
                if shift_label and bp_is_night_or_late_evening_shift(shift_label):
                    continue

                break_priority_rows.append({
                    "שם": emp_name,
                    "דחיפות": "🔴 חייבים לצאת להפסקה",
                })


            for emp_name in sorted(set(due_breaks) - set(late_breaks)):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    continue

                shift_label = bp_get_shift_label(emp_name)

                # לא להציג עובד לפני תחילת משמרת
                if shift_label and not bp_is_now_inside_shift(shift_label):
                    continue

                # לא להציג עובדי לילה / ערב־לילה כדחופים להפסקה לפני או במהלך טיסות לילה
                if shift_label and bp_is_night_or_late_evening_shift(shift_label):
                    continue

                break_priority_rows.append({
                    "שם": emp_name,
                    "דחיפות": "🟠 ב־60 דקות הקרובות",
                })


            for emp_name in sorted(set(active_breaks)):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    continue

                shift_label = bp_get_shift_label(emp_name)

                # מי שבהפסקה כן חשוב להציג, אבל רק אם המשמרת שלו פעילה
                if shift_label and not bp_is_now_inside_shift(shift_label):
                    continue

                break_priority_rows.append({
                    "שם": emp_name,
                    "דחיפות": "🟢 כרגע בהפסקה",
                })

            # בניית טבלת דחיפות אחרי כל הלולאות
            if not break_priority_rows:
                break_priority_df = pd.DataFrame(columns=["שם", "דחיפות"])
            else:
                break_priority_df = pd.DataFrame(break_priority_rows)

                enrich_cols = [
                    c for c in ["שם", "משמרת", "תפקיד", "תפקיד עיקרי", "הערה"]
                    if c in unassigned_df.columns
                ]

                if "שם" in enrich_cols:
                    break_priority_df = break_priority_df.merge(
                        unassigned_df[enrich_cols].drop_duplicates(subset=["שם"]),
                        on="שם",
                        how="left",
                    )

                display_cols = [
                    c for c in ["שם", "משמרת", "תפקיד", "תפקיד עיקרי", "דחיפות", "הערה"]
                    if c in break_priority_df.columns
                ]

                if display_cols:
                    break_priority_df = break_priority_df[display_cols]

                if unassigned_inner_view == INNER_BREAKS:
                    urgent_count = 0
                    due_count = 0
                    active_count = 0

                    if "דחיפות" in break_priority_df.columns:
                        urgent_count = break_priority_df["דחיפות"].astype(str).str.contains("חייבים", na=False).sum()
                        due_count = break_priority_df["דחיפות"].astype(str).str.contains("60 דקות", na=False).sum()
                        active_count = break_priority_df["דחיפות"].astype(str).str.contains("כרגע בהפסקה", na=False).sum()

                    c1, c2, c3 = st.columns(3)

                    with c1:
                        st.error(f"🔴 עובדים דחופים להפסקה: {urgent_count}")

                    with c2:
                        st.warning(f"🟠 ב־60 דקות הקרובות: {due_count}")

                    with c3:
                        st.success(f"🟢 כרגע בהפסקה: {active_count}")

                    st.markdown(f"#### 📋 עובדים לפי דחיפות הפסקה עכשיו ({len(break_priority_df)})")

                    st.dataframe(
                        break_priority_df,
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.markdown("---")
                # ============================================================
                # מסך הפסקות נקי
                # 1. חייבים לפני טיסה
                # 2. עובדים בהפסקה כרגע
                # 3. תכנון המשך משמרת
                # 4. הוצאה יזומה
                # ============================================================

                def _bp_now():
                    return app_now()


                def _bp_format_dt(value):
                    if value is None or value == "":
                        return ""

                    try:
                        dt = pd.to_datetime(value)
                        return dt.strftime("%H:%M")
                    except Exception:
                        return str(value)


                def _bp_elapsed_text(value):
                    if value is None or value == "":
                        return ""

                    try:
                        start_dt = pd.to_datetime(value)
                        now_dt = pd.to_datetime(_bp_now())

                        if getattr(start_dt, "tzinfo", None) is not None:
                            start_dt = start_dt.tz_localize(None)

                        if getattr(now_dt, "tzinfo", None) is not None:
                            now_dt = now_dt.tz_localize(None)

                        minutes = int((now_dt - start_dt).total_seconds() // 60)

                        if minutes < 0:
                            minutes = 0

                        hours = minutes // 60
                        mins = minutes % 60

                        if hours:
                            return f"{hours}:{mins:02d} שעות"

                        return f"{mins} דקות"
                    except Exception:
                        return ""


            def _bp_now():
                return app_now()

            def _bp_format_dt(value):
                if value is None or value == "":
                    return ""
                try:
                    dt = pd.to_datetime(value)
                    return dt.strftime("%H:%M")
                except Exception:
                    return str(value)

            def _bp_elapsed_text(value):
                if value is None or value == "":
                    return ""
                try:
                    start_dt = pd.to_datetime(value)
                    now_dt = pd.to_datetime(_bp_now())
                    if getattr(start_dt, "tzinfo", None) is not None:
                        start_dt = start_dt.tz_localize(None)
                    if getattr(now_dt, "tzinfo", None) is not None:
                        now_dt = now_dt.tz_localize(None)
                    minutes = int((now_dt - start_dt).total_seconds() // 60)
                    if minutes < 0:
                        minutes = 0
                    hours = minutes // 60
                    mins = minutes % 60
                    if hours:
                        return f"{hours}:{mins:02d} שעות"
                    return f"{mins} דקות"
                except Exception:
                    return ""

            def _bp_elapsed_minutes(value):
                if value is None or value == "":
                    return 0
                try:
                    start_dt = pd.to_datetime(value)
                    now_dt = pd.to_datetime(_bp_now())
                    if getattr(start_dt, "tzinfo", None) is not None:
                        start_dt = start_dt.tz_localize(None)
                    if getattr(now_dt, "tzinfo", None) is not None:
                        now_dt = now_dt.tz_localize(None)
                    return max(0, int((now_dt - start_dt).total_seconds() // 60))
                except Exception:
                    return 0

            def _bp_allowed_minutes(break_type):
                if "רענון" in str(break_type):
                    return 20
                return 45

            def _bp_is_active_break(info):
                if not isinstance(info, dict):
                    return False

                if info.get("active") is True:
                    return True

                # תמיכה במבנה הישן של break_log
                if info.get("ts") and not info.get("end_ts"):
                    return True

                if info.get("start_dt") and not info.get("end_dt") and not info.get("end_ts"):
                    return True

                return False


            def _bp_break_start_raw(info):
                if not isinstance(info, dict):
                    return ""

                return (
                    info.get("start_dt")
                    or info.get("ts")
                    or info.get("start")
                    or ""
                )


            def _bp_start_break(emp_name, break_type="הפסקה", source="ידני"):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    return

                now_dt = _bp_now()

                st.session_state.setdefault("break_log", {})
                st.session_state["break_log"][emp_name] = {
                    "active": True,
                    "start": now_dt.strftime("%H:%M"),
                    "start_dt": now_dt.isoformat(),
                    "expected_end": "",
                    "break_type": break_type,
                    "source": source,
                }


            def _bp_end_break(emp_name):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    return

                now_dt = _bp_now()

                st.session_state.setdefault("break_log", {})
                st.session_state["break_log"].setdefault(emp_name, {})
                st.session_state["break_log"][emp_name]["active"] = False
                st.session_state["break_log"][emp_name]["end"] = now_dt.strftime("%H:%M")
                st.session_state["break_log"][emp_name]["end_dt"] = now_dt.isoformat()
                st.session_state["break_log"][emp_name]["end_ts"] = now_dt


            def _bp_reset_break(emp_name):
                emp_name = clean_text(emp_name)

                if not emp_name:
                    return

                if "break_log" in st.session_state:
                    st.session_state["break_log"].pop(emp_name, None)


            def _bp_active_names():
                names = set()

                for emp_name, info in st.session_state.get("break_log", {}).items():
                    if _bp_is_active_break(info):
                        names.add(clean_text(emp_name))

                return names


            def _bp_get_col(row, candidates):
                for col in candidates:
                    if col in row.index:
                        value = row.get(col)
                        if pd.notna(value) and str(value).strip():
                            return clean_text(value)
                return ""


            def _bp_employee_role(row):
                return _bp_get_col(row, ["תפקיד", "תפקיד עיקרי", "Role", "role"])


            active_names = _bp_active_names()


            # ============================================================
            # מסך עובדים לא משובצים
            # ============================================================
            if "עובדים לא משובצים" in str(unassigned_inner_view):
                st.markdown("<h3 style='text-align:right'>👥 עובדים לא משובצים</h3>", unsafe_allow_html=True)

                def clear_unassigned_search():
                    st.session_state["unassigned_search"] = ""

                search_col, clear_col = st.columns([4, 1])
                with search_col:
                    unassigned_search = st.text_input(
                        "🔎 חיפוש עובד",
                        placeholder="הקלידי שם עובד / תפקיד / משמרת",
                        key="unassigned_search",
                    ).strip()
                with clear_col:
                    st.markdown("<br>", unsafe_allow_html=True)
                    st.button(
                        "נקה חיפוש",
                        key="clear_unassigned_search",
                        use_container_width=True,
                        on_click=clear_unassigned_search,
                    )

                unassigned_view_df = pd.DataFrame()
                if unassigned_df is not None and not unassigned_df.empty:
                    unassigned_view_df = unassigned_df.copy()

                if unassigned_search and not unassigned_view_df.empty:
                    search_mask = unassigned_view_df.astype(str).apply(
                        lambda row: row.str.contains(unassigned_search, case=False, na=False).any(),
                        axis=1,
                    )
                    unassigned_view_df = unassigned_view_df[search_mask].copy()

                if unassigned_view_df.empty:
                    st.info("אין כרגע עובדים לא משובצים להצגה.")
                else:
                    display_cols = [
                        c for c in ["שם", "משמרת", "תפקיד", "תפקיד עיקרי", "הערה", "סיבה"]
                        if c in unassigned_view_df.columns
                    ]
                    if display_cols:
                        unassigned_view_df = unassigned_view_df[display_cols].copy()
                    st.markdown(f"<p style='text-align:right;font-size:0.85em;color:gray;'>סה״כ עובדים לא משובצים: {len(unassigned_view_df)}</p>", unsafe_allow_html=True)
                    col_width = f"{100 // max(len(unassigned_view_df.columns), 1)}%"
                    header_html = "".join(
                        f"<th style='text-align:center;padding:8px 12px;background:rgba(255,255,255,0.08);border:1px solid rgba(255,255,255,0.15);color:inherit;width:{col_width};'>{col}</th>"
                        for col in unassigned_view_df.columns
                    )
                    rows_html = ""
                    for _, row in unassigned_view_df.iterrows():
                        cells = "".join(
                            f"<td style='text-align:center;padding:6px 12px;border:1px solid rgba(255,255,255,0.08);'>{'' if pd.isna(v) else v}</td>"
                            for v in row
                        )
                        rows_html += f"<tr>{cells}</tr>"
                    st.markdown(
                        f"<div style='overflow-x:auto'><table style='width:100%;border-collapse:collapse;direction:rtl;color:inherit;table-layout:fixed;'>"
                        f"<thead><tr>{header_html}</tr></thead><tbody>{rows_html}</tbody></table></div>",
                        unsafe_allow_html=True,
                    )

                st.stop()

            # ============================================================
            # 1. חייבים לצאת להפסקה לפני טיסה
            # ============================================================
            st.markdown("---")
           
            urgent_flight_break_df = pd.DataFrame()
            urgent_flight_rows = []
           
            if unassigned_inner_view == INNER_BREAKS:
                st.markdown("<h3 style='text-align:right'>🚨 חייבים לצאת להפסקה לפני טיסה</h3>", unsafe_allow_html=True)
                st.markdown("<p style='text-align:right;color:gray;font-size:0.85em'>עובדים שאם לא יצאו עכשיו או עד הדדליין, לא יישאר להם חלון הפסקה תקין לפני המשימה.</p>", unsafe_allow_html=True)


            def _urgent_clean(value):
                try:
                    return clean_text(value)
                except Exception:
                    return str(value).strip()


            def _urgent_time_to_minutes(value):
                try:
                    if pd.isna(value):
                        return None

                    # אם זה datetime / Timestamp
                    if hasattr(value, "hour") and hasattr(value, "minute"):
                        return int(value.hour) * 60 + int(value.minute)

                    text = str(value).strip()

                    if not text or ":" not in text:
                        return None

                    text = text[:5]
                    hh, mm = text.split(":")[:2]
                    return int(hh) * 60 + int(mm)

                except Exception:
                    return None


            def _urgent_shift_to_minutes(shift_label):
                try:
                    text = str(shift_label).strip()

                    if "-" not in text:
                        return None, None

                    start_txt, end_txt = text.split("-", 1)

                    return _urgent_time_to_minutes(start_txt), _urgent_time_to_minutes(end_txt)

                except Exception:
                    return None, None


            def _urgent_is_early_0200_shift(shift_label):
                start_min, end_min = _urgent_shift_to_minutes(shift_label)

                if start_min is None or end_min is None:
                    return False

                return (
                    1 * 60 + 45 <= start_min <= 2 * 60 + 30
                    and 8 * 60 <= end_min <= 9 * 60 + 45
                )


            def _urgent_find_col(df, options):
                if df is None or df.empty:
                    return None

                for col in options:
                    if col in df.columns:
                        return col

                return None


            def _urgent_flight_text(row):
                flight = ""
                for col in ["טיסה", "טיסה קרובה", "מספר טיסה", "Flight", "flight"]:
                    if col in row.index:
                        val = row.get(col)
                        if pd.notna(val) and str(val).strip():
                            flight = _urgent_clean(val)
                            break

                # שעת המראה = סיום המשימה
                dep_min = _urgent_task_end_minutes(row)
                if dep_min is None:
                    dep_min = _urgent_task_start_minutes(row)
                if dep_min is not None:
                    dep_str = f"{dep_min // 60:02d}:{dep_min % 60:02d}"
                    return f"{flight} {dep_str}".strip() if flight else dep_str

                return flight


            def _urgent_task_start_minutes(row):
                possible_cols = [
                    "התחלה",
                    "תחילת פעילות",
                    "שעת פעילות",
                    "שעת התחלה",
                    "המראה",
                    "זמן טיסה",
                    "זמן המראה",
                    "start",
                    "Start",
                ]
                for col in possible_cols:
                    if col in row.index:
                        value = row.get(col)
                        minutes = _urgent_time_to_minutes(value)
                        if minutes is not None:
                            return minutes
                return None

            def _urgent_task_end_minutes(row):
                """זמן סיום המשימה = שעת המראה בפועל"""
                possible_cols = ["סיום", "שעת סיום", "end", "End", "המראה", "זמן המראה"]
                for col in possible_cols:
                    if col in row.index:
                        value = row.get(col)
                        minutes = _urgent_time_to_minutes(value)
                        if minutes is not None:
                            return minutes
                return None

            def _urgent_is_0200_shift(s_min, e_min):
                """משמרת שמתחילה בין 01:30-02:30 ונגמרת בין 08:00-10:00"""
                if s_min is None or e_min is None:
                    return False
                return 90 <= s_min <= 150 and 480 <= e_min <= 600


            def _urgent_build_shift_map():
                # מפתחות: name_key(שם) → "HH:MM-HH:MM"
                shift_map = {}

                ref = st.session_state.get("shift_map_ref", {})
                for key, info in ref.items():
                    # The second same-named person's entry (TL#39 on סיירת)
                    # shares its "original" with the plain one — its reversed-
                    # name key below would overwrite the other twin's hours.
                    if str(key).endswith(TWIN_ALT_SUFFIX):
                        continue
                    if isinstance(info, dict):
                        s = info.get("start", "")
                        e = info.get("end", "")
                        original = info.get("original", "")
                        if s and e:
                            label = f"{s}-{e}"
                            shift_map[key] = label
                            # גם מפתח עם שם הפוך
                            if original:
                                shift_map[name_key_reversed(original)] = label

                # מקור משני: unassigned_df
                if "unassigned_df" in locals() and unassigned_df is not None and not unassigned_df.empty:
                    nc = _urgent_find_col(unassigned_df, ["שם", "עובד"])
                    sc = _urgent_find_col(unassigned_df, ["משמרת"])
                    if nc and sc:
                        for _, src_row in unassigned_df.iterrows():
                            raw_name = str(src_row.get(nc, ""))
                            k1 = name_key(raw_name)
                            k2 = name_key_reversed(raw_name)
                            v = _urgent_clean(src_row.get(sc, ""))
                            if v:
                                if k1 and k1 not in shift_map:
                                    shift_map[k1] = v
                                if k2 and k2 not in shift_map:
                                    shift_map[k2] = v

                return shift_map


            if (
                "live_schedule" in locals()
                and live_schedule is not None
                and not live_schedule.empty
            ):
                schedule_name_col = _urgent_find_col(
                    live_schedule,
                    ["שם", "עובד", "שם עובד", "דייל", "דייל/ת"]
                )

                shift_map = _urgent_build_shift_map()

                if schedule_name_col:
                    schedule_df_for_urgent = live_schedule.copy()
                    schedule_df_for_urgent["_urgent_emp_name"] = (
                        schedule_df_for_urgent[schedule_name_col]
                        .astype(str)
                        .map(_urgent_clean)
                    )

                    for emp_name in sorted(schedule_df_for_urgent["_urgent_emp_name"].dropna().unique()):
                        emp_name = _urgent_clean(emp_name)

                        if not emp_name:
                            continue

                        # פלייסהולדרים מהשיבוץ — לא עובדים אמיתיים
                        if "חסר" in emp_name or "ראש צוות" in emp_name:
                            continue

                        # עובד שכרגע בהפסקה — לא מציגים
                        if emp_name in active_names:
                            continue

                        def _find_shift(n, _sm=shift_map):
                            import difflib
                            k  = name_key(n)
                            kr = name_key_reversed(n)
                            # 1. exact key
                            if k  in _sm: return _sm[k]
                            if kr in _sm: return _sm[kr]
                            # 2. substring
                            if len(k) >= 4:
                                for key, val in _sm.items():
                                    if k in key or key in k or kr in key or key in kr:
                                        return val
                            # 3. word pairs
                            words = n.strip().split()
                            if len(words) >= 3:
                                for i in range(len(words) - 1):
                                    pair = name_key(words[i] + words[i+1])
                                    if len(pair) >= 4:
                                        for key, val in _sm.items():
                                            if pair in key or key in pair:
                                                return val
                            # 4. כל מילה בודדת (4+ תווים) כ-substring בתוך מפתח
                            for w in sorted(words, key=len, reverse=True):
                                wk = name_key(w)
                                if len(wk) >= 4:
                                    candidates = [(key, val) for key, val in _sm.items() if wk in key]
                                    if candidates:
                                        # בחר את המפתח הקרוב ביותר לשם המלא
                                        best = difflib.get_close_matches(k, [c[0] for c in candidates], n=1, cutoff=0.0)
                                        if best:
                                            return _sm[best[0]]
                            # 5. כל מילות השם קצרות (כמו "נח גל") — בדוק אם כולן נמצאות בשם ארוך
                            short_words = [w for w in words if len(w) >= 2]
                            if short_words:
                                for key, val in _sm.items():
                                    if all(w in key for w in short_words):
                                        return val
                            # 6. difflib fuzzy — cutoff 0.65
                            all_keys = list(_sm.keys())
                            matches = difflib.get_close_matches(k, all_keys, n=1, cutoff=0.65)
                            if matches:
                                return _sm[matches[0]]
                            matches = difflib.get_close_matches(kr, all_keys, n=1, cutoff=0.65)
                            if matches:
                                return _sm[matches[0]]
                            return ""
                        shift_label = _find_shift(emp_name)

                        # משמרת לילה (נייט) — חוקים אחרים, לא מוצגת כאן
                        if shift_label and bp_is_night_or_late_evening_shift(shift_label):
                            continue

                        s_min, e_min = bp_parse_shift_minutes(shift_label)

                        walk_time = 15

                        # משך הפסקה לפי אורך המשמרת
                        if s_min is not None and e_min is not None:
                            raw_dur = (e_min + 1440 - s_min) if e_min < s_min else (e_min - s_min)
                            shift_duration = raw_dur
                            if shift_duration > 6 * 60:
                                break_duration = 45   # משמרת >9.5ש: 45 הפסקה + 20 רענון נפרד
                            else:
                                break_duration = 20
                        else:
                            shift_duration = 7 * 60
                            break_duration = 45

                        # האם עומד לקבל גם רענון (משמרת >9.5ש)
                        needs_refresh = shift_duration > 9 * 60 + 30

                        is_0200 = _urgent_is_0200_shift(s_min, e_min)
                        is_long = shift_duration > 6 * 60

                        emp_tasks = schedule_df_for_urgent[
                            schedule_df_for_urgent["_urgent_emp_name"] == emp_name
                        ].copy()

                        shift_start_min = s_min if s_min is not None else 0

                        # מינימום זמן בדלפקים לפני הפסקה:
                        # משמרת >7ש → 90 דק', אחרת → 60 דק'
                        min_desk_time = 90 if shift_duration > 7 * 60 else 60
                        earliest_break_start = shift_start_min + min_desk_time

                        # כלל 4 שעות: לא יותר מ-4 שעות רצופות
                        four_hour_deadline = shift_start_min + 4 * 60

                        # ─── משמרת 02:00: רק טיסה לפני 05:15 שיש לה חלון הפסקה תקין ───
                        if is_0200:
                            CUTOFF_MIN = 5 * 60 + 15
                            first_task = None
                            first_start_min = None

                            for _, task_row in emp_tasks.iterrows():
                                task_start_min = _urgent_task_start_minutes(task_row)
                                if task_start_min is None:
                                    continue
                                dep = _urgent_task_end_minutes(task_row)
                                dep = dep if dep is not None else task_start_min
                                if dep > CUTOFF_MIN:
                                    continue
                                # חלון הפסקה חייב להיות תקין: break מסתיים לפני task_start
                                pfd = task_start_min - walk_time - break_duration
                                if pfd < earliest_break_start:
                                    continue  # אין זמן להפסקה תקינה לפני טיסה זו
                                if first_start_min is None or task_start_min < first_start_min:
                                    first_start_min = task_start_min
                                    first_task = task_row

                            if first_task is None or first_start_min is None:
                                continue  # אין טיסה לפני 05:15 עם חלון תקין

                            pre_flight_deadline = first_start_min - walk_time - break_duration
                            deadline_min = min(four_hour_deadline, pre_flight_deadline)
                            refresh_note = " רענון 20 דק' נפרד בהמשך." if needs_refresh else ""
                            reason = f"הפסקה {break_duration} דק' + {walk_time} דק' הליכה לפני הטיסה.{refresh_note}"

                        # ─── שאר המשמרות ───
                        else:
                            first_task = None
                            first_start_min = None

                            for _, task_row in emp_tasks.iterrows():
                                task_start_min = _urgent_task_start_minutes(task_row)
                                if task_start_min is None:
                                    continue
                                if first_start_min is None or task_start_min < first_start_min:
                                    first_start_min = task_start_min
                                    first_task = task_row

                            if first_task is None or first_start_min is None:
                                continue

                            if first_start_min < shift_start_min:
                                continue

                            pre_flight_deadline = first_start_min - walk_time - break_duration

                            # מציגים רק כשהטיסה יוצרת דחיפות שמוקדמת מכלל 4 השעות.
                            # אם הדדליין = כלל 4 שעות — הפסקה יזומה רגילה תכסה את הצורך.
                            if pre_flight_deadline >= four_hour_deadline:
                                continue

                            deadline_min = pre_flight_deadline

                            if deadline_min < earliest_break_start:
                                continue

                            refresh_note = " רענון 20 דק' נפרד בהמשך." if needs_refresh else ""
                            if is_long:
                                reason = f"הפסקה {break_duration} דק' + {walk_time} דק' הליכה לפני הטיסה. ניתן גם בסיום השיבוץ / בין טיסות.{refresh_note}"
                            else:
                                reason = f"הפסקה {break_duration} דק' + {walk_time} דק' הליכה לפני הטיסה.{refresh_note}"

                        task_start_fmt = f"{first_start_min // 60:02d}:{first_start_min % 60:02d}"
                        earliest_fmt   = f"{earliest_break_start // 60:02d}:{earliest_break_start % 60:02d}"
                        calc_text = (
                            f"תחילת משמרת: {shift_start_min // 60:02d}:{shift_start_min % 60:02d} | "
                            f"מינימום בדלפקים: {min_desk_time} דק' → הכי מוקדם {earliest_fmt} | "
                            f"תחילת משימה: {task_start_fmt} | "
                            f"דדליין = {task_start_fmt} − {break_duration} דק' הפסקה − {walk_time} דק' הליכה = "
                            f"{deadline_min // 60:02d}:{deadline_min % 60:02d}"
                        )
                        urgent_flight_rows.append({
                            "שם": emp_name,
                            "משמרת": shift_label if shift_label else "—",
                            "טיסה קרובה": _urgent_flight_text(first_task),
                            "דדליין": f"{deadline_min // 60:02d}:{deadline_min % 60:02d}",
                            "סיבה": reason,
                            "_calc": calc_text,
                        })


            urgent_flight_break_df = pd.DataFrame(urgent_flight_rows)

            if urgent_flight_break_df.empty:
                st.info("אין כרגע עובדים שחייבים לצאת להפסקה לפני טיסה.")
            else:
                valid_rows = [(idx, row) for idx, row in urgent_flight_break_df.iterrows() if clean_text(row.get("שם", ""))]

                st.markdown("""
                <style>
                div[data-testid="stHorizontalBlock"]:has(> div[data-testid="column"]) .urgent-cell {
                    border-bottom: 1px solid rgba(255,255,255,0.08);
                    min-height: 42px; display:flex; align-items:center; justify-content:center;
                    font-size: 0.88em; padding: 4px 6px;
                }
                </style>
                """, unsafe_allow_html=True)

                RATIOS  = [1.8, 1.2, 1.1, 1.0, 1.2]
                HEADERS = ["שם", "משמרת", "טיסה", "דדליין", "פעולה"]
                DKEYS   = ["שם", "משמרת", "טיסה קרובה", "דדליין"]

                C_HDR = "background:rgba(255,255,255,0.11);text-align:center;font-weight:700;font-size:0.85em;padding:10px 6px;border-radius:3px;letter-spacing:0.02em;"
                C_CELL = "text-align:center;"

                # כותרת
                hcols = st.columns(RATIOS)
                for hc, ht in zip(hcols, HEADERS):
                    hc.markdown(f"<div style='{C_HDR}'>{ht}</div>", unsafe_allow_html=True)

                # שורות נתונים
                for i, (idx, row) in enumerate(valid_rows):
                    bg = "rgba(255,255,255,0.025)" if i % 2 == 0 else "rgba(255,255,255,0.06)"
                    emp_name = clean_text(row.get("שם", ""))
                    dcols = st.columns(RATIOS)
                    for dc, dk in zip(dcols[:-1], DKEYS):
                        val = str(row.get(dk, ""))
                        dc.markdown(
                            f"<div style='background:{bg};{C_CELL}min-height:42px;display:flex;"
                            f"align-items:center;justify-content:center;font-size:0.88em;"
                            f"padding:4px 4px;border-bottom:1px solid rgba(255,255,255,0.07);'>{val}</div>",
                            unsafe_allow_html=True
                        )
                    with dcols[-1]:
                        if st.button("הוצא להפסקה", key=f"urgent_break_start_{idx}_{emp_name}", use_container_width=True):
                            _bp_start_break(emp_name, break_type="לפני טיסה", source="דדליין לפני טיסה")
                            st.rerun()

                with st.expander("🔍 פירוט חישובי דדליין"):
                    for _, (idx, row) in enumerate(valid_rows):
                        calc = row.get("_calc", "")
                        name = clean_text(row.get("שם", ""))
                        st.markdown(
                            f"<div style='direction:rtl;text-align:right;font-size:0.82em;"
                            f"padding:4px 0;border-bottom:1px solid rgba(255,255,255,0.07);'>"
                            f"<b>{name}</b> — {calc}</div>",
                            unsafe_allow_html=True
                        )


            # ── בדיקת כיסוי הפסקות לכל העובדים המשובצים ──────────────
            if (
                "schedule_df_for_urgent" in dir()
                and schedule_df_for_urgent is not None
                and not schedule_df_for_urgent.empty
            ):
                urgent_names_in_table = set(
                    clean_text(r.get("שם", ""))
                    for r in urgent_flight_rows
                    if not clean_text(r.get("שם", "")).startswith("[debug]")
                )

                coverage_rows = []
                for cov_name in sorted(schedule_df_for_urgent["_urgent_emp_name"].dropna().unique()):
                    cov_name = _urgent_clean(cov_name)
                    if not cov_name or "חסר" in cov_name or "ראש צוות" in cov_name:
                        continue

                    cov_tasks = schedule_df_for_urgent[
                        schedule_df_for_urgent["_urgent_emp_name"] == cov_name
                    ]
                    # רק עובדים משובצים לטיסות
                    has_flight = any(
                        pd.notna(r.get("טיסה", "")) and str(r.get("טיסה", "")).strip()
                        for _, r in cov_tasks.iterrows()
                    )
                    if not has_flight:
                        continue

                    cov_shift = _find_shift(cov_name)
                    cov_s, cov_e = bp_parse_shift_minutes(cov_shift)

                    if bp_is_night_or_late_evening_shift(cov_shift):
                        # בדוק אם שובץ לטיסת לילה:
                        # טיסה שמתחילה מ-22:00 ואילך, או טיסה שמסתיימת עד 02:00
                        # (מכסה טיסות כמו LY 5 שמתחילות בחצות ומסתיימות ב-01:05)
                        NIGHT_FLIGHT_START = 22 * 60   # 22:00
                        NIGHT_FLIGHT_END_MAX = 2 * 60  # 02:00 — גבול סיום טיסת לילה
                        night_flight_end = None
                        for _, tr in cov_tasks.iterrows():
                            ts = _urgent_task_start_minutes(tr)
                            te = _urgent_task_end_minutes(tr)
                            if ts is None:
                                ts = te
                            if te is None:
                                te = ts
                            is_night_flight = (
                                (ts is not None and ts >= NIGHT_FLIGHT_START) or
                                (te is not None and te <= NIGHT_FLIGHT_END_MAX)
                            )
                            if is_night_flight and te is not None:
                                if night_flight_end is None or te > night_flight_end:
                                    night_flight_end = te
                        if night_flight_end is not None:
                            end_str = f"{night_flight_end // 60:02d}:{night_flight_end % 60:02d}"
                            status = f"🌙 הפסקה לאחר טיסת לילה ({end_str})"
                        else:
                            status = "🌙 הפסקה בסגירת הדלפקים"
                        color = "rgba(100,100,200,0.3)"
                    elif cov_name in urgent_names_in_table:
                        status = "🚨 בטבלה הדחופה"
                        color = "rgba(220,80,80,0.25)"
                    else:
                        # בדוק אם יש חלון הפסקה תקין
                        cov_start = cov_s if cov_s is not None else 0
                        cov_dur = ((cov_e + 1440 - cov_s) if (cov_s and cov_e and cov_e < cov_s) else ((cov_e - cov_s) if (cov_s and cov_e) else 7*60))
                        cov_break = 45 if cov_dur > 6*60 else 20
                        cov_min_desk = 90 if cov_dur > 7*60 else 60
                        cov_earliest = cov_start + cov_min_desk
                        cov_4h = cov_start + 4*60

                        first_ts = None
                        for _, tr in cov_tasks.iterrows():
                            ts = _urgent_task_start_minutes(tr)
                            if ts is not None and (first_ts is None or ts < first_ts):
                                first_ts = ts

                        if cov_dur > 6 * 60:
                            status = "✅ ייוצא להפסקה במהלך המשמרת"
                        else:
                            status = "✅ משמרת קצרה — רענון בלבד"
                        color = "rgba(80,180,80,0.15)"

                    first_flight = "—"
                    for _, tr in cov_tasks.iterrows():
                        fl = str(tr.get("טיסה", "")).strip()
                        if fl and fl != "nan":
                            dep = _urgent_task_end_minutes(tr)
                            if dep is None:
                                dep = _urgent_task_start_minutes(tr)
                            dep_str = f" {dep // 60:02d}:{dep % 60:02d}" if dep is not None else ""
                            first_flight = f"{fl}{dep_str}"
                            break

                    # מפתח מיון: לפי שעת התחלת משמרת, כשהיום מתחיל ב-02:00
                    SORT_PIVOT = 2 * 60  # 02:00
                    if cov_s is not None:
                        sort_key = (cov_s - SORT_PIVOT) % 1440
                    else:
                        sort_key = 9999

                    coverage_rows.append({
                        "שם": cov_name,
                        "משמרת": cov_shift or "—",
                        "טיסה": first_flight,
                        "סטטוס": status,
                        "_color": color,
                        "_sort_key": sort_key,
                    })

                coverage_rows.sort(key=lambda r: (r["_sort_key"], r["שם"]))

                with st.expander(f"📋 בדיקת כיסוי הפסקות — {len(coverage_rows)} עובדים משובצים לטיסות"):
                    # כותרות
                    cov_ratios = [1.6, 1.1, 0.9, 2.4]
                    cov_headers = ["שם", "משמרת", "טיסה", "סטטוס"]
                    C_HDR2 = "background:rgba(255,255,255,0.11);text-align:center;font-weight:700;font-size:0.82em;padding:8px 4px;border-radius:3px;"
                    hh = st.columns(cov_ratios)
                    for hc, ht in zip(hh, cov_headers):
                        hc.markdown(f"<div style='{C_HDR2}'>{ht}</div>", unsafe_allow_html=True)

                    for cr in coverage_rows:
                        bg = cr["_color"]
                        cc = st.columns(cov_ratios)
                        for col_w, key in zip(cc, ["שם", "משמרת", "טיסה", "סטטוס"]):
                            col_w.markdown(
                                f"<div style='background:{bg};text-align:center;font-size:0.82em;"
                                f"padding:5px 4px;border-bottom:1px solid rgba(255,255,255,0.06);'>"
                                f"{cr[key]}</div>",
                                unsafe_allow_html=True
                            )

            # ============================================================
            # 2. עובדים בהפסקה כרגע
            # ============================================================

            st.markdown("---")

            active_break_rows = []

            for emp_name, info in st.session_state.get("break_log", {}).items():
                if not _bp_is_active_break(info):
                    continue

                start_raw = _bp_break_start_raw(info)

                break_type = info.get("break_type", "הפסקה")
                elapsed_min = _bp_elapsed_minutes(start_raw)
                allowed_min = _bp_allowed_minutes(break_type)
                over_time = elapsed_min > allowed_min

                active_break_rows.append({
                    "שם": clean_text(emp_name),
                    "התחלת הפסקה": _bp_format_dt(start_raw),
                    "זמן שחלף": _bp_elapsed_text(start_raw),
                    "סוג הפסקה": break_type,
                    "_over_time": over_time,
                    "_allowed_min": allowed_min,
                    "_elapsed_min": elapsed_min,
                })

            active_break_df = pd.DataFrame(active_break_rows)

            active_count = len(active_break_rows)
            count_badge = f" <span style='font-size:0.75em;background:rgba(80,200,80,0.25);border-radius:10px;padding:2px 10px;'>{active_count}</span>" if active_count > 0 else " <span style='font-size:0.75em;color:rgba(255,255,255,0.4);'>(אין)</span>"
            st.markdown(f"<h3 style='text-align:right'>🟢 עובדים בהפסקה כרגע{count_badge}</h3>", unsafe_allow_html=True)

            if active_break_df.empty:
                st.info("אין עובדים בהפסקה כרגע.")
            else:
                AB_RATIOS = [1.8, 1.1, 1.1, 1.4, 1.6]
                AB_HEADERS = ["שם", "התחלה", "זמן שחלף", "סוג", "פעולה"]
                C_HDR = "background:rgba(255,255,255,0.11);text-align:center;font-weight:700;font-size:0.82em;padding:8px 4px;border-radius:3px;"

                hcols = st.columns(AB_RATIOS)
                for hc, ht in zip(hcols, AB_HEADERS):
                    hc.markdown(f"<div style='{C_HDR}'>{ht}</div>", unsafe_allow_html=True)

                for idx, row in active_break_df.iterrows():
                    emp_name = clean_text(row.get("שם", ""))
                    over_time = row.get("_over_time", False)
                    allowed_min = row.get("_allowed_min", 45)
                    elapsed_min = row.get("_elapsed_min", 0)

                    if over_time:
                        bg = "rgba(220,60,60,0.18)"
                    else:
                        bg = "rgba(255,255,255,0.025)" if idx % 2 == 0 else "rgba(255,255,255,0.06)"
                    C_CELL = f"background:{bg};text-align:center;font-size:0.82em;padding:5px 4px;border-bottom:1px solid rgba(255,255,255,0.06);"

                    name_icon = "🔴" if over_time else "🟢"
                    over_label = f" <span style='color:#ff6b6b;font-size:0.8em;'>(חרג ב-{elapsed_min - allowed_min} ד׳)</span>" if over_time else ""
                    elapsed_text = row.get("זמן שחלף", "")

                    rcols = st.columns(AB_RATIOS)
                    rcols[0].markdown(f"<div style='{C_CELL}'>{name_icon} {emp_name}</div>", unsafe_allow_html=True)
                    rcols[1].markdown(f"<div style='{C_CELL}'>{row.get('התחלת הפסקה', '')}</div>", unsafe_allow_html=True)
                    rcols[2].markdown(f"<div style='{C_CELL}'>{elapsed_text}{over_label}</div>", unsafe_allow_html=True)
                    rcols[3].markdown(f"<div style='{C_CELL}'>{row.get('סוג הפסקה', '')}</div>", unsafe_allow_html=True)

                    with rcols[4]:
                        b1, b2 = st.columns(2)
                        with b1:
                            if st.button("סיים", key=f"active_break_end_{idx}_{emp_name}", use_container_width=True):
                                _bp_end_break(emp_name)
                                st.rerun()
                        with b2:
                            if st.button("איפוס", key=f"active_break_reset_{idx}_{emp_name}", use_container_width=True):
                                _bp_reset_break(emp_name)
                                st.rerun()

            # ============================================================
            # בניית טבלת תכנון הפסקות להמשך המשמרת
            # ============================================================

            planned_break_rows = []


            def _bp_time_to_min(time_text):
                try:
                    text = str(time_text).strip()
                    if ":" not in text:
                        return None
                    h, m = text.split(":")[:2]
                    return int(h) * 60 + int(m)
                except Exception:
                    return None


            def _bp_shift_to_minutes(shift_label):
                try:
                    text = str(shift_label).strip()
                    if "-" not in text:
                        return None, None
                    start_txt, end_txt = text.split("-", 1)
                    return _bp_time_to_min(start_txt), _bp_time_to_min(end_txt)
                except Exception:
                    return None, None


            if (
                unassigned_df is not None
                and not unassigned_df.empty
                and "שם" in unassigned_df.columns
                and "משמרת" in unassigned_df.columns
            ):
                for _, emp in unassigned_df.iterrows():
                    emp_name = clean_text(emp.get("שם", ""))
                    shift_label = clean_text(emp.get("משמרת", ""))

                    if not emp_name or not shift_label:
                        continue

                    if emp_name in active_names:
                        continue

                    s_min, e_min = _bp_shift_to_minutes(shift_label)

                    if s_min is None or e_min is None:
                        continue

                    # 02:00-08:30/09:30
                    if 1 * 60 + 45 <= s_min <= 2 * 60 + 30 and 8 * 60 <= e_min <= 9 * 60 + 45:
                        planned_break_rows.append({
                            "שם": emp_name,
                            "משמרת": shift_label,
                            "חלון מומלץ": "03:30-04:15",
                            "סוג": "הפסקה יזומה",
                            "טיסה קשורה": "",
                            "הערה": "כאשר משמרות 03:30 מגיעות, העובד יוצא להפסקה וחוזר לדלפקים.",
                        })

                    # 03:30-11:00
                    elif 3 * 60 <= s_min <= 4 * 60 + 15 and 10 * 60 <= e_min <= 11 * 60 + 30:
                        planned_break_rows.append({
                            "שם": emp_name,
                            "משמרת": shift_label,
                            "חלון מומלץ": "06:00-07:00",
                            "סוג": "הפסקת בוקר מתוכננת",
                            "טיסה קשורה": "",
                            "הערה": "אחרי ההפסקה חזרה לדלפקים, ובהמשך ירידה לטיסה לפי צורך.",
                        })

                    # 11:30/12:30-21:00
                    elif 10 * 60 + 30 <= s_min <= 12 * 60 + 30 and 20 * 60 <= e_min <= 22 * 60:
                        planned_break_rows.append({
                            "שם": emp_name,
                            "משמרת": shift_label,
                            "חלון מומלץ": "16:00-17:00",
                            "סוג": "הפסקה יזומה",
                            "טיסה קשורה": "",
                            "הערה": "רצוי בין טיסות. אם העובד סוגר חור בשיבוץ, אפשר לדחות לפי צורך.",
                        })

                    # ערב / ערב־לילה
                    elif 13 * 60 + 30 <= s_min <= 17 * 60 and (20 * 60 <= e_min <= 22 * 60 or e_min < s_min):
                        planned_break_rows.append({
                            "שם": emp_name,
                            "משמרת": shift_label,
                            "חלון מומלץ": "17:00-19:00",
                            "סוג": "הפסקה יזומה",
                            "טיסה קשורה": "",
                            "הערה": "תכנון תפעולי, בין טיסות או אחרי משימת אולם. לא קריאת חירום.",
                        })


            # ============================================================
            # 3. הוצאה להפסקה - עובדים לא משובצים (מאוחד)
            # ============================================================

            st.markdown("---")
            st.markdown("<h3 style='text-align:right'>הוצאה להפסקה — עובדים לא משובצים</h3>", unsafe_allow_html=True)

            # שמות מהטבלה הדחופה
            urgent_names_set = set()
            if not urgent_flight_break_df.empty and "שם" in urgent_flight_break_df.columns:
                urgent_names_set = set(
                    urgent_flight_break_df["שם"].dropna().astype(str).map(clean_text)
                )

            # חלון מומלץ לפי סוג משמרת
            def _recommended_window(s_min, e_min):
                if s_min is None or e_min is None:
                    return "—"
                dur = (e_min + 1440 - s_min) if e_min < s_min else (e_min - s_min)
                # משמרת לילה: מתחילה מ-20:00, חוצה חצות, מסתיימת אחרי 04:00
                if s_min >= 20*60 and e_min < s_min and e_min >= 4*60:
                    return "בסגירת דלפקים"
                # 02:00 משמרת
                if 1*60+45 <= s_min <= 2*60+30 and 8*60 <= e_min <= 9*60+45:
                    return "03:30–04:15"
                # 03:30 משמרת
                if 3*60 <= s_min <= 4*60+15 and 10*60 <= e_min <= 11*60+30:
                    return "06:00–07:00"
                # צהרים / ערב מוקדם
                if 10*60+30 <= s_min <= 12*60+30 and 20*60 <= e_min <= 22*60:
                    return "16:00–17:00"
                # ערב / ערב-לילה
                if 13*60+30 <= s_min <= 17*60 and (20*60 <= e_min or e_min < s_min):
                    return "17:00–19:00"
                # כלל: אמצע המשמרת ±30 דקות
                mid = (s_min + dur // 2) % 1440
                w_start = (mid - 30) % 1440
                w_end   = (mid + 30) % 1440
                return f"{w_start//60:02d}:{w_start%60:02d}–{w_end//60:02d}:{w_end%60:02d}"

            combined_break_rows = []

            if (
                unassigned_df is not None
                and not unassigned_df.empty
                and "שם" in unassigned_df.columns
            ):
                for _, emp in unassigned_df.iterrows():
                    emp_name = clean_text(emp.get("שם", ""))
                    shift_label = clean_text(emp.get("משמרת", ""))

                    if not emp_name or not shift_label:
                        continue
                    if emp_name in active_names:
                        continue
                    if emp_name in urgent_names_set:
                        continue

                    s_min, e_min = _bp_shift_to_minutes(shift_label)
                    window = _recommended_window(s_min, e_min)

                    combined_break_rows.append({
                        "שם": emp_name,
                        "משמרת": shift_label,
                        "חלון מומלץ": window,
                    })

            if not combined_break_rows:
                st.info("אין כרגע עובדים לא משובצים להוצאה להפסקה.")
            else:
                CB_RATIOS = [1.8, 1.2, 1.4, 2.6]
                CB_HEADERS = ["שם", "משמרת", "חלון מומלץ", "פעולה"]
                C_HDR3 = "background:rgba(255,255,255,0.11);text-align:center;font-weight:700;font-size:0.82em;padding:8px 4px;border-radius:3px;"

                # שורת בקרה: חיפוש + כפתור צמצום
                ctrl_col1, ctrl_col2 = st.columns([3, 1])
                with ctrl_col1:
                    cb_search = st.text_input(
                        "חיפוש עובד",
                        key="cb_search_input",
                        placeholder="הקלד שם לסינון...",
                        label_visibility="collapsed",
                    )
                with ctrl_col2:
                    cb_collapsed = st.toggle("צמצם טבלה", key="cb_collapsed_toggle", value=False)

                # סינון לפי חיפוש
                filtered_rows = combined_break_rows
                if cb_search.strip():
                    filtered_rows = [r for r in combined_break_rows if cb_search.strip() in r["שם"]]

                total = len(combined_break_rows)
                display_rows = filtered_rows[:10] if cb_collapsed else filtered_rows
                shown = len(display_rows)
                st.caption(f"מציג {shown} מתוך {total} עובדים")

                if True:
                    hcols = st.columns(CB_RATIOS)
                    for hc, ht in zip(hcols, CB_HEADERS):
                        hc.markdown(f"<div style='{C_HDR3}'>{ht}</div>", unsafe_allow_html=True)

                    if unassigned_inner_view == INNER_BREAKS:
                        for btn_i, row in enumerate(display_rows):
                            emp_name = row["שם"]
                            bg = "rgba(255,255,255,0.025)" if btn_i % 2 == 0 else "rgba(255,255,255,0.06)"
                            C_CELL3 = f"background:{bg};text-align:center;font-size:0.82em;padding:5px 4px;border-bottom:1px solid rgba(255,255,255,0.06);"

                            rcols = st.columns(CB_RATIOS)
                            rcols[0].markdown(f"<div style='{C_CELL3}'>{emp_name}</div>", unsafe_allow_html=True)
                            rcols[1].markdown(f"<div style='{C_CELL3}'>{row['משמרת']}</div>", unsafe_allow_html=True)
                            rcols[2].markdown(f"<div style='{C_CELL3}'>{row['חלון מומלץ']}</div>", unsafe_allow_html=True)

                            with rcols[3]:
                                safe_key = str(emp_name).replace(" ", "_").replace("/", "_").replace("-", "_")
                                _act_col1, _act_col2 = st.columns(2)
                                with _act_col1:
                                    if st.button("הוצא להפסקה", key=f"combined_break_{btn_i}_{safe_key}", use_container_width=True):
                                        _bp_start_break(emp_name, break_type="יזומה", source="הוצאה יזומה")
                                        st.rerun()
                                with _act_col2:
                                    if st.button("הוצא לרענון", key=f"combined_refresh_{btn_i}_{safe_key}", use_container_width=True):
                                        _bp_start_break(emp_name, break_type="רענון יזום", source="רענון יזום")
                                        st.rerun()
    if active_main_tab == TAB_SCHEDULE:
        excel_data = to_excel_bytes(output_df, workload_df, labeled_df, continuity_df)
        # Respect the "תצוגת טרמינל" filter (הכל/טרמינל 3/טרמינל 1) shown on
        # the schedule tab — the departures report should only include the
        # currently-viewed terminal's flights, same classification (gate,
        # falling back to שלוחה) used for that filter and everywhere else in
        # the app (user rule 2026-07-28: report previously always included
        # both terminals regardless of the on-screen filter).
        _departures_flights_df = flights_editor_df
        _term_view_for_report = st.session_state.get("schedule_term_filter", "הכל")
        if "גייט" in flights_editor_df.columns and _term_view_for_report != "הכל":
            _dep_gate = flights_editor_df.get("גייט", pd.Series("", index=flights_editor_df.index)).astype(str)
            _dep_shloucha = flights_editor_df.get("שלוחה", pd.Series("", index=flights_editor_df.index)).astype(str)
            _dep_is_t1 = pd.Series(
                [
                    get_terminal(clean_text(_g) or clean_text(_s)) == "1"
                    for _g, _s in zip(_dep_gate, _dep_shloucha)
                ],
                index=flights_editor_df.index,
            )
            if _term_view_for_report == "טרמינל 1":
                _departures_flights_df = flights_editor_df[_dep_is_t1]
            elif _term_view_for_report == "טרמינל 3":
                _departures_flights_df = flights_editor_df[~_dep_is_t1]
        _terminal_view_arg = (
            "t1" if _term_view_for_report == "טרמינל 1"
            else "t3" if _term_view_for_report == "טרמינל 3"
            else "both"
        )
        # כשעומדים על סידור של משמרת מסוימת (נייט/דיי/אפטר), הדוח מכיל רק את
        # הטיסות של אותה משמרת ושם הקובץ נושא את שמה.
        _rep_seg_key = st.session_state.get("_view_segment") or st.session_state.get("_segment_current")
        _rep_seg = st.session_state.get("_segment_snapshots", {}).get(_rep_seg_key)
        _rep_suffix = ""
        if _rep_seg and _rep_seg.get("flights"):
            _rep_wanted = set(_rep_seg["flights"])
            _departures_flights_df = _departures_flights_df[
                _departures_flights_df["טיסה"].apply(
                    lambda v: clean_text(str(v)) in _rep_wanted
                )
            ]
            _rep_suffix = f' - סידור {_rep_seg["label"]}'
        departures_excel_data = to_departures_report_excel_bytes(
            _departures_flights_df,
            labeled_df,
            live_employees,
            terminal_view=_terminal_view_arg,
        )
        st.download_button(
            f"⬇️ הורדת דוח שיבוץ טיסות - המראות{_rep_suffix}",
            data=departures_excel_data,
            file_name=f"דוח שיבוץ טיסות - המראות{_rep_suffix}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        if is_admin():
            st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
            if st.button(
                "📣 שגר סידור", use_container_width=True, type="primary",
                help="מפרסם את הסידור הנוכחי — מרגע זה העובדים יראו אותו במסך האישי שלהם",
            ):
                _now = app_now()
                if publish_state.publish_schedule(live_schedule, st.session_state.get("_build_id"), _now):
                    st.toast("✅ הסידור שוגר לעובדים.", icon="📣")
                    st.rerun()
                else:
                    st.error("שגיאה בשיגור הסידור.")
        st.stop()
        st.stop()