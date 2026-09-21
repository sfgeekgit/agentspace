#!/usr/bin/env python3
"""Mirror gate (host-side): the publisher and the refresh trigger against a fresh state dir, a
fixture env row and a fixture log tree in place of the container (extract, extract_stopped and
inspect are the only docker touches, all replaced). Zero tokens, no docker, a few seconds. Covers
manifest validation, publish-by-default, view derivation against logwatch, the four-field event,
key redaction, the library, per-env settings and exclude, reuse of unchanged runs, the atomic
swap, stale / stopped / replaced / pinned containers, results and their escaped reader pages,
the trigger's debounce and refusals, and the viewer's no-HTML-from-data rule.
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
db.upsert_snap({"snap_id": "a0010001", "scenario": "gateworld", "version": "1.0", "ghcr_tag": "x", "scen": "pd",
                "created_at": "2026-01-01", "indexed_at": "2026-01-01", "runtime": "pi", "agents": ["a1", "a2"],
                "creation_message": "world root", "model": "m/base",
                "roster": [{"id": "a1", "role": "scout", "persona": "blank", "model": "m/base"},
                           {"id": "a2", "role": "guard", "persona": "blank", "model": "m/base"}]})
db.upsert_snap({"snap_id": "a0020002", "scenario": "gateworld", "version": "1.1", "ghcr_tag": "y", "parent_snap_id": "a0010001",
                "created_at": "2026-01-02", "indexed_at": "2026-01-02", "runtime": "pi", "agents": ["a1", "a2"],
                "creation_message": "mid-game", "model": "m/base"})
db.upsert_env({"name": "gate_env", "snap_id": "a0020002", "container_id": "c" * 64, "host": "localhost", "status": "dormant",
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
    state["reads"] += 1
    if isinstance(state["files"], Exception):
        raise state["files"]
    return dict(state["files"])


state["reads"] = 0
mirror.extract = mirror.extract_stopped = fake_extract
mirror._inspect = lambda env: state["inspect"] if env["name"] == "gate_env" else None
WEB, MANIFEST = tmp / "webroot", tmp / "mirror.toml"
RUNJSON = "runs/gate_env/run.json"


def publish(text=""):
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


def facts(cur):
    return json.loads((cur / RUNJSON).read_text())


# 1. manifest validation
try:
    mirror.cmd_publish(str(MANIFEST)); msg = ""
except click.ClickException as e:
    msg = e.format_message()
check("a named manifest that is missing is refused with the reason", "no manifest" in msg, msg)
check("bad env name refused", "not an environment name" in refused('[env."../etc"]\nthoughts = false\n'))
check("unknown setting refused", "unknown setting" in refused("[env.gate_env]\nthought = false\n"))
check("wrong type refused", "must be a" in refused('[env.gate_env]\nthoughts = "no"\n') and "list of view names" in refused("[env.gate_env]\nviews = [1]\n"))
check("bad exclude refused", "exclude" in refused('exclude = "gate_env"\n'))
check("unparseable manifest refused", refused("[[run\n") != "")
check("no refused manifest left a build behind", not (WEB / "current").exists())

# 2. publish by default: every env, every view but raw, sessions with thoughts; views match logwatch event for event
db.upsert_env({"name": "gone_env", "snap_id": "a0010001", "container_id": "e" * 64, "host": "localhost", "status": "stopped",
               "created_at": "2026-01-03T00:00:00+00:00", "budget_usd": 1})
cur = publish()
run = facts(cur)
names = [v["name"] for v in run["views"]]
check("with an empty manifest the env is published with every view but raw, agents and facets",
      {"feed", "board", "announcements", "budget", "game log (GM, spoilers)", "a1", "a1:thoughts", "a1:scratchpad", "a2"} <= set(names) and "raw" not in names, str(names))
check("an env whose container is gone and was never published is left out, not an error",
      not (cur / "runs/gone_env").exists() and any("gone_env" in s and "nothing to publish" in s for s in SAID))
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
    got = [json.loads(l) for l in (cur / f"runs/gate_env/views/{entry['file']}.jsonl").read_text().splitlines()] if entry else None
    check(f"view {name!r} matches logwatch event for event", got == want and bool(want), f"{got} != {want}")
    check(f"view {name!r} events carry exactly ts, who, kind, text", all(set(e) == {"ts", "who", "kind", "text"} for e in got or [{}]))
everything = "".join(p.read_text() for p in cur.rglob("*") if p.is_file() and p.suffix != ".zip")
check("a private field in a log record is not published", "PRIVATE-FIELD" not in everything)
check("the env's OpenRouter key is redacted everywhere", KEY not in everything and "[redacted]" in everything)
check("plain-text rendering exists and the zip holds it", "10:00:01 a1 hello all" in (cur / "runs/gate_env/views/feed.txt").read_text()
      and "gate_env/views/feed.txt" in zipfile.ZipFile(cur / "runs/gate_env/all.zip").namelist())
check("facts: status, 12-hex container, forked model, lineage with snapshot ids", run["status"] == "active" and run["container"] == "c" * 12
      and [r["model"] for r in run["roster"]] == ["m/base", "m/forked"]
      and [(l["kind"], l["ref"], l["id"]) for l in run["lineage"]] == [("scenario", "pd", None), ("root", "gateworld:1.0", "a0010001"),
                                                                        ("snapshot", "gateworld:1.1", "a0020002"), ("env", "gate_env", None)])
check("viewer assets and web.css copied", all((cur / f).is_file() for f in ("index.html", "run.html", "scenario.html", "world.html", "mirror.js", "mirror.css", "web.css")))
check("files are world-readable, directories traversable", all((p.stat().st_mode & 0o777) == (0o755 if p.is_dir() else 0o644) for p in cur.rglob("*")))

# 3. the library: every snapshot, every active scenario
site, lib = json.loads((cur / "site.json").read_text()), json.loads((cur / "worlds.json").read_text())["snaps"]
snap = next(s for s in lib if s["id"] == "a0020002")
check("worlds.json lists every snapshot with its root, parent, scenario and environments",
      {s["id"] for s in lib} == {"a0010001", "a0020002"} and (snap["root_id"], snap["parent"], snap["scenario"], snap["envs"], snap["root"]) == ("a0010001", "a0010001", "pd", ["gate_env"], False))
check("site.json: the run, the world root with counts, the scenarios", [r["id"] for r in site["runs"]] == ["gate_env"]
      and [(w["id"], w["snapshots"], w["envs"]) for w in site["worlds"]] == [("a0010001", 1, 1)]
      and {"pd", "mafia"} <= {x["name"] for x in site["scenarios"]} and next(x for x in site["scenarios"] if x["name"] == "pd")["runs"] == ["gate_env"])
scen = json.loads((cur / "scenarios/mafia.json").read_text())
check("a scenario with no run here is published too, with briefing and roles", bool(scen["world"]) and {"mafia", "villager"} <= {r["name"] for r in scen["roles"]})
check("the library carries no key and no env row fields", "openrouter" not in json.dumps(lib).lower() and KEY not in json.dumps(lib))

# 4. per-env settings and exclude
cur = publish("[env.gate_env]\nthoughts = false\n")
text = "".join(p.read_text() for p in (cur / "runs/gate_env/views").iterdir())
check("thoughts=false publishes no reasoning, tool traffic, scratchpad or facets",
      "SECRET-REASONING" not in text and "SCRATCH-LINE" not in text and "→ bash" not in text and "I vote 1" in text
      and not any(":" in v["name"] for v in facts(cur)["views"]))
cur = publish('[env.gate_env]\nagents = false\nviews = ["feed", "no such view"]\ntitle = "Gate run"\n')
check("agents=false and views=[...] publish exactly that; an unknown view is reported and skipped",
      [v["name"] for v in facts(cur)["views"]] == ["feed"] and any("no such view" in s for s in SAID) and facts(cur)["title"] == "Gate run")
cur = publish('exclude = ["gate_env", "nobody"]\n')
check("an excluded env is absent from the build and from the library's env lists; an unknown name is reported",
      not (cur / "runs").exists() and json.loads((cur / "site.json").read_text())["runs"] == [] and any("nobody" in s for s in SAID)
      and all(s["envs"] == [] for s in json.loads((cur / "worlds.json").read_text())["snaps"]))

# 5. unchanged logs are reused, not re-parsed; a change is picked up
cur = publish(); first = facts(cur)
cur = publish(); again = facts(cur)
check("an unchanged run reuses its views by hard link and keeps its event data, with a fresh as_of",
      (cur / "runs/gate_env/views/feed.jsonl").stat().st_nlink > 1 and again["views"] == first["views"] and again["as_of"] >= first["as_of"] and not again["stale"])
state["files"] = FILES | {"/data/gateway/public.jsonl": FILES["/data/gateway/public.jsonl"] + lines({"ts": T % 50, "from": "a2", "text": "a new post"})}
cur = publish()
check("a changed log is re-derived", "a new post" in (cur / "runs/gate_env/views/board.txt").read_text() and (cur / "runs/gate_env/views/board.jsonl").stat().st_nlink == 1)
state["files"] = FILES

# 6. atomic swap: a failing publish leaves the previous build in place, and nothing partial
before = (WEB / "current").resolve()
state["files"] = RuntimeError("boom")
try:
    publish(); raised = False
except RuntimeError:
    raised = True
check("a failing publish raises, `current` is unchanged, nothing partial is left",
      raised and (WEB / "current").resolve() == before and sorted(p.name for p in (WEB / "builds").iterdir())[-1] == before.name)
state["files"] = FILES

# 7. unreachable, stopped, replaced and pinned containers
as_of = facts(publish())["as_of"]
state["inspect"] = None
run = facts(publish())
check("container gone: last copy kept, stale, as_of unchanged", run["stale"] and run["as_of"] == as_of and (WEB / "current/runs/gate_env/views/feed.jsonl").is_file()
      and json.loads((WEB / "current/site.json").read_text())["runs"][0]["stale"])
state["files"], state["inspect"] = ValueError("logs exceed 25 MB"), ("c" * 64, True, "2026-01-03T00:00:01Z")
run = facts(publish())
check("unreadable logs: last copy kept, stale, with the reason", run["stale"] and "25 MB" in run["coverage"])
state["files"], state["inspect"] = FILES, ("c" * 64, False, "2026-01-03T00:00:01Z")
run = facts(publish()); reads = state["reads"]
check("a stopped container is read (docker cp) and published as stopped, not stale", run["status"] == "stopped" and not run["stale"] and run["started"] is None)
run = facts(publish())
check("a stopped run already published is final: not read again", state["reads"] == reads and run["status"] == "stopped" and not run["stale"])
run = facts(publish("[env.gate_env]\nthoughts = false\n"))
check("...unless its settings changed", state["reads"] == reads + 1 and not any(":" in v["name"] for v in run["views"]))
state["inspect"] = ("d" * 64, True, "2026-01-04T00:00:01Z")
check("a new container under the same env name is simply the new run", facts(publish())["container"] == "d" * 12)
run = facts(publish('[env.gate_env]\ncontainer = "cccc"\n'))
check("a pinned container= that does not match is refused, the last copy kept", run["stale"] and any("not the pinned" in s for s in SAID))
state["inspect"] = ("c" * 64, True, "2026-01-03T00:00:01Z")

# 8. results: copied only with matching sha256, with an escaped reader page per text file
bundle = tmp / "results" / "gate_env"
bundle.mkdir(parents=True)
REPORT = f"# report\n\n<script>alert(1)</script> and [a link](javascript:alert(2)) key {KEY}\n".encode()
(bundle / "report.md").write_bytes(REPORT)
(bundle / "manifest.json").write_text(json.dumps({"captured_at": "2026-01-03T12:00:00+00:00", "status": "partial",
                                                  "files": [{"name": "report.md", "size": len(REPORT), "sha256": hashlib.sha256(REPORT).hexdigest()}]}))
cur = publish()
run, page = facts(cur), (cur / "runs/gate_env/results/report.md.html").read_text()
check("results bundle copied, listed, zipped, key redacted", KEY not in (cur / "runs/gate_env/results/report.md").read_text()
      and {"report.md", "manifest.json"} == {f["name"] for f in run["results"]} and json.loads((cur / "site.json").read_text())["runs"][0]["results"] == 2
      and "gate_env/results/report.md" in zipfile.ZipFile(cur / "runs/gate_env/all.zip").namelist())
check("the reader page escapes HTML in the file and refuses a javascript: link", "<script" not in page and "&lt;script&gt;" in page
      and 'href="javascript' not in page and "generated before the run completed" in page and KEY not in page)
cur = publish()
check("an unchanged bundle is linked from the previous build, zip included", (cur / "runs/gate_env/results/report.md.html").stat().st_nlink > 1
      and (cur / "runs/gate_env/all.zip").stat().st_nlink > 1)
check("results=false publishes none", facts(publish("[env.gate_env]\nresults = false\n"))["results"] == [])
(bundle / "report.md").write_text("# tampered\n")
cur = publish()
check("a results file that fails its sha256 is not published", not (cur / "runs/gate_env/results").exists() and facts(cur)["results"] == []
      and not any("results" in n for n in zipfile.ZipFile(cur / "runs/gate_env/all.zip").namelist()))
shutil.rmtree(bundle)

# 9a. pruning and the symlink
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
