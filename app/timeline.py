"""Timeline ("ציר זמן") view of a built schedule: one row per worker, one bar
per task on a shared time axis. Pure HTML/CSS (theme tokens), no iframe.

The axis runs right-to-left, like the rest of the UI. Night schedules cross
midnight, so times are unwrapped onto one continuous axis.
"""
from html import escape

# role label (תפקיד בסיס) -> (css colour token, short tag shown inside the bar)
ROLE_STYLE = {
    "ראש צוות": ("--r-tl", 'ר"צ'),
    "דייל": ("--r-agent", "דייל"),
    "דיילת": ("--r-agent", "דייל"),
    "מפקח TSA": ("--r-insp", "מפקח"),
    "שומר TSA": ("--r-guard", "שומר"),
    "מתאם תורים": ("--r-queue", "מתאם"),
    "טרייני רצ": ("--r-train", "טרייני"),
    "טרייני ר״צ": ("--r-train", "טרייני"),
}
_DEFAULT_STYLE = ("--r-agent", "")


def _to_min(v):
    try:
        h, m = str(v).strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _fmt(m):
    m %= 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def role_options(df):
    """Distinct roles present in the (assigned) schedule, in a stable order."""
    if df is None or df.empty or "תפקיד בסיס" not in df.columns:
        return []
    seen = []
    for r in df["תפקיד בסיס"].astype(str).str.strip():
        if r and r not in seen:
            seen.append(r)
    return seen


def shift_bands(employees, crosses):
    """{worker: (start, end)} from the roster's shift columns, unwrapped like tasks."""
    out = {}
    if employees is None or getattr(employees, "empty", True):
        return out
    if "שם" not in employees.columns or "תחילת משמרת" not in employees.columns or "סוף משמרת" not in employees.columns:
        return out
    for _, r in employees.iterrows():
        n = str(r.get("שם", "")).strip()
        s, e = _to_min(r.get("תחילת משמרת")), _to_min(r.get("סוף משמרת"))
        if not n or s is None or e is None:
            continue
        wraps = e <= s  # e.g. 22:00-06:00
        if crosses and s < 12 * 60:
            s += 1440
            e += 1440
        if e <= s:
            e += 1440
        if wraps and not crosses:
            s = 0  # only the after-midnight part is on this axis
        out[n] = (s, e, str(r.get("תחילת משמרת", "")).strip(), str(r.get("סוף משמרת", "")).strip())
    return out


def build_tasks(df):
    """Assigned rows as dicts with unwrapped minute values."""
    rows = []
    if df is None or df.empty:
        return rows
    for _, r in df.iterrows():
        worker = str(r.get("עובד", "")).strip()
        if not worker or "❌" in worker:
            continue
        s, e = _to_min(r.get("התחלה")), _to_min(r.get("סיום"))
        if s is None or e is None:
            continue
        rows.append({
            "worker": worker,
            "role": str(r.get("תפקיד בסיס", "")).strip(),
            "flight": str(r.get("טיסה", "")).strip(),
            "dest": str(r.get("יעד", "")).strip(),
            "gate": str(r.get("גייט", "")).strip(),
            "cont": str(r.get("המשך אזורי", "")),
            "s": s, "e": e,
        })
    if not rows:
        return rows
    # overnight schedule: evening starts exist together with small-hour starts
    crosses = any(t["s"] >= 17 * 60 for t in rows) and any(t["s"] < 12 * 60 for t in rows)
    for t in rows:
        if crosses and t["s"] < 12 * 60:
            t["s"] += 1440
            t["e"] += 1440
        if t["e"] < t["s"]:
            t["e"] += 1440
    return rows


def axis_bounds(df):
    """(lo, hi) minutes of the automatic axis (whole hours) for a schedule, or None."""
    tasks = build_tasks(df)
    if not tasks:
        return None
    return (min(t["s"] for t in tasks) // 60 * 60, -(-max(t["e"] for t in tasks) // 60) * 60)


def timeline_html(df, role=None, query="", now_min=None, employees=None, sort="start", gap_min=0, view=None):
    """The whole timeline as one HTML string ('' when there is nothing to show)."""
    tasks = build_tasks(df)
    if role:
        tasks = [t for t in tasks if t["role"] == role]
    q = (query or "").strip()
    if q:
        tasks = [t for t in tasks if q in t["worker"]]
    if not tasks:
        return ""

    bands = shift_bands(employees, any(t["s"] >= 1440 for t in tasks))
    # the axis follows the TASKS; shift bands are clipped to it (a full 22:00-06:00
    # shift would otherwise stretch the axis over the whole night and shrink every bar)
    lo = min(t["s"] for t in tasks) // 60 * 60
    hi = -(-max(t["e"] for t in tasks) // 60) * 60
    if view:  # zoom: show only this window of the axis
        lo, hi = view
        if hi - lo < 60:
            hi = lo + 60
    span = max(hi - lo, 60)

    def pos(m):  # % from the RIGHT edge (time flows right → left)
        return (m - lo) / span * 100.0

    # axis: one tick per hour
    ticks = "".join(
        f'<div class="tl-tick" style="right:{pos(m):.3f}%"><span>{_fmt(m)}</span></div>'
        for m in range(lo, hi + 1, 60)
    )
    lines = "".join(
        f'<i class="tl-grid" style="right:{pos(m):.3f}%"></i>' for m in range(lo, hi + 1, 60)
    )
    now_html = ""
    if now_min is not None:
        for cand in (now_min, now_min + 1440):
            if lo <= cand <= hi:
                now_html = f'<i class="tl-now" style="right:{pos(cand):.3f}%" title="עכשיו {_fmt(cand)}"></i>'
                break

    # one row per worker, ordered by first task
    by_worker = {}
    for t in tasks:
        by_worker.setdefault(t["worker"], []).append(t)
    def busy(w):
        return sum(x["e"] - x["s"] for x in by_worker[w])
    if sort == "name":
        order = sorted(by_worker)
    elif sort == "load":
        order = sorted(by_worker, key=lambda w: (-busy(w), w))
    elif sort == "role":
        def _main_role(w):
            rs = [x["role"] for x in by_worker[w]]
            return max(set(rs), key=rs.count)
        _rank = {r: i for i, r in enumerate(ROLE_STYLE)}
        order = sorted(by_worker, key=lambda w: (_rank.get(_main_role(w), 99), _main_role(w),
                                                 min(x["s"] for x in by_worker[w]), w))
    else:
        order = sorted(by_worker, key=lambda w: (min(x["s"] for x in by_worker[w]), w))
    if view:  # drop workers with nothing in the zoom window
        order = [w for w in order if any(x["e"] > lo and x["s"] < hi for x in by_worker[w])]
        if not order:
            return ""

    rows_html = []
    n_gap_workers = set()
    last_group = None
    for w in order:
        if sort == "role":
            grp = _main_role(w)
            if grp != last_group:
                last_group = grp
                cnt = sum(1 for x in order if _main_role(x) == grp)
                rows_html.append(f'<div class="tl-group" dir="rtl">{escape(grp or "ללא תפקיד")} · {cnt}</div>')
        bars = []
        for t in sorted(by_worker[w], key=lambda x: x["s"]):
            if t["e"] <= lo or t["s"] >= hi:
                continue
            cs, ce = max(t["s"], lo), min(t["e"], hi)
            token, tag = ROLE_STYLE.get(t["role"], _DEFAULT_STYLE)
            width = max(pos(ce) - pos(cs), 0.4)
            # pos() grows leftwards, so the bar's right edge is its START
            right = pos(cs)
            label = escape(t["flight"])
            if tag and (t["e"] - t["s"]) >= 40:
                label += f' <small>{escape(tag)}</small>'
            tip = escape(
                f'{t["flight"]} {t["dest"]} · {_fmt(t["s"])}–{_fmt(t["e"])} · {t["role"]}'
                + (f' · גייט {t["gate"]}' if t["gate"] else "")
            )
            bars.append(
                f'<div class="tl-bar" data-fl="{"".join(ch for ch in t["flight"] if ch.isalnum())}" '
                f'style="right:{right:.3f}%;width:{width:.3f}%;'
                f'background:rgba(var({token}),.22);border-color:rgb(var({token}));" title="{tip}">'
                f'<span>{label}</span></div>'
            )
        band = ""
        if w in bands and bands[w][1] > lo and bands[w][0] < hi:
            bs, be = max(bands[w][0], lo), min(bands[w][1], hi)
            band = (f'<i class="tl-shift" style="right:{pos(bs):.3f}%;width:{pos(be) - pos(bs):.3f}%" '
                    f'title="משמרת {_fmt(bands[w][0])}–{_fmt(bands[w][1])}"></i>')
        gaps_html = ""
        if gap_min:
            # only the time BETWEEN two tasks: before the first / after the last task the
            # worker is simply not (yet / any more) part of the built schedule
            spans = []
            cur = None
            prev = None
            for x in sorted(by_worker[w], key=lambda t: t["s"]):
                if cur is not None and x["s"] - cur >= gap_min:
                    spans.append((cur, x["s"], prev))
                cur = x["e"] if cur is None else max(cur, x["e"])
                prev = x
            for gs, ge, before in spans:
                if ge <= lo or gs >= hi:
                    continue
                n_gap_workers.add(w)
                brk = _break_in_gap(before, ge - gs, bands.get(w))
                lbl = f"{(ge - gs) // 60}:{(ge - gs) % 60:02d}" if (ge - gs) >= 45 else ""
                if brk:
                    lbl = ("☕ " if brk[0] == "הפסקה" else "↻ ") + lbl
                tip = f"פנוי {ge - gs} דק׳ · {_fmt(gs)}–{_fmt(ge)}"
                if brk:
                    tip += f" · כולל {brk[0]} {brk[1]} דק׳"
                gaps_html += (
                    f'<div class="tl-gap{" tl-gap-brk" if brk else ""}" style="right:{pos(max(gs, lo)):.3f}%;width:{pos(min(ge, hi)) - pos(max(gs, lo)):.3f}%" '
                    f'title="{tip}"><span>{lbl}</span>'
                    + (f'<b class="tl-brk" style="width:{brk[1] / (ge - gs) * 100:.1f}%" title="{brk[0]} {brk[1]} דק׳ (מיד אחרי המשימה)"></b>' if brk else "")
                    + '</div>'
                )
        hrs = busy(w) / 60
        rows_html.append(
            f'<div class="tl-row"><div class="tl-name" title="{escape(w)} · {len(by_worker[w])} משימות · {hrs:.1f} שעות">'
            f'{escape(w)}<small>{len(by_worker[w])}</small></div>'
            f'<div class="tl-track">{band}{gaps_html}{lines}{now_html}{"".join(bars)}</div></div>'
        )

    gap_note = (
        f'<span class="tl-key"><i class="tl-gap-key"></i>חלון פנוי ({gap_min}+ דק׳) · {len(n_gap_workers)} עובדים</span>'
        '<span class="tl-key">☕ = כולל הפסקה · ↻ = כולל רענון</span>'
        if gap_min else ""
    )
    legend = "".join(
        f'<span class="tl-key"><i style="background:rgba(var({tok}),.22);border-color:rgb(var({tok}))"></i>{escape(role_name)}</span>'
        for role_name, (tok, _) in _legend_roles(tasks)
    )
    return (
        '<div class="tl" dir="rtl">'
        f'<div class="tl-legend">{legend}{gap_note}<span class="tl-key"><i class="tl-now-key"></i>עכשיו</span></div>'
        '<div class="tl-scroll">'
        f'<div class="tl-row tl-head"><div class="tl-name"></div><div class="tl-axis">{ticks}</div></div>'
        f'{"".join(rows_html)}'
        '</div></div>'
    )


def _break_in_gap(before, gap_len, band):
    """(kind, minutes) of the break/refresh the scheduler put right after the task
    `before` (its 'המשך אזורי' mentions it), when it fits in this gap; else None."""
    cont = (before or {}).get("cont", "")
    kind = "הפסקה" if "הפסקה" in cont else "רענון" if "רענון" in cont else ""
    if not kind:
        return None
    emp = {}
    if band and len(band) >= 4:
        emp = {"תחילת משמרת": band[2], "סוף משמרת": band[3]}
    try:
        from utils.helpers import required_break, required_refresh
        dur = (required_break(emp) if kind == "הפסקה" else required_refresh(emp)) if emp else 0
    except Exception:
        dur = 0
    dur = dur or (45 if kind == "הפסקה" else 20)
    return kind, min(dur, gap_len)


def _legend_roles(tasks):
    seen = []
    for t in tasks:
        if t["role"] and t["role"] not in [s[0] for s in seen]:
            seen.append((t["role"], ROLE_STYLE.get(t["role"], _DEFAULT_STYLE)))
    return seen
