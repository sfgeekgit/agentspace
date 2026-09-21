#!/usr/bin/env python3
"""agentspace web UI — the third front end beside the CLI and the menu.

Verbs run as `zookeeper.py` child processes whose output streams to the page
(a run outlives its browser tab); watching and chat call the library directly.
Verb forms are generated from the click tree, so a new click command appears
here with nothing else done. Stdlib only. Binds 127.0.0.1:7788 — reach it with
`ssh -L 7788:127.0.0.1:7788 control-01`; browser actions use the CLI bridge; terminal-only verbs are excluded.
The public demo host (Caddy, agentworldmaker.com) reaches this same process with the
X-Agentspace-Public header set, which switches the bridge to the demo policy below.
Runs as the `agentspace-web` systemd service (deploy/agentspace-web.service).
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

import web_views as ui

import zookeeper                                   # loads secrets; the click tree is zookeeper.cli
from agentspace import audit, budget as budget_mod, builder, db, env as env_mod, logwatch, registry, runtimes, versioning

PORT = int(os.environ.get("AGENTSPACE_WEB_PORT", 7788))   # override for a second worktree; the service uses the default
REPO = Path(__file__).resolve().parent
ZK = [sys.executable, "-u", str(REPO / "zookeeper.py")]   # the gate swaps this for a fixture
SPECIAL = {                      # the only verb names written in this file
    "env watch": "/watch/",      # dedicated page
    "env show": "/watch/",       # its facts fill the watch page header (GET /info/<env>)
    "env chat": "/chat/",        # chat box on the watch page
    "env exec": None,           # terminal only: no browser execution endpoint
    "env enter": None,          # terminal connection instructions are shown inline
    "scen env shell": None,      # terminal only: it hands the tty to `docker run -it`
}
# ---- demo policy ----
# Caddy stamps every request from the public demo host with this header (overwriting any client
# copy); requests over the operator's ssh tunnel never carry it. One function answers "may the demo
# do this?" for the POST handlers (which refuse) and for the pages (which gray the control out).
DEMO_HEADER = "X-Agentspace-Public"
DEMO_VERBS = {"world build", "snap fork", "snap take", "snap note", "snap show", "snap tree", "snap list",
              "env start", "env stop", "env sleep", "env kick", "env post", "env chat", "env logs",
              "env list", "env show", "budget show", "scen list"}
DEMO_DROP = {"attach", "souls", "host", "allow_key_leak"}   # server paths, other hosts, safety off
DEMO_MAX_BUDGET = 2.0                                        # dollars per launch
DEMO_MAX_ENVS = 15                                           # live containers (all of them) before launches are refused
NEEDS_OPERATOR = "needs the operator password"


def demo_denied(verb, fields=None):
    """Why the demo may not run `verb` with these form fields (parse_qs lists), or None."""
    if not ui.PUBLIC.get():
        return None
    if verb not in DEMO_VERBS:
        return NEEDS_OPERATOR
    f = {k: (v or [""])[0].strip() for k, v in (fields or {}).items()}
    if any(f.get(k) and not (k == "host" and f[k] == "localhost") for k in DEMO_DROP):
        return f"server paths and hosts: {NEEDS_OPERATOR}"
    if verb == "snap fork":
        try:
            budget = float(f.get("budget_usd") or "nan")
        except ValueError:
            budget = float("nan")
        if not 0 < budget <= DEMO_MAX_BUDGET:            # blank too: the CLI default is not capped
            return f"demo launches need a budget above $0 and at most ${DEMO_MAX_BUDGET:.0f}"
        if sum(e["status"] in ("active", "dormant", "running") for e in db.list_envs()) >= DEMO_MAX_ENVS:
            return "the demo box is full: sleep or stop an environment first"   # 'running' = just forked
        from agentspace import snap as snap_mod            # the launcher's own resolver: every spelling, fail closed
        try:
            snap = snap_mod.resolve_snap_ref(f.get("snap_ref", ""))
        except click.ClickException as err:
            return f"unknown snapshot: {err.format_message()}"
        if (snap.get("feature_flags") or {}).get("fs_isolation") == "sandbox":
            return f"sandbox-mode snapshots: {NEEDS_OPERATOR}"
    return None


ui.DENIED, ui.DEMO_MAX_BUDGET = demo_denied, DEMO_MAX_BUDGET

DATALISTS = {"name": "dl-envs", "env_name": "dl-envs", "snap_ref": "dl-snaps", "scen_name": "dl-scens"}  # the menu's pickers
TEXT, HTML, JSON, NDJSON = ("text/plain; charset=utf-8", "text/html; charset=utf-8",
                            "application/json", "application/x-ndjson")
esc = html.escape


def page(title, body, **attrs):
    return ui.page(title, ui.frame(body, "scenarios", "Create a world"), **attrs)




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
        f'<span>{esc(next((o for o in p.opts if o.startswith("--")), p.opts[0]))}{"" if p.required else "<i>optional</i>"}</span>{_control(p, path)}</label>'
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
    return ui.overview()


def watch_page(name):
    return ui.watch(name)


def wizard1():
    return ui.scenarios()


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
    body = (f'<div class=wiz><a href="/scenarios/{esc(scen["name"])}">← scenario</a><div class=eyebrow>CREATE A WORLD / 1 OF 2</div><h1>Set the scene.</h1>'
            f'<p class=dim>{esc(scen["description"])}</p>{f"<p class=err>{esc(error)}</p>" if error else ""}'
            f'<form id=step2 method=get action="/new/{esc(scen["name"])}/roster"><h2>Scenario settings</h2>{rows or "<p class=dim>(none)</p>"}'
            f'<h2>Agent count</h2><label><span>Number of agents</span><select name=n>{nopts}</select></label>'
            f'<button class=primary>Configure agents →</button></form></div>')
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
                   f'<td><input name="model_{i}" aria-label="Model for agent {i + 1}" list=models value="{esc(rt.DEFAULT_MODEL)}" required></td>'
                   f'<td><select name="persona_{i}" aria-label="Persona for agent {i + 1}">{popts}</select></td></tr>' for i in range(n))
    ptexts = "".join(f'<details><summary>{esc(p["short_name"])}</summary><pre>{esc(p["text"].strip() or "(no persona text)")}</pre></details>'
                     for p in personas)
    modules = "".join(f'<label class=chk><input type=checkbox name=module value="{esc(m["name"])}">{esc(m["name"])}</label>'
                      for m in registry.list_modules()) or "<p class=dim>Modules: none available yet</p>"
    body = (f'<div class=wiz><a href="/new/{esc(scen["name"])}">← parameters</a><div class=eyebrow>CREATE A WORLD / 2 OF 2</div><h1>Make it your world.</h1><p class=muted>Scenario: {esc(scen["name"])}</p>'
            f'<form id=step3 data-runtime="{esc(scen["runtime"])}" data-build="/new/{esc(scen["name"])}/build">'
            f'<h2>Choose your cast</h2><p class=muted>Models power each agent; personas shape its personality. Roles come from the scenario.</p><table id=roster><thead><tr><th>agent</th>{"<th>role</th>" if show_roles else ""}'
            f'<th>model</th><th>persona</th></tr></thead><tbody>{rows}</tbody></table><datalist id=models>{models}</datalist>'
            f'<p><a href="#" id=copyrow>copy row 1 to all rows</a></p><div class=personas>{ptexts}</div>'
            f'<h2>Optional modules</h2>{modules}<h2>Name your world</h2>'
            f'<label><span>name (blank = scen name)</span><input name=world_name pattern="[a-z0-9_]*" placeholder="{esc(scen["name"])}"></label>'
            f'<input type=hidden name=params value="{esc(json.dumps(params))}"><input type=hidden name=n value="{n}">'
            f'<input type=hidden name=seed value="{seed}">'
            f'<p id=summary>World Root <b id=wname>{esc(scen["name"])}</b> ← scen {esc(scen["name"])} (runtime {esc(scen["runtime"])}, seed {seed})</p>'
            f'<button class=primary>Build world root →</button></form><div id=build-next hidden></div>'
            f'<p class=quiet-note>Building creates a local starting point. Next, launch an environment with a budget and open its live view.</p></div>')
    return page(f"New world — {scen['name']}", body)


def build_run(scen, fields) -> str:
    """The wizard's Build: builder.cmd_build in a child with the menu's per-agent roster."""
    vals = {k: v[0] for k, v in fields.items()}
    n = _n(scen, vals)
    payload = {"scen_name": scen["name"], "world_name": vals.get("world_name") or None,
               "roster": [{"model": vals[f"model_{i}"], "persona": vals[f"persona_{i}"]} for i in range(n)],
               "modules": fields.get("module", []), "params": json.loads(vals["params"]), "seed": int(vals["seed"])}
    return start_run([sys.executable, "-u", "-c", "import json,sys,zookeeper; from agentspace import builder; "
                      "s=builder.cmd_build(**json.load(sys.stdin)); "
                      "print('UI_WORLD_ROOT:'+s['snap_id'])"], stdin=json.dumps(payload), label=f"world build {scen['name']}")


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
        ui.PUBLIC.set(self.headers.get(DEMO_HEADER) == "1")
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
        if m == "GET" and seg == ["scenarios"]:
            return self.reply(200, ui.scenarios(), HTML)
        if m == "GET" and len(seg) == 2 and seg[0] == "scenarios":
            try:
                return self.reply(200, ui.scenario_detail(seg[1]), HTML)
            except registry.RegistryError as err:
                return self.reply(404, str(err))
        if m == "GET" and seg == ["worlds"]:
            return self.reply(200, ui.worlds((q.get("view") or ["roots"])[0]), HTML)
        if m == "GET" and seg == ["environments"]:
            return self.reply(200, ui.environments(), HTML)
        if m == "GET" and len(seg) == 2 and seg[0] in ("worlds", "snapshots", "fork"):
            view = {"worlds": ui.world_detail, "snapshots": ui.snapshot_detail, "fork": ui.fork_page}[seg[0]]
            rendered = view(seg[1])
            return self.reply(200, rendered, HTML) if rendered else self.reply(404, "Snapshot not found")
        if m == "GET" and seg == ["tools"]:
            return self.reply(200, ui.tools_page(LEAVES, SPECIAL, form_html), HTML)
        if m == "GET" and seg[:1] == ["form"]:
            path = tuple(seg[1:])
            if path not in LEAVES or " ".join(path) in SPECIAL:
                return self.reply(404, "No browser form for this operation")
            return self.reply(200, form_html(path, LEAVES[path]), HTML)
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
            if why := demo_denied(" ".join(path), f):
                return self.reply(403, why)
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
        if m == "GET" and len(seg) == 2 and seg[0] == "info":   # env show's facts, as JSON (docker probe + OpenRouter: after paint)
            try:
                return self.reply(200, json.dumps(env_mod.env_info(seg[1])), JSON)
            except click.ClickException as err:
                return self.reply(404, err.format_message())
        if m == "GET" and len(seg) == 2 and seg[0] == "budget":   # budget show's numbers, as JSON
            env = db.get_env(seg[1])
            if env is None:
                return self.reply(404, "no such env")
            u = budget_mod.usage(env)
            return self.reply(200, json.dumps({"used": u and u[0], "limit": u[1] if u else env.get("budget_usd")}), JSON)
        if m == "GET" and len(seg) == 3 and seg[0] == "stream":
            return self.stream_view(seg[1], seg[2], q)
        if m == "POST" and len(seg) == 3 and seg[0] == "chat":
            if why := demo_denied("env chat"):
                return self.reply(403, why)
            return self.chat(seg[1], seg[2], body)
        if m == "GET" and seg == ["help"]:
            return self.reply(200, ui.help_page(), HTML)
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
                    if why := demo_denied("world build", f):
                        return self.reply(403, why)
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

    def stream_view(self, name, view_name, q):
        try:
            replay = max(float((q.get("replay") or ["0"])[0]), 0) or None   # ?replay=N: the run so far, paced, N× speed
        except ValueError:
            return self.reply(400, "replay must be a number")
        try:
            host, _ = env_mod.prepare_watch(name, view_name)
        except click.ClickException as err:   # a hand-typed URL for a stopped env: a message, not a status
            return self.reply(200, json.dumps({"error": err.format_message()}) + "\n", NDJSON)
        views = [v for node, kids in _tree(name, host, False) for v in (node, *kids)]
        view = next((v for v in views if v.name == view_name), None)
        if view is None:
            return self.reply(404, f"no view {view_name!r}. Views: {', '.join(v.name for v in views)}")
        watcher = logwatch.Watcher(host, name, view, backfill=200, replay=replay)
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
