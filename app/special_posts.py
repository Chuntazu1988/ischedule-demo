"""עמדות מיוחדות — manual staffing of the check-in-area posts (outside the gates).

Pure logic, no Streamlit: the tab in streamlit_app.py keeps the state dict
(`st.session_state["special_posts"]`) and calls these helpers. State shape:

    {"posts": {post_key: [{"name": str, "start": "YYYY-MM-DDTHH:MM:SS"}, ...]},
     "log":   [{"post": key, "name": str, "start": iso, "end": iso}, ...]}

Assignment is always manual (the shift manager decides); the app only offers
the eligible workers, orders them by the post's preference, shows the start
time and a running timer, and lets the manager add / replace / leave empty /
finish.
"""

from datetime import datetime

# pref: "restricted" = prefer ילדי עובדים / פורשים (shown first in the picker),
#       "qualified" = דייל מוסמך only — no trainee / ילד עובדים / פורש.
# need: how many are normally required at once (None = varies with the load).
POSTS = [
    {"key": "kiosks", "name": "קיוסקים", "icon": "🖥️", "need": None, "pref": "restricted",
     "desc": "עזרה לנוסעים בצ'ק אין עצמי · מגזר D"},
    {"key": "documents", "name": "מסמכים", "icon": "📄", "need": 1, "pref": "restricted",
     "desc": "שומר סף לתור המקוצר: תג מזוודה + כרטיס עלייה"},
    {"key": "security", "name": "ביטחון", "icon": "🛡️", "need": 1, "pref": "restricted",
     "desc": "כניסה לתור הרגיל — עידוד לתייג עצמית ולעבור לדרופ אוף"},
    {"key": "dropoff_entry", "name": "כניסה ל-DROP OFF", "icon": "🧳", "need": 1, "pref": "restricted",
     "desc": "וידוא מזוודה מתויגת מהקיוסקים לפני התור המקוצר"},
    {"key": "dropoff_funnel", "name": "חבלול DROP OFF", "icon": "↪️", "need": 1, "pref": "restricted",
     "desc": "סוף התור — מנתב לדלפק הפנוי הבא"},
    {"key": "funnel_d", "name": "חבלול D", "icon": "↪️", "need": 1, "pref": "restricted",
     "desc": "סוף התור הרגיל במגזר D"},
    {"key": "funnel_c", "name": "חבלול C", "icon": "↪️", "need": 1, "pref": "restricted",
     "desc": "סוף התור הרגיל במגזר C"},
    {"key": "vip", "name": "אקווריום VIP", "icon": "⭐", "need": 1, "pref": "qualified",
     "desc": "קומה G · לווי VIP של חברה חיצונית — דייל מוסמך בלבד"},
]
POSTS_BY_KEY = {p["key"]: p for p in POSTS}

_RESTRICTED_COLS = ("ילד עובדים", "ילדי עובדים", "פורשים")
_TRAINEE_COLS = ("דייל בטרייני", "טרייני רצ")


def _yes(row, col):
    return str(row.get(col, "")).strip() == "כן"


def worker_kind(row):
    """'restricted' (ילד עובדים / פורש), 'trainee', or 'regular'."""
    if any(_yes(row, c) for c in _RESTRICTED_COLS):
        return "restricted"
    if any(_yes(row, c) for c in _TRAINEE_COLS):
        return "trainee"
    return "regular"


def kind_label(row):
    if _yes(row, "פורשים"):
        return "פורש/ת"
    if _yes(row, "ילד עובדים") or _yes(row, "ילדי עובדים"):
        return "ילד/ת עובדים"
    if any(_yes(row, c) for c in _TRAINEE_COLS):
        return "טרייני"
    return ""


def empty_state():
    return {"posts": {p["key"]: [] for p in POSTS}, "log": []}


def ensure_state(state):
    """Return a valid state dict (fills in missing posts after a restore)."""
    if not isinstance(state, dict):
        return empty_state()
    state.setdefault("posts", {})
    state.setdefault("log", [])
    for p in POSTS:
        state["posts"].setdefault(p["key"], [])
    return state


def _to_min(text):
    try:
        h, m = str(text).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None


def on_shift(row, now):
    """True when `now` falls inside the worker's shift (handles overnight).
    A worker with no readable shift times is not excluded."""
    s, e = _to_min(row.get("תחילת משמרת")), _to_min(row.get("סוף משמרת"))
    if s is None or e is None:
        return True
    cur = now.hour * 60 + now.minute
    return s <= cur <= e if s <= e else (cur >= s or cur <= e)


def is_eligible(row, post_key):
    """VIP needs a fully qualified דייל; every other post accepts anyone."""
    if POSTS_BY_KEY[post_key]["pref"] == "qualified":
        return worker_kind(row) == "regular" and _yes(row, "דייל")
    return True


def candidates(employees_df, post_key, now, exclude=(), only_on_shift=True):
    """Ordered list of (name, label) the manager may pick for this post.
    Restricted-preference posts list ילדי עובדים/פורשים first."""
    if employees_df is None or employees_df.empty:
        return []
    df = employees_df
    if "in_daily_excel" in df.columns:
        df = df[df["in_daily_excel"].astype(str).isin(["1", "1.0", "True"])]
    taken = set(exclude)
    prefer_restricted = POSTS_BY_KEY[post_key]["pref"] == "restricted"
    rows = []
    for _, r in df.iterrows():
        name = str(r.get("שם", "")).strip()
        if not name or name in taken or r.get("חולה") is True:
            continue
        if only_on_shift and not on_shift(r, now):
            continue
        if not is_eligible(r, post_key):
            continue
        kind = worker_kind(r)
        rank = (0 if kind == "restricted" else 1) if prefer_restricted else 0
        tag = kind_label(r)
        label = f"{name} · {tag}" if tag else name
        rows.append((rank, name, label))
    rows.sort(key=lambda x: (x[0], x[1]))
    return [(n, lbl) for _, n, lbl in rows]


def occupied_names(state):
    return {a["name"] for lst in state["posts"].values() for a in lst}


def assign(state, post_key, name, now):
    state["posts"][post_key].append({"name": name, "start": now.strftime("%Y-%m-%dT%H:%M:%S")})


def finish(state, post_key, name, now):
    lst = state["posts"][post_key]
    for a in list(lst):
        if a["name"] == name:
            lst.remove(a)
            state["log"].append({"post": post_key, "name": name, "start": a["start"],
                                 "end": now.strftime("%Y-%m-%dT%H:%M:%S")})


def replace(state, post_key, old_name, new_name, now):
    finish(state, post_key, old_name, now)
    assign(state, post_key, new_name, now)


def elapsed_seconds(start_iso, now):
    try:
        return max(0, int((now - datetime.fromisoformat(start_iso)).total_seconds()))
    except (ValueError, TypeError):
        return 0


def fmt_clock(seconds):
    """M:SS, or H:MM:SS from one hour on — same format the live JS timer uses."""
    h, rem = divmod(int(seconds), 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


# Ticks every `.sp-live[data-el]` in the parent page once a second. data-el is the
# server-side elapsed seconds at render time; the script keeps its own baseline per
# element (reset when data-el changes after a rerun), so the browser clock/timezone
# never matters.
LIVE_TIMER_HTML = """<script>
(function () {
  const P = window.parent, D = P.document;
  if (P.__spTimer) clearInterval(P.__spTimer);
  const base = new Map();
  const fmt = (t) => {
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    const p = (n) => String(n).padStart(2, '0');
    return h ? h + ':' + p(m) + ':' + p(s) : p(m) + ':' + p(s);
  };
  const tick = () => {
    D.querySelectorAll('.sp-live[data-el]').forEach((el) => {
      const sig = el.dataset.el;
      let b = base.get(el);
      if (!b || b.sig !== sig) { b = { sig: sig, t0: Date.now() }; base.set(el, b); }
      const t = Number(sig) + Math.floor((Date.now() - b.t0) / 1000);
      el.textContent = fmt(t);
    });
  };
  P.__spTimer = setInterval(tick, 1000);
})();
</script>"""


def elapsed_minutes(start_iso, now):
    try:
        return max(0, int((now - datetime.fromisoformat(start_iso)).total_seconds() // 60))
    except (ValueError, TypeError):
        return 0


def fmt_elapsed(minutes):
    h, m = divmod(int(minutes), 60)
    return f"{h}:{m:02d} ש׳" if h else f"{m} דק׳"
