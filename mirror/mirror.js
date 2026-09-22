// The public mirror's viewer: board, run page, scenario page. Static files only: every URL is
// relative, the only request that is not a GET is the parameterless POST to `refresh`, and every
// piece of data (all of it model output or operator text) reaches the page through textContent.
'use strict';
const $ = id => document.getElementById(id);
const node = (tag, text = '', cls = '') => { const n = document.createElement(tag); n.textContent = text; n.className = cls; return n; };
const link = (href, text, cls = '') => { const a = node('a', text, cls); a.href = href; return a; };
const enc = encodeURIComponent, params = new URLSearchParams(location.search);
const getJSON = async path => { const r = await fetch(path, {cache: 'no-cache'}); if (!r.ok) throw new Error(path + ': ' + r.status); return r.json(); };
const epoch = ts => ts === '' || ts == null ? null : isNaN(+ts) ? (Date.parse(ts) || null) : +ts * 1000;   // ISO, or a scenario log's bare seconds
const clockOf = ms => ms == null ? '' : new Date(ms).toISOString().slice(11, 19);
const when = iso => iso ? new Date(iso).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : '—';
const span = s => { s = Math.max(0, Math.round(s)); const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? d + 'd ' + h + 'h' : h ? h + 'h ' + m + 'm' : m ? m + 'm' : s + 's'; };
const money = n => '$' + Number(n).toFixed(2);
const spend = r => r.budget_used == null ? 'budget ' + money(r.budget_usd) : money(r.budget_used) + ' of ' + money(r.budget_usd) + ' used';
const models = m => Object.entries(m).map(([name, n]) => (name || 'unknown model') + ' ×' + n).join(', ');
const LABEL = {active: 'live', dormant: 'dormant', stopped: 'stopped'};
function pill(status) { const s = node('span', '', 'status ' + (LABEL[status] ? status : 'unknown')); s.append(node('span', '', 'status-dot'), LABEL[status] || status); return s; }
function color(who) { let h = 0; for (const c of who) h = (h * 31 + c.charCodeAt(0)) % 360; return 'hsl(' + h + ' 45% 34%)'; }

async function refresh(reload) {
  const buttons = document.querySelectorAll('.refresh'), asof = document.querySelectorAll('.as-of');
  for (const b of buttons) { b.disabled = true; b.textContent = 'Refreshing…'; }
  try {
    const r = await (await fetch('refresh', {method: 'POST'})).json();
    await reload();
    if (!r.refreshed) for (const a of asof) a.textContent = (r.error ? 'Refresh failed; still data as of ' : 'Already up to date as of ') + when(r.as_of);
  } catch (e) { for (const a of asof) a.textContent = 'Refresh unavailable'; }
  for (const b of buttons) { b.disabled = false; b.textContent = 'Refresh'; }
}

function boardRow(r, known) {
  const row = node('div', '', 'board-row'), head = node('div', '', 'board-head'), run = 'run.html?id=' + enc(r.id);
  head.append(link(run, r.env, 'row-title'));
  if (r.title) head.append(link(run, r.title));
  if (r.scenario) head.append(known.has(r.scenario) ? link('scenario.html?name=' + enc(r.scenario), r.scenario, 'text-link') : node('span', r.scenario, 'muted'));
  head.append(node('span', r.status === 'stopped' ? 'created ' + when(r.created) + ' · ran ' + span(r.runtime_seconds) + ' · replay and downloads still work' :
    'started ' + when(r.started) + ' · ' + (r.status === 'active' ? 'running ' : 'up ') + span(r.runtime_seconds), 'muted'));
  row.append(pill(r.status), head);
  if (r.blurb) row.append(node('div', r.blurb, 'board-line'));
  row.append(node('div', r.agents + ' agents · ' + models(r.models) + '   ·   ' + spend(r) + '   ·   ' + r.events.toLocaleString() + ' events' +
      (r.last_event_ts ? ' · last ' + span((Date.now() - Date.parse(r.last_event_ts)) / 1000) + ' ago' : '') + (r.stale ? '   ·   not refreshed since ' + when(r.as_of) : ''), 'board-line'),
    node('div', [...r.views, ...(r.agent_views ? [r.agent_views + ' agent sessions'] : [])].join(' · '), 'board-line'));
  const buttons = node('div', '', 'button-row');
  buttons.append(link(run, 'Watch', 'button'), link(run + '&mode=replay', 'Replay', 'button secondary'));
  if (r.results) buttons.append(link(run + '#results', 'Results · ' + r.results + ' files', 'button secondary'));
  buttons.append(link('runs/' + enc(r.id) + '/all.zip', 'Download', 'button secondary'));
  if (r.snap) buttons.append(link('world.html?id=' + enc(r.snap), 'Its snapshot', 'text-link'));
  row.append(buttons); row.dataset.search = [r.env, r.title, r.scenario, r.status, LABEL[r.status], ...Object.keys(r.models)].join(' ').toLowerCase();
  return row;
}
const ORDER = {active: 0, dormant: 1};
function board(site, runs, none = 'No environments here.') {
  const known = new Set(site.scenarios.map(s => s.name));
  $('board').replaceChildren(...(runs.length ? [...runs].sort((a, b) => (ORDER[a.status] ?? 2) - (ORDER[b.status] ?? 2) || (b.created || '').localeCompare(a.created || '')).map(r => boardRow(r, known))
    : [node('p', none, 'empty')]));
}
const asOf = t => { for (const a of document.querySelectorAll('.as-of')) a.textContent = 'Data as of ' + when(t); };
function siteChrome(site) { asOf(site.generated_at); if ($('site-title')) $('site-title').textContent = site.title; }
function worldCard(w) {
  const card = node('div', '', 'world-card'), counts = node('div', '', 'world-counts');
  for (const [n, label] of [[w.envs, 'ENVIRONMENTS'], [w.snapshots, 'SNAPSHOTS'], [w.agents, 'AGENTS']]) { const c = node('span', String(n)); c.append(node('small', label)); counts.append(c); }
  card.append(link('world.html?id=' + enc(w.id), w.ref, 'card-title'), node('p', w.message || 'No description'), counts,
    node('div', (w.scenario || 'scenario not recorded') + ' · ' + when(w.created), 'card-bottom'));
  return card;
}
function snapRow(s, depth = 0) {
  const row = node('div', '', 'snapshot-row'), main = node('div', '', 'snapshot-main'); row.style.setProperty('--depth', Math.min(depth, 8));
  main.append(link('world.html?id=' + enc(s.id), s.ref), node('p', (s.message || 'No description') + (s.envs.length ? ' · environments: ' + s.envs.join(', ') : '')));
  row.append(main, node('span', s.root ? 'World root' : 'Snapshot', 'tag'), node('span', when(s.created), 'muted'));
  return row;
}

async function indexPage() {
  const [site, lib] = await Promise.all([getJSON('site.json'), getJSON('worlds.json')]);
  siteChrome(site); document.title = $('title').textContent = site.title; board(site, site.runs, 'No environments yet.');
  $('board-filter').oninput = () => { const q = $('board-filter').value.toLowerCase(); for (const row of $('board').children) row.hidden = !(row.dataset.search || '').includes(q); };
  $('world-cards').replaceChildren(...site.worlds.map(worldCard));
  $('snapshot-count').textContent = 'All snapshots · ' + lib.snaps.length;
  $('snapshot-list').replaceChildren(...lib.snaps.map(s => snapRow(s)));
  $('scenario-cards').replaceChildren(...site.scenarios.map(s => {
    const card = node('a', '', 'scenario-card'); card.href = 'scenario.html?name=' + enc(s.name);
    card.append(node('h3', s.name), node('p', s.description), node('div', s.agents + ' agents · ' + s.roles + ' roles · ' + s.worlds + ' worlds · ' + s.runs.length + ' environments' + (s.active ? '' : ' · inactive'), 'card-bottom'));
    return card; }));
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
}

const fact = ([k, v]) => { const f = node('span'); f.append(node('small', k), String(v)); return f; };
const doc = (title, text) => { const d = node('details'); d.append(node('summary', title), node('pre', text, 'document')); return d; };

async function scenarioPage() {
  const name = params.get('name') || '', [site, s] = await Promise.all([getJSON('site.json'), getJSON('scenarios/' + enc(name) + '.json')]);
  siteChrome(site); document.title = s.name + ' · ' + site.title;
  $('title').textContent = s.name; $('description').textContent = s.description; $('github').href = s.github;
  const runs = site.runs.filter(r => r.scenario === s.name), worlds = site.worlds.filter(w => w.scenario === s.name);
  $('facts').replaceChildren(...[['AGENTS', s.agents], ['ROLES', s.roles.length], ['RUNTIME', s.runtime || '—'], ['COORDINATION', s.dispatcher ? 'Dispatcher' : 'Open interaction'],
    ['WORLDS', worlds.length], ['ENVIRONMENTS', runs.length], ...(s.active ? [] : [['STATUS', 'inactive']])].map(fact));
  $('briefing').replaceChildren(doc('Shared world briefing', s.world || 'This scenario has no separate shared world briefing.'), ...s.roles.map(r => doc(r.name, r.text)),
    ...(s.readme ? [doc('README', s.readme)] : []));
  board(site, runs, 'No environments from this scenario.');
  $('world-cards').replaceChildren(...(worlds.length ? worlds.map(worldCard) : [node('p', 'No worlds built from this scenario yet.', 'muted')]));
}

async function worldPage() {
  const id = params.get('id') || '', [site, lib] = await Promise.all([getJSON('site.json'), getJSON('worlds.json')]);
  const s = lib.snaps.find(x => x.id === id); if (!s) throw new Error('no such world or snapshot');
  const byId = new Map(lib.snaps.map(x => [x.id, x])), root = byId.get(s.root_id) || s, known = new Set(site.scenarios.map(x => x.name));
  siteChrome(site); document.title = s.ref + ' · ' + site.title;
  $('kind').textContent = s.root ? 'WORLD ROOT' : 'SNAPSHOT'; $('title').textContent = s.ref; $('description').textContent = s.message || 'No description';
  if (!s.root) $('crumbs').append(link('world.html?id=' + enc(root.id), root.ref));
  if (known.has(s.scenario)) $('crumbs').append(link('scenario.html?name=' + enc(s.scenario), 'scenario ' + s.scenario));
  $('facts').replaceChildren(...[['AGENTS', s.roster.length], ['MODEL', s.model || '—'], ['RUNTIME', s.runtime || '—'], ['CREATED', when(s.created)],
    ...(s.taken_from ? [['TAKEN FROM', s.taken_from]] : [])].map(fact));
  board(site, site.runs.filter(r => s.envs.includes(r.id)), 'No environments were launched from this exact snapshot.');
  const kids = p => lib.snaps.filter(x => x.parent === p).sort((a, b) => (a.created || '').localeCompare(b.created || ''));
  const walk = (x, depth) => { const row = snapRow(x, depth); if (x.id === s.id) row.style.background = '#f1f5e7'; return [row, ...kids(x.id).flatMap(k => walk(k, depth + 1))]; };
  $('tree').replaceChildren(...walk(root, 0));
  $('roster').replaceChildren(...s.roster.map(a => { const tr = node('tr'); tr.append(node('td', a.id), node('td', a.role || '—'), node('td', a.persona || '—'), node('td', a.model || '—')); return tr; }));
  const files = Object.entries(s.files);
  $('notes-section').hidden = !s.notes.length && !files.length;
  $('notes').replaceChildren(...s.notes.map(n => { const d = node('div', '', 'saved-note'); d.append(node('p', n.text), node('small', when(n.ts))); return d; }), ...files.map(([name, text]) => doc(name, text)));
  $('details').replaceChildren(...[['Snapshot id', s.id], ['Registry tag', s.tag || '—'], ['Flags', Object.entries(s.flags).map(([k, v]) => k + '=' + v).join(' ') || '—']]
    .flatMap(([k, v]) => [node('dt', k), node('dd', v)]));
}

async function runPage() {
  const id = params.get('id') || '', base = 'runs/' + enc(id) + '/', pane = $('pane');
  let run, view, events = [], stamps = [], shown = 0, agent = null, timer = null, at = 0;   // stamps[i]: event i's time, an undated one inheriting its predecessor's
  const times = () => stamps;
  function render(ev) {
    const n = node('div', '', 'ev kind-' + String(ev.kind).replace(/[^a-z0-9_-]/gi, '')), t = epoch(ev.ts);
    if (t != null) n.append(node('span', clockOf(t), 'ts'));
    if (ev.who) { const who = node('span', ev.who, 'who'); who.style.color = color(ev.who); n.append(who); }
    n.append(node('span', ev.text, 'text'));
    n.hidden = !n.textContent.toLowerCase().includes($('log-filter').value.toLowerCase());
    return n;
  }
  const restamp = () => { let last = null; stamps = events.map(e => last = epoch(e.ts) ?? last); };
  const bottom = () => { pane.scrollTop = pane.scrollHeight; };
  function count(text) { $('event-count').textContent = events.length.toLocaleString() + ' events'; $('live').textContent = text; }
  function showLatest() {                       // the last 500, "load earlier" prepends the 500 before
    shown = Math.min(events.length, 500);
    pane.replaceChildren(...(events.length ? events.slice(-shown).map(render) : [node('div', 'No events in this view.', 'muted')]));
    $('earlier').hidden = shown >= events.length; bottom(); count('As of ' + when(run.as_of));
  }
  $('earlier').onclick = () => { const more = Math.min(events.length - shown, 500);
    pane.prepend(...events.slice(events.length - shown - more, events.length - shown).map(render)); shown += more; $('earlier').hidden = shown >= events.length; };

  // Replay: the server's pacing (each event after its original gap ÷ speed), run in the browser.
  function stop() { clearTimeout(timer); timer = null; $('play').textContent = 'Play'; }
  function seekTo(index) { at = index; pane.replaceChildren(...events.slice(Math.max(0, at - 500), at).map(render)); bottom(); progress(); }
  function progress() {
    const t = times(), first = t.find(x => x != null), last = t[t.length - 1], now = t[Math.max(0, at - 1)];
    if (first != null && last > first && now != null) $('seek').value = Math.round(1000 * (now - first) / (last - first));
    $('clock').textContent = at + ' / ' + events.length + (now != null ? ' · ' + clockOf(now) : '');
    count(timer ? 'Replaying ' + $('speed').value + '×' : at >= events.length ? 'Replay complete' : 'Paused');
  }
  function step() {
    if (at >= events.length) return stop(), progress();
    pane.append(render(events[at++])); bottom();
    const t = times(), gap = at < events.length && t[at] != null && t[at - 1] != null ? (t[at] - t[at - 1]) / 1000 : 0;
    timer = setTimeout(step, gap > +$('gap').value ? 300 : 1000 * gap / +$('speed').value); progress();
  }
  $('play').onclick = () => { if (timer) return stop(), progress(); if (at >= events.length) seekTo(0); $('play').textContent = 'Pause'; timer = setTimeout(step, 0); };
  $('seek').oninput = () => { stop(); const t = times(), first = t.find(x => x != null), target = first + (t[t.length - 1] - first) * $('seek').value / 1000;
    const i = t.findIndex(x => x != null && x >= target); seekTo(i < 0 ? events.length : i); };
  $('mode').onchange = () => { stop(); const replay = $('mode').value === 'replay'; $('replay-tools').hidden = !replay; $('earlier').hidden = true; replay ? seekTo(0) : showLatest(); };
  $('log-filter').oninput = () => { const q = $('log-filter').value.toLowerCase(); for (const n of pane.children) n.hidden = !n.textContent.toLowerCase().includes(q); };

  function tabs() {
    const tab = v => { const b = node('button', v.agent ? v.name.split(':')[1] || 'session' : v.name, 'watch-view' + (v === view ? ' selected' : '')); b.onclick = () => select(v); return b; };
    $('views').replaceChildren(...run.views.filter(v => !v.agent).map(tab));
    if (agent) $('views').append(node('span', '', 'watch-view-break'), node('span', agent, 'watch-view-agent'), ...run.views.filter(v => v.agent === agent).map(tab));
    for (const c of $('agent-cards').children) c.classList.toggle('selected', c.dataset.agent === agent);
  }
  async function load() { const text = await (await fetch(base + 'views/' + view.file + '.jsonl', {cache: 'no-cache'})).text(); return text.split('\n').filter(Boolean).map(l => JSON.parse(l)); }
  async function select(v) {
    stop(); view = v; agent = v.agent; tabs();
    $('dl-txt').href = base + 'views/' + v.file + '.txt'; $('dl-jsonl').href = base + 'views/' + v.file + '.jsonl';
    document.title = run.env + ' · ' + v.name; pane.replaceChildren(node('div', 'Loading…', 'muted'));
    events = await load(); restamp(); $('mode').onchange();
  }
  function facts() {
    document.title = run.env; $('title').textContent = run.env; $('subtitle').textContent = [run.title, run.blurb].filter(Boolean).join(' — ');
    $('status').replaceChildren(pill(run.status)); asOf(run.as_of);
    const s = $('scenario-link'); s.textContent = run.scenario ? 'scenario ' + run.scenario : ''; s.href = 'scenario.html?name=' + enc(run.scenario);
    const counts = {}; for (const a of run.roster) counts[a.model] = (counts[a.model] || 0) + 1;
    $('facts').replaceChildren(...[['created', when(run.created)], ...(run.started ? [['started', when(run.started)]] : []), [run.status === 'stopped' ? 'ran' : 'up', span(run.runtime_seconds)],
      ['agents', run.roster.length + ' · ' + models(counts)], [run.budget_used == null ? 'budget' : 'spent', spend(run).replace(/^budget | used$/g, '')], ['container', run.container]].map(([k, v]) => { const f = node('span', k + ' '); f.append(node('b', v)); return f; }));
    $('lineage').replaceChildren(...run.lineage.flatMap((l, i) => { const step = node('span'), label = l.kind === 'env' ? 'this environment' : l.kind === 'root' ? 'world root ' + l.ref : l.kind + ' ' + (l.ref || 'not recorded');
      step.append(l.id ? link('world.html?id=' + enc(l.id), label) : node('b', label));
      if (l.message) step.append(node('i', ' “' + l.message + '”')); return i ? [node('span', '→'), step] : [step]; }));
    $('coverage').textContent = run.coverage; $('coverage').classList.toggle('stale', run.stale);
    $('agent-count').textContent = run.roster.length;
    $('agent-cards').replaceChildren(...run.roster.map(a => { const c = node('button', '', 'watch-agent'), own = run.views.find(v => v.agent === a.id);
      c.dataset.agent = a.id; c.append(node('b', a.id)); if (a.role) c.append(node('span', a.role, 'agent-role')); if (a.persona) c.append(node('i', a.persona)); if (a.model) c.append(node('small', a.model));
      c.disabled = !own; c.title = own ? '' : 'This agent’s session is not published'; c.onclick = () => select(own); return c; }));
    const box = $('budget'), used = run.budget_used;
    box.querySelector('b').textContent = used == null ? money(run.budget_usd) : money(used);
    box.querySelector('small').textContent = used == null ? 'limit; spend is not published' : 'of ' + money(run.budget_usd);
    box.querySelector('.bar>div').style.width = used != null && run.budget_usd ? Math.min(100, 100 * used / run.budget_usd) + '%' : '0';
    $('dl-all').href = base + 'all.zip';
    if (run.results.length) $('result-files').replaceChildren(...run.results.map(f => { const row = node('div', '', 'mirror-result'), get = link(base + f.file, 'download', 'text-link');
      get.download = f.name; row.append(node('b', f.name), node('small', Math.ceil(f.size / 1024).toLocaleString() + ' KB'));
      if (f.page) row.append(link(base + f.file + '.html', 'read', 'text-link')); row.append(get); return row; }));
  }
  run = await getJSON(base + 'run.json'); facts();
  if (params.get('mode') === 'replay') $('mode').value = 'replay';
  const wanted = run.views.find(v => v.name === params.get('view')) || run.views.find(v => v.name === 'feed') || run.views[0];
  if (wanted) await select(wanted); else pane.replaceChildren(node('div', 'No views are published for this run.', 'muted'));
  if (location.hash === '#results') $('results').scrollIntoView();
  for (const b of document.querySelectorAll('.refresh')) b.onclick = () => refresh(async () => {   // the view files only grow between builds: append what is new
    const before = events.length; run = await getJSON(base + 'run.json'); facts(); tabs();
    if (!view || $('mode').value === 'replay') return;
    events = await load(); restamp(); pane.append(...events.slice(before).map(render)); shown += events.length - before; bottom(); count('As of ' + when(run.as_of));
  });
}

const pages = {index: indexPage, scenario: scenarioPage, world: worldPage, run: runPage}, start = pages[document.body.dataset.page];
if (document.body.dataset.page !== 'run') for (const b of document.querySelectorAll('.refresh')) b.onclick = () => refresh(start);
start().catch(e => { document.querySelector('main').prepend(node('p', 'This page could not be loaded: ' + e.message, 'notice')); });
