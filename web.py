#!/usr/bin/env python3
"""agentspace web UI — the third front end beside the CLI and the menu.

Verbs run as `zookeeper.py` child processes whose output streams to the page
(a run outlives its browser tab); watching and chat call the library directly.
Verb forms are generated from the click tree, so a new click command appears
here with nothing else done. Stdlib only. Binds 127.0.0.1:7788 — reach it with
`ssh -L 7788:127.0.0.1:7788 control-01`; anyone on the port can run any verb.
"""
import collections
import dataclasses
import html
import json
import os
import select
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import click

import zookeeper                                   # loads secrets; the click tree is zookeeper.cli
from agentspace import audit, builder, db, env as env_mod, logwatch, registry, runtimes, versioning

PORT = 7788
REPO = Path(__file__).resolve().parent
ZK = [sys.executable, "-u", str(REPO / "zookeeper.py")]   # the gate swaps this for a fixture
SPECIAL = {                      # the only verb names written in this file
    "env watch": "/watch/",      # dedicated page
    "env chat": "/chat/",        # chat box on the watch page
    "scen env shell": None,      # terminal only: it hands the tty to `docker run -it`
}
DATALISTS = {"name": "dl-envs", "env_name": "dl-envs", "snap_ref": "dl-snaps", "scen_name": "dl-scens"}  # the menu's pickers
TEXT, HTML, JSON, NDJSON = ("text/plain; charset=utf-8", "text/html; charset=utf-8",
                            "application/json", "application/x-ndjson")
esc = html.escape


def page(title, body, **attrs):
    a = "".join(f' data-{k}="{esc(v)}"' for k, v in attrs.items())
    return (f"<!doctype html><html><head><meta charset=utf-8><title>{esc(title)}</title>"
            f"<link rel=stylesheet href=/web.css></head><body{a}>{body}<script src=/web.js></script></body></html>")


OUT = ('<div class=outhead><span id=outlabel>output</span><span id=elapsed></span>'
       '<button id=stop>Stop</button></div><pre id=out></pre>')


# ---- click tree → forms → argv ----

def leaf_commands(group, path=()):
    """(path_tuple, click.Command) for every leaf, depth-first, in declaration order."""
    for name, cmd in group.commands.items():
        if isinstance(cmd, click.Group):
            yield from leaf_commands(cmd, (*path, name))
        else:
            yield (*path, name), cmd


def _control(p, path):
    """One click param → one HTML control. Raises on a shape it does not know
    (the front-end checker's tripwire)."""
    n, t, req = p.name, p.type.name, " required" if p.required else ""
    lst = f' list="{DATALISTS[n]}"' if n in DATALISTS else ""
    step = "1" if t == "integer" else "any"
    if isinstance(p, click.Argument) and t == "text":          # nargs=-1 is one box, shlex-split server-side
        return f'<input name="{n}" data-arg{lst}{req}>'
    if isinstance(p, click.Argument) and t in ("integer", "float"):
        return f'<input type=number step={step} name="{n}" data-arg{req}>'
    if isinstance(p, click.Option) and p.is_flag and p.secondary_opts:
        return (f'<select name="{n}"><option value="">(default)</option><option value=on>{p.opts[0]}</option>'
                f'<option value=off>{p.secondary_opts[0]}</option></select>')
    if isinstance(p, click.Option) and p.is_flag:
        return f'<input type=checkbox name="{n}">'
    if isinstance(p, click.Option) and p.multiple:
        return f'<textarea name="{n}" rows=2 placeholder="one per line"></textarea>'
    if isinstance(p, click.Option) and t in ("integer", "float"):
        return f'<input type=number step={step} name="{n}"{req}>'
    if isinstance(p, click.Option) and t == "text":
        ph = "" if p.default in (None, click.core.UNSET) else f' placeholder="{esc(str(p.default))}"'
        return f'<input type={"password" if "key" in n else "text"} name="{n}"{ph}{lst}{req}>'
    raise ValueError(f"{' '.join(path)}: unsupported param {p}")


def form_html(path, cmd):
    """The <form> for one click command: a label + control per param, in cmd.params order."""
    rows = "".join(
        f'<label title="{esc(getattr(p, "help", None) or "")}">'
        f'<span>{esc(next((o for o in p.opts if o.startswith("--")), p.opts[0]))}</span>{_control(p, path)}</label>'
        for p in cmd.params)
    return f'<form data-path="{" ".join(path)}">{rows}<button>Run</button></form>'


def argv_for(path, cmd, fields):
    """Form fields (parse_qs lists) → child argv: options, then "--", then positionals,
    so positional text that looks like an option (env exec … sh -c …) is safe."""
    opts, args = [], []
    for p in cmd.params:
        v = (fields.get(p.name) or [""])[0].strip()
        if isinstance(p, click.Argument):
            if p.nargs == -1:
                args += shlex.split(v)
            elif v:
                args.append(v)
            elif p.required:
                raise ValueError(f"{p.name} is required")
        elif p.is_flag:
            if v == "off":
                opts.append(p.secondary_opts[0])
            elif v:
                opts.append(p.opts[0])
        elif p.multiple:
            opts += [x for line in v.splitlines() if line.strip() for x in (p.opts[0], line.strip())]
        elif v:
            opts += [p.opts[0], v]
    return [*path, *opts, "--", *args]


LEAVES = dict(leaf_commands(zookeeper.cli))


# ---- runs: child processes whose output is kept in memory ----

class Run:
    def __init__(self, label, proc, follow):
        self.id, self.label, self.proc, self.follow = uuid.uuid4().hex[:8], label, proc, follow
        self.lines = collections.deque(maxlen=5000)    # entries: a line, or an 8 KB piece of one
        self.total = self.nbytes = 0                  # entries ever appended; bytes held
        self.cond = threading.Condition()
        self.done, self.exit, self.started = False, None, time.time()

    def append(self, s):
        with self.cond:
            if len(self.lines) == self.lines.maxlen:
                self.nbytes -= len(self.lines[0])
            self.lines.append(s)
            self.total, self.nbytes = self.total + 1, self.nbytes + len(s)
            self.cond.notify_all()

    def pump(self):
        for piece in iter(lambda: self.proc.stdout.readline(8192), b""):
            self.append(piece.decode("utf-8", "replace"))
        self.exit = self.proc.wait()
        self.done = True
        self.append(f"[exit {self.exit}]\n")


RUNS: dict[str, Run] = {}        # insertion order = start order
RUNS_LOCK = threading.Lock()
MAX_FINISHED, MAX_FINISHED_BYTES = 20, 8_000_000


def start_run(argv, *, stdin=None, label, follow=False) -> str:
    """Spawn a child and keep its output; returns the run id at once."""
    with RUNS_LOCK:
        fin = [r for r in RUNS.values() if r.done]
        while fin and (len(fin) > MAX_FINISHED or sum(r.nbytes for r in fin) > MAX_FINISHED_BYTES):
            del RUNS[fin.pop(0).id]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=REPO,
                            env={**os.environ, "COLUMNS": "120"})   # rich's width when piped
    if stdin is not None:
        proc.stdin.write(stdin.encode())
        proc.stdin.close()
    run = Run(label, proc, follow)
    with RUNS_LOCK:
        RUNS[run.id] = run
    threading.Thread(target=run.pump, daemon=True).start()
    return run.id


def client_gone(handler) -> bool:
    """An HTTP/1.0 client sends nothing after its request, so a readable socket is a close."""
    try:
        return bool(select.select([handler.connection], [], [], 0)[0]) and \
            handler.connection.recv(1, socket.MSG_PEEK) == b""
    except OSError:
        return True


def stream_run(handler, run) -> bool:
    """Write buffered lines, then follow: True when the run is done, False when the client left."""
    sent = 0
    while True:
        with run.cond:
            run.cond.wait_for(lambda: run.total > sent or run.done, timeout=1)
            new, held = run.total - sent, len(run.lines)
            chunk = [f"… ({new - held} earlier lines dropped)\n"] if new > held else []
            chunk += list(run.lines)[held - min(new, held):] if new else []
            sent, done = run.total, run.done
        if chunk:
            try:
                handler.wfile.write("".join(chunk).encode())
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return False
        elif done:
            return True
        elif client_gone(handler):
            return False


def stop_run(run):
    """Ctrl-C the child (its finally blocks run, click prints Aborted!); kill after 5 s."""
    run.proc.send_signal(signal.SIGINT)
    try:
        run.proc.wait(5)
    except subprocess.TimeoutExpired:
        run.proc.kill()


# ---- watch and chat state ----

TREES = {}                       # (env, container_id) → view_tree; a re-forked env is a new container
CHATTING, CHAT_LOCK = set(), threading.Lock()   # (env, agent) with a turn in flight
MODELS = {}                      # runtime → catalog, once per process (PI and OC ids differ)


def _tree(name, host, refresh):
    key = (name, db.get_env(name)["container_id"])
    if refresh or key not in TREES:
        TREES[key] = logwatch.view_tree(host, name)
    return TREES[key]


# ---- pages ----

def console_page():
    envs = "".join(f'<a href="/watch/{esc(x["name"])}" target=_blank class="{esc((x["status"] or "").split(" ")[0])}">'
                   f'{esc(x["name"])}<small>{esc(x["status"] or "?")}</small></a>' for x in db.list_envs())
    snaps = db.list_snaps()
    snaps.sort(key=lambda s: (not versioning.is_world_root(s["version"]), s["scenario"]))   # world roots first
    refs = [f"{s['scenario']}:{s['version']}" for s in snaps]
    snap_html = "".join(f'<div><b>{esc(r)}</b> {esc(s.get("creation_message") or "")}</div>' for r, s in zip(refs, snaps))
    opt = lambda vals: "".join(f'<option value="{esc(v)}">' for v in vals)
    dl = (f'<datalist id=dl-envs>{opt(x["name"] for x in db.list_envs())}</datalist>'
          f'<datalist id=dl-snaps>{opt(refs)}</datalist>'
          f'<datalist id=dl-scens>{opt(s["name"] for s in registry.list_scens())}</datalist>')
    verbs, group = [], None
    for path, cmd in LEAVES.items():
        if path[:-1] != group:
            group = path[:-1]
            verbs.append(f"<h3>{esc(' '.join(group))}</h3>")
        key = " ".join(path)
        if key not in SPECIAL:
            body = form_html(path, cmd)
        elif SPECIAL[key] is None:
            args = " ".join(f"<{p.name}>" for p in cmd.params if isinstance(p, click.Argument))
            body = f"<p class=note>terminal only: <code>python3 zookeeper.py {esc(key)} {esc(args)}</code></p>"
        else:
            body = f"<p class=note>on the watch page: <code>{esc(SPECIAL[key])}&lt;env&gt;</code> — open an env from the list</p>"
        verbs.append(f"<details><summary><b>{esc(key)}</b> <span class=doc>{esc(cmd.get_short_help_str(120))}</span></summary>{body}</details>")
    body = (f'<div class=shell><aside id=nav><div class=brand><b>agentspace</b><small>operator console</small></div>'
            f'<a class=primary href=/new>New world</a><h3>envs</h3><div id=envs>{envs or "<span class=dim>none</span>"}</div>'
            f'<h3>snaps</h3><div id=snaps>{snap_html or "<span class=dim>none</span>"}</div></aside>'
            f'<main><section id=verbs>{"".join(verbs)}</section><section id=outbox><div id=runs></div>{OUT}</section></main></div>{dl}')
    return page("agentspace", body)


def watch_page(name):
    if db.get_env(name) is None:
        return None
    body = (f'<header><a href="/">← console</a><h1>{esc(name)}</h1><span id=sub></span>'
            f'<span class=right><span id=paused hidden>paused — scroll down or Follow</span><button id=follow>Follow</button></span></header>'
            f'<div class=split><ul id=views><li class=dim>loading views…</li></ul><div class=col><div id=pane></div>'
            f'<form id=chat hidden><span id=chatwho></span><input name=text autocomplete=off placeholder="message the agent (Enter to send)">'
            f'<button>Send</button></form><div id=chatlog></div></div></div>')
    return page(f"watch — {name}", body, env=name)


def wizard1():
    """Step 1: scenario — problems first (with the menu's inline Disable), then the active scens."""
    scens, problems = registry.scan_scens()
    probs = "".join(f'<p class=warn>⚠ scenario skipped: {esc(p["name"])} — {esc(p["reason"])}'
                    + (f' <button data-disable="{esc(p["name"])}">Disable</button>' if p["can_disable"] else "") + "</p>"
                    for p in problems)
    items = "".join(f'<a class=card href="/new/{esc(s["name"])}"><b>{esc(s["name"])}</b> — {esc(s["description"])}</a>'
                    for s in scens) or "<p>No scenarios available (add one under scenarios/&lt;name&gt;/)</p>"
    return page("New world", f'<div class=wiz><a href="/">← console</a><h1>New world</h1><h2>1 · Scenario</h2>{probs}{items}</div>')


def _param_control(spec, v):
    name, typ = esc(spec["name"]), spec.get("type")
    if typ in ("int", "float"):
        lim = "".join(f' {k}="{spec[k]}"' for k in ("min", "max") if spec.get(k) is not None)
        return f'<input type=number step={"1" if typ == "int" else "any"} name="{name}" value="{esc(v)}"{lim}>'
    if typ == "bool":   # a select, not a checkbox: a checkbox submits "on" (rejected) or nothing (= the default)
        return (f'<select name="{name}"><option{" selected" if v == "true" else ""}>true</option>'
                f'<option{" selected" if v == "false" else ""}>false</option></select>')
    return f'<input name="{name}" value="{esc(v)}">'


def wizard2(scen, values=None, error=""):
    """Step 2: params, then agent count — exactly what _collect_params and the count select ask."""
    values = values or {}

    def val(spec):
        v = values.get(spec["name"], spec.get("default"))
        return "" if v is None else str(v).lower() if spec.get("type") == "bool" else str(v)

    rows = "".join(f'<label><span>{esc(spec.get("label") or spec["name"])}</span>{_param_control(spec, val(spec))}</label>'
                   for spec in scen["params_schema"])
    nopts = "".join(f'<option{" selected" if str(i) == values.get("n") else ""}>{i}</option>'
                    for i in range(scen["min_agents"], scen["max_agents"] + 1))
    body = (f'<div class=wiz><a href="/new">← scenarios</a><h1>New world — {esc(scen["name"])}</h1>'
            f'<p class=dim>{esc(scen["description"])}</p>{f"<p class=err>{esc(error)}</p>" if error else ""}'
            f'<form id=step2 method=get action="/new/{esc(scen["name"])}/roster"><h2>2 · Parameters</h2>{rows or "<p class=dim>(none)</p>"}'
            f'<h2>3 · Agents</h2><label><span>Number of agents</span><select name=n>{nopts}</select></label>'
            f'<button class=primary>Next</button></form></div>')
    return page(f"New world — {scen['name']}", body)


def _n(scen, vals):
    n = int(vals.get("n") or 0)
    if not scen["min_agents"] <= n <= scen["max_agents"]:   # the select's bound, enforced against a forged request
        raise ValueError(f"n must be {scen['min_agents']}–{scen['max_agents']}")
    return n


def wizard3(scen, fields):
    """Step 3: roles, roster, modules, name, Build — menu_new_world after the count."""
    vals = {k: v[0] for k, v in fields.items()}
    n = _n(scen, vals)
    try:
        params = registry.validate_params(scen["params_schema"], vals)
    except registry.RegistryError as err:
        return wizard2(scen, vals, str(err))
    seed, ids, roles = builder.plan_roster(scen["name"], n, params)
    show_roles = len(set(roles)) > 1
    personas = registry.list_personas()
    if not personas:
        return page("New world", '<div class=wiz><p class=err>No personas available (add files under personas/)</p></div>')
    rt = runtimes.get(scen["runtime"])
    models = "".join(f'<option value="{esc(m)}">' for m in [*rt.recent_models(), rt.DEFAULT_MODEL])
    popts = "".join(f'<option value="{esc(p["short_name"])}"{" selected" if p["short_name"] == zookeeper.DEFAULT_PERSONA else ""}>'
                    f'{esc(p["short_name"])} — {esc(p["summary"] or "(no persona text)")}</option>' for p in personas)
    rows = "".join(f'<tr><td>agent {i + 1}/{n} <code>{esc(ids[i])}</code></td>{f"<td>{esc(roles[i] or "")}</td>" if show_roles else ""}'
                   f'<td><input name="model_{i}" list=models value="{esc(rt.DEFAULT_MODEL)}" required></td>'
                   f'<td><select name="persona_{i}">{popts}</select></td></tr>' for i in range(n))
    ptexts = "".join(f'<details><summary>{esc(p["short_name"])}</summary><pre>{esc(p["text"].strip() or "(no persona text)")}</pre></details>'
                     for p in personas)
    modules = "".join(f'<label class=chk><input type=checkbox name=module value="{esc(m["name"])}">{esc(m["name"])}</label>'
                      for m in registry.list_modules()) or "<p class=dim>Modules: none available yet</p>"
    body = (f'<div class=wiz><a href="/new/{esc(scen["name"])}">← parameters</a><h1>New world — {esc(scen["name"])}</h1>'
            f'<form id=step3 data-runtime="{esc(scen["runtime"])}" data-build="/new/{esc(scen["name"])}/build">'
            f'<h2>4 · Roster</h2><table id=roster><thead><tr><th>agent</th>{"<th>role</th>" if show_roles else ""}'
            f'<th>model</th><th>persona</th></tr></thead><tbody>{rows}</tbody></table><datalist id=models>{models}</datalist>'
            f'<p><a href="#" id=copyrow>copy row 1 to all rows</a></p><div class=personas>{ptexts}</div>'
            f'<h2>5 · Modules</h2>{modules}<h2>6 · World name</h2>'
            f'<label><span>name (blank = scen name)</span><input name=world_name pattern="[a-z0-9_]*" placeholder="{esc(scen["name"])}"></label>'
            f'<input type=hidden name=params value="{esc(json.dumps(params))}"><input type=hidden name=n value="{n}">'
            f'<input type=hidden name=seed value="{seed}">'
            f'<p id=summary>World Root <b id=wname>{esc(scen["name"])}</b> ← scen {esc(scen["name"])} (runtime {esc(scen["runtime"])}, seed {seed})</p>'
            f'<button class=primary>Build</button></form>{OUT}</div>')
    return page(f"New world — {scen['name']}", body)


def build_run(scen, fields) -> str:
    """The wizard's Build: builder.cmd_build in a child with the menu's per-agent roster."""
    vals = {k: v[0] for k, v in fields.items()}
    n = _n(scen, vals)
    payload = {"scen_name": scen["name"], "world_name": vals.get("world_name") or None,
               "roster": [{"model": vals[f"model_{i}"], "persona": vals[f"persona_{i}"]} for i in range(n)],
               "modules": fields.get("module", []), "params": json.loads(vals["params"]), "seed": int(vals["seed"])}
    return start_run([sys.executable, "-u", "-c", "import json,sys,zookeeper; from agentspace import builder; "
                      "builder.cmd_build(**json.load(sys.stdin))"], stdin=json.dumps(payload), label=f"world build {scen['name']}")


# ---- the handler ----

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._dispatch("")

    def do_POST(self):
        if self.headers.get("X-Agentspace") != "1":     # a cross-site form cannot send this header
            return self.reply(403, "missing X-Agentspace header")
        self._dispatch(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace"))

    def reply(self, code, body, ctype=TEXT):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def start_stream(self, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.streaming = ctype

    def _dispatch(self, body):
        self.streaming = None
        url = urlsplit(self.path)
        seg = [unquote(s) for s in url.path.strip("/").split("/")] if url.path.strip("/") else []
        try:
            self.route(seg, parse_qs(url.query, keep_blank_values=True), parse_qs(body, keep_blank_values=True), body)
        except Exception as err:
            msg = err.format_message() if isinstance(err, click.ClickException) else str(err)
            if self.streaming is None:
                return self.reply(500, msg)
            try:   # headers are out: one final line, then close
                self.wfile.write((json.dumps({"error": msg}) if self.streaming == NDJSON else f"[error: {msg}]").encode() + b"\n")
            except OSError:
                pass

    def route(self, seg, q, f, body):
        m = self.command
        if m == "GET" and not seg:
            return self.reply(200, console_page(), HTML)
        if m == "GET" and seg in (["web.css"], ["web.js"]):
            return self.reply(200, (REPO / seg[0]).read_text(), "text/css" if seg[0].endswith("css") else "text/javascript")
        if m == "GET" and seg == ["runs"]:
            return self.reply(200, json.dumps([{"id": r.id, "label": r.label, "started": r.started, "done": r.done,
                                                "exit": r.exit, "follow": r.follow} for r in reversed(RUNS.values())]), JSON)
        if m == "GET" and len(seg) == 2 and seg[0] == "runs":
            if seg[1] not in RUNS:
                return self.reply(404, "no such run")
            self.start_stream(TEXT)
            return stream_run(self, RUNS[seg[1]])
        if m == "POST" and seg[:1] == ["run"]:
            path = tuple(seg[1:])
            if path not in LEAVES or " ".join(path) in SPECIAL:
                return self.reply(404, "no such verb")
            try:
                argv = argv_for(path, LEAVES[path], f)
            except ValueError as err:
                return self.reply(400, str(err))
            label = " ".join([*path, *argv[argv.index("--") + 1:][:1]])   # never the argv (a --key value)
            follow = bool((f.get("follow") or [""])[0])
            run_id = start_run([*ZK, *argv], label=label, follow=follow)
            if not follow:
                return self.reply(200, json.dumps({"id": run_id}), JSON)
            self.start_stream(TEXT)              # a log follow streams on its POST and dies with it
            if not stream_run(self, RUNS[run_id]):
                stop_run(RUNS[run_id])
            return
        if m == "POST" and len(seg) == 3 and seg[0] == "runs" and seg[2] == "stop":
            if seg[1] not in RUNS:
                return self.reply(404, "no such run")
            stop_run(RUNS[seg[1]])
            return self.reply(204, "")
        if m == "GET" and len(seg) == 2 and seg[0] == "watch":
            out = watch_page(seg[1])
            return self.reply(200, out, HTML) if out else self.reply(404, "no such env")
        if m == "GET" and len(seg) == 2 and seg[0] == "views":
            try:
                host, _ = env_mod.prepare_watch(seg[1], "web")
                data = {"views": [[v.name, [k.name for k in kids]] for v, kids in _tree(seg[1], host, True)]}
            except click.ClickException as err:
                data = {"error": err.format_message()}
            return self.reply(200, json.dumps(data), JSON)
        if m == "GET" and len(seg) == 3 and seg[0] == "stream":
            return self.stream_view(seg[1], seg[2])
        if m == "POST" and len(seg) == 3 and seg[0] == "chat":
            return self.chat(seg[1], seg[2], body)
        if m == "GET" and seg == ["new"]:
            return self.reply(200, wizard1(), HTML)
        if seg[:1] == ["new"] and len(seg) in (2, 3):
            try:
                scen = registry.load_scen(seg[1])
            except registry.RegistryError as err:
                return self.reply(404, str(err))
            try:
                if m == "GET" and len(seg) == 2:
                    return self.reply(200, wizard2(scen), HTML)
                if m == "GET" and seg[2] == "roster":
                    return self.reply(200, wizard3(scen, q), HTML)
                if m == "POST" and seg[2] == "build":
                    return self.reply(200, json.dumps({"id": build_run(scen, f)}), JSON)
            except ValueError as err:
                return self.reply(400, str(err))
        if m == "GET" and seg == ["models"]:
            name = (q.get("runtime") or [""])[0]
            try:
                rt = runtimes.get(name)
            except ValueError as err:
                return self.reply(404, str(err))
            if name not in MODELS:
                MODELS[name] = rt.list_all_models()
            return self.reply(200, json.dumps(MODELS[name]), JSON)
        self.reply(404, "not found")

    def stream_view(self, name, view_name):
        try:
            host, _ = env_mod.prepare_watch(name, view_name)
        except click.ClickException as err:   # a hand-typed URL for a stopped env: a message, not a status
            return self.reply(200, json.dumps({"error": err.format_message()}) + "\n", NDJSON)
        views = [v for node, kids in _tree(name, host, False) for v in (node, *kids)]
        view = next((v for v in views if v.name == view_name), None)
        if view is None:
            return self.reply(404, f"no view {view_name!r}. Views: {', '.join(v.name for v in views)}")
        watcher = logwatch.Watcher(host, name, view, backfill=200)
        self.start_stream(NDJSON)
        try:
            for chunk in watcher.events():     # backlog as one chunk, then one event per chunk; [] = keepalive
                self.wfile.write("".join(json.dumps(dataclasses.asdict(ev)) + "\n" for ev in chunk).encode() or b"\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            watcher.stop()

    def chat(self, name, agent, text):
        text = text.strip()
        if not text:
            return self.reply(400, "empty message")
        with CHAT_LOCK:   # two senders would both wait for the same wake and read the same reply
            if (name, agent) in CHATTING:
                return self.reply(409, f"a turn is already in flight for {agent}")
            CHATTING.add((name, agent))
        try:
            reply = env_mod.chat_turn(name, agent, text)   # blocks up to 300 s, the CLI's deadline
            audit.log("env.chat", name, args={"agent": agent})
        finally:
            with CHAT_LOCK:
                CHATTING.discard((name, agent))
        self.reply(200, reply)


def make_server(port=PORT):
    db.get_conn()   # initialize the shared connection before any handler thread runs
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)   # a backgrounded shell job inherits SIGINT ignored; Stop is SIGINT
    print(f"agentspace web UI: http://127.0.0.1:{PORT}/", file=sys.stderr)
    make_server().serve_forever()


if __name__ == "__main__":
    main()
