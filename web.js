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
function followRun(id, lbl, req) {   // req = {url, body}: a log follow that streams on its POST and dies with this page
  const g = ++gen, out = $("out");
  if (ctl) ctl.abort();
  ctl = new AbortController();
  label = lbl; $("outlabel").textContent = lbl; $("stop").dataset.id = req ? "" : id;
  out.replaceChildren(el("div", "running…", "dim"));
  const t0 = Date.now();
  clearInterval(timer);
  timer = setInterval(() => { $("elapsed").textContent = Math.round((Date.now() - t0) / 1000) + "s"; }, 1000);
  const opts = req ? {method: "POST", body: req.body, signal: ctl.signal} : {signal: ctl.signal};
  stream(req ? req.url : "/runs/" + id, lines => { if (g === gen) add(out, lines.map(l => el("div", cut(l)))); }, opts)
    .catch(e => { if (g === gen) add(out, [el("div", e.name === "AbortError" ? "[stopped]" : "[error: " + e.message + "]", "err")]); })
    .finally(() => { if (g === gen) { clearInterval(timer); refreshRuns(); } });
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

// ---- watch page: sidebar of views, one streamed pane, chat on agent views ----
if ($("pane")) {
  const env = document.body.dataset.env, pane = $("pane"), chat = $("chat"), views = $("views");
  const agents = new Set(), logs = {}, busy = new Set();
  let current = null;
  const PAL = ["#0891b2", "#059669", "#ca8a04", "#c026d3", "#2563eb", "#0d9488", "#65a30d", "#d97706", "#db2777", "#7c3aed"];
  const whoColor = w => ["world", "GM"].includes(w) ? "#6366f1"
    : PAL[[...w].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 0) % PAL.length];
  const item = (name, tag, text) => {
    const n = el(tag, text || name, "view");
    n.dataset.name = name;
    n.onclick = e => { e.preventDefault(); select(name); };   // in a <summary>: select, don't toggle
    return n;
  };
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
    current = name; document.title = `watch — ${env} · ${name}`; $("sub").textContent = name;
    pane.replaceChildren(); $("paused").hidden = true;
    for (const v of views.querySelectorAll(".view")) v.classList.toggle("sel", v.dataset.name === name);
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
      .then(() => { if (g === gen) add(pane, [el("div", "stream ended", "dim")]); })
      .catch(e => { if (g === gen && e.name !== "AbortError") add(pane, [el("div", e.message, "ev kind-deny")]); });
  }
  fetch("/views/" + enc(env)).then(r => r.json()).then(d => {
    views.replaceChildren();
    if (d.error) {   // stopped → env start; non-PI → env logs (the menu's raw-tail chooser)
      const verb = d.error.includes("not running") ? "env start" : "env logs";
      const a = el("a", `→ ${verb} ${env} on the console`);
      a.href = `/?open=${enc(verb)}&name=${enc(env)}`;
      views.append(el("li", d.error, "err"), el("li")).lastChild.append(a);
      return;
    }
    for (const [name, kids] of d.views) {
      if (!kids.length) { views.append(item(name, "li")); continue; }
      agents.add(name);
      const li = el("li"), det = el("details"), sum = el("summary"), ul = el("ul");
      sum.append(item(name, "span"));
      for (const k of kids) ul.append(item(k, "li", k.split(":").pop()));
      det.append(sum, ul); li.append(det); views.append(li);
    }
    if (d.views.length) select(d.views[0][0]);
  }).catch(e => views.replaceChildren(el("li", e.message, "err")));
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
  $("follow").onclick = () => { pane.scrollTop = pane.scrollHeight; };
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
