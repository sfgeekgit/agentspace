#!/usr/bin/env python3
"""Web gate (engine, host-side): web.py in-process against a stopped fixture
env on a fresh state dir — zero tokens, no docker needed, ~10s. Covers: every
click leaf has a web form; the argv rules (options, "--", positionals) parse in
click; runs stream, survive or die as specified, Stop works; watch, chat,
wizard and models routes answer; check_frontends passes.
"""
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

tmp = tempfile.mkdtemp(prefix="webgate-")
os.environ["AGENTSPACE_STATE_DIR"] = tmp           # before agentspace.db is imported
signal.signal(signal.SIGINT, signal.SIG_DFL)      # Stop is SIGINT; a background shell job inherits it ignored
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from click.testing import CliRunner               # noqa: E402
import web, zookeeper                             # noqa: E402
from agentspace import db                         # noqa: E402
from unittest.mock import patch                  # noqa: E402

FAIL = []
H = {"X-Agentspace": "1"}


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def http(method, path, data=None, headers=H):
    req = urllib.request.Request(BASE + path, data=data.encode() if data is not None else None,
                                 method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def raw(request: bytes, wait: float):
    """One raw request; read for `wait` seconds; the caller closes the socket."""
    s = socket.create_connection(("127.0.0.1", PORT))
    s.sendall(request)
    s.settimeout(0.2)
    data, t0 = b"", time.time()
    while time.time() - t0 < wait:
        try:
            c = s.recv(65536)
        except socket.timeout:
            continue
        if not c:
            break
        data += c
    return s, data


def wait_done(run, secs):
    t0 = time.time()
    while time.time() - t0 < secs and not run.done:
        time.sleep(0.1)
    return run.done


# 1. fixture: one PI snap, one stopped env on it
db.upsert_snap({"snap_id": "deadbeef", "scenario": "gate", "version": "1.0", "ghcr_tag": "x",
                "created_at": "2026-01-01", "indexed_at": "2026-01-01", "runtime": "pi", "agents": ["a11111"]})
db.upsert_env({"name": "gate_env", "snap_id": "deadbeef", "container_id": "deadbeef", "host": "localhost",
               "status": "stopped", "created_at": "2026-01-01"})

# 2. server on a free port
srv = web.make_server(0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]
BASE = f"http://127.0.0.1:{PORT}"

# 3. console: a form per leaf
st, body = http("GET", "/tools")
leaves = {" ".join(p): c for p, c in web.leaf_commands(zookeeper.cli)}
missing = [k for k in leaves if k not in web.SPECIAL and f'data-path="{k}"' not in body]
check("advanced tools render a form per non-SPECIAL leaf", st == 200 and not missing, str(missing))
check("scen env shell listed as terminal-only", "scen env shell" in body and 'data-path="scen env shell"' not in body)


# 4. argv rules, proven in click with the leaf callback replaced
def invoke(key, fields):
    cmd = leaves[key]
    argv = web.argv_for(tuple(key.split()), cmd, fields)
    got, orig = {}, cmd.callback
    cmd.callback = lambda **kw: got.update(kw)
    try:
        res = CliRunner().invoke(zookeeper.cli, argv)
    finally:
        cmd.callback = orig
    return argv, res, got


argv, res, got = invoke("env exec", {"name": ["x"], "cmd": ["sh -c 'echo hi'"]})
check("argv: env exec → options, --, positionals", argv == ["env", "exec", "--", "x", "sh", "-c", "echo hi"], str(argv))
check("argv: env exec parses in click", res.exit_code == 0 and got.get("cmd") == ("sh", "-c", "echo hi"), f"{res.output} {got}")
argv, res, got = invoke("snap take", {"env_name": ["e1"], "message": ["-weird label"], "attach": ["a.md\nb.md\n"], "note": [""]})
check("argv: snap take repeats --attach, drops the blank --note",
      argv == ["snap", "take", "--message", "-weird label", "--attach", "a.md", "--attach", "b.md", "--", "e1"], str(argv))
check("argv: snap take parses in click", res.exit_code == 0 and got.get("message") == "-weird label"
      and got.get("attach") == ("a.md", "b.md"), f"{res.output} {got}")
argv, res, got = invoke("snap fork", {"snap_ref": ["s:1.0"], "new_env_name": ["n"], "kick": ["off"]})
check("argv: kick=off → --no-kick", "--no-kick" in argv and res.exit_code == 0 and got.get("kick") is False, str(argv))
try:
    web.argv_for(("env", "show"), leaves["env show"], {})
    check("argv: missing required argument raises", False)
except ValueError:
    check("argv: missing required argument raises", True)

# 5–6. a normal run round trip; the header check
st, body = http("POST", "/run/snap/list", "")
rid = json.loads(body).get("id") if st == 200 else None
check("POST /run/snap/list → {id}", st == 200 and bool(rid), f"{st} {body[:80]}")
st, out = http("GET", f"/runs/{rid}")
check("GET /runs/<id> ends with [exit N]", st == 200 and re.search(r"\[exit -?\d+\]\s*$", out), out[-120:])
check("POST without the header → 403", http("POST", "/run/snap/list", "", headers={})[0] == 403)
check("GET /runs lists it done", any(r["id"] == rid and r["done"] for r in json.loads(http("GET", "/runs")[1])))

# 7. run lifetimes, with a silent fixture child
web.ZK = [sys.executable, "-c", "import time; time.sleep(60)"]
form = b"name=gate_env&follow=on"
s, data = raw(b"POST /run/env/logs HTTP/1.0\r\nHost: x\r\nX-Agentspace: 1\r\nContent-Length: %d\r\n\r\n%s" % (len(form), form), 1)
check("follow run: streams on its POST", data.startswith(b"HTTP/1.0 200"), data[:60])
run = [r for r in web.RUNS.values() if r.follow][-1]
s.close()
check("follow run dies with its connection", wait_done(run, 3) and run.exit != 0, f"done={run.done} exit={run.exit}")
rid = json.loads(http("POST", "/run/env/logs", "name=gate_env")[1])["id"]   # http() closes after the reply
time.sleep(2)
check("normal run survives its connection", not web.RUNS[rid].done)
check("POST /runs/<id>/stop → 204", http("POST", f"/runs/{rid}/stop")[0] == 204)
check("stopped run ends", wait_done(web.RUNS[rid], 7), str(web.RUNS[rid].exit))
web.ZK = [sys.executable, "-u", str(web.REPO / "zookeeper.py")]

# 8. watch routes refuse a stopped env with a message
check("GET /watch/gate_env → 200", http("GET", "/watch/gate_env")[0] == 200)
st, body = http("GET", "/views/gate_env")
check("GET /views/gate_env → {error}", st == 200 and "error" in json.loads(body), body[:80])
st, body = http("GET", "/stream/gate_env/feed")
lines = body.strip().splitlines()
check("GET /stream/gate_env/feed → one {error} line", st == 200 and len(lines) == 1 and "error" in json.loads(lines[0]), body[:80])

# 9. wizard
st, body = http("GET", "/new")
check("GET /new lists pd", st == 200 and "/scenarios/pd" in body)
st, body = http("GET", "/new/pd/roster?n=2&rounds=5")
check("roster: two rows and a models datalist", st == 200 and body.count("<tr>") == 3 and "id=models" in body, f"{st} rows={body.count('<tr>') - 1}")
check("roster: n=99 → 400", http("GET", "/new/pd/roster?n=99")[0] == 400)
check("roster: bad param re-renders step 2", "class=err" in http("GET", "/new/pd/roster?n=2&rounds=999")[1])
st, body = http("GET", "/new/mafia/roster?n=6&hard_enforcement=false")
check("roster: bool param false reaches the hidden params", st == 200 and "&quot;hard_enforcement&quot;: false" in body, f"{st} {body[:80]}")
check("build: forged n → 400", http("POST", "/new/pd/build", "n=99")[0] == 400)

# 10. chat guard, with a fake turn; then the real one against the stopped env
orig_turn = web.env_mod.chat_turn
web.env_mod.chat_turn = lambda n, a, t: (time.sleep(1), "ok")[1]
res = []
ts = [threading.Thread(target=lambda: res.append(http("POST", "/chat/gate_env/a11111", "hi"))) for _ in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
check("chat: two concurrent turns → one 200 ok, one 409", sorted(r[0] for r in res) == [200, 409] and "ok" in [r[1] for r in res], str(res))
check("chat: blank → 400", http("POST", "/chat/gate_env/a11111", "  ")[0] == 400)
web.env_mod.chat_turn = orig_turn
st, body = http("POST", "/chat/gate_env/a11111", "hi")
check("chat: real turn on a stopped env → 500 (docker/gateway)", st == 500 and re.search(r"docker|gateway", body, re.I), f"{st} {body[:100]}")

# 11. models: once per runtime
pi, calls = web.runtimes.get("pi"), []
orig_models = pi.list_all_models
pi.list_all_models = lambda: (calls.append(1), ["x/y"])[1]
r1, r2 = http("GET", "/models?runtime=pi"), http("GET", "/models?runtime=pi")
check("models: cached per runtime, fetched once", r1[1] == r2[1] == '["x/y"]' and len(calls) == 1, f"{r1} {r2} calls={len(calls)}")
check("models: unknown runtime → 404", http("GET", "/models?runtime=nope")[0] == 404)
pi.list_all_models = orig_models

# Workspace hierarchy and terminal-only boundary.
for verb in ("env/exec", "env/enter", "scen/env/shell"):
    before = len(web.RUNS)
    check(f"POST {verb} is terminal-only", http("POST", "/run/"+verb, "name=gate_env&cmd=echo+unexpected")[0] == 404)
    check(f"GET form {verb} is unavailable", http("GET", "/form/"+verb)[0] == 404)
    check(f"{verb} did not spawn a child", len(web.RUNS) == before)
check("overview has no command wall", 'data-path="snap fork"' not in http("GET", "/")[1])
scens, _ = web.registry.scan_scens()
problems = [
    {"name": "bad_toml", "reason": "Invalid <manifest>", "can_disable": False},
    {"name": "bad_roles", "reason": "Missing role files", "can_disable": True},
]
with patch.object(web.registry, "scan_scens", return_value=(scens, problems)):
    for path in ("/", "/scenarios"):
        st, body = http("GET", path)
        check(f"{path} shows broken scenario names and escaped reasons",
              st == 200 and "bad_toml" in body and "bad_roles" in body
              and "Invalid &lt;manifest&gt;" in body and "Missing role files" in body)
        check(f"{path} offers Disable only for a parseable manifest",
              body.count('data-action="scen deactivate"') == 1
              and '&quot;scen_name&quot;: &quot;bad_roles&quot;' in body)
check("scenario links to expected GitHub location", "https://github.com/sfgeekgit/agentspace/tree/main/scenarios/support_desk" in http("GET", "/scenarios/support_desk")[1])
check("unknown scenario is 404", http("GET", "/scenarios/no_such_scenario")[0] == 404)
check("unknown snapshot is 404", http("GET", "/snapshots/no_such_snapshot")[0] == 404)
for path in ("/worlds", "/worlds/deadbeef", "/snapshots/deadbeef", "/fork/deadbeef", "/environments", "/help"):
    check(f"workspace {path} renders", http("GET", path)[0] == 200)
check("root launch defaults to waking", 'value="on" selected' in http("GET", "/fork/deadbeef")[1])
base = db.get_snap_by_id("deadbeef")
child = {**base, "snap_id":"child", "version":"1.1", "parent_snap_id":"deadbeef", "parent_version":"1.0"}
other_root = {**base, "snap_id":"another_root", "version":"2.0"}
db.upsert_snap(child); db.upsert_snap(other_root)
check("snapshot resolves to correct root", web.ui.root_for(child,db.list_snaps())["snap_id"] == "deadbeef")
check("family does not include a different root", "gate:2.0" not in http("GET", "/worlds/deadbeef")[1])
check("saved-state launch defaults to waiting", 'value="off" selected' in http("GET", "/fork/child")[1])
legacy={**base,"creation_message":"world root: 2 agents, scen=pd, per-agent sandboxes"}
check("legacy source inference carries its evidence", web.ui.source(legacy) == ("pd","creation note"))
legacy["scen"]="support_desk"
check("explicit provenance takes precedence", web.ui.source(legacy) == ("support_desk","recorded"))
check("browser template escapes snapshot text", "&lt;script&gt;" in web.ui.snapshot_row({**base,"creation_message":"<script>bad</script>"}))
# Build completion must return its own id, never guess the most recently built root.
original_start = web.start_run
captured = {}
def fake_start(argv, **kwargs):
    captured.update(argv=argv, **kwargs)
    return "fixture-build"
web.start_run = fake_start
fields={"n":["2"],"params":["{\"rounds\":5}"],"seed":["123"],"world_name":["fixture_world"],"model_0":["model/a"],"model_1":["model/b"],"persona_0":["blank"],"persona_1":["minimal"]}
check("wizard build dispatches", web.build_run(web.registry.load_scen("pd"), fields)=="fixture-build")
check("build result carries the exact root id", "UI_WORLD_ROOT:" in captured["argv"][-1])
check("build preserves per-agent roster", json.loads(captured["stdin"])["roster"] == [{"model":"model/a","persona":"blank"},{"model":"model/b","persona":"minimal"}])
web.start_run = original_start

# Demo policy: Caddy's header switches the bridge to an allowlist; a refusal spawns nothing.
P = {**H, "X-Agentspace-Public": "1"}
before = len(web.RUNS)
for verb, data in (("env/kill", "name=gate_env"), ("snap/attach", "snap_ref=gate:1.0&files=/etc/passwd"),
                   ("budget/topup", "env_name=gate_env&amount_usd=1"), ("scen/deactivate", "scen_name=pd"),
                   ("snap/fork", "snap_ref=gate:1.0&new_env_name=d1&budget_usd=1&souls=a=/etc/passwd"),
                   ("snap/fork", "snap_ref=gate:1.0&new_env_name=d1&budget_usd=1&host=other-box"),
                   ("snap/fork", "snap_ref=gate:1.0&new_env_name=d1&budget_usd=50"),
                   ("snap/fork", "snap_ref=gate:1.0&new_env_name=d1")):
    st, out = http("POST", "/run/" + verb, data, headers=P)
    check(f"demo: {verb} [{data.split('&')[-1]}] refused", st == 403, f"{st} {out[:60]}")
check("demo: refusals spawned nothing", len(web.RUNS) == before)
st, out = http("POST", "/run/snap/list", "", headers=P)
check("demo: an allowed verb runs", st == 200 and bool(json.loads(out).get("id")), f"{st} {out[:60]}")
st, out = http("POST", "/run/snap/fork", "snap_ref=gate:1.0&new_env_name=d1&budget_usd=2&host=localhost&kick=off", headers=P)
check("demo: a capped fork on localhost is allowed", st == 200, f"{st} {out[:60]}")
check("demo: chat reaches the handler (blank → 400, not 403)", http("POST", "/chat/gate_env/a11111", "  ", headers=P)[0] == 400)
check("demo: results show reaches the handler, generate stays refused",
      http("GET", "/results/gate_env", headers=P)[0] != 403 and http("POST", "/run/results/generate", "name=gate_env", headers=P)[0] == 403)
body = http("GET", "/tools", headers=P)[1]
check("demo: tools shows every form, refused ones disabled",
      '<fieldset disabled class="demo-off"><form data-path="env kill">' in body
      and 'data-path="snap list"' in body and '<fieldset disabled class="demo-off"><form data-path="snap list">' not in body)
body = http("GET", "/", headers=P)[1]
check("demo: notice with the GitHub link", "shared demo" in body and 'href="https://github.com/sfgeekgit/agentspace"' in body)
body = http("GET", "/watch/gate_env", headers=P)[1]
check("demo: watch page names the refused buttons", 'data-demo="1"' in body and "env kill" in re.search(r'data-denied="([^"]*)"', body).group(1))
check("demo: snapshot page disables publish", 'disabled title="needs the operator password">Publish' in http("GET", "/snapshots/deadbeef", headers=P)[1])
body = http("GET", "/fork/deadbeef", headers=P)[1]
check("demo: launch page caps the budget and disables host and souls", 'max="2"' in body and 'name="host" value="localhost" disabled' in body)
check("tunnel: no header → nothing disabled, no notice",
      "demo-off" not in http("GET", "/tools")[1] and "shared demo" not in http("GET", "/")[1] and "data-demo" not in http("GET", "/watch/gate_env")[1])

# The demo-tier fixes of 2026-09-21: sandbox snapshots under every spelling, unknown refs, the cap,
# negative budgets, agent ids that reach a shell, persona names that leave personas/.
sandbox = {**base, "snap_id": "5a4db0c5deadbeef5a4db0c5deadbeef", "version": "3.0",
           "ghcr_tag": "ghcr.io/sfgeekgit/agentspace:snap-gate-3.0", "feature_flags": {"fs_isolation": "sandbox"}}
db.upsert_snap(sandbox)
for ref in ("gate:3.0", "5a4db0c5", sandbox["ghcr_tag"], "snap-gate-3.0", "no_such:9.9", ""):
    st, out = http("POST", "/run/snap/fork", f"snap_ref={ref}&new_env_name=d2&budget_usd=1", headers=P)
    check(f"demo: sandbox or unknown snapshot refused as {ref!r}", st == 403, f"{st} {out[:60]}")
check("demo: a negative budget is refused", http("POST", "/run/snap/fork", "snap_ref=gate:1.0&new_env_name=d2&budget_usd=-5", headers=P)[0] == 403)
for i in range(web.DEMO_MAX_ENVS):
    db.upsert_env({"name": f"cap{i}", "snap_id": "deadbeef", "container_id": None, "openrouter_key": None,
                   "budget_usd": 1, "host": "localhost", "status": "running", "created_at": "2026-09-21T00:00:00Z"})
check("demo: just-forked ('running') envs count toward the cap",
      http("POST", "/run/snap/fork", "snap_ref=gate:1.0&new_env_name=d3&budget_usd=1", headers=P)[0] == 403)
for i in range(web.DEMO_MAX_ENVS):
    db.delete_env(f"cap{i}")
for bad in ("a1;id", "$(id)", "../a1", "a1 b"):
    try:
        web.env_mod.cmd_logs("gate_env", agent=bad); ok = False
    except Exception as err:
        ok = "invalid agent id" in str(err)
    check(f"env logs: agent id {bad!r} rejected before any command", ok)
for bad in ("../README", "/etc/passwd", "a/b", ".hidden"):
    try:
        web.registry.load_persona(bad); ok = False
    except web.registry.RegistryError:
        ok = True
    check(f"persona name {bad!r} refused", ok)

# 13. the front-end checker
check("scripts/check_frontends.py exits 0",
      subprocess.run([sys.executable, str(web.REPO / "scripts/check_frontends.py")], capture_output=True).returncode == 0)

srv.shutdown()
shutil.rmtree(tmp, ignore_errors=True)
print("WEB GATE: " + ("ALL PASS" if not FAIL else f"FAILED {FAIL}"))
sys.exit(1 if FAIL else 0)
