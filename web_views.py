"""Human-facing workspace views; operations remain in web.py's CLI bridge."""
import contextvars
import html
import json
import re
import shlex
from urllib.parse import quote

from agentspace import db, registry, versioning

esc = lambda x: html.escape(str(x if x is not None else ""))
PUBLIC = contextvars.ContextVar("public", default=False)   # this request came through the public demo host (web.py sets it from Caddy's header)
DENIED = lambda verb, fields=None: None                     # web.py installs its demo policy here: why the demo may not run a verb, or None
DEMO_MAX_BUDGET = None                                      # web.py sets it; the launch page shows the cap
WATCH_VERBS = ("env start", "env kick", "env sleep", "env stop", "env post", "env logs", "env roll-sessions", "snap take", "env kill")
DEMO_NOTICE = ('<div class="notice demo-notice"><span><b>This is a shared demo of agentspace.</b> Build a world, launch it, and watch it run. '
               'Some controls are disabled unless you have the operator password, because they reach this server\u2019s own files and registry. '
               'Want all of it, with your own keys and no limits? <b>Fork the source at '
               '<a href="https://github.com/sfgeekgit/agentspace" target="_blank" rel="noopener noreferrer">github.com/sfgeekgit/agentspace</a> '
               'and run it on your own server.</b> Every control is yours there.</span></div>')
u = lambda x: quote(str(x), safe="")

ICONS = {
    'overview': '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    'scenario': '<path d="M5 4h10l4 4v12H5zM14 4v5h5M8 13h8M8 16h5"/>',
    'world': '<circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/>',
    'environment': '<path d="m9 5 11 7-11 7z"/>',
    'tools': '<path d="m4 5 6 7-6 7m9 0h7"/>',
    'help': '<circle cx="12" cy="12" r="9"/><path d="M9 9a3 3 0 0 1 6 0c0 2-3 2-3 5m0 3h.01"/>',
    'arrow': '<path d="M4 12h15m-6-6 6 6-6 6"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'snap': '<path d="m12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5"/>',
    'search': '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
}


def icon(name):
    return f'<svg class="icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS.get(name, ICONS["world"])}</svg>'


def page(title, body, **attrs):
    if PUBLIC.get():   # the watch page builds its buttons in JS; tell it which verbs the demo refuses
        attrs = {**attrs, 'demo': '1', 'denied': ','.join(v for v in WATCH_VERBS if DENIED(v))}
    attrs = ''.join(f' data-{k}="{esc(v)}"' for k, v in attrs.items())
    return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Agentspace</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/web.css"></head><body{attrs}>{body}<script src="/web.js"></script></body></html>'


def frame(body, active="overview", title="Workspace"):
    nav = ''.join(f'<a href="{href}" class="nav-link {"selected" if active == key else ""}" {"aria-current=page" if active == key else ""}>{icon(ico)}{label}</a>' for key, href, ico, label in [
        ('overview', '/', 'overview', 'Overview'), ('scenarios', '/scenarios', 'scenario', 'Scenarios'),
        ('worlds', '/worlds', 'world', 'Worlds & snapshots'), ('environments', '/environments', 'environment', 'Environments')])
    return f'''<a class="skip" href="#content">Skip to content</a><aside class="sidebar"><a class="brand" href="/"><span class="brand-mark">a<span>✳</span></span>agentspace<span class="brand-period">.</span></a>
    <div class="workspace-label">YOUR WORKSPACE</div><nav aria-label="Main navigation">{nav}</nav>
    <div class="sidebar-bottom"><a class="nav-link {"selected" if active == "tools" else ""}" href="/tools">{icon('tools')}Advanced tools</a><a class="nav-link {"selected" if active == "help" else ""}" href="/help">{icon('help')}Getting started</a></div></aside>
    <div class="workspace"><header class="topbar"><span>{esc(title)}</span><div class="topbar-right"><a href="/help">Quick guide ↗</a></div></header><main id="content">{DEMO_NOTICE if PUBLIC.get() else ""}{body}</main><footer>Agentspace <span>Explore. Run. Observe. Repeat.</span></footer></div>
    <dialog id="action-dialog" aria-labelledby="dialog-title"><div class="dialog-head"><div><span class="eyebrow">WORKSPACE ACTION</span><h2 id="dialog-title"></h2></div><button type="button" class="icon-button" data-close aria-label="Close dialog">×</button></div><p id="dialog-note" class="muted"></p><div id="dialog-fields"></div></dialog>
    <section id="activity" class="activity" hidden aria-label="Action output"><div class="activity-head"><span id="outlabel">Activity</span><span id="elapsed"></span><button id="stop" class="small">Interrupt</button><button class="icon-button" id="close-output" aria-label="Minimize output">×</button></div><pre id="out" tabindex="0" role="log" aria-label="Operation output"></pre><div id="result-link" aria-live="polite"></div></section><button id="activity-toggle" hidden>View activity</button><div id="toast" role="status" hidden></div>'''


def header(kicker, title, description, action=''):
    return f'<div class="page-heading"><div><div class="eyebrow">{esc(kicker)}</div><h1>{esc(title)}</h1><p class="muted">{esc(description)}</p></div>{action}</div>'


def link(href, text, cls='button', ico=None):
    return f'<a href="{esc(href)}" class="{cls}">{icon(ico) if ico else ""}{esc(text)}</a>'


def action(verb, text, fields=None, cls='button secondary', note=''):
    if why := DENIED(verb):
        return f'<button class="{cls}" disabled title="{esc(why)}">{esc(text)} <span class="tag">{esc(why)}</span></button>'
    return f'<button class="{cls}" data-action="{esc(verb)}" data-fields="{esc(json.dumps(fields or {}))}" data-note="{esc(note)}">{esc(text)}</button>'


def status(value):
    value = value or 'unknown'
    cls = value.split(' ')[0]
    if cls not in ('active', 'dormant', 'stopped', 'missing'):
        cls = 'unknown'
    return f'<span class="status {cls}"><span class="status-dot"></span>{esc(value)}</span>'


def empty(text, href='', label=''):
    return f'<div class="empty">{icon("world")}<p>{esc(text)}</p>{link(href, label) if href else ""}</div>'


def source(snap):
    """Return source and evidence, preserving uncertainty in pre-provenance roots."""
    if snap.get('scen'):
        return snap['scen'], 'recorded'
    match = re.search(r'(?:^|[ ,])scen=([a-zA-Z0-9_]+)', snap.get('creation_message') or '')
    if match:
        return match[1], 'creation note'
    if (registry.SCENARIOS_DIR / snap['scenario'] / 'scenario.toml').is_file():
        return snap['scenario'], 'matching name'
    return None, 'not recorded'


def root_for(snap, snaps):
    by_id = {s['snap_id']: s for s in snaps}
    cur, seen = snap, set()
    while cur and cur['snap_id'] not in seen:
        seen.add(cur['snap_id'])
        if versioning.is_world_root(cur['version']):
            return cur
        cur = by_id.get(cur.get('parent_snap_id'))
    # Legacy labels may retain the version path but no parent id.
    ver = snap['version'].split('.')[0] + '.0'
    return next((s for s in snaps if s['scenario'] == snap['scenario'] and s['version'] == ver), None)


def ref(s):
    return f"{s['scenario']}:{s['version']}"


def snap_url(s):
    return '/snapshots/' + u(s['snap_id'])


def world_url(s):
    return '/worlds/' + u(s['snap_id'])


def crumb(items):
    return '<nav class="breadcrumbs" aria-label="Breadcrumb">' + '<span>›</span>'.join(link(url, title, '') if url else f'<strong>{esc(title)}</strong>' for title, url in items) + '</nav>'


def flow(current=0):
    items = [('scenario', 'Scenario', 'Choose the recipe'), ('world', 'World root', 'Prepare a starting point'), ('environment', 'Environment', 'Run and observe'), ('snap', 'Snapshot', 'Save and branch')]
    return '<div class="workflow">' + ''.join(f'<div class="flow-step {"current" if i == current else ""}"><span class="step-icon">{icon(ico)}</span><div><b>{title}</b><small>{subtitle}</small></div></div>{"<span class=flow-arrow>→</span>" if i < 3 else ""}' for i, (ico,title,subtitle) in enumerate(items)) + '</div>'


def env_table(envs, snaps, limit=None):
    by_id = {s['snap_id']:s for s in snaps}
    rows = ''
    for e in envs[:limit] if limit else envs:
        s = by_id.get(e['snap_id'])
        root = root_for(s, snaps) if s else None
        lineage = link(world_url(root), ref(root), 'text-link') if root else 'Root not indexed'
        rows += f'<tr data-search="{esc(e["name"] + " " + (ref(s) if s else "") + " " + (e["status"] or ""))}"><td><a class="row-title" href="/watch/{u(e["name"])}">{icon("environment")}{esc(e["name"])}</a><small>{esc(e.get("host") or "localhost")}</small></td><td>{lineage}<small>{"From starting point" if s and root and s["snap_id"] == root["snap_id"] else "From " + esc(ref(s)) if s else "Unknown snapshot"}</small></td><td>{status(e["status"])}</td><td class="row-end">{link("/watch/"+u(e["name"]), "Open environment →", "text-link")}</td></tr>'
    return f'<div class="table-wrap"><table class="env-table"><thead><tr><th>Environment</th><th>World root / source</th><th>Recorded status</th><th><span class="sr-only">Open</span></th></tr></thead><tbody>{rows}</tbody></table></div>' if rows else empty('No environments yet. Launch one from a world root.', '/worlds', 'Explore worlds')


def scenario_card(s, snaps):
    roots = [x for x in snaps if versioning.is_world_root(x['version']) and source(x)[0] == s['name']]
    count = str(s['min_agents']) if s['min_agents'] == s['max_agents'] else f'{s["min_agents"]}–{s["max_agents"]}'
    return f'''<a class="scenario-card" data-search="{esc(s['name']+' '+s['description'])}" href="/scenarios/{u(s['name'])}"><div class="card-top"><span class="tile-icon">{icon('scenario')}</span><span class="tag">{esc(s.get('runtime') or 'unavailable')}</span></div><h3>{esc(s['name'])}</h3><p>{esc(s['description'])}</p><div class="card-bottom"><span>{count} agents · {'Dispatcher' if s.get('has_dispatch') else 'Open interaction'}</span><span>↗</span></div><div class="card-footnote">{len(roots)} world roots · {'Available' if s['active'] else 'Inactive'}</div></a>'''


def world_card(s, snaps, envs):
    family = [x for x in snaps if (root_for(x, snaps) or {}).get('snap_id') == s['snap_id']]
    ids = {x['snap_id'] for x in family}
    runs = [e for e in envs if e['snap_id'] in ids]
    scen, evidence = source(s)
    return f'''<article class="world-card" data-search="{esc(ref(s)+' '+(scen or '')+' '+(s.get('creation_message') or ''))}"><div class="card-top"><span class="tile-icon">{icon('world')}</span><span class="tag root-tag">ROOT {esc(s['version'])}</span></div><a class="card-title" href="{world_url(s)}">{esc(s['scenario'])}</a><p>From {esc(scen or 'an unrecorded scenario')}{' <span title="Inferred from '+esc(evidence)+'">↝</span>' if scen and evidence != 'recorded' else ''}</p><div class="world-counts"><span>{len(s.get('agents') or [])}<small>agents</small></span><span>{max(0,len(family)-1)}<small>saved snaps</small></span><span>{len(runs)}<small>environments</small></span></div><div class="card-bottom">{link(world_url(s), 'Explore world', 'text-link')}{link('/fork/'+u(s['snap_id']), 'Launch', 'button small', 'environment')}</div></article>'''


def scenario_warnings(problems):
    return ''.join(
        f'<div class="notice">Could not load {esc(p["name"])}: {esc(p["reason"])} '
        f'{action("scen deactivate", "Disable", {"scen_name": p["name"]}) if p["can_disable"] else ""}</div>'
        for p in problems
    )


def overview():
    snaps, envs = db.list_snaps(), db.list_envs()
    roots = sorted((s for s in snaps if versioning.is_world_root(s['version'])), key=lambda s:s['created_at'], reverse=True)
    scens, problems = registry.scan_scens()
    recent = sorted(envs, key=lambda e:(e['status']=='active', e.get('created_at') or ''), reverse=True)
    art = '''<div class="world-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="planet"><span>✳</span></div><span class="satellite sat-one"></span><span class="satellite sat-two"></span><span class="art-label art-root">01 / CREATE</span><span class="art-label art-run">02 / OBSERVE</span><span class="art-label art-save">03 / BRANCH</span></div>'''
    body = f'''<section class="hero"><div class="hero-copy"><div class="eyebrow">A SPACE FOR AGENT EXPERIMENTS</div><h1>Build a world.<br><em>See what unfolds.</em></h1><p>Start with a scenario. Bring a cast of agents to life.<br>Save a moment, take another path, and see what changes.</p><div class="hero-actions">{link('/scenarios','Create a world','button','plus')}{link('/worlds','Explore existing worlds →','text-link')}</div></div>{art}</section>
    {flow()}<div class="stats-strip">{''.join(f'<a href="{href}"><strong>{n:02d}</strong><span>{label}</span>{icon(ico)}</a>' for n,label,href,ico in [(len(registry.list_scens(include_inactive=True)),'loadable scenarios','/scenarios','scenario'),(len(roots),'world roots','/worlds','world'),(len(snaps)-len(roots),'saved snapshots','/worlds?view=snapshots','snap'),(len(envs),'environments','/environments','environment')])}</div>
    {scenario_warnings(problems)}
    <section class="section"><div class="section-heading"><div><h2>Continue exploring</h2><p class="muted">Your environments, ready to observe.</p></div>{link('/environments','All environments →','text-link')}</div><div class="panel">{env_table(recent, snaps, 4)}</div></section>
    <section class="section"><div class="section-heading"><div><h2>Start somewhere new</h2><p class="muted">A scenario is a recipe. Make it your own.</p></div>{link('/scenarios','Browse all scenarios →','text-link')}</div><div class="cards three">{''.join(scenario_card(s,snaps) for s in sorted(scens,key=lambda s:({'pd':0,'support_desk2':1,'mafia':2}.get(s['name'],3),s['name']))[:3])}</div></section>
    <section class="section"><div class="section-heading"><div><h2>Recently built worlds</h2><p class="muted">Prepared starting points for your next run.</p></div>{link('/worlds','All worlds →','text-link')}</div><div class="cards three">{''.join(world_card(s,snaps,envs) for s in roots[:3])}</div></section>'''
    return page('Overview', frame(body))


def searchbar(placeholder):
    return f'<label class="search-box">{icon("search")}<input id="filter" type="search" placeholder="{esc(placeholder)}" aria-label="{esc(placeholder)}"><kbd>/</kbd></label><p id="no-matches" class="empty" hidden>No matches. Try a different name.</p>'


def scenarios():
    _, problems = registry.scan_scens()
    scens = registry.list_scens(include_inactive=True)
    snaps = db.list_snaps()
    body = header('01 / SCENARIOS','Choose a starting point','Explore the rules, roles, and possibilities. Build a world when you’re ready.')
    body += searchbar('Find a scenario…') + scenario_warnings(problems) + '<div class="cards three">' + ''.join(scenario_card(s,snaps) for s in scens) + '</div>'
    body += '<div class="quiet-note">Creating an entirely new scenario starts with its files. <a href="/help#authoring">See the authoring guide →</a></div>'
    return page('Scenarios',frame(body,'scenarios','Scenario library'))


def scenario_detail(name):
    s = registry.load_scen(name)
    snaps, envs = db.list_snaps(), db.list_envs()
    roots = sorted([x for x in snaps if versioning.is_world_root(x['version']) and source(x)[0] == name], key=lambda x:x['created_at'], reverse=True)
    roles_dir = s.get('roles_dir')
    roles = ''.join(f'<details><summary>{esc(p.stem)}</summary><pre class="document" tabindex="0">{esc(p.read_text())}</pre></details>' for p in sorted(roles_dir.glob('*.md'))) if roles_dir else '<p class="muted">No separate role briefings.</p>'
    world = s['dir']/'world.md'
    text = world.read_text() if world.is_file() else 'This scenario has no separate shared world briefing.'
    body = crumb([('Scenarios','/scenarios'),(name,'')]) + header('SCENARIO',name,s['description'],link('/new/'+u(name),'Build world root','button','plus') if s['active'] else '<span class="tag">Inactive scenario</span>')
    body += f'<div class="fact-strip"><span><small>AGENTS</small>{s["min_agents"]}–{s["max_agents"]}</span><span><small>RUNTIME</small>{esc(s.get("runtime"))}</span><span><small>COORDINATION</small>{"Dispatcher" if s.get("has_dispatch") else "Open interaction"}</span><span><small>WORLD ROOTS</small>{len(roots)}</span></div>'
    body += f'<section class="section"><div class="section-heading"><div><h2>Worlds from this scenario</h2><p class="muted">Reuse a starting point, or build one with your own settings. ↝ marks inferred legacy links.</p></div></div><div class="cards three">{"".join(world_card(x,snaps,envs) for x in roots)}</div>{empty("No worlds yet. Build the first starting point.") if not roots else ""}</section>'
    body += f'<section class="section panel padded"><h2>What the agents are told</h2><p class="muted">The scenario’s shared briefing and private role descriptions.</p><details><summary>Shared world briefing</summary><pre class="document" tabindex="0">{esc(text)}</pre></details>{roles}</section>'
    body += f'<p class="quiet-note"><a href="https://github.com/sfgeekgit/agentspace/tree/main/scenarios/{u(name)}" target="_blank" rel="noopener noreferrer">View scenario on GitHub ↗</a></p>'
    body += '<details class="section"><summary>Scenario maintenance</summary><div class="button-row">'+action('scen deactivate','Deactivate scenario',{'scen_name':name},note='This hides this scenario from new builds. Existing worlds remain available.')+link('/tools?group=scen','Environment image tools','button secondary')+'</div></details>'
    return page(name,frame(body,'scenarios','Scenario library'))


def worlds(view='roots'):
    snaps, envs = db.list_snaps(), db.list_envs()
    roots = sorted([s for s in snaps if versioning.is_world_root(s['version'])],key=lambda s:s['created_at'],reverse=True)
    body = header('02 / WORLDS','Worlds & snapshots','Every world begins with a root. Every saved moment opens another path.',link('/scenarios','Create a world','button','plus'))
    body += f'<div class="tab-nav">{link("/worlds",f"World roots · {len(roots)}","selected" if view!="snapshots" else "")}{link("/worlds?view=snapshots",f"All snapshots · {len(snaps)}","selected" if view=="snapshots" else "")}</div>'+searchbar('Find a world, scenario, or snapshot…')
    if view == 'snapshots':
        body += '<div class="panel snapshot-list">'+''.join(snapshot_row(s) for s in sorted(snaps,key=lambda s:s['created_at'],reverse=True))+'</div>'
    else:
        body += '<div class="cards three">'+''.join(world_card(s,snaps,envs) for s in roots)+'</div>'
    if not snaps:
        body += empty('Build a world root to begin.', '/scenarios','Choose a scenario')
    body += '<div class="quiet-note">Have a published snapshot? '+action('snap pull','Import from registry',cls='text-button')+'</div>'
    return page('Worlds & snapshots',frame(body,'worlds','World library'))


def snapshot_row(s, depth=0):
    root = versioning.is_world_root(s['version'])
    return f'<div class="snapshot-row" style="--depth:{min(depth,8)}" data-search="{esc(ref(s)+" "+(s.get("creation_message") or ""))}"><span class="snapshot-icon">{icon("world" if root else "snap")}</span><div class="snapshot-main"><a href="{snap_url(s)}">{esc(ref(s))}</a><p>{esc(s.get("creation_message") or "No description")}</p></div><span class="tag">{"World root" if root else "Snapshot"}</span>{"<span class=dirty>Unpublished notes</span>" if s.get("notes_dirty") else ""}{link("/fork/"+u(s['snap_id']),"Launch →","text-link")}</div>'


def world_detail(sid):
    snaps, envs = db.list_snaps(), db.list_envs()
    root = next((s for s in snaps if s['snap_id']==sid),None)
    if not root: return None
    if not versioning.is_world_root(root['version']):
        return snapshot_detail(sid)
    family = [s for s in snaps if (root_for(s,snaps) or {}).get('snap_id')==sid]
    ids={s['snap_id'] for s in family}
    scen,evidence=source(root)
    body=crumb([('Worlds','/worlds'),(ref(root),'')])+header('WORLD ROOT',ref(root),'A prepared world, before its agents have taken a turn.',link('/fork/'+u(sid),'Launch environment','button','environment'))
    body += f'<div class="notice">{icon("scenario")} Scenario: {link("/scenarios/"+u(scen),scen,"text-link") if scen else "Not recorded"} <span class="muted">{esc(" · inferred from "+evidence) if scen and evidence != "recorded" else ""}</span><span class="push-right">{link(snap_url(root),"Root details & notes →","text-link")}</span></div>'
    body += f'<section class="section"><div class="section-heading"><div><h2>Environments</h2><p class="muted">Independent runs launched from this world’s snapshots.</p></div></div><div class="panel">{env_table([e for e in reversed(envs) if e["snap_id"] in ids],snaps)}</div></section>'
    children={}
    for s in family:
        children.setdefault(s.get('parent_snap_id'),[]).append(s)
    seen=set()
    def tree(s,depth=0):
        if s['snap_id'] in seen:return ''
        seen.add(s['snap_id'])
        return snapshot_row(s,depth)+''.join(tree(c,depth+1) for c in children.get(s['snap_id'],[]))
    rows=tree(root)
    for s in family:
        if s['snap_id'] not in seen:rows+=tree(s)
    body += f'<section class="section"><div class="section-heading"><div><h2>Saved history</h2><p class="muted">Launch any saved moment to explore another continuation.</p></div><span class="tag">{len(family)} snapshots including root</span></div><div class="panel snapshot-list">{rows}</div></section>'
    return page(ref(root),frame(body,'worlds','World library'))


def roster_table(s):
    roster=s.get('roster') or [{'id':a} for a in s.get('agents') or []]
    return '<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Role</th><th>Model</th><th>Persona</th></tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{esc(a.get(k) or (s.get("model") if k=="model" else None) or "—")}</td>' for k in ['id','role','model','persona'])+'</tr>' for a in roster)+'</tbody></table></div>'


def snapshot_detail(sid):
    s=db.get_snap_by_id(sid)
    if not s:return None
    root=root_for(s,db.list_snaps())
    crumbs=[('Worlds','/worlds')]+([(ref(root),world_url(root))] if root else [])+[(ref(s),'')]
    body=crumb(crumbs)+header('WORLD ROOT SNAPSHOT' if versioning.is_world_root(s['version']) else 'SAVED SNAPSHOT',ref(s),s.get('creation_message') or 'A saved moment.',link('/fork/'+u(sid),'Launch environment','button','environment'))
    body += f'<div class="fact-strip"><span><small>CREATED</small>{esc(s["created_at"][:10])}</span><span><small>RUNTIME</small>{esc(s.get("runtime"))}</span><span><small>PARENT</small>{esc(s.get("parent_version") or "Starting point")}</span><span><small>CAPTURED FROM</small>{esc(s.get("env_name") or "World build")}</span></div>'
    body += '<section class="section panel padded"><h2>Agent roster</h2>'+roster_table(s)+'</section>'
    notes=s.get('notes') or []
    notes_html=''.join(f'<article class="saved-note"><p>{esc(n.get("text",n)) if isinstance(n,dict) else esc(n)}</p><small>{esc(n.get("ts",n.get("at",""))) if isinstance(n,dict) else ""}</small></article>' for n in notes)
    body += '<section class="section panel padded"><div class="section-heading"><div><h2>Notes & findings</h2><p class="muted">Annotations stay local until you publish them.</p></div>'+action('snap note','Add note',{'snap_ref':ref(s)})+'</div>'+notes_html+('<p class="muted">No notes yet. Record what makes this moment worth keeping.</p>' if not notes else '')+('<p class="dirty">Unpublished changes</p>' if s.get('notes_dirty') else '')+'</section>'
    files=s.get('files') or {}
    body += '<details class="section panel padded"><summary>Attachments & provenance</summary>'+''.join(f'<details><summary>{esc(name)}</summary><pre class="document" tabindex="0">{esc(text)}</pre></details>' for name,text in files.items())+f'<p>Registry reference</p><code class="copy-line">{esc(s.get("ghcr_tag"))}</code><div class="button-row">'+action('snap attach','Attach files',{'snap_ref':ref(s)},note='Choose text file paths on this control machine, one shell-quoted path per file.')+action('snap extract','Extract files & scenario source',{'snap_ref':ref(s)})+action('snap show','Full snapshot metadata',{'snap_ref':ref(s)})+'</div></details>'
    body += '<div class="button-row">'+action('snap push','Publish snapshot / notes',{'snap_ref':ref(s)},note='Publishes this image and its annotations to the configured container registry.')+'</div>'
    return page(ref(s),frame(body,'worlds','Snapshot details'))


def environments():
    body=header('03 / ENVIRONMENTS','Where worlds come to life','Open a run to watch its logs, meet its agents, and save what happens.',link('/worlds','Launch an environment','button','plus'))+searchbar('Find an environment, world, or status…')
    body+='<div class="panel">'+env_table(list(reversed(db.list_envs())),db.list_snaps())+'</div><p class="quiet-note">Statuses here are last recorded. Open an environment to check its live status.</p>'
    return page('Environments',frame(body,'environments','Environments'))


def fork_page(sid):
    s=db.get_snap_by_id(sid)
    if not s:return None
    root=versioning.is_world_root(s['version'])
    demo=PUBLIC.get()
    budget_max=f' max="{DEMO_MAX_BUDGET:g}"' if demo else ''
    budget_note=f'All agents share this cap. Demo launches are capped at ${DEMO_MAX_BUDGET:g}.' if demo else 'All agents share this cap. You can top up later.'
    off=' disabled' if demo else ''
    off_tag=f' <span class="tag">{esc(DENIED("snap attach") or "")}</span>' if demo else ''
    body=crumb([('Worlds','/worlds'),(ref(s),snap_url(s)),('Launch environment','')])+header('LAUNCH / ENVIRONMENT','Give this world a life of its own.','Create an independent environment from this saved starting point.')
    body+=f'''<div class="form-layout"><form class="panel padded form-card" data-path="snap fork" id="launch-form"><h2>Launch settings</h2><input type="hidden" name="snap_ref" value="{esc(ref(s))}"><label>Environment name<input name="new_env_name" required pattern="[a-zA-Z0-9][a-zA-Z0-9_.-]*" placeholder="{esc(s['scenario'])}_run" autocomplete="off"><small>A unique name for this run.</small></label><label>Shared budget (USD)<input type="number" name="budget_usd" min="0.01" step="0.01" value="2.00" required{budget_max}><small>{budget_note}</small></label><label>When the environment is ready<select name="kick"><option value="on" {"selected" if root else ""}>Wake agents and begin the run</option><option value="off" {"selected" if not root else ""}>Wait for me to wake the agents</option></select></label><details><summary>Advanced launch settings</summary><label>Host{off_tag}<input name="host" value="localhost"{off}></label><label>Override model<input name="model" placeholder="Keep the snapshot’s model"></label><label>Persona file overrides{off_tag}<textarea name="souls" placeholder="agent_id=path/to/file.md (one per line)"{off}></textarea></label><label>Existing OpenRouter key<input type="password" name="existing_key" autocomplete="off"><small>Leave blank to provision a fresh key for this environment.</small></label></details><p class="notice">Launching provisions a budgeted environment. Agents can spend credits as soon as they are woken.</p><button class="button" type="submit">Launch environment {icon('arrow')}</button></form><aside class="launch-summary"><div class="eyebrow">YOUR STARTING POINT</div><h2>{esc(ref(s))}</h2><span class="tag">{'Never-run world root' if root else 'Saved snapshot'}</span><p>{esc(s.get('creation_message'))}</p><dl><dt>Agents</dt><dd>{len(s.get('agents') or [])}</dd><dt>Runtime</dt><dd>{esc(s.get('runtime'))}</dd><dt>State</dt><dd>{'Fresh start' if root else 'Continues from saved state'}</dd></dl><hr><p>The source snapshot stays unchanged. When this environment is ready, you’ll go straight to its live view.</p></aside></div>'''
    return page('Launch environment',frame(body,'environments','Launch environment'))


def terminal_note(name):
    cmd='python3 zookeeper.py env exec '+shlex.quote(name)+' ls /world'
    enter='python3 zookeeper.py env enter '+shlex.quote(name)
    return f'<details class="terminal-note"><summary>{icon("tools")} Terminal access</summary><p>Run a command inside this container from your terminal:</p><div class="copy-row"><code>{esc(cmd)}</code><button class="small" data-copy="{esc(cmd)}">Copy</button></div><p>For connection instructions:</p><div class="copy-row"><code>{esc(enter)}</code><button class="small" data-copy="{esc(enter)}">Copy</button></div></details>'


def watch(name):
    e = db.get_env(name)
    if not e:
        return None
    s = db.get_snap_by_id(e['snap_id']) or {}
    root = root_for(s, db.list_snaps()) if s else None
    roster = {a['id']: a for a in s.get('roster') or []}
    cards = ''
    for aid in s.get('agents') or []:
        a = roster.get(aid, {})
        cards += (f'<button class="watch-agent" data-agent="{esc(aid)}" aria-pressed="false" '
                  f'aria-label="View agent {esc(aid)}"><b>{esc(aid)}</b>'
                  + (f'<span class="agent-role">{esc(a["role"])}</span>' if a.get('role') else '')
                  + (f'<i>{esc(a["persona"])}</i>' if a.get('persona') else '')
                  + (f'<small>{esc(a["model"])}</small>' if a.get('model') else '') + '</button>')
    breadcrumbs = crumb([('← Environments', '/environments')]
                       + ([(ref(root), world_url(root))] if root else []))
    facts = (f'<span>snapshot {link(snap_url(s), ref(s), "text-link") if s else "not indexed"}</span>'
             f'<span>runtime <b>{esc(s.get("runtime") or "—")}</b></span>'
             f'<span>host <b>{esc(e["host"] or "localhost")}</b></span>'
             f'<span>agents <b>{len(s.get("agents") or [])}</b></span>'
             f'<span>created <b>{esc((e.get("created_at") or "")[:16])}</b></span>'
             '<span id="runtime-fact"></span><span id="started-fact"></span>')
    speeds = ''.join(f'<option value="{n}">Replay {n}×</option>' for n in (1, 2, 5, 10, 30))
    body = (f'<header class="watch-header"><div class="watch-topline">{breadcrumbs}'
            '<a href="/help" class="text-link">Watch guide ↗</a></div>'
            f'<div class="watch-heading"><h1>{esc(name)}</h1><span id="live-status">{status(e["status"])}</span>'
            '<div id="actions" class="button-row"></div></div>'
            f'<div class="watch-facts">{facts}<span id="status-check">Checking live status…</span></div></header>'
            '<div class="watch-layout"><aside id="agents" aria-label="Agents">'
            f'<h2>Agents <span>{len(s.get("agents") or [])}</span></h2>{cards or "<p class=muted>No agents recorded.</p>"}</aside>'
            '<div class="watch-divider" data-resize="agents" tabindex="0" role="separator" '
            'aria-label="Resize agent list" aria-orientation="vertical" aria-controls="agents"></div>'
            '<section class="watch-main" aria-label="Environment logs"><div class="watch-tabs">'
            '<div id="views" role="tablist" aria-label="Log views"><span class="muted">Loading views…</span></div>'
            f'<label class="watch-playback"><span class="sr-only">Playback</span><select id="speed"><option value="">Live</option>{speeds}</select></label></div>'
            '<div class="watch-log-tools"><label class="watch-filter"><span class="sr-only">Filter visible events</span>'
            '<input id="log-filter" type="search" placeholder="Filter this view…"></label>'
            '<button id="reconnect" class="text-button">Reconnect</button><button id="download-logs" class="text-button">Download view</button></div>'
            '<div id="log-panel" role="tabpanel"><div id="pane" tabindex="0" role="log" aria-label="Environment events"></div></div>'
            '<div class="watch-stream-status"><span id="live">Connecting…</span><span id="event-count">0 events</span>'
            '<span id="paused" hidden>Scroll paused</span><button id="jump-latest" class="text-button">Jump to latest ↓</button></div>'
            '<form id="chat" hidden><label id="chatwho" for="chat-text">Message agent</label>'
            '<div class="chat-input"><input id="chat-text" name="text" autocomplete="off" placeholder="Message this agent (wakes it)" required>'
            '<button>Send message</button></div></form><div id="chatlog" tabindex="0" aria-live="polite" aria-label="Operator conversation"></div></section>'
            '<div class="watch-divider" data-resize="side" tabindex="0" role="separator" '
            'aria-label="Resize budget and output" aria-orientation="vertical" aria-controls="side"></div>'
            '<aside id="side" aria-label="Budget and output"><section class="watch-budget"><h2>Shared budget</h2>'
            '<div id="budget"><b>…</b><div class="bar"><div></div></div><small>Checking usage</small></div>'
            + action('budget topup', 'Top up', {'env_name': name}, 'button secondary small')
            + '</section><section id="watch-output" aria-label="Action output area"><h2>Action output</h2>'
            '<p id="output-placeholder" class="muted">Output from the controls above appears here.</p></section>'
            '<details class="watch-details"><summary>Environment details</summary><dl id="environment-details" class="detail-facts">'
            '<dt>Status</dt><dd>Checking…</dd></dl></details>' + terminal_note(name) + '</aside></div>')
    return page(name, frame(body, 'environments', 'Environment watch'), env=name)


def tools_page(leaves,special,form_html):
    body=header('ADVANCED / TOOLS','The rest of the toolbox','Less frequent operations, grouped by task. Each uses the same commands as the CLI.')+searchbar('Find an operation…')
    names={'snap':'Snapshots & publishing','env':'Environment operations','budget':'Budget management','world':'Build with explicit overrides','scen':'Scenario maintenance'}
    body+='<div id="verbs" class="tool-list">'
    for group in dict.fromkeys(path[0] for path in leaves):
        title = names.get(group, group.replace("_", " ").title())
        body+=f'<section data-search-group><h2 id="group-{group}">{title}</h2>'
        for path,cmd in leaves.items():
            key=' '.join(path)
            if path[0]!=group:continue
            if key in special:
                if special[key]:continue
                args=' '.join('<'+p.name+'>' for p in cmd.params if getattr(p,'param_type_name','')=='argument')
                body+=f'<details data-search="{esc(key)}"><summary>{esc(key)} <span class="tag">Terminal only</span></summary><p>Use your terminal for this operation.</p><code class="copy-line">python3 zookeeper.py {esc(key)} {esc(args)}</code></details>'
            else:
                why=DENIED(key)   # the demo shows every form; refused ones are disabled and say why
                form=f'<fieldset disabled class="demo-off">{form_html(path,cmd)}</fieldset>' if why else form_html(path,cmd)
                body+=f'<details data-search="{esc(key+" "+cmd.get_short_help_str())}"><summary>{esc(key)}{f"<span class=tag>{esc(why)}</span>" if why else ""}<span>{esc(cmd.get_short_help_str(110))}</span></summary>{form}</details>'
        body+='</section>'
    body+='</div><section class="section"><h2>Recent operations</h2><div id="runs"></div></section>'
    refs=[ref(s) for s in db.list_snaps()]
    for id,vals in [('dl-envs',[e['name'] for e in db.list_envs()]),('dl-snaps',refs),('dl-scens',[s['name'] for s in registry.list_scens()])]:
        body+=f'<datalist id="{id}">'+''.join(f'<option value="{esc(v)}">' for v in vals)+'</datalist>'
    return page('Advanced tools',frame(body,'tools','Advanced tools'))


def help_page():
    body=header('FIELD GUIDE','A small guide to possible worlds.','Four ideas, and a workflow you can return to.')+flow()
    body+='''<div class="guide panel padded"><h2>Start with a scenario</h2><p>A <b>scenario</b> (or <b>scen</b>) is a recipe: the rules, roles, shared situation, and materials. Choose one to explore its briefing and existing worlds.</p><h2>Build a world root</h2><p>Choose your agent count, models, personas, and scenario parameters. <b>Build</b> prepares a starting point before any agents have acted. A root is technically a special snapshot, but it is the beginning of your world’s family. Building is local; publishing is separate.</p><h2>Launch an environment</h2><p><b>Fork</b> creates an independent environment from any snapshot. Give it a unique name and a budget. Root forks wake agents by default; later snapshots wait for a wake by default. The source snapshot stays unchanged.</p><h2>Observe, save, and branch</h2><p>The watch page keeps agents on the left, log views in the center, and budget and action output on the right. Use the log tabs for the feed, board, announcements, budget events, and scenario-specific logs. Click an agent to open its session and transcript facets, with private messaging below the logs. Playback replays recorded events; it does not run agents again. Save a snapshot at a useful moment, then launch another environment from it.</p><h2>Names tell a story</h2><p><code>desk_trial:1.0</code> is a world root; <code>desk_trial:1.1</code> is a child snapshot; <code>desk_trial:1.1.1</code> branches from that child. <code>desk_trial:2.0</code> is another root. The source scenario might be <code>support_desk2</code>—world names and scenario names can differ.</p><h2>Know when agents are running</h2><table><thead><tr><th>Control</th><th>Effect</th></tr></thead><tbody><tr><td>Start</td><td>Power on the container and gateway. Wake triggers agent activity.</td></tr><tr><td>Wake</td><td>Wake agents, or start/resume the dispatcher.</td></tr><tr><td>Sleep</td><td>Halt activity and spending, leaving the container available for logs and replay.</td></tr><tr><td>Stop</td><td>Power off the container; keep its disk.</td></tr><tr><td>Remove</td><td>Delete the container and disable its key. Saved snapshots survive.</td></tr></tbody></table><p>Active means the runtime is active, not necessarily that an agent is taking a turn. Agents respond to wakes. A private message wakes its recipient; a public-board post does not wake everyone.</p><h2>Keep what matters</h2><p>A snapshot saves disk state, including memories and logs, rather than an in-flight process. Prefer a clean boundary between turns. <b>Save snapshot</b> captures and publishes the run. Notes and attachments remain local until you <b>Publish</b>. Roots also remain local until published. Sharing saved state does not guarantee identical future model responses.</p><h2 id="authoring">Creating your own scenario</h2><p>Scenario authoring starts in the project files. Read <code>docs/HOW_TO_MAKE_WORLDS_START_HERE.md</code>, then use the scenario library here to build roots from your new recipe. A persona describes who an agent is; a role describes its job in this world.</p><p>An <b>environment image</b> in the authoring docs means a scenario’s software base, not a live experimental environment.</p><h2>Terminal companion</h2><p>Container shell and arbitrary command execution are terminal operations. Each environment has copyable instructions under <b>Terminal access</b> in the watch page’s right sidebar. More operator commands are available under <b>Advanced tools</b>.</p></div>'''
    return page('Getting started',frame(body,'help','Getting started'))
