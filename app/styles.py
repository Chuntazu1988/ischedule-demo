"""CSS styles for iSchedule application."""

import re

import streamlit as st


def right_heading(text: str, level: str = "h4") -> None:
    """A right-aligned/RTL section heading — plain st.markdown("#### ...")
    left-flows the icon+Hebrew-text line under Streamlit's default styling
    (found on the employee panel, 2026-09-22). Shared here so every admin
    screen gets the same fix instead of each one rolling its own copy."""
    st.markdown(
        f"<{level} style='text-align:right;direction:rtl;margin:22px 0 8px;'>{text}</{level}>",
        unsafe_allow_html=True,
    )


def rtl_form_css(panel_key: str) -> str:
    """Scoped RTL fix for st.text_input/st.selectbox labels + st.caption/
    st.markdown text inside a screen wrapped in st.container(key=panel_key)
    — these widgets default to left-to-right regardless of the label's own
    language (same root cause fixed on the employee panel, 2026-09-22:
    justify-content/text-align don't follow content language on their own).
    Callers still inject the returned string themselves via st.markdown(...,
    unsafe_allow_html=True) — this only builds the (page-wide but
    class-scoped) <style> text."""
    return f"""<style>
.st-key-{panel_key} [data-testid="stMarkdownContainer"],
.st-key-{panel_key} [data-testid="stCaptionContainer"] {{
    text-align: right !important;
    direction: rtl !important;
}}
.st-key-{panel_key} div[data-testid="stTextInput"],
.st-key-{panel_key} div[data-testid="stSelectbox"] {{
    direction: rtl !important;
}}
.st-key-{panel_key} div[data-testid="stTextInput"] label,
.st-key-{panel_key} div[data-testid="stSelectbox"] label {{
    text-align: right !important;
    width: 100% !important;
}}
.st-key-{panel_key} div[data-testid="stTextInput"] input {{ text-align: right !important; }}
</style>"""


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Rubik:wght@400;500;600;700;800&family=Chakra+Petch:wght@500;600;700&display=swap');
/* Rubik for all UI text (reads well in Hebrew); Chakra Petch only for
   numbers/codes (flight numbers, clock, stats). Material icon spans set
   their own font-family, so they're unaffected by the inheritance here. */
html, body, .stApp, .stApp button, .stApp input, .stApp textarea, .stApp select,
[data-testid="stMarkdownContainer"], [data-testid="stWidgetLabel"], [data-testid="stExpander"] summary {
    font-family: 'Rubik', sans-serif !important;
}
.stApp { background: var(--bg) !important; }
/* living background (grey + pastel, 2026-09-26): three soft pastel light
   blobs drifting slowly over a faint navigation-chart grid */
[data-testid="stAppViewContainer"]::before {
    content: ""; position: fixed; inset: -15%; z-index: 0; pointer-events: none;
    background:
      radial-gradient(38% 34% at 82% 12%, var(--blob-1), transparent 70%),
      radial-gradient(34% 30% at 12% 38%, var(--blob-2), transparent 70%),
      radial-gradient(40% 36% at 55% 92%, var(--blob-3), transparent 70%);
    animation: blobDrift 28s ease-in-out infinite alternate;
}
[data-testid="stAppViewContainer"]::after {
    content: ""; position: fixed; inset: 0; z-index: 0; pointer-events: none;
    background-image:
      linear-gradient(var(--grid) 1px, transparent 1px),
      linear-gradient(90deg, var(--grid) 1px, transparent 1px);
    background-size: 44px 44px;
    mask-image: radial-gradient(120% 90% at 50% 0%, #000 40%, transparent 100%);
}
@keyframes blobDrift {
    0%   { transform: translate(0, 0) scale(1); }
    50%  { transform: translate(3%, 2%) scale(1.06); }
    100% { transform: translate(-3%, -1%) scale(1.02); }
}
[data-testid="stMain"], [data-testid="stHeader"] { position: relative; z-index: 1; }
[data-testid="stHeader"] { background: transparent !important; }

/* frosted-glass surfaces */
.hero, .stat, [class*="st-key-build_panel_"], [class*="st-key-nav_bar"],
[class*="st-key-fc_"] [data-testid="stExpander"], .st-key-emp_profile,
[class*="st-key-mode_"] div[data-testid="stButton"] button, [data-testid="stExpander"],
.seg-card, .login-card, [data-testid="stForm"] {
    background: var(--glass) !important;
    box-shadow: 0 18px 44px rgba(var(--shadow-rgb),.22), inset 0 1px 0 var(--glass-line);
    backdrop-filter: blur(16px) saturate(150%);
    -webkit-backdrop-filter: blur(16px) saturate(150%);
    border-color: var(--glass-line) !important;
}
/* input fields: a clearly separate well inside glass cards (they used to
   melt into a same-colour card), gold ring on focus */
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
    background: rgba(var(--ink-rgb),.055) !important;
    border: 1px solid rgba(var(--ink-rgb),.20) !important;
    border-radius: 10px !important;
}
[data-baseweb="input"] [data-baseweb="base-input"], [data-baseweb="input"] input {
    background: transparent !important; border: none !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="select"] > div:focus-within,
[data-baseweb="textarea"]:focus-within {
    border-color: var(--acc) !important;
    box-shadow: 0 0 0 3px rgba(var(--acc-rgb),.18) !important;
}
/* paper planes drifting across the background (login screen; reusable):
   .pp (fly along a straight line) > .pp-a (rotate to the heading, dotted
   trail behind) > .pp-b (the plane itself: mask shape + bobbing). The heading
   comes from the same dx/dy the flight uses, via CSS atan2(). */
.paper-planes { position: fixed; inset: 0; z-index: -1; pointer-events: none; overflow: hidden; }
.pp {
    position: absolute; left: var(--x); top: var(--y); width: 0; height: 0;
    animation: ppFly var(--dur) linear var(--delay) infinite;
    opacity: 0;
}
@keyframes ppFly {
    0%   { transform: translate(0, 0); opacity: 0; }
    8%   { opacity: .95; }
    92%  { opacity: .95; }
    100% { transform: translate(var(--dx), var(--dy)); opacity: 0; }
}
.pp-a { position: absolute; left: 0; top: 0; transform: rotate(atan2(var(--dy), var(--dx))); }
.pp-a::before {                       /* dotted trail, fading backwards */
    content: ""; position: absolute; right: calc(var(--s) * .55); top: 0;
    width: calc(var(--s) * 4.2); height: 0; border-top: 2px dotted rgba(var(--acc-rgb),.55);
    -webkit-mask-image: linear-gradient(to left, #000, transparent);
            mask-image: linear-gradient(to left, #000, transparent);
}
.pp-b {
    position: absolute; left: calc(var(--s) * -.5); top: calc(var(--s) * -.42);
    width: var(--s); height: calc(var(--s) * .84);
    background: var(--c);
    -webkit-mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 84'><path d='M100 0 0 38 30 50 100 0 42 58 42 84 58 64 78 78z' fill='black'/></svg>") center/contain no-repeat;
            mask: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 84'><path d='M100 0 0 38 30 50 100 0 42 58 42 84 58 64 78 78z' fill='black'/></svg>") center/contain no-repeat;
    animation: ppBob 2.6s ease-in-out infinite alternate;
    filter: drop-shadow(0 4px 6px rgba(var(--shadow-rgb),.25));
}
.pp-flip .pp-b { scale: 1 -1; }
@keyframes ppBob {
    from { transform: translateY(-5px) rotate(-7deg); }
    to   { transform: translateY(5px) rotate(7deg); }
}
@media (prefers-reduced-motion: reduce) { .paper-planes { display: none; } }

/* ═════ Upload screen (2026-09-26): hero + step tracker + glass drop cards ═════ */
.up-hero { direction: rtl; text-align: center; padding: 26px 20px 10px; position: relative; }
.up-emblem { position: relative; width: 300px; max-width: 86vw; height: 210px; margin: 0 auto 0; animation: upFloat 6s ease-in-out infinite; }
.up-emblem .emblem { position: relative; z-index: 2; width: 100%; height: 100%; overflow: visible; filter: drop-shadow(0 14px 26px rgba(var(--shadow-rgb),.32)); }
.hero-emblem { width: 112px; height: 78px; flex: none; }
.hero-emblem .emblem { width: 100%; height: 100%; overflow: visible; filter: drop-shadow(0 6px 12px rgba(var(--shadow-rgb),.28)); }
.login-emblem { width: 168px; height: 118px; margin: -8px auto 0; }
.login-emblem .emblem { width: 100%; height: 100%; overflow: visible; filter: drop-shadow(0 8px 16px rgba(var(--shadow-rgb),.28)); }
@media (prefers-reduced-motion: reduce) { .emblem animate, .emblem animateMotion { display: none; } }
.up-emblem img { position: relative; width: 100%; height: 100%; border-radius: 50%; object-fit: cover; z-index: 2;
    box-shadow: 0 14px 34px rgba(var(--shadow-rgb),.35); }
.up-ring, .up-ring2 { position: absolute; inset: -9px; border-radius: 50%; z-index: 1; }
.up-ring  { background: conic-gradient(from 0deg, transparent, var(--grad-1) 30%, var(--grad-2) 55%, var(--grad-3) 80%, transparent);
    animation: radarSweep 5s linear infinite; filter: blur(.4px); }
.up-ring2 { inset: 26px 56px; opacity: .32; filter: blur(26px);
    background: conic-gradient(from 90deg, var(--grad-1), var(--grad-2), var(--grad-3), var(--grad-1)); animation: radarSweep 9s linear infinite reverse; }
@keyframes upFloat { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-7px); } }
.up-title { font-family: 'Chakra Petch','Rubik',sans-serif; font-size: 46px; font-weight: 700; letter-spacing: 1px; line-height: 1.1;
    background: var(--acc-grad); -webkit-background-clip: text; background-clip: text; color: transparent; margin-top: 10px; }
.up-tag { font-family: 'Chakra Petch',monospace; font-size: 12px; letter-spacing: 5px; color: rgba(var(--ink-rgb),.74); margin-top: 4px; }
.up-arc { position: relative; height: 54px; max-width: 560px; margin: 8px auto 0; }
.up-arc svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.up-arc path { fill: none; stroke: rgba(var(--acc-rgb),.55); stroke-width: 1.5; stroke-dasharray: 5 7; }
.up-arc .hero-plane { offset-path: path("M6,48 Q280,-14 554,48"); }
/* step tracker: three steps joined by a line that fills as steps are done */
.up-steps { direction: rtl; display: flex; align-items: flex-start; justify-content: center; gap: 0; max-width: 640px; margin: 4px auto 6px; }
.up-step { flex: 0 0 auto; width: 150px; text-align: center; position: relative; }
.up-step i { display: flex; align-items: center; justify-content: center; width: 38px; height: 38px; margin: 0 auto 8px; border-radius: 50%;
    font-style: normal; font-weight: 800; font-size: 16px; background: var(--glass); border: 2px solid rgba(var(--ink-rgb),.22);
    color: rgba(var(--ink-rgb),.74); transition: all .3s; }
.up-step span { font-size: 14px; font-weight: 600; color: rgba(var(--ink-rgb),.74); }
.up-step.done i { background: var(--acc-grad); border-color: transparent; color: #fff; box-shadow: 0 0 0 5px rgba(var(--acc-rgb),.16); }
.up-step.done span { color: var(--ink); }
.up-step.cur i { border-color: var(--acc); color: var(--acc-strong); box-shadow: 0 0 0 6px rgba(var(--acc-rgb),.18); animation: stepPulse 1.8s ease-in-out infinite; }
.up-step.cur span { color: var(--ink); font-weight: 800; }
@keyframes stepPulse { 50% { box-shadow: 0 0 0 11px rgba(var(--acc-rgb),0); } }
.up-line { flex: 1 1 40px; height: 2px; margin-top: 18px; background: rgba(var(--ink-rgb),.18); border-radius: 2px; position: relative; overflow: hidden; }
.up-line.done::after { content: ""; position: absolute; inset: 0; background: var(--acc-grad); }

/* drop cards: keyed containers up_<uploader-key>_<ok|todo> */
[class*="st-key-up_main_"] {
    direction: rtl; border-radius: 18px; padding: 16px 18px 14px; margin-bottom: 14px;
    transition: transform .2s, box-shadow .2s, border-color .2s;
    animation: upCardIn .55s cubic-bezier(.2,.8,.2,1) both;
}
[class*="st-key-up_main_"]:hover { transform: translateY(-3px); box-shadow: 0 20px 46px rgba(var(--shadow-rgb),.28), inset 0 1px 0 var(--glass-line); border-color: rgba(var(--acc-rgb),.55) !important; }
[class*="st-key-up_main_"][class*="_ok"] { border-color: rgba(var(--acc-rgb),.75) !important; box-shadow: 0 0 0 1px rgba(var(--acc-rgb),.25), 0 16px 40px rgba(var(--acc-rgb),.16); }
[class*="st-key-up_main_t1_"] { animation-delay: .08s; }
[class*="st-key-up_main_fids1_"] { animation-delay: .12s; }
[class*="st-key-up_main_fids2_"] { animation-delay: .2s; }
@keyframes upCardIn { from { opacity: 0; transform: translateY(18px) scale(.985); } to { opacity: 1; transform: none; } }
.up-head { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; direction: rtl; }
.up-ico { flex: none; width: 46px; height: 46px; border-radius: 14px; display: flex; align-items: center; justify-content: center; font-size: 23px;
    background: linear-gradient(135deg, rgba(var(--acc-rgb),.22), rgba(var(--r-tl),.2)); box-shadow: inset 0 0 0 1px rgba(var(--acc-rgb),.35); }
.up-head > div { flex: 1; min-width: 0; text-align: right; }
.up-title-s { font-size: 16px; font-weight: 800; color: var(--ink); }
.up-hint { font-size: 12.5px; color: rgba(var(--ink-rgb),.74); margin-top: 1px; }
.up-chip { flex: none; padding: 3px 11px; border-radius: 999px; font-size: 12px; font-weight: 700; }
.up-chip-req { background: rgba(var(--acc-rgb),.18); color: var(--acc-strong); border: 1px solid rgba(var(--acc-rgb),.5); }
.up-chip-opt { background: rgba(var(--ink-rgb),.06); color: rgba(var(--ink-rgb),.74); border: 1px solid rgba(var(--ink-rgb),.2); }
.up-chip-rec { background: rgba(var(--r-guard),.14); color: var(--r-guard-ink); border: 1px solid rgba(var(--r-guard),.45); }
.up-chip-ok  { background: var(--acc-grad); color: #fff; border: none; }
/* the native uploader restyled: label hidden (the card header is the label) */
[class*="st-key-up_main_"] [data-testid="stFileUploader"] { background: transparent !important; border: none !important; padding: 0 !important; }
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzone"] {
    background: rgba(var(--ink-rgb),.035) !important; border: 2px dashed rgba(var(--acc-rgb),.5) !important; border-radius: 14px !important;
    min-height: 74px; direction: rtl; gap: 14px; transition: background .2s, border-color .2s;
}
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzone"]:hover { background: rgba(var(--acc-rgb),.09) !important; border-color: var(--acc) !important; }
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzoneInstructions"] span,
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzoneInstructions"] small { display: none !important; }
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzoneInstructions"]::after {
    content: "גרור קובץ לכאן או לחץ על הכפתור"; font-size: 14px; color: rgba(var(--ink-rgb),.74); display: block; text-align: right; }
[class*="st-key-up_main_daily_"] [data-testid="stFileUploaderDropzoneInstructions"]::after { content: "גרור לכאן את קובץ הסידור היומי · XLSX"; }
[class*="st-key-up_main_t1_"] [data-testid="stFileUploaderDropzoneInstructions"]::after { content: "גרור לכאן את סידור טרמינל 1 · XLSX"; }
[class*="st-key-up_main_fids"] [data-testid="stFileUploaderDropzoneInstructions"]::after { content: "גרור לכאן את קובץ ה-FIDS · HTML"; }
/* "Upload" → "בחירת קובץ" (the label is a <p> inside the button; keep the icon) */
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p { font-size: 0 !important; }
[class*="st-key-up_main_"] [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {
    content: "בחירת קובץ"; font-size: 14px; font-weight: 700; }
[class*="st-key-up_main_"] [data-testid="stFileUploaderFile"] { background: rgba(var(--acc-rgb),.1); border-radius: 12px; padding: 6px 10px; }
[class*="st-key-up_confirm"] { margin-top: 6px; }
[class*="st-key-up_confirm"] div[data-testid="stButton"] button { min-height: 52px !important; font-size: 17px !important; border-radius: 14px !important; }
.st-key-up_confirm_ready div[data-testid="stButton"] button { animation: ctaGlow 2.2s ease-in-out infinite; }
@keyframes ctaGlow { 50% { box-shadow: 0 0 0 1px rgba(var(--acc-rgb),.3), 0 0 34px rgba(var(--acc-rgb),.5) !important; } }
.up-missing { direction: rtl; text-align: center; font-size: 13.5px; color: rgba(var(--ink-rgb),.74); margin: 8px 0 2px; }
.up-missing b { color: var(--ink); }
@media (prefers-reduced-motion: reduce) { .up-emblem, .up-ring, .up-ring2, .up-step.cur i, [class*="st-key-up_main_"] { animation: none !important; } }

/* ── Sidebar (the file panel): same glass/uploader look as the upload screen ── */
[data-testid="stSidebar"] { background: var(--glass) !important; backdrop-filter: blur(18px) saturate(150%);
    border-left: 1px solid var(--glass-line); }
[data-testid="stSidebar"] [data-testid="stSidebarContent"] { background: transparent !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { font-size: 18px; }
[data-testid="stSidebar"] [data-testid="stFileUploader"] { background: transparent !important; padding: 0 !important; border: none !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] {
    background: rgba(var(--ink-rgb),.035) !important; border: 2px dashed rgba(var(--acc-rgb),.5) !important; border-radius: 14px !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] span,
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"] small { display: none !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzoneInstructions"]::after {
    content: "גרור קובץ לכאן"; font-size: 13px; color: rgba(var(--ink-rgb),.74); }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p { font-size: 0 !important; }
[data-testid="stSidebar"] [data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after {
    content: "בחירת קובץ"; font-size: 14px; font-weight: 700; }
/* ═════ Native widgets in the theme (alerts, dialogs, menus, focus) ═════ */
.stApp, .stApp p, .stApp label, .stApp li, .stApp h1, .stApp h2, .stApp h3, .stApp h4 { color: var(--ink); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: rgba(var(--ink-rgb),.74) !important; }

/* alerts: glass card, a coloured edge AND an icon glyph (never colour alone) */
[data-testid="stAlertContainer"] {
    background: var(--glass) !important; color: var(--ink) !important;
    border: 1px solid rgba(var(--ink-rgb),.14) !important; border-right: 5px solid var(--kind, rgba(var(--ink-rgb),.4)) !important;
    border-radius: 14px !important; direction: rtl; text-align: right; gap: 12px;
    backdrop-filter: blur(12px); box-shadow: 0 8px 22px rgba(var(--shadow-rgb),.16);
}
[data-testid="stAlertContainer"] * { color: var(--ink) !important; }
[data-testid="stAlertContainer"]::before {
    content: var(--glyph, "ℹ"); flex: none; width: 26px; height: 26px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
    font-weight: 900; font-size: 15px; background: var(--kind, rgba(var(--ink-rgb),.4)); color: #fff !important; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"])    { --kind: rgb(var(--r-guard)); --glyph: "i"; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) { --kind: rgb(var(--acc-rgb));  --glyph: "✓"; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) { --kind: rgb(var(--t1-rgb));   --glyph: "!"; }
[data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"])   { --kind: rgb(var(--r-insp));   --glyph: "✕"; }

/* toasts, dialogs, popovers/menus */
[data-testid="stToast"] { background: var(--card) !important; color: var(--ink) !important; border: 1px solid var(--glass-line); border-radius: 14px !important;
    box-shadow: 0 14px 34px rgba(var(--shadow-rgb),.3); }
[data-testid="stToast"] * { color: var(--ink) !important; }
div[role="dialog"] { background: var(--card) !important; color: var(--ink) !important; border: 1px solid var(--glass-line) !important; border-radius: 20px !important; }
div[role="dialog"] * { color: var(--ink); }
[data-baseweb="popover"] > div, [data-baseweb="menu"], ul[role="listbox"] { background: var(--card) !important; color: var(--ink) !important; border-radius: 12px !important; }
/* selectbox/multiselect dropdown options: right-to-left, right-aligned — these
   render in a portal outside our rtl containers, so they don't inherit it */
[data-baseweb="menu"], ul[role="listbox"] { direction: rtl; }
[data-baseweb="menu"] li, ul[role="listbox"] li { color: var(--ink) !important; text-align: right; }
[data-baseweb="menu"] li:hover, ul[role="listbox"] li:hover, ul[role="listbox"] li[aria-selected="true"] { background: rgba(var(--acc-rgb),.16) !important; }

/* accessibility: a clearly visible keyboard focus ring everywhere */
:focus-visible { outline: 3px solid var(--acc) !important; outline-offset: 2px !important; border-radius: 8px; }
button:focus-visible, [role="tab"]:focus-visible { box-shadow: 0 0 0 5px rgba(var(--acc-rgb),.28) !important; }

/* status banners (build time, published/draft, terminal/hours labels): glass + coloured edge,
   the meaning also carried by the emoji/icon in the text — never by colour alone */
.banner { direction: rtl; text-align: center; padding: 12px 18px; border-radius: 14px; margin: 0 0 12px; font-weight: 700; font-size: 16px;
    background: var(--glass); color: var(--ink); border: 1px solid rgba(var(--ink-rgb),.14); border-right: 5px solid var(--kind, rgba(var(--ink-rgb),.4));
    backdrop-filter: blur(12px); box-shadow: 0 8px 22px rgba(var(--shadow-rgb),.14); }
.banner-ok   { --kind: rgb(var(--acc-rgb)); }
.banner-info { --kind: rgb(var(--r-guard)); }
.banner-warn { --kind: rgb(var(--t1-rgb));
    background: repeating-linear-gradient(135deg, rgba(var(--t1-rgb),.13) 0 10px, transparent 10px 20px), var(--glass); }
/* Every other file uploader in the app (employee import, etc.): same glass dropzone,
   Hebrew instruction + button (the upload screen / sidebar rules above are more specific) */
[data-testid="stFileUploaderDropzone"] { background: rgba(var(--ink-rgb),.035) !important; border: 2px dashed rgba(var(--acc-rgb),.5) !important;
    border-radius: 14px !important; }
[data-testid="stFileUploaderDropzone"]:hover { background: rgba(var(--acc-rgb),.09) !important; border-color: var(--acc) !important; }
[data-testid="stFileUploaderDropzoneInstructions"] span, [data-testid="stFileUploaderDropzoneInstructions"] small { display: none !important; }
[data-testid="stFileUploaderDropzoneInstructions"]::after { content: "גרור קובץ לכאן או לחץ על הכפתור"; font-size: 13.5px; color: rgba(var(--ink-rgb),.74); }
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p { font-size: 0 !important; }
[data-testid="stFileUploaderDropzone"] button [data-testid="stMarkdownContainer"] p::after { content: "בחירת קובץ"; font-size: 14px; font-weight: 700; }
[data-testid="stFileUploaderFile"] { background: rgba(var(--acc-rgb),.1); border-radius: 12px; }
/* no-brown (2026-09-26): yellow/orange at low alpha over the charcoal palette reads as
   brown/khaki, so in the dark base warm fills become neutral glass — the colour stays
   in the edge, the text and the icon */
html[data-ischedule-base="dark"] .role-queue, html[data-ischedule-base="dark"] .role-inspector,
html[data-ischedule-base="dark"] .role-new-assignment, html[data-ischedule-base="dark"] .role-trainee {
    background: rgba(var(--ink-rgb),.07) !important; }
html[data-ischedule-base="dark"] .banner-warn {
    background: repeating-linear-gradient(135deg, rgba(var(--ink-rgb),.09) 0 10px, transparent 10px 20px), var(--glass) !important; }
html[data-ischedule-base="dark"] .stat-warn { background: rgba(var(--ink-rgb),.05); }
html[data-ischedule-base="dark"] .req-line, html[data-ischedule-base="dark"] .panel-title { background: transparent; }
html[data-ischedule-base="dark"] [class*="st-key-mode_"] div[data-testid="stButton"] button[kind^="primary"] {
    background: rgba(var(--ink-rgb),.08) !important; }
html[data-ischedule-base="dark"] div[data-testid="stButton"] button[kind^="primary"],
html[data-ischedule-base="dark"] div[data-testid="stFormSubmitButton"] button[kind^="primary"] {
    background: linear-gradient(120deg, rgba(var(--ink-rgb),.12), rgba(var(--r-tl),.16)) !important; }
/* gradient accent text */
.hero-title, .stat-gold b, .login-title {
    background: var(--acc-grad); -webkit-background-clip: text; background-clip: text;
    color: transparent !important;
}
/* Hide only the yellow decoration bar — keep toolbar and sidebar toggle */
[data-testid="stDecoration"],
[data-testid="stStatusWidget"] { display: none !important; }

/* type="primary" buttons/form-submit-buttons default to WHITE text no
   matter how bright config.toml's primaryColor is — confirmed via computed
   style: white-on-var(--acc) is ~1.6:1 contrast, badly under WCAG AA. Same
   dark-navy fix already applied by hand to the login/upload screen's own
   buttons, generalised here so every primary button site-wide gets it
   (found 2026-09-25 on the "צור משתמש" button in user_management.py). */
/* Descendant (not child) selectors: a button with help= is wrapped in a
   tooltip element, so `> button` silently missed every such button.
   2026-09-26 user: the solid-gold primary button was "too big" and its label
   "not clearly visible". Primary is now a dark glass button with a gold
   edge, gold label and a soft glow — the label reads at ~8:1 instead of
   dark-on-mid-gold — and every button site-wide is a notch more compact. */
div[data-testid="stButton"] button,
div[data-testid="stDownloadButton"] button,
div[data-testid="stFormSubmitButton"] button {
    min-height: 36px !important;
    padding: 4px 14px !important;
    border-radius: 10px !important;
    font-size: 14px !important;
    transition: background .15s, border-color .15s, box-shadow .15s, color .15s !important;
}
div[data-testid="stButton"] button p,
div[data-testid="stDownloadButton"] button p,
div[data-testid="stFormSubmitButton"] button p {
    font-size: 14px !important;
}
div[data-testid="stButton"] button[kind="secondary"]:hover,
div[data-testid="stDownloadButton"] button:hover {
    border-color: rgba(var(--acc-rgb),.55) !important;
    color: var(--acc-strong) !important;
}
div[data-testid="stButton"] button[kind^="primary"],
div[data-testid="stFormSubmitButton"] button[kind^="primary"] {
    background: linear-gradient(120deg, rgba(var(--acc-rgb),.26), rgba(var(--acc-rgb),.10) 60%, rgba(var(--r-tl),.16)) !important;
    border: 1px solid var(--acc) !important;
    color: var(--acc-strong) !important;
    font-weight: 700 !important;
    box-shadow: 0 0 0 1px rgba(var(--acc-rgb),.12), 0 0 18px rgba(var(--acc-rgb),.16) !important;
}
div[data-testid="stButton"] button[kind^="primary"] p,
div[data-testid="stFormSubmitButton"] button[kind^="primary"] p {
    font-weight: 700 !important;
}
div[data-testid="stButton"] button[kind^="primary"]:hover,
div[data-testid="stFormSubmitButton"] button[kind^="primary"]:hover {
    background: linear-gradient(180deg, rgba(var(--acc-rgb),.32), rgba(var(--acc-rgb),.16)) !important;
    color: var(--acc-strong) !important;
    box-shadow: 0 0 0 1px rgba(var(--acc-rgb),.25), 0 0 26px rgba(var(--acc-rgb),.28) !important;
}

/* ── Build panels (full-day terminal chooser / by-shift / by-hours) ──
   One dark glass card, fully RTL: Streamlit's radio and columns flow
   left-to-right by default, which pushed everything to the left edge. */
[class*="st-key-build_panel_"] {
    direction: rtl;
    background: linear-gradient(180deg, var(--card) 0%, var(--card-2) 100%);
    border: 1px solid rgba(var(--acc-rgb),.28);
    border-radius: 16px;
    padding: 16px 20px 18px;
    margin: 12px 0 6px;
    box-shadow: 0 10px 30px rgba(var(--shadow-rgb),.35), inset 0 1px 0 rgba(var(--ink-rgb),.04);
    animation: panelIn .22s ease-out both;
}
@keyframes panelIn {
    from { opacity: 0; transform: translateY(-6px); }
    to   { opacity: 1; transform: translateY(0); }
}
[class*="st-key-build_panel_"] [data-testid="stMarkdownContainer"],
[class*="st-key-build_panel_"] [data-testid="stCaptionContainer"],
[class*="st-key-build_panel_"] [data-testid="stWidgetLabel"] {
    direction: rtl !important;
    text-align: right !important;
}
[class*="st-key-build_panel_"] [data-testid="stWidgetLabel"] {
    width: 100% !important;
    justify-content: flex-start !important;
}
[class*="st-key-build_panel_"] [data-testid="stRadio"],
[class*="st-key-build_panel_"] [data-testid="stRadio"] > div,
[class*="st-key-build_panel_"] [role="radiogroup"] {
    direction: rtl !important;
    flex-direction: row !important;
    justify-content: flex-start !important;
    width: 100% !important;
}
[class*="st-key-build_panel_"] [role="radiogroup"] { gap: 6px 26px !important; }
[class*="st-key-build_panel_"] [data-testid="stTextInput"] { direction: rtl; }
[class*="st-key-build_panel_"] [data-testid="stTextInput"] input {
    text-align: center !important;
    font-size: 16px !important;
    letter-spacing: 1px;
    font-variant-numeric: tabular-nums;
}
/* the panels' ✕ close: a small round icon button, not a full-size box */
.st-key-full_cancel div[data-testid="stButton"] button,
.st-key-seg_cancel div[data-testid="stButton"] button,
.st-key-tf_cancel div[data-testid="stButton"] button {
    width: 34px !important;
    min-width: 34px !important;
    min-height: 34px !important;
    height: 34px !important;
    padding: 0 !important;
    border-radius: 50% !important;
    background: rgba(var(--ink-rgb),.04) !important;
    border: 1px solid rgba(var(--ink-rgb),.12) !important;
    color: rgba(var(--ink-rgb),.74) !important;
}
[class*="st-key-build_panel_"] [data-testid="stWidgetLabel"] {
    flex-direction: row !important;
    align-items: center !important;
    gap: 6px;
}
.build-panel-title {
    font-size: 17px;
    font-weight: 800;
    color: rgba(var(--ink-rgb),.95);
}
.build-panel-sub {
    font-size: 12.5px;
    color: rgba(var(--ink-rgb),.74);
    margin-top: 2px;
}
.seg-card {
    direction: rtl;
    text-align: right;
    background: rgba(var(--ink-rgb),.03);
    border: 1px solid rgba(var(--ink-rgb),.08);
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 8px;
    min-height: 96px;
}
.seg-card-built { border-color: rgba(var(--ok-rgb),.45); background: rgba(var(--ok-rgb),.06); }
.seg-card-empty { opacity: .5; }
.seg-name { font-size: 15px; font-weight: 800; color: rgba(var(--ink-rgb),.95); }
.seg-built {
    margin-right: 8px; font-size: 11px; font-weight: 800; color: var(--ok-ink);
    background: rgba(var(--ok-rgb),.15); border-radius: 999px; padding: 1px 8px;
}
.seg-range { font-size: 12.5px; color: rgba(var(--ink-rgb),.74); margin-top: 4px; }
.seg-count { font-size: 13px; color: rgba(var(--ink-rgb),.75); margin-top: 6px; }
.seg-count b { color: var(--acc); font-size: 18px; }
.seg-scope { color: rgba(var(--ink-rgb),.74); font-size: 12px; }

/* Main navigation — one pill bar (nav_bar / nav_bar_pre container) holding
   the nav_tab_* buttons. Active tab: gold glass pill; the rest plain text.
   Solid-fill gold stays reserved for nothing — actions use the glass
   primary style, the active tab the pill. */
[class*="st-key-nav_bar"] {
    background: rgba(var(--ink-rgb),.04);
    border: 1px solid rgba(var(--ink-rgb),.06);
    border-radius: 14px;
    padding: 5px;
    margin: 6px 0 14px;
    /* stays in view while scrolling long schedules */
    background: var(--glass);
    -webkit-backdrop-filter: blur(14px); backdrop-filter: blur(14px);
    box-shadow: 0 6px 18px rgba(var(--shadow-rgb),.12);
}
[data-testid="stLayoutWrapper"]:has(> [class*="st-key-nav_bar"]) { position: sticky; top: .3rem; z-index: 60; }
[class*="st-key-nav_bar"] [data-testid="stHorizontalBlock"] { gap: 6px !important; direction: ltr !important; }  /* order is set in Python; a screen-level rtl must not flip it */
[class*="st-key-nav_tab_"] div[data-testid="stButton"] button {
    background: transparent !important;
    border: none !important;
    border-radius: 10px !important;
    box-shadow: none !important;
    color: rgba(var(--ink-rgb),.74) !important;
    min-height: 38px !important;
}
[class*="st-key-nav_tab_"] div[data-testid="stButton"] button:hover {
    color: rgba(var(--ink-rgb),.95) !important;
    background: rgba(var(--ink-rgb),.05) !important;
}
[class*="st-key-nav_tab_"] div[data-testid="stButton"] button[kind^="primary"] {
    background: rgba(var(--acc-rgb),.18) !important;
    box-shadow: inset 0 0 0 1px rgba(var(--acc-rgb),.55) !important;
    color: var(--acc-strong) !important;
    font-weight: 700 !important;
}

/* Build-mode cards (mode_full / mode_segment / mode_hours / mode_new):
   two-line buttons — bold title + grey description — right-aligned. The
   open mode is the primary (gold glass) one. */
[class*="st-key-mode_"] div[data-testid="stButton"] button {
    min-height: 66px !important;
    padding: 10px 16px !important;
    border-radius: 14px !important;
    background: rgba(var(--ink-rgb),.03) !important;
    border: 1px solid rgba(var(--ink-rgb),.1) !important;
    box-shadow: none !important;
    transition: transform .15s, box-shadow .15s, border-color .15s, background .15s !important;
}
/* the label sits in button > div > span > markdown, each shrink-wrapped —
   stretch all of them so the RTL text can hug the right edge */
[class*="st-key-mode_"] div[data-testid="stButton"] button > div,
[class*="st-key-mode_"] div[data-testid="stButton"] button > div > span,
[class*="st-key-mode_"] div[data-testid="stButton"] button [data-testid="stMarkdownContainer"] {
    width: 100% !important; direction: rtl; justify-content: flex-start !important;
}
[class*="st-key-mode_"] div[data-testid="stButton"] button p {
    direction: rtl; text-align: right !important; width: 100%;
    font-size: 15px !important; line-height: 1.45 !important; color: rgba(var(--ink-rgb),.92);
}
[class*="st-key-mode_"] div[data-testid="stButton"] button:hover {
    transform: translateY(-2px);
    border-color: rgba(var(--acc-rgb),.45) !important;
    box-shadow: 0 10px 26px rgba(var(--shadow-rgb),.35) !important;
}
[class*="st-key-mode_"] div[data-testid="stButton"] button[kind^="primary"] {
    background: linear-gradient(180deg, rgba(var(--acc-rgb),.16), rgba(var(--acc-rgb),.05)) !important;
    border: 1px solid var(--acc) !important;
    box-shadow: 0 0 22px rgba(var(--acc-rgb),.18) !important;
}
[class*="st-key-mode_"] div[data-testid="stButton"] button[kind^="primary"] p { color: var(--acc-strong); }
.st-key-mode_new div[data-testid="stButton"] button {
    background: transparent !important;
    border: 1px dashed rgba(var(--ink-rgb),.16) !important;
}
.st-key-mode_new div[data-testid="stButton"] button p { color: rgba(var(--ink-rgb),.74); font-size: 14px !important; }

/* History tab: the delete button reads as destructive, not as a 3rd download */
[class*="st-key-del_archive_"] div[data-testid="stButton"] button {
    background: transparent !important;
    border: 1px solid rgba(var(--bad-rgb),.4) !important;
    color: var(--bad-ink) !important;
}
[class*="st-key-del_archive_"] div[data-testid="stButton"] button:hover {
    background: rgba(var(--bad-rgb),.12) !important;
    border-color: rgba(var(--bad-rgb),.7) !important;
}

.block-container, [data-testid="stMainBlockContainer"] {
    padding-top: 1rem;
    /* generous bottom room: on Windows the taskbar can cover the bottom of the browser
       window, which used to clip the last row even at the end of the scroll */
    padding-bottom: 9rem !important;
}

.ops-rtl {
    direction: rtl;
    text-align: right;
    font-family: Arial, "Noto Sans Hebrew", sans-serif;
}

/* ── Metrics: centered ── */
[data-testid="stMetric"] {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
}
[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] > div,
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] > div,
[data-testid="stMetricDelta"],
[data-testid="stMetricDelta"] > div {
    text-align: center !important;
    width: 100%;
    justify-content: center;
}

/* ── Dashboard RTL: radio tabs ── */
[data-testid="stRadio"] > label {
    direction: rtl;
    text-align: right;
    display: block;
    width: 100%;
}
[data-testid="stRadio"] > div {
    display: flex !important;
    flex-direction: row-reverse !important;
    justify-content: center !important;
    width: 100% !important;
    gap: 24px;
}
/* circle on the right of each tab label */
[data-testid="stRadio"] > div label {
    direction: rtl !important;
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    gap: 6px;
}
[data-testid="stRadio"] > div label input[type="radio"] {
    order: 1 !important;
    margin: 0 !important;
}

/* ── "Runway Control" theme (2026-09-23) — dark charcoal-navy + amber/gold
   aviation accent, same direction applied to the login/upload screen.
   Every hue below keeps the SAME role→color mapping the light theme used
   (the legend and the coloured row-edge are how people identify a role at
   a glance every day) — only lightness/background were re-tuned so they
   read on a dark page instead of a white one. ── */

/* ── "Control Tower + Runway lights" (A+C concept, approved 2026-09-26) ──
   Hero: radar sweep + live chip + clock, gold runway lights moving under it. */
.hero {
    direction: rtl;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 24px 16px;
    margin-bottom: 14px;
    border: 1px solid rgba(var(--acc-rgb),.22);
    border-radius: 18px;
    background: linear-gradient(180deg, rgba(var(--ink-rgb),.04), rgba(var(--ink-rgb),.01));
    box-shadow: 0 12px 30px rgba(var(--shadow-rgb),.3);
}
.hero-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }
.hero-brand { display: flex; align-items: center; gap: 16px; }
.hero-radar {
    width: 58px; height: 58px; border-radius: 50%; flex-shrink: 0;
    border: 1px solid rgba(var(--acc-rgb),.45);
    position: relative; overflow: hidden;
    background: radial-gradient(circle, rgba(var(--acc-rgb),.10), transparent 70%);
}
.hero-radar::before {
    content: ""; position: absolute; inset: 0;
    background: conic-gradient(from 0deg, rgba(var(--acc-rgb),.55), transparent 25%);
    animation: radarSweep 6s linear infinite;
}
.hero-radar::after {
    content: ""; position: absolute; inset: 18px; border-radius: 50%;
    border: 1px solid rgba(var(--acc-rgb),.3);
}
/* flight path across the header: dashed arc, a plane gliding along it */
.hero-flight { position: relative; width: 520px; height: 60px; flex-shrink: 1; }
.hero-flight svg { position: absolute; inset: 0; overflow: visible; }
.hero-flight path { fill: none; stroke: rgba(var(--acc-rgb),.55); stroke-width: 1.5; stroke-dasharray: 5 7; }
.hero-plane {
    position: absolute; left: 0; top: 0; font-size: 20px; line-height: 1;
    color: var(--grad-2);
    offset-path: path("M10,50 Q260,-12 510,50"); offset-rotate: auto;
    transform: scaleX(-1);  /* glides right-to-left, so mirror the ✈ to face its heading */
    animation: planeGlide 9s cubic-bezier(.45,.05,.55,.95) infinite;
    filter: drop-shadow(0 0 6px rgba(var(--acc-rgb),.6));
}
@keyframes planeGlide {
    0% { offset-distance: 100%; opacity: 0; }
    8% { opacity: 1; } 92% { opacity: 1; }
    100% { offset-distance: 0%; opacity: 0; }
}
@media (max-width: 1100px) { .hero-flight { display: none; } }
@keyframes radarSweep { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
.hero-title { font-family: 'Chakra Petch', 'Rubik', sans-serif; font-size: 30px; font-weight: 700; letter-spacing: 1px; color: var(--ink); line-height: 1.1; }
.hero-sub { font-size: 14px; color: var(--acc); margin-top: 2px; }
.hero-meta { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hero-live {
    display: flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px;
    background: rgba(var(--ok-rgb),.1); border: 1px solid rgba(var(--ok-rgb),.35); font-size: 13px; color: var(--ok-ink);
}
.hero-live i { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: livePulse 1.6s ease-in-out infinite; }
@keyframes livePulse { 0%,100% { opacity: 1; } 50% { opacity: .3; } }
.hero-clock {
    display: flex; align-items: center; gap: 8px; padding: 5px 14px; border-radius: 999px;
    background: rgba(var(--ink-rgb),.04); border: 1px solid rgba(var(--ink-rgb),.1);
    font-size: 13px; color: rgba(var(--ink-rgb),.75);
}
.hero-clock b { font-family: 'Chakra Petch', sans-serif; font-size: 22px; color: var(--acc-strong); font-weight: 700; }
.hero-runway {
    height: 3px; border-radius: 2px; opacity: .75;
    background-image: repeating-linear-gradient(90deg, rgba(var(--acc-rgb),.85) 0 26px, transparent 26px 60px);
    background-size: 120px 3px;
    animation: runwayLights 2.4s linear infinite;
}
@keyframes runwayLights { from { background-position: 0 0; } to { background-position: -120px 0; } }

/* Flight cards (display.render_flight_card_with_swap): each flight is a
   keyed container fc_<ok|miss>_<t1|t3>_... holding a staffing fill bar
   (absolutely placed at the left end of the header row) + the expander. */
[class*="st-key-fc_"] { position: relative; }
[class*="st-key-fc_"] [data-testid="stElementContainer"]:has(.fc-fill) {
    position: absolute; left: 52px; top: 12px; z-index: 2; width: auto !important; pointer-events: none;
}
[class*="st-key-fc_"] [data-testid="stExpander"] {
    background: linear-gradient(180deg, var(--card), var(--card-2)) !important;
    border: 1px solid rgba(var(--ink-rgb),.09) !important;
    border-radius: 14px !important;
    transition: transform .15s, box-shadow .15s, border-color .15s;
}
/* lift only while closed — an open card must stay put while swapping */
[class*="st-key-fc_"] [data-testid="stExpander"]:has(details:not([open])):hover {
    transform: translateY(-2px);
    border-color: rgba(var(--acc-rgb),.45) !important;
    box-shadow: 0 12px 28px rgba(var(--shadow-rgb),.4);
}
[class*="st-key-fc_ok_t1_"] [data-testid="stExpander"] { border-color: rgba(var(--t1-rgb),.38) !important; }
[class*="st-key-fc_miss_"] [data-testid="stExpander"] {
    border: 1px dashed rgba(var(--ink-rgb),.5) !important;
}
[class*="st-key-fc_"] [data-testid="stExpander"] summary p { font-size: 15px; }
.fc-fill {
    scroll-margin-top: 110px;  /* land below the sticky nav */
    display: flex; align-items: center; gap: 8px; direction: rtl;
    font-family: 'Chakra Petch', sans-serif; font-size: 13px; font-weight: 600; color: var(--acc-strong);
}
.fc-bar { width: 110px; height: 6px; border-radius: 3px; background: rgba(var(--ink-rgb),.08); overflow: hidden; }
.fc-bar i { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, var(--acc), var(--acc-strong)); }
/* short-staffed: same gold fill, the empty remainder hatched, text white
   with a ⚠ — told apart by pattern and text, not by red vs green/gold */
.fc-fill-miss { color: var(--ink); }
.fc-fill-miss .fc-bar {
    background: repeating-linear-gradient(135deg, rgba(var(--ink-rgb),.35) 0 3px, rgba(var(--ink-rgb),.08) 3px 6px);
}
@media (max-width: 700px) { .fc-bar { width: 56px; } }

/* stats row */
.stat-grid { direction: rtl; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 0 0 14px; }
.stat {
    padding: 12px 16px; border-radius: 14px;
    background: rgba(var(--ink-rgb),.03); border: 1px solid rgba(var(--ink-rgb),.08);
}
.stat-warn { background: rgba(var(--bad-rgb),.06); border-color: rgba(var(--bad-rgb),.25); }
.stat span { display: block; font-size: 12px; color: rgba(var(--ink-rgb),.74); }
.stat b { font-family: 'Chakra Petch', sans-serif; font-size: 28px; font-weight: 700; color: var(--ink); line-height: 1.25; }
.stat-gold b { color: var(--acc-strong); }
.stat-warn b { color: var(--bad-ink); }
.stat b small { font-size: 18px; }
.sr-only { position: absolute !important; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.stat-jump { color: inherit !important; text-decoration: none !important; border-bottom: 2px dotted rgba(var(--acc-rgb),.7); }
.stat-jump:hover { border-bottom-style: solid; }
.tl-help { direction: rtl; text-align: right; font-size: 13px; color: rgba(var(--ink-rgb),.8); margin: 8px 2px 0; }
/* timeline filters: right-aligned inputs / select text */
.st-key-tl_role input, .st-key-tl_sort input, .st-key-tl_gap input, .st-key-tl_query input, .st-key-tl_role [data-baseweb="select"] *, .st-key-tl_sort [data-baseweb="select"] *, .st-key-tl_gap [data-baseweb="select"] *, .st-key-tl_query [data-baseweb="select"] * { direction: rtl; text-align: right; }

/* quick-jump palette (Ctrl+K) */
.qk-overlay { position: fixed; inset: 0; z-index: 100000; background: rgba(var(--shadow-rgb),.35);
  -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px); display: flex; justify-content: center; align-items: flex-start; padding-top: 14vh; }
.qk-card { width: min(560px, 92vw); background: var(--card); color: var(--ink); border: 1px solid var(--glass-line);
  border-radius: 16px; box-shadow: 0 24px 60px rgba(var(--shadow-rgb),.45); overflow: hidden; }
.qk-card input { width: 100%; box-sizing: border-box; padding: 16px 18px; font-size: 17px; border: 0; outline: 0;
  background: transparent; color: var(--ink); border-bottom: 1px solid rgba(var(--ink-rgb),.15); direction: rtl; }
.qk-card ul { list-style: none; margin: 0; padding: 6px; max-height: 50vh; overflow: auto; }
.qk-card li { display: flex; justify-content: space-between; gap: 12px; padding: 9px 12px; border-radius: 10px; cursor: pointer; font-size: 15px; }
.qk-card li small { color: rgba(var(--ink-rgb),.7); white-space: nowrap; }
.qk-card li.on, .qk-card li:hover { background: rgba(var(--acc-rgb),.18); }
.qk-card li.none { cursor: default; color: rgba(var(--ink-rgb),.7); }
/* timeline view */
.tl { direction: rtl; margin: 8px 0; }
.tl-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 0 0 8px; font-size: 13px; }
.tl-key { display: inline-flex; align-items: center; gap: 6px; }
.tl-key i { width: 14px; height: 14px; border-radius: 4px; border: 1.5px solid; display: inline-block; }
.tl-key i.tl-now-key { width: 0; height: 14px; border: none; border-inline-start: 2px dashed rgb(var(--acc-rgb)); border-radius: 0; }
.tl-scroll { max-height: 68vh; overflow: auto; border-radius: 14px; background: var(--glass);
  border: 1px solid var(--glass-line); box-shadow: 0 4px 14px rgba(var(--shadow-rgb),.08); }
.tl-row { display: flex; align-items: stretch; min-width: 900px; border-bottom: 1px solid rgba(var(--ink-rgb),.08); }
.tl-name { flex: 0 0 150px; padding: 8px 12px; font-size: 13px; font-weight: 700; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; position: sticky; right: 0; z-index: 3; background: var(--card); }
.tl-track, .tl-axis { position: relative; flex: 1 1 auto; min-height: 38px; margin-inline: 22px; }
.tl-head { position: sticky; top: 0; z-index: 4; background: var(--card); border-bottom: 2px solid rgba(var(--ink-rgb),.2); }
.tl-head .tl-name { background: var(--card); }
.tl-tick { position: absolute; top: 0; height: 100%; transform: translateX(50%); }
.tl-tick span { display: block; padding: 8px 0; font-size: 12px; font-family: 'Chakra Petch', sans-serif; color: rgba(var(--ink-rgb),.85); }
.tl-grid { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(var(--ink-rgb),.10); }
.tl-now { position: absolute; top: 0; bottom: 0; width: 0; border-inline-start: 2px dashed rgb(var(--acc-rgb)); z-index: 2; }
.tl-bar { position: absolute; top: 6px; bottom: 6px; border: 1.5px solid; border-radius: 8px; overflow: hidden;
  display: flex; align-items: center; padding: 0 6px; font-size: 12px; font-weight: 700; white-space: nowrap; color: var(--ink);
  transition: transform .12s, box-shadow .12s; z-index: 1; }
.tl-bar small { font-weight: 400; opacity: .85; margin-inline-start: 4px; }
.tl-shift { position: absolute; top: 3px; bottom: 3px; border-radius: 8px; background: rgba(var(--acc-rgb),.09);
  border: 1px dashed rgba(var(--acc-rgb),.45); }
.tl-name small { margin-inline-start: 8px; padding: 1px 7px; border-radius: 9px; background: rgba(var(--ink-rgb),.08);
  font-weight: 600; font-size: 11px; }
.tl-gap { position: absolute; top: 8px; bottom: 8px; border-radius: 6px; display: flex; align-items: center; justify-content: center;
  border: 1.5px dashed rgb(var(--acc-rgb)); font-size: 11px; font-weight: 800; color: var(--acc-strong);
  background: repeating-linear-gradient(135deg, rgba(var(--acc-rgb),.30) 0 5px, rgba(var(--acc-rgb),.08) 5px 10px); z-index: 0; }
.tl-key i.tl-gap-key { background: repeating-linear-gradient(135deg, rgba(var(--acc-rgb),.45) 0 3px, rgba(var(--acc-rgb),.1) 3px 6px);
  border: 1.5px dashed rgb(var(--acc-rgb)); }
.tl-gap { overflow: hidden; }
.tl-gap-brk { border-style: solid; }
.tl-gap .tl-brk { position: absolute; right: 0; top: 0; bottom: 0; background: rgba(var(--acc-rgb),.45); border-inline-start: 2px solid rgb(var(--acc-strong-rgb, var(--acc-rgb))); z-index: 0; }
.tl-gap span { position: relative; z-index: 1; }
.tl-group { text-align: right; direction: rtl; min-width: 900px; padding: 7px 14px; font-weight: 800; font-size: 13px; background: rgba(var(--acc-rgb),.10);
  border-bottom: 1px solid rgba(var(--acc-rgb),.35); position: sticky; right: 0; }
.tl-bar[data-fl] { cursor: pointer; }
.tl-bar:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(var(--shadow-rgb),.3); z-index: 5; }
/* coverage meter under the stats row */
.cov { direction: rtl; margin: 0 0 14px; padding: 12px 16px; border-radius: 14px;
  background: var(--glass); border: 1px solid var(--glass-line); box-shadow: 0 4px 14px rgba(var(--shadow-rgb),.06); }
.cov-head { display: flex; justify-content: space-between; align-items: baseline; }
.cov-head b { font-size: 14px; }
.cov-pct { font-family: 'Chakra Petch', sans-serif; font-size: 24px; font-weight: 700; color: var(--acc-strong); }
.cov-bar { height: 10px; border-radius: 6px; overflow: hidden; margin: 6px 0 8px;
  background: repeating-linear-gradient(135deg, rgba(var(--ink-rgb),.30) 0 4px, rgba(var(--ink-rgb),.08) 4px 8px); }
.cov-ok .cov-bar { background: rgba(var(--ink-rgb),.08); }
.cov-bar i { display: block; height: 100%; border-radius: 6px; background: var(--acc-grad); transition: width .6s ease; }
.cov-sub { font-size: 13px; color: rgba(var(--ink-rgb),.85); }
.cov-miss .cov-sub { font-weight: 700; }
/* pre-send check dialog */
.chk-table { width: 100%; border-collapse: collapse; font-size: 14px; margin: 6px 0 4px; }
.chk-table th, .chk-table td { padding: 7px 10px; text-align: right; border-bottom: 1px solid rgba(var(--ink-rgb),.12); }
.chk-table th { background: rgba(var(--ink-rgb),.06); font-weight: 800; }
.chk-more, .chk-note { font-size: 13px; color: rgba(var(--ink-rgb),.8); padding: 4px 2px; }
/* swap popup: the action radio reads right-to-left like the other filters */
[class*="st-key-swap_action_"], [class*="st-key-swap_action_"] [data-testid="stRadio"] { width: 100% !important; direction: rtl; }
[class*="st-key-swap_action_"] [role="radiogroup"] { direction: rtl !important; flex-direction: row !important; justify-content: flex-start !important; }
/* selectbox with a visible label used as a filter: label right-aligned, bold, accent tick (like the radio filters) */
.st-key-avail_role_filter, .st-key-tl_role, .st-key-tl_sort, .st-key-tl_gap, .st-key-tl_query, .st-key-tl_from, .st-key-tl_to { direction: rtl; }
.st-key-avail_role_filter [data-testid="stWidgetLabel"], .st-key-tl_role [data-testid="stWidgetLabel"], .st-key-tl_sort [data-testid="stWidgetLabel"], .st-key-tl_gap [data-testid="stWidgetLabel"], .st-key-tl_query [data-testid="stWidgetLabel"], .st-key-tl_from [data-testid="stWidgetLabel"], .st-key-tl_to [data-testid="stWidgetLabel"] { width: 100%; justify-content: flex-start; }
.st-key-avail_role_filter [data-testid="stWidgetLabel"] p, .st-key-tl_role [data-testid="stWidgetLabel"] p, .st-key-tl_sort [data-testid="stWidgetLabel"] p, .st-key-tl_gap [data-testid="stWidgetLabel"] p, .st-key-tl_query [data-testid="stWidgetLabel"] p, .st-key-tl_from [data-testid="stWidgetLabel"] p, .st-key-tl_to [data-testid="stWidgetLabel"] p {
    font-size: 15px !important; font-weight: 800 !important; color: var(--ink) !important; text-align: right;
    padding-inline-start: 9px; border-inline-start: 4px solid rgb(var(--acc-rgb)); line-height: 1.2; }
/* the build-mode cards row: order is set in Python (LTR columns) — the unassigned screen's page-wide rtl must not flip it */
[data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [class*="st-key-mode_"]) { direction: ltr !important; }
/* native st.metric = the same glass stat card as the header row */
[data-testid="stMetric"] {
    background: var(--glass); border: 1px solid var(--glass-line); border-radius: 14px;
    padding: 12px 10px 10px; box-shadow: 0 4px 14px rgba(var(--shadow-rgb),.08);
    -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
}
[data-testid="stMetricLabel"] p { font-size: 13px !important; color: rgba(var(--ink-rgb),.8) !important; }
[data-testid="stMetricValue"] > div { font-family: 'Chakra Petch', sans-serif; font-weight: 700; font-size: 30px; color: var(--ink); }
/* section heading inside a screen */
.sec-title { direction: rtl; text-align: right; font-size: 18px; font-weight: 800; color: var(--ink);
    margin: 26px 0 12px; padding: 2px 10px 2px 0; border-right: 4px solid rgb(var(--acc-rgb)); }
/* "only short-staffed" toggle: right-aligned, accent track */
.st-key-only_missing_chk, .st-key-only_missing_chk [data-testid="stCheckbox"], .st-key-cb_collapsed_toggle, .st-key-cb_collapsed_toggle [data-testid="stCheckbox"] { width: 100% !important; direction: rtl; margin: 4px 0 6px; }
.st-key-only_missing_chk label, .st-key-cb_collapsed_toggle label { direction: rtl !important; display: flex !important; justify-content: flex-start !important; width: 100% !important; gap: 10px; }
.st-key-only_missing_chk label p, .st-key-cb_collapsed_toggle label p { font-size: 14px !important; font-weight: 400 !important; color: var(--ink) !important; }
/* filter headings (role / terminal view): bold with an accent tick so they read as titles */
.st-key-schedule_term_filter [data-testid="stRadio"] > label p, .st-key-wf_term_filter [data-testid="stRadio"] > label p,
.st-key-wf_role_filter [data-testid="stRadio"] > label p, .st-key-unassigned_inner_view [data-testid="stRadio"] > label p {
    font-size: 15px !important; font-weight: 800 !important; color: var(--ink) !important;
    display: inline-block; padding-inline-start: 9px; border-inline-start: 4px solid rgb(var(--acc-rgb)); line-height: 1.2;
}
/* schedule tab: terminal-view radio sits on the right like the rest */
.st-key-schedule_term_filter, .st-key-wf_term_filter, .st-key-wf_role_filter, .st-key-unassigned_inner_view,
.st-key-schedule_term_filter [data-testid="stRadio"], .st-key-wf_term_filter [data-testid="stRadio"], .st-key-wf_role_filter [data-testid="stRadio"], .st-key-unassigned_inner_view [data-testid="stRadio"] { width: 100% !important; direction: rtl; }
.st-key-schedule_term_filter [data-testid="stRadio"] > label, .st-key-wf_term_filter [data-testid="stRadio"] > label, .st-key-wf_role_filter [data-testid="stRadio"] > label, .st-key-unassigned_inner_view [data-testid="stRadio"] > label { display: block !important; text-align: right !important; width: 100% !important; }
.st-key-schedule_term_filter [data-testid="stRadio"] > div, .st-key-wf_term_filter [data-testid="stRadio"] > div, .st-key-wf_role_filter [data-testid="stRadio"] > div, .st-key-unassigned_inner_view [data-testid="stRadio"] > div,
.st-key-schedule_term_filter [role="radiogroup"], .st-key-wf_term_filter [role="radiogroup"], .st-key-wf_role_filter [role="radiogroup"], .st-key-unassigned_inner_view [role="radiogroup"] {
    direction: rtl !important; flex-direction: row !important; justify-content: flex-start !important; }
.stat-jump-note { display: block; font-family: 'Rubik', sans-serif; font-size: 12px !important; font-weight: 400; color: rgba(var(--ink-rgb),.8); }
@media (max-width: 850px) { .stat-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }

.flight-card {
    direction: rtl;
    text-align: right;
    border: 1px solid rgba(var(--ink-rgb),.09);
    border-radius: 18px;
    background: var(--card);
    box-shadow: 0 3px 14px rgba(var(--shadow-rgb),.3);
    overflow: hidden;
    margin-bottom: 14px;
}

.flight-head {
    background:
      radial-gradient(circle at 12% 10%, rgba(var(--ink-rgb),.08), transparent 24%),
      linear-gradient(90deg, var(--bg) 0%, var(--card-2) 48%, var(--bg) 100%);
    color: var(--ink);
    padding: 13px 16px;
}

.flight-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
}

.flight-name {
    font-size: 21px;
    font-weight: 900;
}

.flight-name::before {
    content: "LY ";
    color: var(--acc);
    font-size: 12px;
    margin-left: 5px;
    letter-spacing: 1px;
}

.flight-meta {
    color: rgba(var(--ink-rgb),.74);
    font-size: 13px;
    font-weight: 800;
}

/* Inside an open flight card: the required-roles line and the ניהול/דיילים
   section titles are plain labels now, not bordered boxes — as boxes they
   carried the same visual weight as the assignment rows themselves and
   broke one flight into 3-5 separate-looking blocks. */
.req-line {
    direction: rtl;
    text-align: right;
    margin: 4px 0 6px;
    color: rgba(var(--acc-rgb),.8);
    font-size: 13px;
    font-weight: 700;
    max-width: 100%;
}

.panel-title {
    direction: rtl;
    text-align: right;
    color: var(--acc);
    font-size: 13px;
    font-weight: 800;
    letter-spacing: .3px;
    margin: 14px 0 6px 0;
    padding: 0 2px 4px;
    border-bottom: 1px solid rgba(var(--acc-rgb),.22);
}

.assignment-line {
    direction: rtl;
    text-align: right;
    padding: 8px 10px;
    margin-bottom: 7px;
    border-radius: 10px;
    border: 1px solid rgba(var(--ink-rgb),.08);
    line-height: 1.45;
    font-size: 14px;
    box-shadow: 0 1px 3px rgba(var(--shadow-rgb),.2);
    font-weight: 750;
}

/* Role colours — colour-blind-safe (2026-09-26: the old red TSA-inspector /
   green TSA-guard / red "missing" rows sat side by side and were hard to tell
   apart with red-green colour blindness). Based on the Okabe-Ito palette:
   the hues also differ in LIGHTNESS, and "missing" additionally has a
   hatched background + dashed edge, so no role is identified by hue alone.
     ר"צ purple · מפקח TSA vermillion · שומר TSA blue · דייל neutral grey
     מתאם תורים yellow · טרייני light cyan · חסר hatched + dashed          */
.role-teamlead {
    background: rgba(var(--r-tl),.14);
    color: var(--r-tl-ink);
    border-right: 5px solid rgb(var(--r-tl));
    font-weight: 900;
}

.role-inspector {
    background: rgba(var(--r-insp),.14);
    color: var(--r-insp-ink);
    border-right: 5px solid rgb(var(--r-insp));
    font-weight: 900;
}

.role-guard {
    background: rgba(var(--r-guard),.15);
    color: var(--r-guard-ink);
    border-right: 5px solid rgb(var(--r-guard));
    font-weight: 900;
}

.role-agent {
    background: rgba(var(--r-agent),.16);
    color: var(--ink);
    border: 1px solid rgba(var(--r-agent),.42);
    border-right: 5px solid rgb(var(--r-agent));
}

.role-queue {
    background: rgba(var(--r-queue),.13);
    color: var(--r-queue-ink);
    border-right: 5px solid rgb(var(--r-queue));
    font-weight: 900;
}

.role-trainee {
    background: rgba(var(--r-train),.10);
    color: var(--r-train-ink);
    border-right: 5px solid rgb(var(--r-train));
    font-weight: 900;
}

.role-missing {
    background: repeating-linear-gradient(135deg,
        rgba(var(--ink-rgb),.07) 0 8px, rgba(var(--ink-rgb),.015) 8px 16px) !important;
    color: var(--ink);
    border: 1px dashed rgba(var(--ink-rgb),.45) !important;
    border-right: 5px dashed var(--ink) !important;
    font-weight: 900;
}

.role-new-assignment {
    background: rgba(var(--acc-rgb),.16);
    color: var(--acc-strong);
    border-right: 5px solid var(--acc);
    border: 2px solid var(--acc);
    font-weight: 900;
    box-shadow: 0 0 0 2px rgba(var(--acc-rgb),.2);
}

.role-transfer-delay {
    background: rgba(var(--r-guard),.14);
    color: var(--r-guard-ink);
    border-right: 5px solid rgb(var(--r-guard));
    border: 2px solid rgb(var(--r-guard));
    font-weight: 900;
    box-shadow: 0 0 0 2px rgba(var(--r-guard),.2);
}

.role-removed-worker {
    background: rgba(var(--bad-rgb),.12);
    color: var(--bad-ink);
    border-right: 5px solid rgb(var(--bad-rgb));
    border: 2px solid rgb(var(--bad-rgb));
    font-weight: 700;
    opacity: 0.75;
    text-decoration: line-through;
}

.small-note {
    direction: rtl;
    text-align: right;
    color: rgba(var(--ink-rgb),.74);
    font-size: 13px;
}

.ops-top-strip {
    direction: rtl;
    text-align: center;
    display: grid;
    grid-template-columns: repeat(4, minmax(120px, 1fr));
    gap: 10px;
    margin: 12px 0 16px 0;
}

.ops-mini-card {
    background: var(--card);
    border: 1px solid rgba(var(--ink-rgb),.09);
    border-top: 3px solid rgba(var(--acc-rgb),.5);
    border-radius: 10px;
    padding: 10px 12px;
    box-shadow: 0 2px 10px rgba(var(--shadow-rgb),.3);
    font-weight: 900;
    color: rgba(var(--ink-rgb),.92);
}

.ops-mini-card span {
    display: block;
    color: rgba(var(--ink-rgb),.74);
    font-size: 12px;
    font-weight: 700;
    margin-top: 2px;
}

.color-legend {
    direction: rtl;
    text-align: right;
    background: var(--card);
    border: 1px solid rgba(var(--ink-rgb),.09);
    border-radius: 16px;
    padding: 14px 16px;
    margin-bottom: 16px;
    box-shadow: 0 2px 12px rgba(var(--shadow-rgb),.3);
}

.color-legend-title {
    font-weight: 900;
    color: rgba(var(--ink-rgb),.92);
    margin-bottom: 10px;
    font-size: 15px;
}

.legend-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 8px 12px;
}

.legend-item {
    display: grid;
    grid-template-columns: 16px 82px 1fr;
    align-items: center;
    gap: 8px;
    background: rgba(var(--ink-rgb),.04);
    border: 1px solid rgba(var(--ink-rgb),.08);
    border-radius: 999px;
    padding: 7px 10px;
    font-size: 13px;
}

.legend-dot {
    width: 13px;
    height: 13px;
    border-radius: 50%;
    display: inline-block;
    border: 1px solid rgba(var(--ink-rgb),.3);
}

.legend-color-name { color: rgba(var(--ink-rgb),.74); font-weight: 900; }
.legend-role-name  { color: rgba(var(--ink-rgb),.92); font-weight: 900; }

.dot-teamlead  { background: rgb(var(--r-tl)); }
.dot-agent     { background: rgb(var(--r-agent)); }
.dot-queue     { background: rgb(var(--r-queue)); }
.dot-inspector { background: rgb(var(--r-insp)); }
.dot-guard     { background: rgb(var(--r-guard)); }
.dot-trainee   { background: rgb(var(--r-train)); }
.dot-missing   { background: repeating-linear-gradient(135deg, var(--ink) 0 2px, transparent 2px 4px); }

body { background: var(--bg); }

/* Swap row styles */
.swap-row-wrap { position: relative; margin-bottom: 7px; }
.swap-row-wrap .assignment-line {
    margin-bottom: 0;
    cursor: default;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
}

.swap-popup {
    direction: rtl;
    background: var(--card-2);
    border: 1.5px solid rgba(var(--acc-rgb),.35);
    border-radius: 14px;
    box-shadow: 0 8px 32px rgba(var(--shadow-rgb),.45), 0 2px 8px rgba(var(--shadow-rgb),.25);
    padding: 16px 18px 14px 18px;
    margin-top: 6px;
    animation: popIn 0.18s cubic-bezier(.4,1.4,.6,1) both;
    z-index: 100;
}

@keyframes popIn {
    from { opacity: 0; transform: translateY(-8px) scale(0.97); }
    to   { opacity: 1; transform: translateY(0)   scale(1);    }
}

.swap-popup-title {
    font-size: 13px;
    font-weight: 900;
    color: rgba(var(--ink-rgb),.92);
    margin-bottom: 10px;
    border-bottom: 1px solid rgba(var(--ink-rgb),.1);
    padding-bottom: 8px;
    text-align: right;
}

.swap-popup-label {
    direction: rtl; text-align: right;
    font-size: 14px;
    font-weight: 800;
    color: var(--ink);
    margin-bottom: 6px;
    margin-top: 14px;
    padding-inline-start: 9px; border-inline-start: 4px solid rgb(var(--acc-rgb)); line-height: 1.2;
}

@media (max-width: 850px) {
    .ops-title      { font-size: 25px; }
    .ops-subtitle   { font-size: 15px; }
    .flight-name    { font-size: 18px; }
    .ops-top-strip  { grid-template-columns: repeat(2, 1fr); }
    .legend-grid    { grid-template-columns: 1fr; }
    .legend-item    { grid-template-columns: 16px 70px 1fr; }
}

/* History: each saved schedule is its own card */
div[class*="st-key-hist_card_"] {
  background: var(--glass); border: 1px solid var(--glass-line);
  border-inline-start: 5px solid rgb(var(--acc-rgb));
  border-radius: 16px; padding: 14px 18px 12px; margin: 0 0 18px;
  box-shadow: 0 6px 18px rgba(var(--shadow-rgb),.10);
  backdrop-filter: blur(10px);
}
div[class*="st-key-hist_card_"]:nth-of-type(even) { border-inline-start-color: rgb(var(--r-agent)); }
div[class*="st-key-hist_card_"]:hover { box-shadow: 0 10px 26px rgba(var(--shadow-rgb),.18); }

/* Build wait banner */
.build-wait { border-radius: 14px; padding: 14px 18px; margin: 8px 0;
  background: var(--glass); border: 1px solid var(--glass-line);
  border-inline-start: 5px solid rgb(var(--acc-rgb)); }
.build-wait .bw-title { font-weight: 800; font-size: 17px; }
.build-wait .bw-sub { font-size: 13px; color: rgba(var(--ink-rgb),.74); margin: 2px 0 10px; }
.build-wait .bw-bar { height: 6px; border-radius: 6px; overflow: hidden; background: rgba(var(--ink-rgb),.10); }
.build-wait .bw-bar i { display: block; height: 100%; width: 40%; border-radius: 6px;
  background: var(--acc-grad); animation: bw-slide 1.3s ease-in-out infinite; }
@keyframes bw-slide { 0% { margin-inline-start: -40%; } 100% { margin-inline-start: 100%; } }
@media (prefers-reduced-motion: reduce) { .build-wait .bw-bar i { animation: none; width: 100%; } }
</style>
"""

# ── Theme tokens (grey + pastel, user choice 2026-09-26) ─────────────────────
# Every colour in CSS above and in the inline styles across the app is a
# var(--…) from here. "*-rgb" / "--r-*" hold "r,g,b" triplets so callers can
# write rgba(var(--ink-rgb),.5). Light/mid run on Streamlit's LIGHT base,
# charcoal on its DARK base (.streamlit/config.toml [theme.light]/[theme.dark])
# so native widgets and data tables (canvas-drawn, not CSS-stylable) match.
THEMES = {
    "light": dict(
        label="בהיר", base="light",
        bg="#e4e6ea", card="#f5f6f8", card2="#eceef1",
        ink="#262c36", ink_rgb="38,44,54",
        acc="#5b5bd6", acc_rgb="91,91,214", acc_strong="#3730a3",
        glow="rgba(91,91,214,.10)",
        r_tl="139,111,224", r_insp="217,115,63", r_guard="74,130,220",
        r_agent="140,150,165", r_queue="226,180,26", r_train="52,169,187",
        r_tl_ink="#2e2350", r_insp_ink="#4a2410", r_guard_ink="#15305c",
        r_queue_ink="#4a3a00", r_train_ink="#0e3d45",
        ok_rgb="46,140,90", ok_ink="#1f6b43", bad_rgb="200,70,50", bad_ink="#8a2a1a",
        t1_rgb="219,84,124", shadow_rgb="110,120,140",
        blobs=("rgba(160,125,255,.55)", "rgba(110,205,235,.42)", "rgba(110,170,255,.52)"),
        grid="rgba(38,44,54,.07)", glass="rgba(255,255,255,.70)", glass_line="rgba(255,255,255,.95)",
        grad=("#5b6cf0", "#8b6fe0", "#2fb0e6"),
    ),
    "mid": dict(
        label="בינוני", base="light",
        bg="#b2b8c1", card="#d4d8de", card2="#c8cdd4",
        ink="#141820", ink_rgb="20,24,32",
        acc="#4c4cc4", acc_rgb="76,76,196", acc_strong="#312e81",
        glow="rgba(76,76,196,.10)",
        r_tl="129,100,214", r_insp="207,104,52", r_guard="64,118,208",
        r_agent="120,130,146", r_queue="216,168,18", r_train="40,152,170",
        r_tl_ink="#2a1f4a", r_insp_ink="#44200c", r_guard_ink="#132c55",
        r_queue_ink="#433400", r_train_ink="#0c3840",
        ok_rgb="40,128,82", ok_ink="#1a5e3a", bad_rgb="190,62,44", bad_ink="#7c2416",
        t1_rgb="208,70,112", shadow_rgb="90,100,120",
        blobs=("rgba(120,95,230,.34)", "rgba(70,170,210,.26)", "rgba(80,130,230,.30)"),
        grid="rgba(20,24,32,.10)", glass="rgba(214,218,224,.72)", glass_line="rgba(255,255,255,.45)",
        grad=("#4f5be0", "#7c5fd6", "#279fd8"),
    ),
    "charcoal": dict(
        label="פחם", base="dark",
        bg="#393c43", card="#454951", card2="#40444b",
        ink="#f3f4f6", ink_rgb="243,244,246",
        acc="#e6c878", acc_rgb="196,181,253", acc_strong="#f3d78e",
        glow="rgba(230,200,120,.08)",
        r_tl="196,178,255", r_insp="255,171,130", r_guard="156,194,255",
        r_agent="184,192,204", r_queue="245,220,122", r_train="143,227,238",
        r_tl_ink="#efeaff", r_insp_ink="#ffeadf", r_guard_ink="#e8f1ff",
        r_queue_ink="#fff6d6", r_train_ink="#e6fbfd",
        ok_rgb="134,219,170", ok_ink="#b9f0cf", bad_rgb="255,150,130", bad_ink="#ffd2c8",
        t1_rgb="244,143,177", shadow_rgb="0,0,0",
        blobs=("rgba(167,139,250,.32)", "rgba(236,140,190,.17)", "rgba(110,170,255,.26)"),
        grid="rgba(255,255,255,.04)", glass="rgba(255,255,255,.06)", glass_line="rgba(255,255,255,.14)",
        grad=("#f3d78e", "#ffb08a", "#c4b2ff"),
    ),
}


def _vars_block(t) -> str:
    return f"""
    --bg: {t["bg"]}; --card: {t["card"]}; --card-2: {t["card2"]};
    --ink: {t["ink"]}; --ink-rgb: {t["ink_rgb"]};
    --acc: {t["acc"]}; --acc-rgb: {t["acc_rgb"]}; --acc-strong: {t["acc_strong"]};
    --glow: {t["glow"]};
    --r-tl: {t["r_tl"]}; --r-insp: {t["r_insp"]}; --r-guard: {t["r_guard"]};
    --r-agent: {t["r_agent"]}; --r-queue: {t["r_queue"]}; --r-train: {t["r_train"]};
    --r-tl-ink: {t["r_tl_ink"]}; --r-insp-ink: {t["r_insp_ink"]}; --r-guard-ink: {t["r_guard_ink"]};
    --r-queue-ink: {t["r_queue_ink"]}; --r-train-ink: {t["r_train_ink"]};
    --ok-rgb: {t["ok_rgb"]}; --ok-ink: {t["ok_ink"]};
    --bad-rgb: {t["bad_rgb"]}; --bad-ink: {t["bad_ink"]};
    --t1-rgb: {t["t1_rgb"]}; --shadow-rgb: {t["shadow_rgb"]};
    --blob-1: {t["blobs"][0]}; --blob-2: {t["blobs"][1]}; --blob-3: {t["blobs"][2]};
    --grid: {t["grid"]}; --glass: {t["glass"]}; --glass-line: {t["glass_line"]};
    --acc-grad: linear-gradient(120deg, {t["grad"][0]}, {t["grad"][1]} 55%, {t["grad"][2]});
    --grad-1: {t["grad"][0]}; --grad-2: {t["grad"][1]}; --grad-3: {t["grad"][2]};
"""


def theme_vars_css(key: str) -> str:
    """Variables for the chosen LIGHT-base palette ("light"/"mid") on :root/.stApp,
    plus the charcoal set under html[data-ischedule-base="dark"] — that attribute
    is set client-side from Streamlit's ACTUAL light/dark base (theme_sync_html),
    so the palette can never disagree with Streamlit's own widgets."""
    _light = key if THEMES.get(key, {}).get("base") == "light" else "light"
    # The data tables are drawn on a canvas in config.toml's [theme.light]
    # colours, shared by light and mid — tone them down to sit on mid's slate.
    _grid = ('html:not([data-ischedule-base="dark"]) [data-testid="stDataFrame"] canvas '
             '{ filter: brightness(.9); }') if _light == "mid" else ""
    return f"""<style>
:root, .stApp {{{_vars_block(THEMES[_light])}}}
{_grid}
html[data-ischedule-base="dark"], html[data-ischedule-base="dark"] .stApp {{{_vars_block(THEMES["charcoal"])}}}
/* while the sync script clicks Streamlit's own Light/Dark menu item, keep the
   menu invisible (it opens for ~100 ms) */
html[data-ischedule-switching] [role="menu"], html[data-ischedule-switching] [data-baseweb="popover"] {{
    opacity: 0 !important; pointer-events: none !important;
}}
</style>"""


def iframe_theme_head(key: str) -> str:
    """Theme variables for an st.components.html iframe (its own document does not
    inherit the page's CSS variables): the same <style> plus a tiny script that
    mirrors the parent's data-ischedule-base attribute."""
    return theme_vars_css(key) + """<script>
    (function () {
      function sync() { try {
        const b = window.parent.document.documentElement.getAttribute('data-ischedule-base');
        if (b) document.documentElement.setAttribute('data-ischedule-base', b);
      } catch (e) {} }
      sync(); setInterval(sync, 700);
    })();
    </script>"""


def theme_sync_html(want: str) -> str:
    """Zero-height script: (1) keeps html[data-ischedule-base] equal to Streamlit's
    real base (localStorage stActiveTheme, "System" → the OS setting); (2) when
    the wanted base ("Light"/"Dark") changes, switches Streamlit to it by clicking
    its own ⋮ → Light/Dark menu item — live, no reload, so the login and the
    loaded files survive. If the menu can't be found nothing happens (the
    palette still follows the real base)."""
    return """<script>
(function () {
  try {
    const P = window.parent, D = P.document, want = "%s";
    const key = () => 'stActiveTheme-' + P.location.pathname + '-v2';
    function current() {
      let v = 'System';
      try { v = JSON.parse(P.localStorage.getItem(key()) || '"System"'); } catch (e) {}
      if (v === 'System') return P.matchMedia('(prefers-color-scheme: dark)').matches ? 'Dark' : 'Light';
      return v;
    }
    function mark() {
      D.documentElement.setAttribute('data-ischedule-base', current() === 'Dark' ? 'dark' : 'light');
    }
    function switchTo(target) {
      const root = D.documentElement;
      root.setAttribute('data-ischedule-switching', '1');
      const done = () => { root.removeAttribute('data-ischedule-switching'); mark(); };
      const btn = D.querySelector('[data-testid="stMainMenuButton"]');
      if (!btn) return done();
      btn.click();
      let tries = 0;
      const t = setInterval(() => {
        const item = D.querySelector('[data-testid="stMainMenuItem-theme-' + target + '"]');
        if (item || ++tries > 25) {
          clearInterval(t);
          if (item) item.click();
          setTimeout(() => {
            if (D.querySelector('[data-testid="stMainMenuItem-theme-Light"]')) btn.click();
            setTimeout(done, 120);
          }, 80);
        }
      }, 60);
    }
    mark();
    // handlers live on the parent page but were created by THIS iframe's script; a rebuilt iframe
    // kills the old ones, so drop them and install fresh copies on every run
    (P.__ischedL || []).forEach((h) => { try { h[0].removeEventListener(h[1], h[2], h[3]); } catch (e) {} });
    P.__ischedL = [];
    if (P.__ischedPoll) { try { P.clearInterval(P.__ischedPoll); } catch (e) {} P.__ischedPoll = null; }
    const on = (t, ev, fn, cap) => { t.addEventListener(ev, fn, cap); P.__ischedL.push([t, ev, fn, cap]); };
    if (P.__ischedWant !== want) {
      const first = P.__ischedWant === undefined;
      P.__ischedWant = want;
      if (current() !== want) setTimeout(() => switchTo(want), first ? 600 : 0);
    }
    // accessible names for icon-only buttons (Streamlit can't set aria-label)
    function label() {
      D.querySelectorAll('button').forEach((b) => {
        const t = (b.innerText || '').trim();
        if (t === '\\u25bc') b.setAttribute('aria-label', 'החלף עובד — פתח רשימת מחליפים');
        else if (t === '\\u25b2') b.setAttribute('aria-label', 'סגור רשימת מחליפים');
      });
    }
    // "next flight" stat → scroll inside Streamlit's own scroll pane (a plain
    // #anchor can't: Streamlit opens links in a new tab) and open the card
    {
      on(D, 'click', (ev) => {
        const a = ev.target.closest && ev.target.closest('a.stat-jump');
        if (!a) return;
        ev.preventDefault(); ev.stopPropagation();
        const el = D.getElementById((a.getAttribute('href') || '').slice(1));
        if (!el) {
          if (!a.parentNode.querySelector('.stat-jump-note')) {
            const n = D.createElement('small'); n.className = 'stat-jump-note';
            n.textContent = 'הטיסה לא מוצגת בתצוגה הנוכחית';
            a.after(n); setTimeout(() => n.remove(), 3500);
          }
          return;
        }
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        const card = el.closest('[class*="st-key-fc_"]');
        const det = card && card.querySelector('details');
        if (det && !det.open) { const sm = det.querySelector('summary'); if (sm) sm.click(); }
      }, true);
    }
    // keyboard: Alt+1..9 switch tabs, Ctrl+K opens a quick-jump palette (tabs + flights)
    {
      const ORDER = ['schedule', 'workflow', 'timeline', 'dashboard', 'unassigned', 'available', 'users', 'employees', 'archive'];
      const navBtn = (k) => D.querySelector('.st-key-nav_tab_' + k + ' button');
      const typing = () => { const a = D.activeElement; return a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA' || a.isContentEditable); };
      let box = null, sel = 0, items = [];
      function close() { if (box) { box.remove(); box = null; } }
      function openFlight(id) {
        let tries = 0;
        const t = setInterval(() => {
          const el = D.getElementById(id);
          if (el || ++tries > 40) {
            clearInterval(t);
            if (!el) return;
            el.scrollIntoView({ behavior: 'smooth', block: 'start' });
            const card = el.closest('[class*="st-key-fc_"]');
            const det = card && card.querySelector('details');
            if (det && !det.open) { const sm = det.querySelector('summary'); if (sm) sm.click(); }
          }
        }, 150);
      }
      function collect() {
        const out = [];
        ORDER.forEach((k, i) => { const b = navBtn(k); if (b) out.push({ kind: 'tab', label: b.innerText.trim(), hint: 'Alt+' + (i + 1), run: () => b.click() }); });
        D.querySelectorAll('[class*="st-key-fc_"]').forEach((c) => {
          const f = c.querySelector('.fc-fill[id^="fl-"]'); const sm = c.querySelector('summary');
          if (f && sm) out.push({ kind: 'flight', label: (sm.querySelector('p') || sm).innerText.split(/\\s+/).join(' ').trim(), hint: 'טיסה', run: () => openFlight(f.id) });
        });
        return out;
      }
      function draw(list, q) {
        const ul = box.querySelector('ul'); ul.innerHTML = '';
        items = list.filter((it) => !q || it.label.toLowerCase().includes(q.toLowerCase())).slice(0, 12);
        if (sel >= items.length) sel = 0;
        items.forEach((it, i) => {
          const li = D.createElement('li'); li.className = i === sel ? 'on' : '';
          li.innerHTML = '<span></span><small></small>'; li.firstChild.textContent = it.label; li.lastChild.textContent = it.hint;
          li.addEventListener('mousedown', (e) => { e.preventDefault(); close(); it.run(); });
          ul.appendChild(li);
        });
        if (!items.length) { const li = D.createElement('li'); li.className = 'none'; li.textContent = 'לא נמצא'; ul.appendChild(li); }
      }
      function openPalette() {
        if (box) return close();
        const all = collect(); sel = 0;
        box = D.createElement('div'); box.className = 'qk-overlay'; box.setAttribute('role', 'dialog'); box.setAttribute('aria-label', 'קפיצה מהירה');
        box.innerHTML = '<div class="qk-card" dir="rtl"><input type="text" placeholder="חפשי טיסה או מסך…  (Esc לסגירה)" aria-label="חיפוש"><ul></ul></div>';
        D.body.appendChild(box);
        const inp = box.querySelector('input'); inp.focus(); draw(all, '');
        inp.addEventListener('input', () => { sel = 0; draw(all, inp.value); });
        inp.addEventListener('keydown', (e) => {
          if (e.key === 'Escape') { close(); }
          else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, items.length - 1); draw(all, inp.value); }
          else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); draw(all, inp.value); }
          else if (e.key === 'Enter') { const it = items[sel]; close(); if (it) it.run(); }
        });
        box.addEventListener('mousedown', (e) => { if (e.target === box) close(); });
      }
      // timeline bar click -> open that flight's card on the schedule tab
      on(D, 'click', (e) => {
        const bar = e.target.closest && e.target.closest('.tl-bar[data-fl]');
        if (!bar) return;
        const b = navBtn('schedule'); if (b) b.click();
        openFlight('fl-' + bar.getAttribute('data-fl'));
      }, true);
      on(D, 'keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); openPalette(); return; }
        if (e.altKey && !e.ctrlKey && !e.metaKey && /^[1-9]$/.test(e.key) && !typing()) {
          const b = navBtn(ORDER[Number(e.key) - 1]); if (b) { e.preventDefault(); b.click(); }
        }
      }, true);
    }
    P.__ischedPoll = P.setInterval(() => { mark(); label(); }, 500);
  } catch (e) {}
})();
</script>""" % want


# (x %, y %, dx vw, dy vh, size px, duration s, delay s, colour, flip) — hand-placed so the
# flights cross the page at different heights/speeds and never bunch up
_PAPER_PLANES = [
    (-8,  78, 122, -62, 46, 17,  0, "rgb(var(--r-tl))",    False),
    (108, 22, -122, 48, 34, 21,  3, "rgb(var(--r-insp))",  True),
    (-8,  30, 122,  30, 28, 14,  6, "var(--acc)",          False),
    (108, 88, -122,-70, 40, 19,  9, "rgb(var(--r-guard))", True),
    (-8,  55, 122, -20, 24, 25, 12, "rgb(var(--r-train))", False),
    (108, 62, -122, 22, 30, 16, 15, "var(--grad-2)",       True),
]


def paper_planes_html(subtle: bool = False) -> str:
    """Decorative paper planes flying across the whole page (fixed, behind content)."""
    out = []
    for x, y, dx, dy, size, dur, delay, colour, flip in _PAPER_PLANES:
        out.append(
            f'<div class="pp{" pp-flip" if flip else ""}" style="--x:{x}vw;--y:{y}vh;--dx:{dx}vw;--dy:{dy}vh;'
            f'--s:{size}px;--dur:{dur}s;--delay:{delay}s;--c:{colour}">'
            f'<div class="pp-a"><div class="pp-b"></div></div></div>'
        )
    _op = ' style="opacity:.45"' if subtle else ""   # subtle: dimmer, for content-heavy screens
    return f'<div class="paper-planes" aria-hidden="true"{_op}>' + "".join(out) + "</div>"


def emblem_svg(uid: str = "e") -> str:
    """The iSchedule emblem, drawn in SVG (theme-aware, sharp at any size): a pastel
    globe whose meridians slowly turn, circled by an orbit swoosh with a plane flying
    around it (behind the globe on the far half, in front on the near half) trailing a
    comet tail. `uid` keeps the gradient/clip ids unique when several are on one page."""
    plane = ('<g id="pl%s"><path d="M19,0 L-11,-10 L-4.5,0 L-11,10 Z" fill="#fff" stroke="var(--acc)" stroke-width="1.1" '
             'stroke-linejoin="round"/><animateMotion dur="8s" repeatCount="indefinite" rotate="auto" '
             'path="M8,100 A92,26 0 0,1 192,100 A92,26 0 0,1 8,100 Z"/></g>') % uid
    orbit = ('<ellipse cx="100" cy="100" rx="92" ry="26" fill="none" stroke="url(#or%s)" stroke-width="3.2" '
             'stroke-linecap="round"/>') % uid
    tail = ('<ellipse cx="100" cy="100" rx="92" ry="26" fill="none" stroke="var(--acc)" stroke-width="4" '
            'stroke-linecap="round" stroke-dasharray="64 336" stroke-opacity=".95"><animate attributeName="stroke-dashoffset" '
            'values="64;-336" dur="8s" repeatCount="indefinite"/></ellipse>')
    merid = "".join(
        '<ellipse cx="100" cy="100" rx="0" ry="58" fill="none" stroke="#fff" stroke-opacity=".42" stroke-width="1.3">'
        '<animate attributeName="rx" values="0;41;58;41;0" dur="6s" begin="-%ss" repeatCount="indefinite"/></ellipse>' % b
        for b in ("0", "2", "4"))
    parallels = "".join(
        '<ellipse cx="100" cy="%d" rx="%d" ry="%d" fill="none" stroke="#fff" stroke-opacity=".3" stroke-width="1.1"/>' % v
        for v in ((100, 58, 11), (73, 50, 8), (127, 50, 8)))
    return re.sub(r"\s+", " ", (
        '<svg class="emblem" viewBox="0 30 200 140" role="img" aria-label="iSchedule" xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink"><defs>'
        '<radialGradient id="gl%(u)s" cx="36%%" cy="30%%" r="85%%"><stop offset="0" style="stop-color:#fff"/>'
        '<stop offset=".3" style="stop-color:var(--grad-3)"/><stop offset=".72" style="stop-color:var(--grad-2)"/>'
        '<stop offset="1" style="stop-color:var(--grad-1)"/></radialGradient>'
        '<radialGradient id="hl%(u)s"><stop offset="0" style="stop-color:var(--acc);stop-opacity:.45"/>'
        '<stop offset="1" style="stop-color:var(--acc);stop-opacity:0"/></radialGradient>'
        '<linearGradient id="or%(u)s" x1="0" x2="1" y1="0" y2="0"><stop offset="0" style="stop-color:var(--grad-1)"/>'
        '<stop offset=".5" style="stop-color:var(--grad-2)"/><stop offset="1" style="stop-color:var(--grad-3)"/></linearGradient>'
        '<clipPath id="gc%(u)s"><circle cx="100" cy="100" r="58"/></clipPath>'
        '<clipPath id="cb%(u)s"><rect x="-30" y="-30" width="260" height="130"/></clipPath>'
        '<clipPath id="cf%(u)s"><rect x="-30" y="100" width="260" height="130"/></clipPath>'
        '%(plane)s</defs>'
        '<circle cx="100" cy="100" r="86" fill="url(#hl%(u)s)"/>'
        '<g transform="rotate(-22 100 100)"><g clip-path="url(#cb%(u)s)" opacity=".55">%(orbit)s%(tail)s'
        '<use href="#pl%(u)s" xlink:href="#pl%(u)s"/></g></g>'
        '<circle cx="100" cy="100" r="58" fill="url(#gl%(u)s)"/>'
        '<g clip-path="url(#gc%(u)s)">%(merid)s%(parallels)s'
        '<ellipse cx="78" cy="72" rx="26" ry="15" fill="#fff" opacity=".28" transform="rotate(-28 78 72)"/></g>'
        '<circle cx="100" cy="100" r="58" fill="none" stroke="#fff" stroke-opacity=".35" stroke-width="1.2"/>'
        '<g transform="rotate(-22 100 100)"><g clip-path="url(#cf%(u)s)">%(orbit)s%(tail)s'
        '<use href="#pl%(u)s" xlink:href="#pl%(u)s"/></g></g></svg>'
    ) % dict(u=uid, plane=plane, orbit=orbit, tail=tail, merid=merid, parallels=parallels))


def upload_hero_html(steps) -> str:
    """Upload-screen hero: glowing logo, gradient title, flight arc with a gliding plane
    and the 3-step tracker. steps: [(label, "done"|"cur"|"todo")] in reading order."""
    items = []
    for i, (label, st_) in enumerate(steps):
        mark = "✓" if st_ == "done" else str(i + 1)
        items.append(f'<div class="up-step {st_}"><i>{mark}</i><span>{label}</span></div>')
        if i < len(steps) - 1:
            items.append(f'<div class="up-line {"done" if st_ == "done" else ""}"></div>')
    return f"""
<div class="up-hero">
  <div class="up-emblem"><div class="up-ring2"></div>{emblem_svg("up")}</div>
  <div class="up-title">iSchedule</div>
  <div class="up-tag">AIRPORT OPERATIONS · SMART SCHEDULING</div>
  <div class="up-arc" aria-hidden="true"><svg viewBox="0 0 560 60" preserveAspectRatio="none"><path d="M6,48 Q280,-14 554,48"/></svg><span class="hero-plane">✈</span></div>
  <div class="up-steps">{"".join(items)}</div>
</div>
"""


def hero_html(date_text: str, time_text: str) -> str:
    """Top header: radar + name (right), live chip + clock (left), runway lights."""
    return f"""
<div class="hero">
  <div class="hero-row">
    <div class="hero-brand">
      <div class="hero-emblem">{emblem_svg("hd")}</div>
      <div><div class="hero-title">iSchedule</div><div class="hero-sub">סידור עבודה? קטן עלי!</div></div>
    </div>
    <div class="hero-flight" aria-hidden="true">
      <svg viewBox="0 0 520 60" width="520" height="60"><path d="M10,50 Q260,-12 510,50" /></svg>
      <span class="hero-plane">✈</span>
    </div>
    <div class="hero-meta">
      <div class="hero-live"><i></i>מערכת פעילה</div>
      <div class="hero-clock">{date_text} <b>{time_text}</b></div>
    </div>
  </div>
  <div class="hero-runway"></div>
</div>
"""


def stats_html(items) -> str:
    """items: [(label, value, variant)] — variant "", "gold" or "warn"."""
    cells = "".join(
        f'<div class="stat{" stat-" + v if v else ""}"><span>{label}</span><b>{value}</b></div>'
        for label, value, v in items
    )
    return f'<div class="stat-grid">{cells}</div>'

