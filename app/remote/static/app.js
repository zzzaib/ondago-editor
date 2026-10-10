/* OnDaGo phone app - talks to the OnDaGo Editor running on your laptop. No build step, no trackers. */
"use strict";

// ------------------------------------------------------------------ tiny helpers
const $ = (s, el = document) => el.querySelector(s);
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v; // only ever used with constant strings
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
function fill(el, ...kids) { // replaceChildren that accepts nested arrays / nulls like h()
  el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false)
    .map(k => k instanceof Node ? k : document.createTextNode(String(k))));
}
const fmt = t => { t = Math.max(0, Math.floor(t || 0)); const m = Math.floor(t / 60), s = t % 60;
  return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`; };
const mb = b => b > 1e9 ? (b / 1e9).toFixed(2) + " GB" : (b / 1e6).toFixed(1) + " MB";
let toastTimer;
function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = "", 3200);
}
function sw(checked, onchange, disabled) {
  const i = h("input", { type: "checkbox", disabled });
  i.checked = !!checked; i.addEventListener("change", () => onchange(i.checked));
  return h("label", { class: "switch" }, i, h("span"));
}
function sel(value, choices, onchange, attrs) {
  const s = h("select", attrs || {}, choices.map(([v, l]) => h("option", { value: v }, l)));
  s.value = value ?? choices[0][0]; s.addEventListener("change", () => onchange(s.value)); return s;
}

// ------------------------------------------------------------------ API
class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
async function api(method, url, body, raw, fast) {
  const opt = { method, headers: { "X-OnDaGo": "1" }, credentials: "same-origin" };
  if (fast) { opt.credentials = "omit"; opt.headers.Authorization = "Bearer " + fast.token; url = fast.base + url; }
  if (raw !== undefined) { opt.body = raw; opt.headers["Content-Type"] = "application/octet-stream"; }
  else if (body !== undefined) { opt.body = JSON.stringify(body); opt.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(url, opt); } catch (e) { setDev("off"); throw new ApiError(0, "Can't reach your laptop. Is it on and online?"); }
  if (r.status === 401 && !url.startsWith("/api/login") && !url.startsWith("/api/pair")) { S.authed = false; render(); throw new ApiError(401, "Please log in"); }
  let data = null;
  try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok && r.status !== 409) throw new ApiError(r.status, (data && data.detail) || r.statusText);
  if (r.status === 409 && !(data && "received" in data)) throw new ApiError(409, (data && data.detail) || "Conflict");
  return data;
}

// ------------------------------------------------------------------ state
const S = { authed: null, status: null, jobs: [], job: null, tab: "watch", tasks: {}, opts: null, pick: null,
  presets: null, adv: false,
  src: "phone", browse: null, upload: null, playerKind: "youtube", es: null, logOpen: false,
  net: { via: "", fast: null, same: false, lan: [], code: "" } };

const KINDS = [["idea", "💡 Idea"], ["question", "❓ Question"], ["warning", "❗ Warning"], ["emphasis", "💥 Emphasis"],
  ["love", "❤️ Love"], ["best", "👑 Best"], ["amazing", "⭐ Amazing"], ["magic", "✨ Magic"], ["point_me", "👉 Me"],
  ["yes", "✅ Yes"], ["no", "❌ No"], ["money", "💰 Money"], ["growth", "📈 Growth"], ["fast", "🚀 Fast"],
  ["energy", "⚡ Energy"], ["time", "⏰ Time"], ["hot", "🔥 Hot"], ["goal", "🎯 Goal"], ["look", "👀 Look"],
  ["confused", "😵 Confused"], ["happy", "😄 Happy"], ["music", "🎵 Music"], ["search", "🔍 Search"],
  ["win", "🏆 Win"], ["wow", "💬 WOW"]];
const STAGE = { new: ["Queued", "run"], needs_broll: ["Needs you", "warn"], analyzed: ["Ready to render", "warn"],
  rendered: ["Done", "ok"] };

function setDev(mode, name) {
  const d = $("#dev"); d.className = "dev " + mode;
  if (name) $("#devname").textContent = name;
}

// ------------------------------------------------------------------ live updates (SSE + fallback poll)
function connectEvents() {
  if (S.es) S.es.close();
  try {
    S.es = new EventSource("/api/events");
    S.es.onopen = () => setDev(anyBusy() ? "busy" : "on");
    S.es.onerror = () => setDev("off");
    S.es.onmessage = e => {
      const ev = JSON.parse(e.data);
      if (ev.type === "task") onTask(ev.task);
      else if (ev.type === "log" && S.job && ev.job === S.job.name) {
        S.job.log = (S.job.log || []).concat(ev.line).slice(-200);
        const lg = $("#log"); if (lg) { lg.textContent = S.job.log.join("\n"); lg.scrollTop = lg.scrollHeight; }
      }
    };
  } catch (e) { /* old browser: polling below */ }
}
const anyBusy = () => Object.values(S.tasks).some(t => t.state === "running" || t.state === "queued");
function onTask(t) {
  const prev = S.tasks[t.job];
  S.tasks[t.job] = t;
  setDev(anyBusy() ? "busy" : "on");
  const j = S.jobs.find(x => x.name === t.job); if (j) j.task = t;
  if (S.job && S.job.name === t.job) {
    S.job.task = t;
    patchProgress();
    if ((t.state === "done" || t.state === "failed") && (!prev || prev.state !== t.state)) {
      if (t.state === "failed") toast(`${t.label} failed: ${t.error.split("\n").pop()}`, true);
      else toast(`${t.label} finished`);
      loadJob(t.job, true);
    }
  } else if (location.hash === "#/" || location.hash === "") {
    if (t.state === "done" || t.state === "failed") loadHome(); else patchHomeCard(t);
  }
}
setInterval(async () => { // safety net for networks that kill event streams
  if (!S.authed || document.hidden) return;
  if (anyBusy()) { try { const st = await api("GET", "/api/status"); for (const t of st.tasks) onTask(t); } catch (e) { /* ignore */ } }
}, 6000);

// ------------------------------------------------------------------ bottom nav
function navOn(route) {
  const bar = $("#tabbar"); if (!bar) return;
  bar.classList.toggle("hidden", !route);
  document.body.classList.toggle("nonav", !route);
  const k = route === "job" ? "home" : route;
  for (const a of bar.querySelectorAll("a")) a.classList.toggle("on", a.dataset.k === k);
}

// ------------------------------------------------------------------ router
window.addEventListener("hashchange", render);
async function render() {
  const app = $("#app");
  if (S.authed === null) {
    try { const me = await (await fetch("/api/me", { credentials: "same-origin" })).json(); S.authed = me.ok; }
    catch (e) { app.replaceChildren(h("div", { class: "center" }, h("h2", {}, "Laptop offline"),
      h("p", { class: "muted" }, "Make sure the laptop is on, OnDaGo Editor is running and you're connected (Tailscale / Wi-Fi)."),
      h("button", { class: "btn", onclick: () => location.reload() }, "Try again"))); setDev("off"); return; }
  }
  const pair = new URLSearchParams(location.hash.split("?")[1] || "").get("pair") || (location.hash.match(/pair=([A-Z0-9]+)/) || [])[1];
  if (pair) {
    try { await api("POST", "/api/pair", { code: pair }); S.authed = true; toast("Phone paired!"); history.replaceState(null, "", "#/"); }
    catch (e) { toast(e.message, true); history.replaceState(null, "", "#/"); }
  }
  navOn(S.authed ? (location.hash.replace(/^#\/?/, "").split("/")[0] || "home") : "");
  if (!S.authed) { app.replaceChildren(viewLogin()); return; }
  if (!S.status) { await loadStatus(); connectEvents(); setupNet(); }
  const route = location.hash.replace(/^#\/?/, "").split("/");
  if (route[0] === "job" && route[1]) { await loadJob(decodeURIComponent(route[1])); return; }
  if (route[0] === "new") { app.replaceChildren(await viewNew()); return; }
  if (route[0] === "settings") { app.replaceChildren(viewSettings()); return; }
  S.job = null;
  await loadHome();
}

// ------------------------------------------------------------------ same Wi-Fi = direct, fast, no internet data
async function probe(base, id) {
  const c = new AbortController(); const t = setTimeout(() => c.abort(), 1500);
  try { const r = await fetch(base + "/api/ping", { signal: c.signal, mode: "cors", credentials: "omit" });
    const j = await r.json(); return j.app === "ondago" && j.id === id; }
  catch (e) { return false; } finally { clearTimeout(t); }
}
async function setupNet() {
  let info; try { info = await api("GET", "/api/lan"); } catch (e) { return; }
  S.net = { via: info.via, same: info.same_network, lan: info.lan || [], code: info.code, fast: null };
  // opened through Tailscale / the public link over plain http: talk to the laptop's Wi-Fi address directly
  if (info.via !== "lan" && location.protocol === "http:") {
    for (const u of S.net.lan) { if (await probe(u, info.id)) { S.net.fast = { base: u, token: info.token }; break; } }
  }
  showNet();
}
const isLocal = () => S.net.via === "lan" || !!S.net.fast;
function showNet() {
  let b = $("#netb");
  if (!b) { b = h("span", { id: "netb", class: "netb" }); $("#dev").append(b); }
  const v = isLocal() ? ["⚡ Wi-Fi", "Same Wi-Fi: direct connection, full speed, no internet data"] :
    S.net.via === "tailscale" ? ["🔒 Tailscale", "Private Tailscale connection (direct when on the same Wi-Fi)"] :
    S.net.via === "public" ? ["🌍 Internet", "Through the public link"] : ["", ""];
  b.textContent = v[0]; b.title = v[1];
  if (location.hash === "#/" || location.hash === "") loadHome();
}
// heavy transfers (uploads, videos, downloads) go straight to the laptop when we're on its Wi-Fi
const fastMedia = (path, full) => {
  let u = path; if (full !== undefined) u = u.replace(/q=preview/, isLocal() || S.hd ? "q=full" : "q=preview");
  if (!S.net.fast) return u;
  return S.net.fast.base + u + (u.includes("?") ? "&" : "?") + "t=" + encodeURIComponent(S.net.fast.token);
};
function netBanner() {
  if (isLocal() || S.net.via !== "public" || !S.net.same || !S.net.lan.length || !S.net.code) return null;
  return h("div", { class: "card", style: "border-color:#3E2F7A;background:#1D1838" },
    h("b", {}, "📶 You're on the same Wi-Fi as your laptop"),
    h("p", { class: "small muted", style: "margin:6px 0 10px" }, "Switch to the direct local connection: uploads and videos go straight to the laptop at full speed without using internet data."),
    h("button", { class: "btn primary", onclick: () => { location.href = `${S.net.lan[0]}/#/?pair=${S.net.code}`; } }, "⚡ Switch to fast local mode"));
}

async function loadStatus() {
  try { S.status = await api("GET", "/api/status"); for (const t of S.status.tasks) S.tasks[t.job] = S.tasks[t.job] && S.tasks[t.job].id > t.id ? S.tasks[t.job] : t;
    setDev(S.status.busy ? "busy" : "on", S.status.device + (S.status.gpu ? " · " + S.status.gpu.replace("NVIDIA GeForce ", "") : "")); }
  catch (e) { if (e.status !== 401) toast(e.message, true); }
}

// ------------------------------------------------------------------ login
function viewLogin() {
  const pw = h("input", { type: "password", placeholder: "Phone-app password", autocomplete: "current-password" });
  const go = async () => {
    try { await api("POST", "/api/login", { password: pw.value }); S.authed = true; S.status = null; render(); }
    catch (e) { toast(e.message, true); }
  };
  pw.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
  return h("div", { class: "login" },
    h("img", { src: "/static/icon-512.png", alt: "" }), h("h1", {}, "OnDaGo Editor"),
    h("p", { class: "muted" }, "Edit on the go. Your laptop does the heavy lifting."),
    h("div", { class: "card", style: "text-align:left" }, h("label", { class: "f" }, "Password"), pw,
      h("button", { class: "btn primary big", style: "margin-top:12px", onclick: go }, "Log in")),
    h("p", { class: "muted small" }, "Tip: on the laptop open Phone app and scan the QR code to log in without typing."));
}

// ------------------------------------------------------------------ home
async function loadHome() {
  try { S.jobs = await api("GET", "/api/jobs"); } catch (e) { if (e.status !== 401) toast(e.message, true); return; }
  if (location.hash !== "#/" && location.hash !== "" ) return;
  $("#app").replaceChildren(viewHome());
}
function stageChip(j) {
  const t = j.task;
  if (t && (t.state === "running" || t.state === "queued")) return h("span", { class: "chip run" }, t.state === "queued" ? "Waiting" : `${Math.round(t.progress * 100)}%`);
  if (t && t.state === "failed") return h("span", { class: "chip bad" }, "Failed");
  if (j.stage === "needs_broll") return h("span", { class: "chip warn" }, `Needs you · ${j.need_broll}`);
  const [l, c] = STAGE[j.stage] || [j.stage, ""]; return h("span", { class: "chip " + c }, l);
}
function projCard(j) {
  const t = j.task, busy = t && (t.state === "running" || t.state === "queued");
  return h("div", { class: "pcard tap", "data-job": j.name, onclick: () => location.hash = "#/job/" + encodeURIComponent(j.name) },
    h("div", { class: "th", style: j.thumb ? `background-image:url('${j.thumb}')` : "" }, j.thumb ? "" : "🎬",
      j.edit_duration || j.duration ? h("span", { class: "dur" }, fmt(j.edit_duration || j.duration)) : null),
    h("div", { class: "pbody" },
      h("div", { class: "ttl ell" }, j.title || j.name),
      h("div", { class: "row small muted", style: "margin-top:6px;gap:8px" }, stageChip(j),
        j.created ? h("span", {}, new Date(j.created * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" })) : null),
      busy ? h("div", { style: "margin-top:8px" }, h("div", { class: "bar" + (t.state === "queued" ? " ind" : "") },
        h("i", { style: `width:${Math.round(t.progress * 100)}%` })),
        h("div", { class: "small muted ell", style: "margin-top:4px" }, t.msg || t.label)) : null));
}
function patchHomeCard(t) {
  const el = document.querySelector(`[data-job="${CSS.escape(t.job)}"]`);
  const j = S.jobs.find(x => x.name === t.job);
  if (el && j) el.replaceWith(projCard(j)); else if (!j) loadHome();
}
function viewHome() {
  const busy = S.jobs.filter(j => j.task && (j.task.state === "running" || j.task.state === "queued")).length;
  const need = S.jobs.filter(j => j.stage === "needs_broll").length;
  return h("div", {}, netBanner(),
    h("div", { class: "hero tap", onclick: () => location.hash = "#/new" },
      h("div", { class: "grow" }, h("div", { class: "htitle" }, "Edit a new video"),
        h("div", { class: "small", style: "opacity:.85;margin-top:2px" }, "Pick a video, pick a style, done. Your laptop does the work.")),
      h("div", { class: "plus" }, "＋")),
    busy || need ? h("div", { class: "row wrap small", style: "margin:0 2px 4px;gap:8px" },
      busy ? h("span", { class: "chip run" }, `${busy} editing now`) : null,
      need ? h("span", { class: "chip warn" }, `${need} need you`) : null) : null,
    h("div", { class: "sechead" }, h("h2", {}, "Your projects"), S.jobs.length ? h("span", { class: "muted small" }, S.jobs.length) : null),
    S.jobs.length ? h("div", { class: "pgrid" }, S.jobs.map(projCard)) :
      h("div", { class: "empty" }, h("div", { class: "big" }, "🎥"), h("p", {}, "No edits yet. Tap “Edit a new video” and pick a video from your phone or your laptop.")));
}

// ------------------------------------------------------------------ new edit
const OPT_UI = [
  ["Clean-up", [["remove_fillers", "Remove um / uh"], ["remove_silences", "Remove silences"], ["remove_retakes", "Remove retakes"]]],
  ["Look & feel", [["_captions", "Captions", [["bold_pop", "Bold pop"], ["clean", "Clean box"], ["minimal", "Minimal"], ["off", "Off"]]],
    ["caption_script", "Caption script", [["auto", "Auto"], ["hinglish", "Hinglish (Roman)"], ["native", "Native script"]]],
    ["zoom_style", "Zooms", [["jumpcut", "Punch-ins"], ["emphasis", "Emphasis only"], ["off", "Off"]]],
    ["smooth_zoom", "Smooth camera push-ins"], ["color_grade", "Colour grade"], ["callouts", "Pop-up text"],
    ["progress_bar", "Progress bar"], ["sfx", "Sound effects"], ["music", "Background music"]]],
  ["Doodles ✨", [["doodles", "Animated doodles", null, "Hand-drawn icons that draw themselves next to you"],
    ["doodle_density", "How many", [["low", "A few"], ["medium", "Some"], ["high", "Lots"]]],
    ["doodle_outline", "Glowing outline around you", null, "On key moments"],
    ["callout_doodles", "Underline / circle pop-up text"],
    ["doodle_ai", "AI custom doodles", null, "FLUX draws anything (RTX GPU only)"]]],
  ["B-roll", [["broll_mode", "B-roll", [["both", "Library + online"], ["offline", "Offline only"], ["online", "Online only"], ["off", "None"]]],
    ["broll_missing_action", "If nothing fits", [["auto", "Decide for me"], ["ask", "Ask me"], ["card", "Text card"], ["ai", "AI image"], ["skip", "Skip"]]],
    ["broll_layout", "Layout", [["auto", "Auto (split-screen + full)"], ["split", "Split-screen"], ["full", "Full screen"]]]]],
  ["More", [["review_before_render", "Let me review before rendering"],
    ["rotation", "Orientation", [["auto", "Auto-fix"], ["0", "As recorded"], ["90", "Rotate right"], ["270", "Rotate left"], ["180", "Upside down"]]],
    ["language", "Language", [["auto", "Auto"], ["hinglish", "Hinglish"], ["en", "English"], ["hi", "Hindi"], ["ur", "Urdu"], ["es", "Spanish"], ["de", "German"], ["fr", "French"]]]]],
];
function optionsCard() {
  const o = S.opts;
  return OPT_UI.map(([title, rows]) => h("div", { class: "card" }, h("h3", {}, title), rows.map(([k, label, choices, hint]) => {
    const aiOff = k === "doodle_ai" && !(S.status && S.status.ai_images);
    const ddOff = (k.startsWith("doodle_") || k === "callout_doodles") && !o.doodles;
    const disabled = aiOff || (ddOff && k !== "doodles");
    const sub = aiOff ? ((S.status && S.status.ai_reason) || "Needs an RTX GPU") : hint;
    let ctl;
    if (k === "_captions") ctl = sel(o.captions ? o.caption_style : "off", choices, v => { o.captions = v !== "off"; if (v !== "off") o.caption_style = v; markPreset(); });
    else if (choices) ctl = sel(String(o[k]), choices, v => { o[k] = v; markPreset(); }, { disabled });
    else ctl = sw(o[k] && !aiOff, v => { o[k] = v; markPreset(); if (k === "doodles") rerenderNew(); }, disabled);
    return h("div", { class: "opt" + (disabled ? " disabled" : "") }, h("div", { class: "lbl" }, label, sub ? h("small", {}, sub) : null), ctl);
  })));
}
// ---------- style presets (same list as the laptop app)
const presetOf = o => {
  for (const p of (S.presets || [])) if (Object.entries(p.options).every(([k, v]) => o[k] === v)) return p.id;
  return "custom";
};
function markPreset() {
  const id = S.opts.preset = presetOf(S.opts);
  for (const el of document.querySelectorAll(".preset")) el.classList.toggle("on", el.dataset.pid === id);
  const b = $("#presetstate"); if (b) b.style.display = id === "custom" ? "" : "none";
}
function usePreset(p) {
  Object.assign(S.opts, p.options);
  if (!("max_pause" in p.options)) S.opts.max_pause = 0.45;
  S.opts.preset = p.id;
  markPreset();
  if (S.adv) rerenderNew();
}
function presetGrid() {
  const cur = presetOf(S.opts);
  return h("div", { class: "presets" }, (S.presets || []).map(p =>
    h("div", { class: "preset tap" + (p.id === cur ? " on" : ""), "data-pid": p.id, onclick: () => usePreset(p) },
      h("div", { class: "pe" }, p.emoji), h("div", { class: "pt" }, p.title), h("div", { class: "pd" }, p.desc))));
}
function outputChips() {
  const o = S.opts;
  const chip = (k, label) => h("button", { class: "tog" + (o[k] ? " on" : ""), onclick: e => {
    o[k] = !o[k]; e.currentTarget.classList.toggle("on", !!o[k]);
    const rl = $("#reellen"); if (rl) rl.classList.toggle("hidden", !o.output_reel);
  } }, h("span", { class: "tick" }, "✓"), label);
  return h("div", {}, h("div", { class: "row wrap", style: "gap:8px" }, chip("output_youtube", "▶ YouTube 16:9"), chip("output_reel", "📱 Reel 9:16")),
    h("div", { id: "reellen", class: "opt" + (o.output_reel ? "" : " hidden"), style: "border:none;padding-bottom:0" },
      h("div", { class: "lbl" }, "Reel length"),
      sel(String(o.reel_mode), [["auto", "Full if under 3 min"], ["highlight", "Best highlight"], ["full", "Full video"]], v => { o.reel_mode = v; })));
}

let newScroll = 0;
function rerenderNew() { newScroll = window.scrollY; viewNew().then(v => { $("#app").replaceChildren(v); window.scrollTo(0, newScroll); }); }
async function viewNew() {
  if (!S.opts) { try { S.opts = await api("GET", "/api/options"); } catch (e) { toast(e.message, true); return h("div"); } }
  if (!S.presets) { try { S.presets = (await api("GET", "/api/presets")).presets; } catch (e) { S.presets = []; } }
  const seg = h("div", { class: "seg" },
    h("button", { class: S.src === "phone" ? "on" : "", onclick: () => { S.src = "phone"; rerenderNew(); } }, "📱 From this phone"),
    h("button", { class: S.src === "laptop" ? "on" : "", onclick: () => { S.src = "laptop"; S.pick = null; rerenderNew(); } }, "💻 On the laptop"));
  let picker;
  if (S.src === "phone") {
    const inp = h("input", { type: "file", accept: "video/*", class: "hidden" });
    inp.addEventListener("change", () => { if (inp.files[0]) { S.pick = { file: inp.files[0] }; rerenderNew(); } });
    const f = S.pick && S.pick.file;
    picker = h("div", { class: "drop", onclick: () => inp.click() }, inp,
      f ? h("div", {}, h("div", { class: "big" }, "🎞️"), h("b", {}, f.name), h("div", { class: "muted small" }, mb(f.size) + " · tap to change"))
        : h("div", {}, h("div", { class: "big" }, "⬆️"), h("b", {}, "Choose a video"), h("div", { class: "muted small" }, "It uploads to your laptop, then gets edited there.")));
  } else {
    picker = await laptopBrowser();
  }
  const startBtn = h("button", { class: "btn primary big", id: "startbtn", onclick: startEdit }, "✨  Edit my video");
  const save = h("input", { type: "checkbox", id: "savedef" });
  const cur = presetOf(S.opts);
  return h("div", {}, h("h1", {}, "New edit"),
    h("div", { class: "step" }, h("span", { class: "num" }, "1"), "Pick a video"),
    seg, h("div", { style: "height:10px" }), picker, h("div", { id: "upl" }),
    h("div", { class: "step" }, h("span", { class: "num" }, "2"), "Pick a style",
      h("span", { class: "chip", id: "presetstate", style: cur === "custom" ? "" : "display:none" }, "Custom")),
    presetGrid(),
    h("div", { class: "step" }, h("span", { class: "num" }, "3"), "What to make"),
    h("div", { class: "card" }, outputChips()),
    h("button", { class: "advbtn" + (S.adv ? " open" : ""), onclick: () => { S.adv = !S.adv; rerenderNew(); } },
      h("span", {}, "⚙️  Advanced options"), h("span", { class: "car" }, "›")),
    S.adv ? h("div", { class: "adv" }, h("p", { class: "small muted", style: "margin:0 2px 10px" },
      "Everything here is set by the style above. Change anything and the style becomes “Custom”."), optionsCard()) : null,
    h("label", { class: "row small muted", style: "margin:10px 2px 0" }, save, "Remember these choices for next time"),
    h("div", { class: "sticky" }, h("div", { class: "inner" }, startBtn)));
}
async function laptopBrowser() {
  const box = h("div", { class: "card filelist" });
  const load = async (root, path) => {
    try {
      if (root === undefined) {
        const fs = await api("GET", "/api/folders");
        fill(box, h("h3", {}, "Pick a folder"), fs.length ? fs.map(f => h("div", { class: "it", onclick: () => load(f.i, "") },
          h("span", { class: "emoji" }, "📁"), h("div", { class: "grow ell" }, f.name), h("span", { class: "muted" }, "›"))) :
          h("p", { class: "muted" }, "No folders shared. Add folders on the laptop in Phone app."));
        return;
      }
      const d = await api("GET", `/api/browse?root=${root}&path=${encodeURIComponent(path)}`);
      S.browse = d;
      const parts = d.path ? d.path.split("/") : [];
      fill(box,
        h("div", { class: "crumbs" }, h("a", { href: "javascript:void 0", onclick: () => load() }, "Folders"),
          parts.map((p, i) => h("a", { href: "javascript:void 0", onclick: () => load(root, parts.slice(0, i + 1).join("/")) }, p))),
        d.dirs.map(x => h("div", { class: "it", onclick: () => load(root, (d.path ? d.path + "/" : "") + x.name) },
          h("span", { class: "emoji" }, "📁"), h("div", { class: "grow ell" }, x.name), h("span", { class: "muted" }, "›"))),
        d.files.map(x => {
          const rel = (d.path ? d.path + "/" : "") + x.name;
          const on = S.pick && S.pick.root === root && S.pick.path === rel;
          return h("div", { class: "it" + (on ? " sel" : ""), onclick: () => { S.pick = { root, path: rel, name: x.name }; load(root, d.path); } },
            h("span", { class: "emoji" }, on ? "✅" : "🎞️"), h("div", { class: "grow" }, h("div", { class: "ell" }, x.name),
              h("div", { class: "small muted" }, mb(x.size) + " · " + new Date(x.mtime * 1000).toLocaleDateString())));
        }),
        !d.dirs.length && !d.files.length ? h("p", { class: "muted" }, "No videos here.") : null);
    } catch (e) { toast(e.message, true); }
  };
  if (S.browse && S.pick && S.pick.root !== undefined) load(S.browse.root, S.browse.path); else load();
  return box;
}

// resumable chunked upload (works through Cloudflare's 100 MB request limit, survives flaky mobile data)
// one chunk with byte-level progress; aborted if nothing moves for `stallMs` (a dead Wi-Fi/tunnel connection
// would otherwise hang forever)
function putChunk(url, blob, fast, onBytes, stallMs) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", fast ? fast.base + url : url);
    x.setRequestHeader("X-OnDaGo", "1");
    x.setRequestHeader("Content-Type", "application/octet-stream");
    if (fast) x.setRequestHeader("Authorization", "Bearer " + fast.token);
    let last = Date.now();
    const dog = setInterval(() => { if (Date.now() - last > stallMs) x.abort(); }, 1000);
    const done = () => clearInterval(dog);
    x.upload.onprogress = e => { last = Date.now(); onBytes(e.loaded); };
    x.onload = () => {
      done(); let j = null; try { j = JSON.parse(x.responseText); } catch (e) { /* not json */ }
      if (x.status === 200 || (x.status === 409 && j && "received" in j)) resolve(j);
      else { if (x.status === 401) { S.authed = false; } reject(new ApiError(x.status, (j && j.detail) || x.statusText || "upload failed")); }
    };
    x.onerror = () => { done(); reject(new ApiError(0, "connection dropped")); };
    x.onabort = () => { done(); reject(new ApiError(0, "connection stalled")); };
    x.send(blob);
  });
}

// resumable chunked upload: live speed + time left, adaptive chunk size, stall watchdog, auto-resume
async function uploadFile(file, onprog) {
  const key = (file.name + file.size + file.lastModified).replace(/[^A-Za-z0-9]/g, "").slice(-60);
  let fast = S.net.fast, fastFails = 0;
  const route = () => fast ? "⚡ Wi-Fi direct" : S.net.via === "lan" ? "⚡ Wi-Fi" : S.net.via === "tailscale" ? "🔒 Tailscale" :
    S.net.via === "public" ? "🌍 Internet" : "";
  const call = async (m, u, b) => {
    if (fast) { try { return await api(m, u, b, undefined, fast); } catch (e) { if (e.status) throw e; fast = null; } }
    return api(m, u, b);
  };
  onprog(0, `Connecting to your laptop… ${route()}`);
  const init = await call("POST", "/api/uploads", { name: file.name, size: file.size, key });
  const id = init.id; let off = init.received || 0;
  if (init.done) { onprog(1, "Already on the laptop"); return id; }
  if (off) toast(`Resuming at ${mb(off)}`);
  let CH = 4 * 1024 * 1024; const MAXC = 16 * 1024 * 1024, MINC = 1024 * 1024;
  let tries = 0, samples = [[Date.now(), off]];
  const stats = cur => {
    const now = Date.now(); samples.push([now, cur]); while (samples.length > 2 && now - samples[0][0] > 4000) samples.shift();
    const [t0, b0] = samples[0]; const sp = (cur - b0) / Math.max(0.2, (now - t0) / 1000);
    const left = sp > 0 ? (file.size - cur) / sp : 0;
    const eta = left > 90 ? `${Math.round(left / 60)} min left` : left > 0 ? `${Math.max(1, Math.round(left))} s left` : "";
    onprog(cur / file.size, `${mb(cur)} of ${mb(file.size)} · ${(sp / 1e6).toFixed(1)} MB/s${eta ? " · " + eta : ""} · ${route()}`);
  };
  let lock = null; try { lock = await navigator.wakeLock?.request("screen"); } catch (e) { /* only on https */ }
  try {
    while (off < file.size) {
      const end = Math.min(file.size, off + CH);
      const t = Date.now();
      try {
        const r = await putChunk(`/api/uploads/${id}?offset=${off}`, file.slice(off, end), fast, n => stats(off + n), 20000);
        off = r.received; tries = 0;
        const secs = (Date.now() - t) / 1000;   // grow chunks on fast links, shrink on slow ones
        if (secs < 3 && CH < MAXC) CH *= 2; else if (secs > 12 && CH > MINC) CH = Math.max(MINC, CH / 2);
        stats(off);
      } catch (e) {
        if (e.status === 401) { render(); throw e; }
        if (e.status && e.status !== 409 && e.status < 500) throw e;
        if (fast && ++fastFails >= 2) { fast = null; toast("Wi-Fi direct path lost - continuing through the normal connection"); }
        if (++tries > 10) throw new ApiError(0, "Upload keeps failing. Check the connection and tap Start again - it resumes.");
        CH = Math.max(MINC, CH / 2);
        onprog(off / file.size, document.hidden ? "Paused - the phone stopped the upload in the background. Come back to this screen to continue."
          : `Connection hiccup (${e.message}) - retrying ${tries}/10…`);
        await new Promise(r => setTimeout(r, Math.min(10000, 800 * 2 ** Math.min(tries, 4))));
        try { const again = await call("POST", "/api/uploads", { name: file.name, size: file.size, key }); off = again.received || off; } catch (e2) { /* next round */ }
        samples = [[Date.now(), off]];
      }
    }
  } finally { try { lock && lock.release(); } catch (e) { /* ignore */ } }
  onprog(1, "Finishing…");
  await call("POST", `/api/uploads/${id}/complete`);
  return id;
}
async function startEdit() {
  if (!S.pick) { toast("Pick a video first", true); return; }
  const o = S.opts;
  if (!o.output_youtube && !o.output_reel) { toast("Pick at least one output", true); return; }
  const btn = $("#startbtn"); btn.disabled = true;
  const opts = {}; for (const [k, v] of Object.entries(o)) opts[k] = v;
  if (!(S.status && S.status.ai_images)) opts.doodle_ai = false;
  try {
    let source;
    if (S.pick.file) {
      const box = $("#upl"); const bar = h("i"); const pct = h("b", {}, "0%");
      const msg = h("div", { class: "small muted", style: "margin-top:6px" }, "Starting upload…");
      box.replaceChildren(h("div", { class: "card" }, h("div", { class: "row" }, h("h3", { class: "grow", style: "margin:0" }, "Uploading to your laptop"), pct),
        h("div", { class: "bar", style: "margin-top:10px" }, bar), msg,
        h("p", { class: "small muted" }, "Keep this screen open and unlocked (iPhone: Safari pauses uploads in the background). If it stops, tap Start again - it resumes where it left off.")));
      box.scrollIntoView({ behavior: "smooth", block: "center" });
      const id = await uploadFile(S.pick.file, (p, m) => { bar.style.width = (p * 100).toFixed(1) + "%"; pct.textContent = Math.floor(p * 100) + "%"; msg.textContent = m; });
      source = { upload: id };
    } else source = { root: S.pick.root, path: S.pick.path };
    const j = await api("POST", "/api/jobs", { source, options: opts, save_defaults: $("#savedef").checked });
    S.pick = null; toast("Editing started - I'll keep you posted");
    location.hash = "#/job/" + encodeURIComponent(j.name);
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}

// ------------------------------------------------------------------ job
async function loadJob(name, keepTab) {
  try { const j = await api("GET", "/api/jobs/" + encodeURIComponent(name));
    if (!S.job || S.job.name !== name) { S.tab = j.stage === "needs_broll" ? "broll" : "watch"; S.logOpen = false; }
    else if (!keepTab) S.tab = S.tab || "watch";
    S.job = j; if (j.task) S.tasks[j.name] = j.task;
  } catch (e) { if (e.status === 404) { toast("Project not found", true); location.hash = "#/"; } else if (e.status !== 401) toast(e.message, true); return; }
  if (!location.hash.startsWith("#/job/")) return;
  const y = window.scrollY;
  $("#app").replaceChildren(viewJob());
  if (keepTab) window.scrollTo(0, y);
}
async function act(method, url, body, okmsg) {
  try { const r = await api(method, `/api/jobs/${encodeURIComponent(S.job.name)}${url}`, body);
    if (r && r.name && r.broll !== undefined) { S.job = { ...S.job, ...r }; } else if (r && r.task) { S.job.task = r.task; S.tasks[S.job.name] = r.task; }
    if (okmsg) toast(okmsg);
    const y = window.scrollY; $("#app").replaceChildren(viewJob()); window.scrollTo(0, y);
    return r;
  } catch (e) { toast(e.message, true); }
}
const running = j => j.task && (j.task.state === "running" || j.task.state === "queued");

const STEPS = [["Understanding", 0.17], ["Planning", 0.30], ["B-roll & thumbnails", 0.55], ["Rendering", 0.985], ["Done", 1.01]];
function stepper(t) {
  if (!t || t.label !== "Auto-edit") return null;
  const p = t.state === "done" ? 1.01 : t.progress || 0;
  const cur = STEPS.findIndex(([, end]) => p < end);
  const c = cur < 0 ? STEPS.length - 1 : cur;
  return h("div", { class: "steps", id: "steps" }, STEPS.map(([n], i) =>
    h("div", { class: "st" + (i < c ? " done" : i === c ? " now" : "") }, h("i", {}, i < c ? "✓" : i + 1), h("span", {}, n))));
}
function progressCard(j) {
  const t = j.task;
  const busy = running(j);
  const lg = h("div", { class: "log" + (S.logOpen ? "" : " hidden"), id: "log" }, (j.log || []).join("\n"));
  return h("div", { class: "card", id: "prog" },
    h("div", { class: "row" }, h("div", { class: "grow" }, h("b", { id: "pmsg" }, busy ? (t.msg || t.label) : (t && t.state === "failed" ? `${t.label} failed` : stageText(j)))),
      h("span", { class: "muted small", id: "ppct" }, busy ? Math.round(t.progress * 100) + "%" : "")),
    busy ? stepper(t) : null,
    busy ? h("div", { class: "bar" + (t.state === "queued" ? " ind" : ""), style: "margin-top:10px" }, h("i", { id: "pbar", style: `width:${Math.round(t.progress * 100)}%` })) : null,
    t && t.state === "failed" ? h("p", { class: "small", style: "color:#FF9AA4" }, t.error.split("\n").slice(-2).join(" ")) : null,
    h("div", { class: "btns" },
      busy && t.state === "queued" ? h("button", { class: "btn sm", onclick: async () => { try { await api("DELETE", "/api/tasks/" + t.id); toast("Cancelled"); loadJob(j.name, true); } catch (e) { toast(e.message, true); } } }, "Cancel") : null,
      (j.log || []).length ? h("button", { class: "btn sm ghost", onclick: () => { S.logOpen = !S.logOpen; lg.classList.toggle("hidden"); } }, S.logOpen ? "Hide details" : "Show details") : null),
    lg);
}
function stageText(j) {
  return { new: "Waiting to start", needs_broll: `I need your help with ${j.need_broll} B-roll moment(s)`, analyzed: "Analysis ready - review, then render",
    rendered: "Your edit is ready 🎉" }[j.stage] || j.stage;
}
function patchProgress() {
  const j = S.job; if (!j) return; const t = j.task; if (!t) return;
  const bar = $("#pbar"), msg = $("#pmsg"), pct = $("#ppct");
  if (bar && running(j)) {
    bar.style.width = Math.round(t.progress * 100) + "%"; msg.textContent = t.msg || t.label; pct.textContent = Math.round(t.progress * 100) + "%";
    const st = $("#steps"), ns = stepper(t); if (st && ns) st.replaceWith(ns);
  }
  else if (!bar && running(j)) { const p = $("#prog"); if (p) p.replaceWith(progressCard(j)); }
}

function viewJob() {
  const j = S.job;
  const need = j.broll.filter(s => s.status === "needs_input").length;
  const tabs = [["watch", "Watch"], ["broll", "B-roll", need], ["doodles", "Doodles"], ["text", "Text"], ["thumb", "Thumbnail"], ["publish", "Publish"]];
  const body = { watch: tabWatch, broll: tabBroll, doodles: tabDoodles, text: tabText, thumb: tabThumb, publish: tabPublish }[S.tab] || tabWatch;
  const canRender = !running(j) && ["needs_broll", "analyzed", "rendered"].includes(j.stage);
  return h("div", {},
    h("div", { class: "row" }, h("a", { href: "#/", class: "iconbtn", style: "font-size:22px" }, "‹"),
      h("div", { class: "grow" }, h("div", { class: "ell", style: "font-weight:800;font-size:18px" }, j.title), h("div", { class: "row small" }, stageChip(j),
        j.edit_duration ? h("span", { class: "muted" }, `${fmt(j.edit_duration)} · cut ${j.cuts.seconds}s`) : null))),
    h("div", { style: "height:12px" }),
    progressCard(j),
    need && S.tab !== "broll" ? h("div", { class: "banner" }, `${need} moment(s) need B-roll. `, h("a", { href: "javascript:void 0", onclick: () => { S.tab = "broll"; loadJob(j.name, true); } }, "Choose now →")) : null,
    h("div", { class: "tabs" }, tabs.map(([k, l, n]) => h("button", { class: S.tab === k ? "on" : "", onclick: () => { S.tab = k; $("#app").replaceChildren(viewJob()); } }, l, n ? h("span", { class: "n" }, n) : null))),
    body(j),
    canRender ? h("div", { class: "sticky" }, h("div", { class: "inner" }, renderButton(j, need))) : null);
}
function renderButton(j, need) {
  const label = j.stage === "rendered" ? "🔁  Re-render with my changes" : "🎬  Render my video";
  return h("button", { class: "btn primary big", onclick: async () => {
    let missing = "";
    if (need) {
      const c = confirm(`${need} moment(s) still have no B-roll.\n\nOK = use animated text cards\nCancel = skip them`);
      missing = c ? "cards" : "skip";
    }
    await act("POST", "/render", { missing }, "Rendering started");
  } }, label);
}

// ---------- watch
function tabWatch(j) {
  const outs = j.outputs_detail || {};
  const kinds = Object.keys(outs);
  if (!kinds.length) return h("div", { class: "empty" }, h("div", { class: "big" }, running(j) ? "⏳" : "🎬"),
    h("p", {}, running(j) ? "Your laptop is editing. You can close this app and come back any time." : "Not rendered yet."),
    j.stage === "analyzed" ? h("p", { class: "muted" }, "Check B-roll, doodles and text, then tap Render.") : null);
  if (!kinds.includes(S.playerKind)) S.playerKind = kinds[0];
  const k = S.playerKind;
  const poster = j.thumbnails && j.thumbnails[j.thumb_choice] ? j.thumbnails[j.thumb_choice].url : "";
  const v = h("video", { controls: true, playsinline: true, preload: "metadata", poster: k === "youtube" ? poster : null, src: fastMedia(outs[k].preview, true) });
  S.player = v;
  return h("div", {},
    kinds.length > 1 ? h("div", { class: "seg", style: "margin-bottom:10px" }, kinds.map(x => h("button", { class: x === k ? "on" : "", onclick: () => { S.playerKind = x; $("#app").replaceChildren(viewJob()); } }, x === "youtube" ? "▶ YouTube" : "📱 Reel"))) : null,
    h("div", { class: "player" + (k === "reel" ? " v" : "") }, v),
    isLocal() ? h("p", { class: "small muted" }, "⚡ Same Wi-Fi: playing the full-quality file directly from your laptop.") :
      h("div", { class: "row small muted", style: "margin:8px 0" }, h("span", { class: "grow" }, S.hd ? "Full quality (uses more data)" : "Phone preview (720p) - saves data"),
        h("button", { class: "btn sm", onclick: () => { S.hd = !S.hd; $("#app").replaceChildren(viewJob()); } }, S.hd ? "Preview" : "HD")),
    h("div", { class: "btns" },
      h("a", { class: "btn", href: fastMedia(outs[k].full + "&dl=1") }, `⬇ Download ${k === "youtube" ? "YouTube" : "Reel"} (${mb(outs[k].size)})`),
      j.export_dir ? h("a", { class: "btn", href: fastMedia(`/media/${encodeURIComponent(j.name)}/file/thumbnail`) }, "⬇ Thumbnail") : null),
    h("h2", {}, "Add something here"),
    h("p", { class: "small muted" }, "Pause the video where you want it, then:"),
    h("div", { class: "btns" },
      h("button", { class: "btn", onclick: () => addDoodleHere() }, "✨ Doodle here"),
      h("button", { class: "btn", onclick: () => addTextHere() }, "💬 Pop-up text here")));
}
async function addDoodleHere() {
  const t = S.player ? S.player.currentTime : 0;
  const kinds = KINDS.map(([k, l], i) => `${i + 1}. ${l}`).join("\n");
  const pick = prompt(`Doodle at ${fmt(t)} - type a number:\n${kinds}`, "7");
  const i = parseInt(pick, 10) - 1; if (!(i >= 0 && i < KINDS.length)) return;
  await act("POST", "/doodles", { t, from: S.playerKind, kind: KINDS[i][0] }, "Doodle added - re-render to see it");
}
async function addTextHere() {
  const t = S.player ? S.player.currentTime : 0;
  const text = prompt(`Pop-up text at ${fmt(t)} (max 4 words):`); if (!text || !text.trim()) return;
  await act("POST", "/callouts", { t, from: S.playerKind, text: text.trim() }, "Text added - re-render to see it");
}

// ---------- b-roll
function tabBroll(j) {
  if (!j.broll.length) return h("div", { class: "empty" }, h("div", { class: "big" }, "🎞️"), h("p", {}, "No B-roll in this edit."));
  const st = S.status || {};
  const order = [...j.broll].sort((a, b) => (a.status !== "needs_input") - (b.status !== "needs_input") || a.s - b.s);
  return h("div", {}, order.map(s => {
    const need = s.status === "needs_input";
    const q = h("input", { type: "text", value: (s.queries || [])[0] || "", placeholder: "search stock footage" });
    const file = h("input", { type: "file", accept: "video/*,image/*", class: "hidden" });
    file.addEventListener("change", async () => {
      const f = file.files[0]; if (!f) return;
      toast("Uploading your clip…");
      try { const id = await uploadFile(f, (p, m) => toast(`Uploading ${Math.round(p * 100)}% · ${m}`)); await act("POST", `/broll/${s.id}/file`, { upload: id, start: 0 }, "Your clip is in"); }
      catch (e) { toast(e.message, true); }
    });
    const chip = { resolved: ["Ready · " + (s.kind === "graphic" ? "text card" : s.source), "ok"], needs_input: ["Needs you", "warn"], skipped: ["Skipped", ""], pending: ["Pending", ""] }[s.status] || [s.status, ""];
    return h("div", { class: "card moment" + (need ? " warn" : "") },
      h("div", { class: "row" }, h("span", { class: "when" }, fmt(s.s)), h("span", { class: "chip " + chip[1] }, chip[0])),
      s.preview ? h("img", { class: "slotprev", src: s.preview, alt: "", style: "margin-top:8px" }) : null,
      h("div", { class: "quote" }, `“${(s.line || "").slice(0, 180)}”`),
      s.description ? h("div", { class: "idea" }, h("b", {}, "Idea: "), s.description) : null,
      s.status !== "resolved" && s.shot_suggestion ? h("div", { class: "idea" }, h("b", {}, "Shoot it yourself: "), s.shot_suggestion) : null,
      s.candidates.length ? h("div", {}, h("div", { class: "small muted", style: "margin-top:6px" }, "Tap an option to use it:"),
        h("div", { class: "cands" }, s.candidates.map(c => h("div", { class: "cand", onclick: () => act("POST", `/broll/${s.id}/use`, { idx: c.i }, "Using that clip") },
          c.preview ? h("img", { src: c.preview, alt: "", loading: "lazy" }) : h("img", { alt: "" }),
          h("div", {}, c.source + (c.vlm !== null && c.vlm !== undefined ? ` · ${c.vlm}/10` : "")))))) : null,
      st.online_broll ? h("div", { class: "row", style: "margin-top:10px" }, q,
        h("button", { class: "btn sm", onclick: () => act("POST", `/broll/${s.id}/search`, { query: q.value }, "Searching…") }, "Search")) : null,
      h("div", { class: "btns" }, file,
        h("button", { class: "btn sm", onclick: () => file.click() }, "📁 My file"),
        h("button", { class: "btn sm", onclick: () => { const t = prompt("Text card (2-4 words):", s.card_text || ""); if (t !== null) act("POST", `/broll/${s.id}/card`, { text: t }, "Text card set"); } }, "🔤 Text card"),
        st.ai_images ? h("button", { class: "btn sm", onclick: () => act("POST", `/broll/${s.id}/ai`, {}, "Generating…") }, "🎨 AI image") : null,
        h("button", { class: "btn sm", onclick: () => act("POST", `/broll/${s.id}/skip`, {}) }, s.status === "skipped" ? "Un-skip" : "Skip"),
        h("button", { class: "btn sm danger", onclick: () => { if (confirm("Remove this B-roll moment?")) act("POST", `/broll/${s.id}/remove`, {}); } }, "Remove")));
  }));
}

// ---------- doodles
function tabDoodles(j) {
  const ds = j.doodles || [], ol = j.outlines || [];
  return h("div", {},
    h("p", { class: "small muted" }, "Hand-drawn doodles pop up next to you. Change or switch them off, then re-render. Add new ones from Watch."),
    h("div", { class: "card" }, ds.length ? ds.map(d => h("div", { class: "doodle" },
      h("span", { class: "t" }, fmt(d.s)),
      h("div", { class: "grow small muted ell" }, d.shape === "custom" ? `🎨 AI: ${d.prompt}` : (d.line || "")),
      d.shape === "custom" ? null : sel(d.kind, KINDS, v => act("PATCH", `/doodles/${d.id}`, { kind: v })),
      sw(d.enabled, v => act("PATCH", `/doodles/${d.id}`, { enabled: v })))) : h("p", { class: "muted" }, "No doodles yet.")),
    ol.length ? h("h2", {}, "Outline around you") : null,
    ol.length ? h("div", { class: "card" }, ol.map(o => h("div", { class: "doodle" }, h("span", { class: "t" }, fmt(o.s)),
      h("div", { class: "grow" }, "Glowing outline"), sw(o.enabled, v => act("PATCH", `/outlines/${o.id}`, { enabled: v }))))) : null);
}

// ---------- text
function tabText(j) {
  return h("div", {},
    h("p", { class: "small muted" }, "Pop-up text on screen (max 4 words). Edits save when you leave the box."),
    h("div", { class: "card" }, j.callouts.length ? j.callouts.map(c => {
      const inp = h("input", { type: "text", value: c.text, maxlength: "40" });
      inp.addEventListener("change", () => act("PATCH", `/callouts/${c.i}`, { text: inp.value }, "Saved"));
      return h("div", { class: "doodle" }, h("span", { class: "t" }, fmt(c.s)), h("div", { class: "grow" }, inp),
        sw(c.enabled, v => act("PATCH", `/callouts/${c.i}`, { enabled: v })));
    }) : h("p", { class: "muted" }, "No pop-up text. Add some from Watch.")));
}

// ---------- thumbnail
function tabThumb(j) {
  const texts = [0, 1, 2].map(i => h("input", { type: "text", value: (j.thumb_texts || [])[i] || "", placeholder: `Text ${i + 1}`, maxlength: "40" }));
  return h("div", {},
    j.thumbnails.length ? h("div", { class: "thumbs" }, j.thumbnails.map(t => h("img", { src: t.url, alt: "", class: t.i === j.thumb_choice ? "on" : "",
      onclick: () => act("POST", "/thumbnail", { choice: t.i }, "Thumbnail selected") }))) : h("p", { class: "muted" }, "No thumbnails yet."),
    h("h2", {}, "Thumbnail text"), h("div", { class: "card" }, texts.map(t => h("div", { style: "margin:6px 0" }, t)),
      h("button", { class: "btn", style: "margin-top:8px", disabled: running(j), onclick: () => act("POST", "/thumbnails/regenerate", { texts: texts.map(t => t.value) }, "Making new thumbnails…") }, "🎨 Regenerate thumbnails")));
}

// ---------- publish
function tabPublish(j) {
  const m = j.metadata || {}, st = S.status || {};
  const title = h("input", { type: "text", value: m.title || "", maxlength: "100" });
  const titles = (m.titles || []).filter(t => t && t !== m.title);
  const desc = h("textarea", {}, m.description || "");
  const tags = h("input", { type: "text", value: (m.tags || []).join(", ") });
  const ig = h("textarea", {}, m.instagram_caption || "");
  const when = h("input", { type: "datetime-local" });
  if (m.publish_at) { const d = new Date(m.publish_at); if (!isNaN(d)) when.value = new Date(d - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); }
  const save = async quiet => act("PUT", "/metadata", { title: title.value, description: desc.value, tags: tags.value.split(",").map(s => s.trim()).filter(Boolean),
    instagram_caption: ig.value, publish_at: when.value ? new Date(when.value).toISOString().replace(/\.\d+Z$/, "Z") : "" }, quiet ? null : "Saved");
  const pub = where => async () => {
    if (!confirm(`Upload to ${where === "youtube" ? "YouTube" : "Instagram"} now?`)) return;
    await save(true); await act("POST", `/publish/${where}`, undefined, "Uploading…");
  };
  const hasYt = (j.outputs || []).includes("youtube"), hasReel = (j.outputs || []).includes("reel");
  return h("div", {},
    h("div", { class: "card" },
      h("label", { class: "f" }, "YouTube title"), title,
      titles.length ? h("div", { class: "btns" }, titles.map(t => h("button", { class: "btn sm ghost", onclick: () => title.value = t }, t.length > 42 ? t.slice(0, 40) + "…" : t))) : null,
      h("label", { class: "f" }, "Description"), desc,
      h("label", { class: "f" }, "Tags (comma separated)"), tags,
      h("label", { class: "f" }, "Schedule (optional)"), when,
      h("label", { class: "f" }, "Instagram caption"), ig,
      h("button", { class: "btn", style: "margin-top:12px", onclick: () => save() }, "💾 Save")),
    h("div", { class: "card" },
      h("div", { class: "btns", style: "margin-top:0" },
        h("button", { class: "btn primary", disabled: !hasYt || !st.youtube_connected || running(j), onclick: pub("youtube") }, "▶ Upload to YouTube"),
        h("button", { class: "btn primary", disabled: !hasReel || !st.instagram_ready || running(j), onclick: pub("instagram") }, "📸 Upload Reel")),
      !st.youtube_connected ? h("p", { class: "small muted" }, "YouTube: connect your account once in the desktop app (Settings → YouTube).") : null,
      !st.instagram_ready ? h("p", { class: "small muted" }, "Instagram: add your account in the desktop app (Settings → Instagram).") : null,
      Object.entries(j.published || {}).map(([k, v]) => h("p", {}, `${k}: `, h("a", { href: v, target: "_blank", rel: "noopener" }, v)))));
}

// ------------------------------------------------------------------ settings
function viewSettings() {
  const st = S.status || {}, u = st.urls || {};
  const links = [["This laptop (Wi-Fi)", (u.lan || [])[0]], ["Tailscale", u.tailscale_https || u.tailscale], ["Public link", u.public]].filter(x => x[1]);
  return h("div", {}, h("h1", {}, "Settings"),
    h("div", { class: "card" },
      h("div", { class: "kv" }, h("span", {}, "Laptop"), h("span", {}, st.device || "-")),
      h("div", { class: "kv" }, h("span", {}, "GPU"), h("span", {}, st.gpu || "-")),
      h("div", { class: "kv" }, h("span", {}, "AI images & doodles"), h("span", {}, st.ai_images ? "Available" : (st.ai_reason || "Not available"))),
      h("div", { class: "kv" }, h("span", {}, "YouTube"), h("span", {}, st.youtube_connected ? "Connected" : "Connect on laptop")),
      h("div", { class: "kv" }, h("span", {}, "Instagram"), h("span", {}, st.instagram_ready ? "Ready" : "Set up on laptop")),
      h("div", { class: "kv" }, h("span", {}, "Online B-roll"), h("span", {}, st.video_keys ? "Video clips (Pixabay/Pexels)" : "Free photos - add a Pixabay key for video")),
      h("div", { class: "kv" }, h("span", {}, "Version"), h("span", {}, st.version || ""))),
    h("h2", {}, "Stock video for B-roll"),
    (() => {
      const k = h("input", { type: "password", placeholder: st.video_keys ? "Key saved - paste a new one to replace" : "Paste your free Pixabay API key" });
      return h("div", { class: "card" },
        h("p", { class: "small muted", style: "margin-top:0" }, "Free key, takes a minute: sign up at ", h("a", { href: "https://pixabay.com/api/docs/", target: "_blank", rel: "noopener" }, "pixabay.com/api/docs"), ", log in, and your key is shown on that page. Then OnDaGo picks real video clips for you automatically."),
        k, h("button", { class: "btn", style: "margin-top:10px", onclick: async () => {
          if (!k.value.trim()) return;
          try { await api("PUT", "/api/keys", { pixabay_key: k.value.trim() }); k.value = ""; toast("Pixabay key saved"); await loadStatus(); $("#app").replaceChildren(viewSettings()); }
          catch (e) { toast(e.message, true); } } }, "Save key"));
    })(),
    h("h2", {}, "Ways to reach your laptop"),
    h("div", { class: "card urls" }, links.length ? links.map(([k, v]) => h("div", {}, h("b", {}, k + ": "), v)) : h("p", { class: "muted" }, "Only this connection.")),
    h("h2", {}, "Install on your home screen"),
    h("div", { class: "card small" },
      h("p", {}, h("b", {}, "iPhone: "), "Safari → Share → Add to Home Screen."),
      h("p", {}, h("b", {}, "Android: "), "Chrome → ⋮ menu → Add to Home screen / Install app."),
      h("p", { class: "muted" }, "For alerts when an edit finishes, install the free ntfy app and subscribe to the topic shown on the laptop.")),
    h("button", { class: "btn danger big", onclick: async () => { await api("POST", "/api/logout"); S.authed = false; S.status = null; if (S.es) S.es.close(); render(); } }, "Log out"));
}

// ------------------------------------------------------------------ boot
if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.authed) { loadStatus(); if (S.job) loadJob(S.job.name, true); } });
render();
