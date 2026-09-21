#!/usr/bin/env python3
"""Mirror gate (host-side): the publisher and the refresh trigger against a fresh state dir, a
fixture env row and a fixture log tree in place of the container (extract and inspect are the
only two docker touches, both replaced). Zero tokens, no docker, a few seconds. Covers manifest
validation, view derivation against logwatch, the four-field event, the atomic swap, stale
carry-over, env-name reuse, results, unpublish, the trigger's debounce and refusals, and the
viewer's no-HTML-from-data rule.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from fnmatch import fnmatch
from http.server import HTTPServer
from pathlib import Path

tmp = Path(tempfile.mkdtemp(prefix="mirrorgate-"))
os.environ |= {"AGENTSPACE_STATE_DIR": str(tmp / "state"), "AGENTSPACE_MIRROR_DIR": str(tmp / "webroot"),
               "AGENTSPACE_RESULTS_DIR": str(tmp / "results")}          # before the modules read them
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import click                                       # noqa: E402
import mirror_refresh                              # noqa: E402
from agentspace import db, logwatch, mirror        # noqa: E402

FAIL, SAID = [], []
mirror.console.print = lambda *a, **k: SAID.append(" ".join(map(str, a)))


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def lines(*records):
    return "".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in records).encode()


# ---- fixture: a snapshot chain, an env, and the log tree the extractor would return ----
KEY = "sk-or-v1-gatekey0123456789"
db.upsert_snap({"snap_id": "root0001", "scenario": "gateworld", "version": "1.0", "ghcr_tag": "x", "scen": "pd",
                "created_at": "2026-01-01", "indexed_at": "2026-01-01", "runtime": "pi", "agents": ["a1", "a2"],
                "creation_message": "world root", "model": "m/base",
                "roster": [{"id": "a1", "role": "scout", "persona": "blank", "model": "m/base"},
                           {"id": "a2", "role": "guard", "persona": "blank", "model": "m/base"}]})
db.upsert_snap({"snap_id": "snap0002", "scenario": "gateworld", "version": "1.1", "ghcr_tag": "y", "parent_snap_id": "root0001",
                "created_at": "2026-01-02", "indexed_at": "2026-01-02", "runtime": "pi", "agents": ["a1", "a2"],
                "creation_message": "mid-game", "model": "m/base"})
db.upsert_env({"name": "gate_env", "snap_id": "snap0002", "container_id": "c" * 64, "host": "localhost", "status": "dormant",
               "created_at": "2026-01-03T00:00:00+00:00", "openrouter_key": KEY, "budget_usd": 2})
T = "2026-01-03T10:00:%02d+00:00"
FILES = {
    "/mirror/gateway": b"", "/agents/a1": b"", "/agents/a2": b"",
    "/world/world.json": json.dumps({"model": "m/base", "models": {"a2": "m/forked"}, "watch": [
        {"name": "game log (GM, spoilers)", "file": "/gm/game_log.jsonl", "format": "jsonl", "fields": {"ts": "ts", "text": "text"}}]}).encode(),
    "/data/gateway/audit.jsonl": lines(
        {"event": "post_public", "ts": T % 1, "frm": "a1", "text": "hello all", "private_note": "PRIVATE-FIELD"},
        {"event": "send", "ts": T % 2, "frm": "a1", "to": "a2", "text": "psst"},
        {"event": "read_public", "ts": T % 3, "frm": "a2"},
        "not json at all", [1, 2], {"no_event_key": True},
        {"event": "submit", "ts": T % 4, "frm": "a2", "action": "vote 1"}) + b'{"event": "post_public", "ts": "partial line, no newline',
    "/data/gateway/public.jsonl": lines({"ts": T % 1, "from": "a1", "text": "hello all"}, {"ts": T % 5, "from": "dispatch", "text": "round 2"}),
    "/data/gateway/budget.jsonl": lines({"ts": T % 6, "agent": "a1", "cost_total": 0.0123, "input": 10, "output": 5, "dur_s": 2}),
    "/gm/game_log.jsonl": lines({"ts": 1767434400.5, "text": "round 1 resolved", "hidden_state": "PRIVATE-FIELD"}),
    "/agents/a1/sessions/2026-01-03T10-00.jsonl": lines(
        {"timestamp": T % 7, "message": {"role": "user", "content": [{"type": "text", "text": "your turn"}]}},
        {"timestamp": T % 8, "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "SECRET-REASONING about the vote"}, {"type": "text", "text": f"I vote 1. My key is {KEY}"},
            {"type": "toolCall", "name": "bash", "arguments": {"cmd": "ls"}}]}}),
    "/agents/a1/sessions/2026-01-03T11-00.jsonl": lines(
        {"timestamp": T % 9, "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}),
    "/agents/a1/scratch/notes.md": b"SCRATCH-LINE one\n",
}
state = {"files": FILES, "inspect": ("c" * 64, True, "2026-01-03T00:00:01Z")}


def fake_extract(env):
    if isinstance(state["files"], Exception):
        raise state["files"]
    return state["files"]


mirror.extract = fake_extract
mirror._inspect = lambda env: state["inspect"]
WEB, MANIFEST = tmp / "webroot", tmp / "mirror.toml"
RUN = ('[[run]]\nid = "gate-run"\nenv = "gate_env"\ntitle = "Gate run"\n'
       'views = ["feed", "board", "budget", "game log (GM, spoilers)", "no such view"]\nagents = true\n')


def publish(text):
    MANIFEST.write_text("keep_builds = 2\n" + text)
    SAID.clear()
    mirror.cmd_publish(str(MANIFEST))
    return WEB / "current"


def refused(text):
    MANIFEST.write_text(text)
    try:
        mirror.cmd_publish(str(MANIFEST))
    except click.ClickException as e:
        return e.format_message()
    return ""


# 1. manifest validation
try:
    mirror.cmd_publish(str(MANIFEST)); msg = ""
except click.ClickException as e:
    msg = e.format_message()
check("missing manifest refused with the reason", "no manifest" in msg, msg)
check("bad run id refused", "lowercase" in refused('[[run]]\nid = "Bad_ID"\nenv = "gate_env"\n'))
check("bad env name refused", "not an environment name" in refused('[[run]]\nid = "ok"\nenv = "../etc"\n'))
check("duplicate run id refused", "twice" in refused('[[run]]\nid = "a"\nenv = "gate_env"\n[[run]]\nid = "a"\nenv = "gate_env"\n'))
check("unparseable manifest refused", refused("[[run\n") != "")
check("no refused manifest left a build behind", not (WEB / "current").exists())

# 2. a publish: views match logwatch event for event, four fields only
cur = publish(RUN + "thoughts = true\n")
run = json.loads((cur / "runs/gate-run/run.json").read_text())
check("unknown view name reported and skipped", any("no such view" in s for s in SAID) and "no such view" not in [v["name"] for v in run["views"]])
nodes = logwatch.tree(logwatch.declared_views(FILES["/world/world.json"].decode()), ["a1", "a2"])
by_name = {v.name: v for node, kids in nodes for v in (node, *kids)}
for name in ("feed", "board", "budget", "game log (GM, spoilers)", "a1", "a1:thoughts"):
    view, entry = by_name[name], next((v for v in run["views"] if v["name"] == name), None)
    want = []
    for path in sorted(FILES):                       # the streamer's order: sorted files, lines in order, complete lines only
        if any(fnmatch(path, pat) for pat in view.patterns):
            for line in FILES[path].decode().split("\n")[:-1]:
                try:
                    ev = view.parse(path, line)
                except (KeyError, TypeError, AttributeError):
                    ev = None
                if ev:
                    want.append({"ts": str(ev.ts), "who": ev.who, "kind": ev.kind, "text": ev.text.replace(KEY, "[redacted]")})
    got = [json.loads(l) for l in (cur / f"runs/gate-run/views/{entry['file']}.jsonl").read_text().splitlines()] if entry else None
    check(f"view {name!r} matches logwatch event for event", got == want and bool(want), f"{got} != {want}")
    check(f"view {name!r} events carry exactly ts, who, kind, text", all(set(e) == {"ts", "who", "kind", "text"} for e in got or [{}]))
everything = "".join(p.read_text() for p in cur.rglob("*") if p.is_file() and p.suffix != ".zip")
check("a private field in a log record is not published", "PRIVATE-FIELD" not in everything)
check("the env's OpenRouter key is redacted", KEY not in everything and "[redacted]" in everything)
check("plain-text rendering exists and the zip holds it", "hello all" in (cur / "runs/gate-run/views/feed.txt").read_text()
      and "gate-run/views/feed.txt" in zipfile.ZipFile(cur / "runs/gate-run/all.zip").namelist())
check("facts: status, 12-hex container, forked model, lineage", run["status"] == "active" and run["container"] == "c" * 12
      and [r["model"] for r in run["roster"]] == ["m/base", "m/forked"]
      and [(l["kind"], l["ref"]) for l in run["lineage"]] == [("scenario", "pd"), ("root", "gateworld:1.0"), ("snapshot", "gateworld:1.1"), ("env", "gate_env")])
site = json.loads((cur / "site.json").read_text())
check("site.json lists the run and its scenario", [r["id"] for r in site["runs"]] == ["gate-run"] and [s["name"] for s in site["scenarios"]] == ["pd"]
      and (cur / "scenarios/pd.json").is_file())
check("viewer assets and web.css copied", all((cur / f).is_file() for f in ("index.html", "run.html", "scenario.html", "mirror.js", "web.css")))
check("files are world-readable, directories traversable", all((p.stat().st_mode & 0o777) == (0o755 if p.is_dir() else 0o644) for p in cur.rglob("*")))

# 3. thoughts = false: only what the agents said
cur = publish(RUN)
run = json.loads((cur / "runs/gate-run/run.json").read_text())
text = "".join(p.read_text() for p in (cur / "runs/gate-run/views").iterdir())
check("thoughts=false publishes no reasoning, tool traffic, scratchpad or facets",
      "SECRET-REASONING" not in text and "SCRATCH-LINE" not in text and "→ bash" not in text and "I vote 1" in text
      and not any(":" in v["name"] for v in run["views"]))
cur = publish(RUN.replace("agents = true", "agents = false"))
check("agents=false publishes no agent view", not any(v["agent"] for v in json.loads((cur / "runs/gate-run/run.json").read_text())["views"]))

# 4. atomic swap: a failing run leaves the previous build in place, and no partial build
before = cur.resolve()
state["files"] = RuntimeError("boom")
try:
    publish(RUN); raised = False
except RuntimeError:
    raised = True
check("a failing publish raises, `current` is unchanged, nothing partial is left",
      raised and (WEB / "current").resolve() == before and sorted(p.name for p in (WEB / "builds").iterdir())[-1] == before.name)
state["files"] = FILES

# 5. stale: an unreachable container carries the previous files over
as_of = json.loads((cur / "runs/gate-run/run.json").read_text())["as_of"]
state["inspect"] = None
cur = publish(RUN)
run = json.loads((cur / "runs/gate-run/run.json").read_text())
check("missing container: files carried over, stale, as_of unchanged", run["stale"] and run["as_of"] == as_of
      and (cur / "runs/gate-run/views/feed.jsonl").is_file() and json.loads((cur / "site.json").read_text())["runs"][0]["stale"])
state["inspect"] = ("c" * 64, False, "2026-01-03T00:00:01Z")
run = json.loads((publish(RUN) / "runs/gate-run/run.json").read_text())
check("stopped container: carried over and shown as stopped", run["stale"] and run["status"] == "stopped")

# 6. name reuse: a different container under the same env name is refused until pinned
state["inspect"] = ("d" * 64, True, "2026-01-04T00:00:01Z")
run = json.loads((publish(RUN) / "runs/gate-run/run.json").read_text())
check("new container under an old env name is refused, old copy kept", run["stale"] and run["container"] == "c" * 12
      and any("is now container dddddddddddd" in s for s in SAID), str(SAID))
run = json.loads((publish(RUN + 'container = "dddd"\n') / "runs/gate-run/run.json").read_text())
check("pinning container= publishes the new one", not run["stale"] and run["container"] == "d" * 12)
run = json.loads((publish(RUN + 'container = "eeee"\n') / "runs/gate-run/run.json").read_text())
check("a pin that does not match is refused", run["stale"] and any("not the pinned" in s for s in SAID))
state["inspect"] = None
cur = publish(RUN.replace('"gate-run"', '"fresh-id"'))
check("a never-published run with no container is left out, not an error",
      not (cur / "runs/fresh-id").exists() and any("nothing published" in s for s in SAID))
state["inspect"] = ("c" * 64, True, "2026-01-03T00:00:01Z")

# 7. results: copied only with a matching sha256; an absent bundle is fine
bundle = tmp / "results" / "gate_env"
bundle.mkdir(parents=True)
(bundle / "report.md").write_text("# report\n")
(bundle / "manifest.json").write_text(json.dumps({"files": [{"name": "report.md", "size": 9, "sha256": hashlib.sha256(b"# report\n").hexdigest()}]}))
shutil.rmtree(WEB)                                   # a clean slate: the pinned-container history above is done
cur = publish(RUN + "results = true\n")
run = json.loads((cur / "runs/gate-run/run.json").read_text())
check("results bundle copied and listed", (cur / "runs/gate-run/results/report.md").read_text() == "# report\n"
      and "report.md" in [f["name"] for f in run["results"]] and json.loads((cur / "site.json").read_text())["runs"][0]["results"])
(bundle / "report.md").write_text("# tampered\n")
cur = publish(RUN + "results = true\n")
check("a results file that fails its sha256 is not published", not (cur / "runs/gate-run/results").exists()
      and not json.loads((cur / "runs/gate-run/run.json").read_text())["results"])
shutil.rmtree(bundle)
check("absent bundle: published without results", not json.loads((publish(RUN + "results = true\n") / "runs/gate-run/run.json").read_text())["results"])

# 8. unpublish and pruning
cur = publish("")
check("a run removed from the manifest is gone from the next build", not (cur / "runs").exists() and json.loads((cur / "site.json").read_text())["runs"] == [])
check("old builds pruned to keep_builds", len(list((WEB / "builds").iterdir())) == 2)
check("`current` is a relative symlink into builds/", os.readlink(WEB / "current").startswith("builds/"))

# 9. the trigger: debounce, refusals
counter = tmp / "publishes"
fixture = tmp / "fake_publish.py"
fixture.write_text("import json, sys, datetime, pathlib\n"
                   f"open({str(counter)!r}, 'a').write('x')\n"
                   f"p = pathlib.Path({str(WEB / 'current' / 'site.json')!r})\n"
                   "s = json.loads(p.read_text()); s['generated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat(); p.write_text(json.dumps(s))\n"
                   "sys.exit(int(sys.argv[1]))\n")
mirror_refresh.PUBLISH, mirror_refresh.LOCK = [sys.executable, str(fixture), "0"], str(tmp / "lock")
srv = HTTPServer(("127.0.0.1", 0), mirror_refresh.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def http(method, path, data=None):
    try:
        with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, method=method), timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def age_build():
    p = WEB / "current" / "site.json"
    p.write_text(json.dumps(json.loads(p.read_text()) | {"generated_at": "2020-01-01T00:00:00+00:00", "min_refresh_seconds": 20}))


age_build()
a, b = http("POST", "/refresh"), http("POST", "/refresh")
check("two POSTs inside the minimum interval cause one publish", counter.read_text() == "x" and a[1]["refreshed"] and not b[1]["refreshed"]
      and a[1]["as_of"] == b[1]["as_of"], f"{a} {b}")
check("POST with a body is refused", http("POST", "/refresh", b"x=1")[0] == 400)
check("POST with a query string is refused", http("POST", "/refresh?now=1")[0] == 404)
check("GET is 404", http("GET", "/refresh")[0] == 404 and http("GET", "/")[0] == 404)
check("refusals published nothing", counter.read_text() == "x")
age_build()
mirror_refresh.PUBLISH, mirror_refresh.last_attempt = [sys.executable, str(fixture), "1"], 0.0
st, out = http("POST", "/refresh")
age_build()
check("a failing publish answers 200 with an error and is debounced too",
      st == 200 and out.get("error") == "publish failed" and "error" not in http("POST", "/refresh")[1] and counter.read_text() == "xx", str(out))
check("the trigger imports nothing from agentspace or zookeeper",
      not re.search(r"^\s*(import|from) (zookeeper|agentspace|web)\b", (REPO / "mirror_refresh.py").read_text(), re.M))
srv.shutdown()

# 10. the viewer never builds HTML from data; every URL in it is relative
src = "".join(p.read_text() for p in (REPO / "mirror").iterdir())
check("viewer has no innerHTML / outerHTML / insertAdjacentHTML / document.write", not any(w in src for w in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write")))
check("viewer fetches nothing absolute", "fetch('/" not in src and 'href="/' not in src and 'src="/' not in src)

# 11. verb wiring, and the demo may not publish
check("scripts/check_frontends.py exits 0", subprocess.run([sys.executable, str(REPO / "scripts/check_frontends.py")], capture_output=True).returncode == 0)
check("mirror verbs are not demo verbs", not any(v.startswith("mirror") for v in __import__("web").DEMO_VERBS))

shutil.rmtree(tmp, ignore_errors=True)
print("MIRROR GATE: " + ("ALL PASS" if not FAIL else f"FAILED {FAIL}"))
sys.exit(1 if FAIL else 0)
