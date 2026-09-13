// agentspace web UI — one script for the console, watch and wizard pages (each page uses the part it has).
const H = {"X-Agentspace": "1"};   // every POST carries it; a cross-site form cannot
const $ = id => document.getElementById(id);
const enc = encodeURIComponent;
const el = (tag, text = "", cls = "") => { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; };
const cut = s => s.length > 8192 ? s.slice(0, 8192) + " …[8 KB shown]" : s;

async function post(url, body) {   // body: URLSearchParams (form-encoded, repeats kept) or a string
  const r = await fetch(url, {method: "POST", headers: H, body});
  if (!r.ok) throw new Error(await r.text());
  return r;
}

// Read a close-delimited response; onLines gets each read's complete lines as one array.
async function stream(url, onLines, opts = {}) {
  const r = await fetch(url, {...opts, headers: H});
  if (!r.ok) throw new Error(await r.text());
  const reader = r.body.getReader(), dec = new TextDecoder();
  let rest = "";
  for (;;) {
    const {value, done} = await reader.read();
    if (done) { if (rest) onLines([rest]); return; }
    const parts = (rest + dec.decode(value, {stream: true})).split("\n");
    rest = parts.pop();
    if (parts.length) onLines(parts);
  }
}

// Append nodes as one fragment; keep 5000 entries; scroll once per frame, only if it was at the bottom.
const atBottom = p => p.scrollHeight - p.scrollTop - p.clientHeight < 4;
function add(pane, nodes) {
  if (!nodes.length) return;   // a keepalive-only read schedules no frame
  const stick = atBottom(pane), frag = document.createDocumentFragment();
  for (const n of nodes) frag.append(n);
  pane.append(frag);
  if (pane.children.length > 5000) {
    while (pane.children.length > 5000) pane.firstElementChild.remove();
    pane.prepend(el("div", "older output omitted", "cut"));
  }
  if (stick && !pane.raf) pane.raf = requestAnimationFrame(() => { pane.raf = 0; pane.scrollTop = pane.scrollHeight; });
}

// ---- the output pane (console and wizard): follows one run at a time ----
let gen = 0, ctl = null, timer = null, label = "";
function followRun(id, lbl, req, onEnd) {   // req = {url, body}: a log follow that streams on its POST and dies with this page
  const g = ++gen, out = $("out");
  let exit = null;
  if (ctl) ctl.abort();
  ctl = new AbortController();
  label = lbl; $("outlabel").textContent = lbl; $("stop").dataset.id = req ? "" : id;
  out.replaceChildren(el("div", "running…", "dim"));
  const t0 = Date.now();
  clearInterval(timer);
  timer = setInterval(() => { $("elapsed").textContent = Math.round((Date.now() - t0) / 1000) + "s"; }, 1000);
  const opts = req ? {method: "POST", body: req.body, signal: ctl.signal} : {signal: ctl.signal};
  stream(req ? req.url : "/runs/" + id, lines => {
    if (g !== gen) return;
    add(out, lines.map(l => el("div", cut(l))));
    for (const l of lines) { const m = l.match(/^\[exit (-?\d+)\]$/); if (m) exit = +m[1]; }
  }, opts)
    .catch(e => { if (g === gen) add(out, [el("div", e.name === "AbortError" ? "[stopped]" : "[error: " + e.message + "]", "err")]); })
    .finally(() => { if (g === gen) { clearInterval(timer); refreshRuns(); if (onEnd) onEnd(exit); } });
}
if ($("stop")) $("stop").onclick = () => {
  if (!ctl) return;
  if (!$("stop").dataset.id) return ctl.abort();   // a log follow: closing the connection ends the child
  if (confirm(`Interrupt ${label}? This is Ctrl-C: a fork or build stops half-done.`))
    post("/runs/" + $("stop").dataset.id + "/stop").catch(e => alert(e.message));
};
async function refreshRuns() {
  const box = $("runs");
  if (!box) return;
  const runs = await (await fetch("/runs")).json();
  box.replaceChildren(...runs.map(r => {
    const a = el("a", `${r.label} · ${r.done ? "exit " + r.exit : "running " + Math.round(Date.now() / 1000 - r.started) + "s"}`, r.done ? "done" : "");
    a.onclick = () => followRun(r.id, r.label);
    return a;
  }));
  if (!runs.length) box.append(el("span", "no runs yet", "dim"));
}

// ---- console: generated verb forms ----
if ($("verbs")) {
  const firstArg = f => { const a = f.querySelector("[data-arg]"); return a ? a.value.trim() : ""; };
  for (const f of document.querySelectorAll("#verbs form")) f.onsubmit = e => {
    e.preventDefault();
    const path = f.dataset.path, url = "/run/" + path.replaceAll(" ", "/"), lbl = (path + " " + firstArg(f)).trim();
    const body = new URLSearchParams(new FormData(f));
    if (f.elements.force) { if (!confirm(`${lbl}? This cannot be undone.`)) return; body.set("force", "on"); }
    if (f.elements.follow && f.elements.follow.checked) return followRun(null, lbl, {url, body});
    post(url, body).then(r => r.json()).then(({id}) => followRun(id, lbl)).catch(e => alert(e.message));
  };
  const q = new URLSearchParams(location.search), open = q.get("open") && document.querySelector(`form[data-path="${q.get("open")}"]`);
  if (open) {   // ?open=env start&name=desk2 — a link from the watch page pre-fills a form
    open.closest("details").open = true;
    for (const [k, v] of q) if (open.elements[k]) open.elements[k].value = v;
    open.scrollIntoView();
  }
  refreshRuns();
}

// ---- watch page: agent cards, view tabs, one streamed pane, chat on agent views ----
if ($("pane")) {
  const env = document.body.dataset.env, pane = $("pane"), chat = $("chat"), views = $("views");
  const cards = [...document.querySelectorAll("#agents .card")], agents = new Set(cards.map(c => c.dataset.name));
  const logs = {}, busy = new Set(), facets = {}, world = [];   // facets[agent] = its facet view names
  let current = null;
  const PAL = ["#0891b2", "#059669", "#ca8a04", "#c026d3", "#2563eb", "#0d9488", "#65a30d", "#d97706", "#db2777", "#7c3aed"];
  const whoColor = w => ["world", "GM"].includes(w) ? "#6366f1"
    : PAL[[...w].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0) % PAL.length];
  for (const c of cards) { c.querySelector("b").style.color = whoColor(c.dataset.name); c.onclick = () => select(c.dataset.name); }
  const st = document.querySelector(".dot").textContent.replace("● ", "").split(" ")[0];   // last-known status
  const known = /^(active|dormant|stopped|missing)$/.test(st);
  const okFor = {Start: st === "stopped", Wake: /^(active|dormant)$/.test(st), Sleep: st === "active",
                 "Take snap": st !== "missing", Kill: true, "Top up": true};   // Wake stays on while active: a nudge for a stuck world
  const PARAM = {"snap take": "env_name", "budget topup": "env_name"};   // the env argument's name; "name" otherwise
  const action = (label, verb, asks = {}, after) => {   // runs the verb from here; output streams into the side column
    const off = known && !okFor[label], b = el("button", label, "btn" + (label === "Kill" ? " danger" : ""));
    b.disabled = off;
    if (off) b.title = `not while ${st}`;
    b.onclick = () => {
      const body = new URLSearchParams({[PARAM[verb] || "name"]: env});
      for (const [k, q] of Object.entries(asks)) { const v = prompt(q); if (v == null || !v.trim()) return; body.set(k, v.trim()); }
      if (label === "Kill") { if (!confirm(`${verb} ${env}? This cannot be undone.`)) return; body.set("force", "on"); }
      $("outbox").hidden = false;
      post("/run/" + verb.replaceAll(" ", "/"), body).then(r => r.json())
        .then(({id}) => followRun(id, `${verb} ${env}`, null, exit => { if (exit === 0 && after) after(); })).catch(e => alert(e.message));
    };
    return b;
  };
  const reload = () => location.reload();
  $("actions").replaceChildren(action("Start", "env start", {}, reload), action("Wake", "env kick", {}, reload), action("Sleep", "env sleep", {}, reload),
                               action("Take snap", "snap take", {message: "One-line label for the snap:"}),
                               action("Kill", "env kill", {}, () => { location.href = "/"; }));
  $("budget").append(action("Top up", "budget topup", {amount_usd: "Amount to add (USD):"}, reload));
  const tab = (name, label) => { const li = el("li", label, "view" + (name === current ? " sel" : "")); li.onclick = () => select(name); return li; };
  function tabs() {   // world views always; the selected agent's session + facets after a separator
    const agent = current && current.split(":")[0];
    views.replaceChildren(...world.map(n => tab(n, n)));
    if (agents.has(agent)) {   // second row: the agent's session, facets, and a shortcut to the chat box
      const msg = el("li", "message", "view act");
      msg.onclick = () => { if (current !== agent) select(agent); chat.elements.text.focus(); };
      views.append(el("li", "", "brk"), el("li", agent, "lbl"), tab(agent, "session"),
                   ...(facets[agent] || []).map(k => tab(k, k.split(":").pop())), msg);
    }
    for (const c of cards) c.classList.toggle("sel", c.dataset.name === agent);
  }
  function render(ev) {
    if (ev.error) return el("div", ev.error, "ev kind-deny");
    const d = el("div", "", "ev kind-" + ev.kind);
    if (ev.ts) d.append(el("span", ev.ts.slice(11, 19), "ts"));
    if (ev.who) { const w = el("span", ev.who, "who"); w.style.color = whoColor(ev.who); d.append(w); }
    d.append(el("span", cut(ev.text), "text"));
    return d;
  }
  function select(name) {
    const g = ++gen;
    if (ctl) ctl.abort();
    ctl = new AbortController();
    current = name; document.title = `watch — ${env} · ${name}`;
    tabs(); pane.replaceChildren(); $("paused").hidden = true; $("live").hidden = false;
    chat.hidden = !agents.has(name);
    if (!chat.hidden) {
      $("chatlog").replaceChildren(logs[name] ??= el("div"));   // each agent keeps its own log
      chat.elements.text.disabled = busy.has(name);
      $("chatwho").textContent = `you → ${name}:`;
    }
    let first = true, empty = null;
    stream(`/stream/${enc(env)}/${enc(name)}`, lines => {
      if (g !== gen) return;
      const evs = lines.filter(l => l).map(l => JSON.parse(l));   // a blank line is a keepalive
      if (first && !evs.length) pane.append(empty = el("div", "nothing to see here yet", "dim"));
      first = false;
      if (evs.length && empty) { empty.remove(); empty = null; }
      add(pane, evs.map(render));
    }, {signal: ctl.signal})
      .then(() => { if (g === gen) { $("live").hidden = true; add(pane, [el("div", "stream ended", "dim")]); } })
      .catch(e => { if (g === gen && e.name !== "AbortError") { $("live").hidden = true; add(pane, [el("div", e.message, "ev kind-deny")]); } });
  }
  fetch("/views/" + enc(env)).then(r => r.json()).then(d => {
    if (d.error) {   // stopped → env start; non-PI → env logs (the menu's raw-tail chooser)
      const verb = d.error.includes("not running") ? "env start" : "env logs";
      const a = el("a", `→ ${verb} ${env} on the console`);
      a.href = `/?open=${enc(verb)}&name=${enc(env)}`;
      views.replaceChildren(el("li", d.error, "err"), el("li")); views.lastChild.append(a);
      return;
    }
    for (const [name, kids] of d.views) kids.length ? facets[name] = kids : world.push(name);
    if (d.views.length) select(d.views[0][0]);
  }).catch(e => views.replaceChildren(el("li", e.message, "err")));
  fetch("/budget/" + enc(env)).then(async r => { if (!r.ok) throw new Error(await r.text()); return r.json(); }).then(b => {
    const used = +b.used || 0, limit = +b.limit || 0, box = $("budget");
    box.querySelector("b").textContent = `$${used.toFixed(2)}`;
    box.querySelector("small").textContent = limit ? `of $${limit.toFixed(2)} limit · $${Math.max(0, limit - used).toFixed(2)} remaining` : "no limit recorded";
    box.querySelector(".bar div").style.width = limit ? Math.min(100, 100 * used / limit) + "%" : "0";
  }).catch(e => { $("budget").querySelector("small").textContent = e.message; });
  chat.onsubmit = e => {
    e.preventDefault();
    const text = chat.elements.text.value.trim(), agent = current, log = logs[agent];
    if (!text || busy.has(agent)) return;
    const wait = el("div", "sent — waiting for the turn to end (10–60 s)", "dim");
    log.append(el("div", `you → ${agent}: ${text}`, "you"), wait);
    chat.elements.text.value = ""; chat.elements.text.disabled = true; busy.add(agent);
    post(`/chat/${enc(env)}/${enc(agent)}`, text).then(r => r.text())
      .then(reply => wait.replaceWith(el("div", `${agent}: ${reply}`, "reply")))
      .catch(err => wait.replaceWith(el("div", err.message, "err")))
      .finally(() => { busy.delete(agent); if (current === agent) chat.elements.text.disabled = false; });
  };
  pane.onscroll = () => { $("paused").hidden = atBottom(pane); };
}

// ---- wizard ----
if ($("step3")) {
  const f = $("step3"), models = $("models");
  fetch("/models?runtime=" + enc(f.dataset.runtime)).then(r => r.json()).then(ids => {   // the catalog, after paint
    const have = new Set([...models.options].map(o => o.value));
    for (const id of ids) if (!have.has(id)) models.append(new Option(id));
  }).catch(() => {});
  $("copyrow").onclick = e => {
    e.preventDefault();
    const rows = [...$("roster").tBodies[0].rows];
    for (const r of rows) for (const s of ["[name^=model_]", "[name^=persona_]"]) r.querySelector(s).value = rows[0].querySelector(s).value;
  };
  f.elements.world_name.oninput = () => { $("wname").textContent = f.elements.world_name.value || f.elements.world_name.placeholder; };
  f.onsubmit = e => {
    e.preventDefault();
    post(f.dataset.build, new URLSearchParams(new FormData(f))).then(r => r.json())
      .then(({id}) => followRun(id, "world build " + f.elements.world_name.placeholder)).catch(err => alert(err.message));
  };
}
for (const b of document.querySelectorAll("button[data-disable]")) b.onclick = () =>   // step 1: the menu's inline disable
  post("/run/scen/deactivate", new URLSearchParams({scen_name: b.dataset.disable})).then(r => r.json())
    .then(({id}) => stream("/runs/" + id, () => {})).then(() => location.reload()).catch(err => alert(err.message));

// ---- draggable column edges (console: nav, verbs; watch: agents, side); widths remembered per browser ----
for (const [id, sign] of [["nav", 1], ["verbs", 1], ["agents", 1], ["side", -1]]) {
  const col = $(id);
  if (!col) continue;
  const g = el("div", "", "gutter"), key = "col:" + id;
  try { if (localStorage[key]) col.style.width = localStorage[key]; } catch {}
  col.insertAdjacentElement(sign > 0 ? "afterend" : "beforebegin", g);
  g.onpointerdown = e => {
    const x0 = e.clientX, w0 = col.offsetWidth;
    g.setPointerCapture(e.pointerId); g.classList.add("drag"); document.body.style.userSelect = "none";
    g.onpointermove = ev => { col.style.width = Math.max(120, w0 + sign * (ev.clientX - x0)) + "px"; };
    g.onpointerup = () => {
      g.onpointermove = null; g.classList.remove("drag"); document.body.style.userSelect = "";
      try { localStorage[key] = col.style.width; } catch {}
    };
  };
}
