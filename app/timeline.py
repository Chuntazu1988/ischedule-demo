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
    for idx, r in df.iterrows():
        worker = str(r.get("עובד", "")).strip()
        if not worker or "❌" in worker:
            continue
        s, e = _to_min(r.get("התחלה")), _to_min(r.get("סיום"))
        if s is None or e is None:
            continue
        rows.append({
            "idx": idx,
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


def crosses_midnight(df):
    """Whether this schedule's tasks were unwrapped onto an overnight axis —
    callers that independently unwrap other rows (e.g. unfilled slots) need
    the same flag build_tasks used, or their minutes won't line up."""
    tasks = build_tasks(df)
    return any(t["s"] >= 1440 for t in tasks)


def unfilled_in_window(df, crosses, gs, ge):
    """Open (❌) slots in `df` fully contained in the unwrapped window [gs, ge] —
    i.e. tasks a worker free for exactly that window could be assigned to."""
    out = []
    if df is None or df.empty:
        return out
    for idx, r in df.iterrows():
        worker = str(r.get("עובד", "")).strip()
        if "❌" not in worker:
            continue
        s, e = _to_min(r.get("התחלה")), _to_min(r.get("סיום"))
        if s is None or e is None:
            continue
        if crosses and s < 12 * 60:
            s += 1440
            e += 1440
        if e <= s:
            e += 1440
        if gs <= s and e <= ge:
            out.append({
                "idx": idx,
                "role": str(r.get("תפקיד בסיס", "")).strip(),
                "flight": str(r.get("טיסה", "")).strip(),
                "dest": str(r.get("יעד", "")).strip(),
                "s": s, "e": e,
            })
    out.sort(key=lambda t: t["s"])
    return out


def axis_bounds(df):
    """(lo, hi) minutes of the automatic axis (whole hours) for a schedule, or None."""
    tasks = build_tasks(df)
    if not tasks:
        return None
    return (min(t["s"] for t in tasks) // 60 * 60, -(-max(t["e"] for t in tasks) // 60) * 60)


def timeline_html(df, role=None, query="", now_min=None, employees=None, sort="start", gap_min=0, view=None):
    """Returns (html, gaps, flights, workers): html is the whole timeline as one
    string ('' when there is nothing to show); gaps is [{id, worker, s, e}] for
    every free window drawn (only when gap_min is set), for the caller to
    render an assign-into-this-window action per gap; flights is [{id, flight}]
    for every distinct flight with a bar drawn, for a per-flight preview
    trigger; workers is the ordered worker names drawn, one per row (row's
    position = its "data-w" in the HTML), for a per-row drag-and-drop target."""
    tasks = build_tasks(df)
    if role:
        tasks = [t for t in tasks if t["role"] == role]
    q = (query or "").strip()
    if q:
        tasks = [t for t in tasks if q in t["worker"]]
    if not tasks:
        return "", [], [], []

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
            return "", [], [], []

    rows_html = []
    n_gap_workers = set()
    gaps_out = []
    flights_out = {}
    last_group = None
    for wi, w in enumerate(order):
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
            _fid = "".join(ch for ch in t["flight"] if ch.isalnum())
            flights_out.setdefault(_fid, t["flight"])
            bars.append(
                f'<div class="tl-bar" draggable="true" data-fl="{_fid}" data-task="{t["idx"]}" '
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
                free_s = gs + (brk[1] if brk else 0)
                gid = f"g{len(gaps_out)}_{gs}_{ge}"
                gaps_out.append({
                    "id": gid, "worker": w, "s": gs, "e": ge,
                    # the break/refresh sits right at the START of the gap (right after
                    # the previous task) — free_s is where this worker is ACTUALLY free,
                    # i.e. past their break; callers must search [free_s, e], not [s, e]
                    "break": brk, "free_s": free_s,
                })
                # the break/refresh portion: its own non-clickable box, not part of
                # the assignable free window at all (drawn only if it isn't clipped
                # entirely outside the visible [lo, hi] zoom range)
                if brk and gs < hi and free_s > lo:
                    _bs, _be = max(gs, lo), min(free_s, hi)
                    gaps_html += (
                        f'<div class="tl-brk-box" '
                        f'style="right:{pos(_bs):.3f}%;width:{max(pos(_be) - pos(_bs), 0.4):.3f}%" '
                        f'title="{brk[0]} {brk[1]} דק׳ · {_fmt(gs)}–{_fmt(free_s)}">'
                        f'<span>{"☕" if brk[0] == "הפסקה" else "↻"} {brk[1]}\'</span></div>'
                    )
                # the actual free window — this is what's clickable and what
                # unfilled_in_window searches (gap_min applies to the ORIGINAL
                # gs-ge span, so a short residual free_s..ge can still show,
                # possibly with no usable minutes — that's fine, it's informative)
                if free_s < ge and free_s < hi and ge > lo:
                    _fs, _fe = max(free_s, lo), min(ge, hi)
                    _flen = ge - free_s
                    lbl = f"{_flen // 60}:{_flen % 60:02d}" if _flen >= 45 else ""
                    tip = f"פנוי {_flen} דק׳ · {_fmt(free_s)}–{_fmt(ge)} · לחיצה = מה אפשר לשבץ כאן"
                    gaps_html += (
                        f'<div class="tl-gap" data-gap="{gid}" '
                        f'style="right:{pos(_fs):.3f}%;width:{max(pos(_fe) - pos(_fs), 0.4):.3f}%" '
                        f'title="{tip}"><span>{lbl}</span></div>'
                    )
        hrs = busy(w) / 60
        rows_html.append(
            f'<div class="tl-row"><div class="tl-name" title="{escape(w)} · {len(by_worker[w])} משימות · {hrs:.1f} שעות">'
            f'{escape(w)}<small>{len(by_worker[w])}</small></div>'
            f'<div class="tl-track" data-w="{wi}">{band}{gaps_html}{lines}{now_html}{"".join(bars)}</div></div>'
        )

    gap_note = (
        f'<span class="tl-key"><i class="tl-gap-key"></i>חלון פנוי ({gap_min}+ דק׳) · {len(n_gap_workers)} עובדים</span>'
        '<span class="tl-key">☕ = משבצת הפסקה · ↻ = משבצת רענון (לא לשיבוץ)</span>'
        if gap_min else ""
    )
    legend = "".join(
        f'<span class="tl-key"><i style="background:rgba(var({tok}),.22);border-color:rgb(var({tok}))"></i>{escape(role_name)}</span>'
        for role_name, (tok, _) in _legend_roles(tasks)
    )
    html = (
        '<div class="tl" dir="rtl">'
        f'<div class="tl-legend">{legend}{gap_note}<span class="tl-key"><i class="tl-now-key"></i>עכשיו</span></div>'
        '<div class="tl-scroll">'
        f'<div class="tl-row tl-head"><div class="tl-name"></div><div class="tl-axis">{ticks}</div></div>'
        f'{"".join(rows_html)}'
        '</div></div>'
    )
    return html, gaps_out, [{"id": fid, "flight": fl} for fid, fl in flights_out.items()], order


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


def fmt_minutes(m):
    """Public HH:MM formatter for callers rendering slot/gap times outside this module."""
    return _fmt(m)


def _legend_roles(tasks):
    seen = []
    for t in tasks:
        if t["role"] and t["role"] not in [s[0] for s in seen]:
            seen.append((t["role"], ROLE_STYLE.get(t["role"], _DEFAULT_STYLE)))
    return seen
