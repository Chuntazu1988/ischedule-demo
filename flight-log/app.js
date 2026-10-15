import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, onAuthStateChanged, GoogleAuthProvider, signInWithPopup, signOut } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { initializeFirestore, persistentLocalCache, persistentMultipleTabManager, doc, collection,
         onSnapshot, setDoc, addDoc, deleteDoc } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { firebaseConfig } from "./firebase-config.js";

const ROLES = ["ראש צוות", "דייל", "מתאם תורים", "מפקח TSA"];
const LEAD = "ראש צוות";
const TIERS = [[100, 1200], [50, 900], [25, 600]];   // מינימום טיסות בחודש → גמול בשקלים

const S = { phase: "loading", uid: null, name: "", onlyLead: true, /* קבוע: רק ראש צוות נספר */ flights: [], view: "month",
            cursor: startOfDay(new Date()), confirmId: null, msg: "", busy: false };
let auth, db, unsubProfile = null, unsubFlights = null;

function startOfDay(d) { const x = new Date(d); x.setHours(0,0,0,0); return x; }
const iso = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const fromIso = s => { const [y,m,d] = s.split("-").map(Number); return new Date(y, m-1, d); };
const fmt = (d, o) => d.toLocaleDateString("he-IL", o);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const $ = id => document.getElementById(id);

function range() {
  const c = S.cursor;
  if (S.view === "day") return [new Date(c), new Date(c)];
  if (S.view === "week") { const a = new Date(c); a.setDate(a.getDate() - a.getDay()); const b = new Date(a); b.setDate(b.getDate() + 6); return [a, b]; }
  return [new Date(c.getFullYear(), c.getMonth(), 1), new Date(c.getFullYear(), c.getMonth()+1, 0)];
}
function title() {
  const [a, b] = range();
  if (S.view === "day") return fmt(a, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  if (S.view === "week") return `${fmt(a, {day:"numeric", month:"short"})} – ${fmt(b, {day:"numeric", month:"short", year:"numeric"})}`;
  return fmt(a, { month: "long", year: "numeric" });
}
const inRange = (f, a, b) => f.date >= iso(a) && f.date <= iso(b);
const showNum = n => /^\d+$/.test(n) ? "LY" + n : n;
const bonusFor = n => (TIERS.find(([min]) => n >= min) || [0, 0])[1];

// ---------- תצוגה ----------
function render() {
  const app = $("app");
  if (S.phase === "loading") { app.innerHTML = `<div class="center">טוען…</div>`; return; }
  if (S.phase === "config") {
    app.innerHTML = `<div class="center"><b>האפליקציה עוד לא חוברה למסד הנתונים</b><br>פתח את הקובץ firebase-config.js והדבק בו את הפרטים מ-Firebase (ראה הוראות ב-README).</div>`; return;
  }
  if (S.phase === "auth") { renderAuth(app); return; }
  if (S.phase === "name") {
    app.innerHTML = `<div class="welcome"><h2>ברוך הבא ליומן הטיסות ✈️</h2><p>איך קוראים לך?</p>
      <form id="nameForm"><label for="nameIn">השם שלך</label>
      <input id="nameIn" value="${esc(S.name)}" maxlength="40" autocomplete="name" placeholder="לדוגמה: דנה">
      <div class="err" id="nameErr">${esc(S.msg)}</div>
      <div class="actions"><button class="primary" type="submit">המשך</button></div></form></div>`;
    $("nameForm").onsubmit = saveName; return;
  }

  const [a, b] = range();
  const counts = f => f.role === LEAD;
  const items = S.flights.filter(f => inRange(f, a, b)).sort((x, y) => x.date.localeCompare(y.date) || showNum(x.num).localeCompare(showNum(y.num)));
  const ms = new Date(S.cursor.getFullYear(), S.cursor.getMonth(), 1), me = new Date(S.cursor.getFullYear(), S.cursor.getMonth()+1, 0);
  const mc = S.flights.filter(f => inRange(f, ms, me) && counts(f)).length;
  const bonus = bonusFor(mc);
  const next = [...TIERS].reverse().find(([min]) => mc < min);
  const prevMin = [0, ...TIERS.map(t => t[0]).reverse()].filter(m => m <= mc).pop();
  const pct = next ? Math.min(100, ((mc - prevMin) / (next[0] - prevMin)) * 100) : 100;

  let list = "";
  if (!items.length) list = `<div class="empty">אין טיסות בתקופה הזו.<br>לחץ על + כדי להוסיף.</div>`;
  else {
    let last = "";
    for (const f of items) {
      if (S.view !== "day" && f.date !== last) { list += `<div class="day-head">${esc(fmt(fromIso(f.date), {weekday:"long", day:"numeric", month:"numeric"}))}</div>`; last = f.date; }
      list += `<div class="flight"><div><b>${esc(showNum(f.num))}</b><span class="tag ${f.role === LEAD ? "" : "other"}">${esc(f.role)}</span></div>
        <button class="del" data-id="${esc(f.id)}">${S.confirmId === f.id ? "למחוק? לחץ שוב" : "מחיקה"}</button></div>`;
    }
  }

  app.innerHTML = `
    <header><h1>✈️ יומן טיסות</h1><button class="who" id="who" aria-label="שינוי שם">${esc(S.name)} ✎</button></header>
    <div class="tabs" id="tabs">${[["day","יום"],["week","שבוע"],["month","חודש"]].map(([v,t]) => `<button data-v="${v}" class="${S.view===v?"on":""}">${t}</button>`).join("")}</div>
    <div class="nav"><button id="next" aria-label="הבא">›</button><div class="title">${esc(title())}</div><button id="prev" aria-label="קודם">‹</button></div>
    <div class="card">
      <div class="row"><div><div class="muted">טיסות בתקופה</div><div class="big">${items.length}</div></div>
        <div style="text-align:left"><div class="muted">גמול ${esc(fmt(ms, {month:"long"}))}</div><div class="big" style="color:var(--good)">₪${bonus}</div></div></div>
      <div class="bar"><div style="width:${pct}%"></div></div>
      <div class="muted">${mc} טיסות נספרות החודש · ${next ? `עוד ${next[0]-mc} כדי להגיע ל-₪${next[1]}` : "הגעת לגמול המקסימלי 🎉"}</div>
      <div class="steps"><span>0</span><span>25 · ₪600</span><span>50 · ₪900</span><span>100 · ₪1200</span></div>
    </div>
    <div class="card">${list}</div>
    <div class="card"><div class="muted">לגמול נספרות רק טיסות בתפקיד ראש צוות.</div>
      <button class="link" id="logout">התנתקות</button></div>
    <button class="fab" id="add" aria-label="הוספת טיסה">+</button>`;

  $("who").onclick = () => { S.phase = "name"; S.msg = ""; render(); };
  $("tabs").onclick = e => { const v = e.target.dataset.v; if (v) { S.view = v; S.confirmId = null; render(); } };
  $("next").onclick = () => shift(1);
  $("prev").onclick = () => shift(-1);
  $("add").onclick = openAdd;
  $("logout").onclick = () => signOut(auth);
  app.querySelectorAll(".del").forEach(btn => btn.onclick = () => deleteFlight(btn.dataset.id));
}

function renderAuth(app) {
  app.innerHTML = `<div class="welcome" style="text-align:center"><h2>✈️ יומן טיסות</h2>
    <p>תיעוד הטיסות והגמול החודשי שלך, שמור בענן ונגיש מכל טלפון.</p>
    <div class="actions" style="margin-top:28px"><button class="primary" id="google" ${S.busy ? "disabled" : ""}>כניסה עם Google</button></div>
    <div class="err" id="authErr">${esc(S.msg)}</div>
    <p class="muted" style="margin-top:16px">אין צורך בסיסמה או בהרשמה. בטלפון חדש נכנסים עם אותו חשבון Google וכל הנתונים שם.</p></div>`;
  $("google").onclick = async () => {
    S.busy = true; S.msg = ""; renderAuth(app);
    try { await signInWithPopup(auth, new GoogleAuthProvider()); }
    catch (e) {
      S.msg = e.code === "auth/popup-closed-by-user" || e.code === "auth/cancelled-popup-request" ? ""
            : e.code === "auth/network-request-failed" ? "אין חיבור לאינטרנט"
            : e.code === "auth/unauthorized-domain" ? "הכתובת הזו לא הוגדרה ב-Firebase (ראה README, שלב 4)"
            : e.code === "auth/operation-not-allowed" ? "כניסה עם Google לא הופעלה ב-Firebase (ראה README)"
            : "הכניסה נכשלה. נסה שוב.";
    }
    S.busy = false; if (S.phase === "auth") renderAuth(app);
  };
}

function shift(dir) {
  const c = S.cursor;
  if (S.view === "day") c.setDate(c.getDate() + dir);
  else if (S.view === "week") c.setDate(c.getDate() + 7 * dir);
  else S.cursor = new Date(c.getFullYear(), c.getMonth() + dir, 1);
  S.confirmId = null; render();
}

// ---------- שמירה ----------
const profileRef = () => doc(db, "users", S.uid);
const flightsCol = () => collection(db, "users", S.uid, "flights");

async function saveProfile() {
  try { await setDoc(profileRef(), { name: S.name, onlyLead: S.onlyLead }); } catch (e) { toastErr(e); }
}
async function saveName(ev) {
  ev.preventDefault();
  const v = $("nameIn").value.trim();
  if (!v) { $("nameErr").textContent = "כתוב את השם שלך כדי להמשיך"; return; }
  S.name = v; S.phase = "app"; render();
  await saveProfile();
}
async function deleteFlight(id) {
  if (S.confirmId !== id) { S.confirmId = id; render(); return; }
  S.confirmId = null;
  try { await deleteDoc(doc(db, "users", S.uid, "flights", id)); } catch (e) { toastErr(e); }
}
function toastErr() {
  const t = document.createElement("div");
  t.textContent = "השמירה נכשלה. בדוק חיבור ונסה שוב.";
  t.style.cssText = "position:fixed;inset-inline:16px;bottom:100px;background:var(--bad);color:#fff;padding:12px;border-radius:10px;text-align:center;z-index:9";
  document.body.appendChild(t); setTimeout(() => t.remove(), 4000);
}

function openAdd() {
  let dlg = $("dlg"); if (dlg) dlg.remove();
  dlg = document.createElement("dialog"); dlg.id = "dlg";
  let lastRole = LEAD; try { lastRole = localStorage.getItem("lastRole") || LEAD; } catch {}
  dlg.innerHTML = `<form id="form"><h2>הוספת טיסה</h2>
    <label for="fNum">מספר טיסה</label>
    <div class="numrow" dir="ltr"><span class="pre">LY</span>
      <input id="fNum" required placeholder="001" inputmode="numeric" pattern="[0-9]{1,4}" maxlength="4" autocomplete="off"></div>
    <label for="fDate">תאריך</label>
    <input id="fDate" type="date" required value="${iso(S.cursor)}">
    <label for="fRole">תפקיד</label>
    <select id="fRole">${ROLES.map(r => `<option ${r === lastRole ? "selected" : ""}>${r}</option>`).join("")}</select>
    <div class="actions"><button type="button" class="cancel" id="cancel">ביטול</button><button type="submit" class="save">שמירה</button></div></form>`;
  document.body.appendChild(dlg);
  $("cancel").onclick = () => dlg.close();
  $("form").onsubmit = ev => {
    ev.preventDefault();
    const f = { num: "LY" + $("fNum").value.replace(/\D/g, ""), date: $("fDate").value, role: $("fRole").value, createdAt: Date.now() };
    if (f.num === "LY" || !f.date) return;
    try { localStorage.setItem("lastRole", f.role); } catch {}
    dlg.close();
    S.cursor = fromIso(f.date);
    addDoc(flightsCol(), f).catch(toastErr);   // עובד גם בלי רשת ומסתנכרן אחר כך
  };
  dlg.showModal(); $("fNum").focus();
}

// ---------- התחלה ----------
function stopListening() { unsubProfile?.(); unsubFlights?.(); unsubProfile = unsubFlights = null; }

function start() {
  if (!firebaseConfig || !firebaseConfig.apiKey || firebaseConfig.apiKey.startsWith("PASTE")) { S.phase = "config"; render(); return; }
  const fbApp = initializeApp(firebaseConfig);
  auth = getAuth(fbApp);
  db = initializeFirestore(fbApp, { localCache: persistentLocalCache({ tabManager: persistentMultipleTabManager() }) });

  onAuthStateChanged(auth, user => {
    stopListening();
    if (!user) { S.uid = null; S.flights = []; S.name = ""; S.phase = "auth"; render(); return; }
    S.uid = user.uid; S.phase = "loading"; render();
    unsubProfile = onSnapshot(profileRef(), snap => {
      const d = snap.exists() ? snap.data() : null;
      if (d && d.name) { S.name = d.name; if (S.phase === "loading" || S.phase === "auth") S.phase = "app"; }
      else if (S.phase === "loading") S.phase = "name";
      render();
    });
    unsubFlights = onSnapshot(flightsCol(), snap => {
      S.flights = snap.docs.map(d => ({ id: d.id, ...d.data() }));
      if (S.phase === "app") render();
    });
  });
}
start();
