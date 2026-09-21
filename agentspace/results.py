"""Host-side Recess results: read-only capture, reproducible files, isolated publishing.

No model calls and no scenario code execution. Container lifecycle is independent
of game completion. Only explicit game/runtime completion counts as complete.
"""
import collections
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from urllib.parse import urlsplit

import click

from . import db, docker_host

REPO = Path(__file__).resolve().parents[1]
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def validate_name(name):
    if not NAME.fullmatch(name):
        raise ValueError("Invalid environment or file name")
    return name


def root():
    return Path(os.environ.get("AGENTSPACE_RESULTS_DIR", "/opt/agentspace-results"))


def scenario(snap):
    if snap.get("scen"):
        return snap["scen"]
    match = re.search(r"(?:^|[ ,])scen=([a-zA-Z0-9_]+)", snap.get("creation_message") or "")
    return match[1] if match else snap.get("scenario", "")


def is_recess(snap):
    return scenario(snap).startswith("recess_") or scenario(snap) == "recess"


def environment(name):
    validate_name(name)
    env = db.get_env(name)
    if not env:
        raise ValueError("Environment not found")
    snap = db.get_snap_by_id(env["snap_id"]) or {}
    origin, seen = snap, set()
    while origin and not is_recess(origin) and origin.get("parent_snap_id") not in seen:
        parent = origin.get("parent_snap_id")
        if not parent:
            break
        seen.add(parent)
        origin = db.get_snap_by_id(parent) or {}
    if not is_recess(origin):
        raise ValueError("This exporter currently supports Recess scenarios")
    if not is_recess(snap):
        snap = {**snap, "scen": scenario(origin)}
    return env, snap


def json_lines(text):
    """Ignore a truncated final write; fail on corruption in completed lines."""
    lines = text.splitlines()
    rows = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            if i != len(lines) - 1 or text.endswith("\n"):
                raise
    return rows


def copy_texts(env, path):
    """docker cp also works on stopped containers. Never extract container paths."""
    proc = docker_host.run(env.get("host") or "localhost", "cp", f"{env['name']}:{path}", "-", check=False, timeout=60)
    if proc.returncode:
        return {}
    files = {}
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
        for member in archive:
            if member.isfile() and (member.name.endswith((".json", ".jsonl", ".md", ".log")) or member.name.endswith(".sysprompt")):
                files[member.name] = archive.extractfile(member).read().decode("utf-8", "replace")
    return files


def read_file(env, path):
    return next(iter(copy_texts(env, path).values()), "")


# The running-container fast path reads only prompt material and session logs.
# In particular it never reads .pi/auth.json, environment variables or API keys.
AGENT_READER = '''import json,pathlib,sys
out={}
for aid in json.loads(sys.argv[1]):
 h=pathlib.Path('/agents')/aid
 paths=list(h.glob('*.md'))+list((h/'sessions').rglob('*.jsonl'))+list((h/'sessions').rglob('*.sysprompt'))
 paths += [h/'sessions'/'.sysprompt',h/'prompt_log.jsonl',h/'.FIRST_WAKE.md.done']
 out[aid]={str(p.relative_to(h)):p.read_text() for p in paths if p.is_file() and not p.is_symlink()}
print(json.dumps(out))
'''


def capture(name, agents=True):
    env, snap = environment(name)
    raw = read_file(env, "/dispatch/state.json")
    if not raw:
        raise ValueError("No dispatcher state is available yet")
    state = json.loads(raw)
    captured = now()
    world = json.loads(read_file(env, "/world/world.json") or "{}")
    events = state.get("events")
    if events is None:
        events = json_lines(read_file(env, "/dispatch/game_log.jsonl"))
    log = read_file(env, "/dispatch/dispatchd.log")
    runtime = json.loads(read_file(env, "/dispatch/run_status.json") or "{}")
    host = env.get("host") or "localhost"
    procs = docker_host.stdout(host, "top", name, "-eo", "args", check=False, timeout=15)
    running = "/runtime_pi/dispatchd.py" in procs
    data = {"name": name, "scenario": scenario(snap), "snap_id": snap.get("snap_id"),
            "captured_at": captured, "state": state, "events": events, "world": world,
            "dispatch_log": log, "runtime": runtime, "dispatcher_running": running,
            "roster": snap.get("roster") or [], "agents": {}}
    if not agents:
        return data
    data["audit"] = json_lines(read_file(env, "/data/gateway/audit.jsonl"))
    data["usage"] = json_lines(read_file(env, "/data/gateway/budget.jsonl"))
    ids = sorted(set(snap.get("agents") or []) | {a["id"] for a in data["roster"]})
    for aid in ids:
        validate_name(aid)
    proc = docker_host.run(host, "exec", name, "python3", "-c", AGENT_READER, json.dumps(ids), check=False, timeout=60)
    if proc.returncode == 0:
        data["agents"] = json.loads(proc.stdout)
    else:
        for aid in ids:
            files = copy_texts(env, f"/agents/{aid}/sessions")
            for filename in ("SOUL.md", "ROLE.md", "WORLD.md", "MEMORY.md", "FIRST_WAKE.md", ".FIRST_WAKE.md.done", "prompt_log.jsonl"):
                files.update(copy_texts(env, f"/agents/{aid}/{filename}"))
            data["agents"][aid] = files
    return data


def completion(data):
    s, events = data["state"], data["events"]
    phase, ending = s.get("phase"), s.get("ended")
    lifecycle = [line for line in data.get("dispatch_log", "").splitlines()
                 if any(t in line for t in ("dispatch start:", "dispatch crashed:", "game complete"))]
    last = lifecycle[-1] if lifecycle else ""
    terminal = phase == "done" or any(e.get("kind") == "game_over" for e in events)
    terminal = terminal or "game complete" in last or data.get("runtime", {}).get("status") == "complete"
    if terminal and not s.get("paused_reason"):
        cap = data.get("world", {}).get("params", s.get("params", {})).get("max_turns")
        status, reason = "complete", ending or ("turn_cap" if cap and s.get("turn", 0) >= cap else "dispatcher_completed")
    elif data.get("dispatcher_running"):
        status, reason = "in_progress", "Dispatcher is running"
    elif s.get("paused_reason"):
        status, reason = "paused", s["paused_reason"]
    elif "dispatch crashed:" in last or data.get("runtime", {}).get("status") == "failed":
        status, reason = "stalled", "Dispatcher exited with an error"
    else:
        status, reason = "incomplete", "No completion marker; dispatcher is not running"
    return {"status": status, "complete": status == "complete", "reason": reason,
            "turns": s.get("turn", 0), "max_turns": data.get("world", {}).get("params", s.get("params", {})).get("max_turns"),
            "phase": phase, "ending": ending, "checked_at": data["captured_at"]}


def metrics(data):
    s, events = data["state"], data["events"]
    counts = collections.Counter(e.get("kind") for e in events)
    npc_ids = {n["agent"] for n in s.get("npcs", {}).values()}
    calls = sum(e.get("event") == "dispatch_wake" and e.get("to") in npc_ids for e in data.get("audit", []))
    conversations = sum(counts[k] for k in ("npc", "schedule", "conversation", "scheduled_character"))
    stats = s.get("attributes") if "attributes" in s else s.get("player", {}).get("stats", {})
    changes, details = 0, []
    if "attributes" in s:
        fixed = s.get("fixed_attributes", list(stats))
        values, last = dict.fromkeys(fixed, 0), {}
        for e in events:
            if e.get("kind") != "accepted_plan":
                continue
            for change in json.loads(e["text"]).get("attributes", []):
                key, delta, turn = change["name"], change["delta"], e["turn"]
                if key in fixed and turn - last.get(key, -100) < 3:
                    continue
                before = values.get(key, 0)
                after = max(-8, min(8, before + delta)) if key in fixed else before + delta
                values[key], last[key] = after, turn
                if after != before:
                    changes += 1
                    details.append({"turn": turn, "stat": key, "before": before, "after": after})
        verified = values == stats
    else:
        values = dict.fromkeys(stats, 0)
        verified = True
        for e in events:
            if e.get("kind") == "stat":
                match = re.search(r"(\S+) ([+-]\d+) -> (-?\d+)$", e["text"])
                if match:
                    key, delta, after = match[1], int(match[2]), int(match[3])
                    verified = verified and values.get(key, 0) == after - delta
                    values[key] = after
                    if delta != 0:
                        changes += 1
                        details.append({"turn": e["turn"], "stat": key, "before": after-delta, "after": after})
                else:
                    verified = False
        verified = verified and values == stats
    return {"npc_calls": calls if "audit" in data else None, "npc_interactions": conversations,
            "stat_updates": changes, "stat_replay_matches_state": verified, "stats": stats,
            "stat_changes": details, "event_counts": dict(counts)}


def message_text(content):
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content or [] if p.get("type") == "text")


def agent_order(data):
    s = data["state"]
    player = s["player"] if isinstance(s["player"], str) else s["player"]["agent"]
    gm = s["gm"]
    return [player, gm] + sorted(set(data["agents"]) - {player, gm})


def prompt_exports(data):
    """Actual text messages + exact recorded systems, or explicitly labeled reconstruction.

    Historical Pi sessions omit system prompts. Never present a reconstruction
    from today's files as a verbatim historical record. Never export thinking.
    """
    transcripts, inputs, systems, warnings = [], [], [], []
    roles = {a["id"]: a.get("role", "agent") for a in data["roster"]}
    order = agent_order(data)
    for aid in order:
        files = data["agents"].get(aid, {})
        sessions = []
        for path, raw in files.items():
            if path.startswith("sessions/") and path.endswith(".jsonl"):
                rows = json_lines(raw)
                if rows:
                    sessions.append((rows[0].get("timestamp", ""), path, rows))
        sessions.sort()
        recorded = json_lines(files.get("prompt_log.jsonl", ""))
        prompt_versions = []
        for record in recorded:
            if record.get("kind") == "provider_system":
                for msg in record.get("messages", []):
                    prompt_versions.append({"text": message_text(msg.get("content")), "source": "recorded provider system prompt",
                                            "timestamp": record["timestamp"], "session": Path(record.get("session") or "").name})
        frozen = files.get("sessions/.sysprompt")
        source = "reconstructed from frozen .sysprompt and Pi date/cwd suffix"
        if frozen is None:
            names = sorted((p for p in files if "/" not in p and p.endswith(".md") and not p.startswith(".") and p != "FIRST_WAKE.md"),
                           key=lambda p: (p != "SOUL.md", p == "MEMORY.md", p))
            frozen = "\n\n---\n\n".join(f"# {p}\n\n{files[p].strip()}".strip() for p in names)
            source = "reconstructed from current home Markdown files; historical edits are not recoverable"
        reconstructed = False
        for timestamp, path, rows in sessions:
            relevant = [p for p in prompt_versions if p["session"] == Path(path).name]
            if not relevant:
                archived = files.get(path.removesuffix(".jsonl") + ".sysprompt")
                base = archived if archived is not None else frozen
                if base:
                    p = {"text": base + f"\nCurrent date: {timestamp[:10]}\nCurrent working directory: /agents/{aid}",
                         "source": "reconstructed from archived session sandwich and Pi date/cwd suffix" if archived is not None else source,
                         "timestamp": timestamp, "session": Path(path).name}
                    prompt_versions.append(p)
                    relevant = [p]
                    reconstructed = True
            timeline = []
            for index, p in enumerate(relevant):
                # Provider hooks run after the input is appended to the session.
                # Put each system before the input of that request in the reading order.
                preceding = [r.get("timestamp", "") for r in rows if r.get("message", {}).get("role") == "user" and r.get("timestamp", "") <= p["timestamp"]]
                at = timestamp if index == 0 else preceding[-1] if preceding else p["timestamp"]
                timeline.append({"role": "system", **p, "recorded_at": p["timestamp"], "timestamp": at})
            for row in rows:
                if row.get("type") == "compaction":
                    timeline.append({"role": "context_summary", "text": row.get("summary", ""), "timestamp": row.get("timestamp", ""), "source": path})
                if row.get("type") != "message":
                    continue
                msg = row.get("message", {})
                role = msg.get("role")
                text = message_text(msg.get("content"))
                if text and role in {"user", "assistant", "system", "toolResult"}:
                    item = {"role": role, "text": text, "timestamp": row.get("timestamp", ""), "source": path,
                            "session": Path(path).name, "message_id": row.get("id")}
                    timeline.append(item)
                    if role != "assistant":
                        inputs.append({"agent": aid, **item})
            timeline.sort(key=lambda r: r.get("timestamp", ""))
            if aid == order[0]:
                transcripts.extend(timeline)
        if not sessions and frozen:
            prompt_versions.append({"text": frozen, "source": "prepared prompt sandwich; agent has no saved session", "timestamp": "", "session": ""})
        if reconstructed:
            warnings.append(f"{aid}: historical full system prompts were not recorded. Reconstructed prompts include Pi's known date/cwd suffix; past file edits, date changes within sessions, or extra Pi context may be unrecoverable.")
        if not prompt_versions:
            warnings.append(f"{aid}: no system prompt is available.")
        seen = set()
        for p in prompt_versions:
            if p["text"] not in seen:
                systems.append({"agent": aid, "role": roles.get(aid, "agent"), **p})
                seen.add(p["text"])
    if not transcripts:
        warnings.append("No player session logs available; transcript contains only the scenario's saved dialogue, not complete player context.")
        for row in data["state"].get("transcript", []):
            if "speaker" in row:
                transcripts.append({"role": "assistant" if row["speaker"] == "Player" else "user", "text": row["text"], "source": "state fallback"})
            else:
                for key, role in (("in", "assistant"), ("out", "user")):
                    if row.get(key):
                        transcripts.append({"role": role, "text": row[key], "source": "state fallback"})
    return transcripts, systems, inputs, warnings


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def lines(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def render(data):
    status, stats = completion(data), metrics(data)
    transcript, prompts, inputs, warnings = prompt_exports(data)
    if not stats["stat_replay_matches_state"]:
        warnings.append("Attribute replay differs from final state; stat update count is provisional.")
    if not status["complete"]:
        warnings.append("Partial snapshot: this run has not completed. Regenerate after it finishes.")
    warnings.append("Capture is read-only and does not pause the run. Files from an active run may span adjacent moments; captured_at marks the state read.")
    names = {"system": "System prompt", "user": "Game master / player input", "assistant": "Player", "context_summary": "Context compaction", "toolResult": "Tool result"}
    transcript_md = f"# {data['name']} — player transcript\n\n" + "\n".join(f"- {w}" for w in warnings) + "\n\n"
    transcript_md += "\n\n".join(f"## {names.get(m['role'], m['role'])}\n\n{m.get('timestamp', '')} · {m.get('source', '')}\n\n{m['text']}" for m in transcript) + "\n"
    prompts_md = "# Agent system prompts\n\nPlayer first, then GM, then other agents. Unique system prompt versions only; no generated game dialogue. Per-wake inputs are in agent_inputs.jsonl. Agents without sessions have prepared prompts, not a claim of delivery.\n\n"
    prompts_md += "\n\n".join(f"## {p['agent']} — {p['role']}\n\n{p['source']}\n\n{p['text']}" for p in prompts) + "\n"
    summary = [f"# {data['name']} — results", f"Status: {status['status']}. Turns: {status['turns']}/{status['max_turns']}. Ending: {status['ending'] or 'none'}.",
               f"NPC calls (includes retries): {stats['npc_calls']}. Completed NPC interactions: {stats['npc_interactions']}. Nonzero stat updates: {stats['stat_updates']}.",
               "## Attributes", *[f"- {k}: {v}" for k, v in stats["stats"].items()]]
    state = data["state"]
    player = state["player"] if isinstance(state["player"], dict) else state
    location = player.get("loc")
    summary += ["## World and player", f"Location: {state.get('map', {}).get(location, {}).get('name', location)}. Places: {len(state.get('map', {}))}. NPCs: {len(state.get('npcs', {}))}."]
    inventory = player.get("inventory", [k for k, v in state.get("items", {}).items() if v.get("holder") == "player"])
    summary += ["Inventory: " + (", ".join(map(str, inventory)) or "nothing"), "## NPCs"]
    for key, npc in state.get("npcs", {}).items():
        summary.append(f"- {key}: at {npc.get('loc')}; conversations {npc.get('visits', 'not tracked')}; relationship {npc.get('bond', 'not tracked')}; unlocked {', '.join(npc.get('unlocked', [])) or 'none'}.")
    summary += ["## Accomplishments", ", ".join(state.get("achievements", [])) or "None recorded.", "## Flags", dumps(state.get("flags", {})),
                "## Engine events", *[f"- {k}: {v}" for k, v in sorted(stats["event_counts"].items())],
                "## Export notes", *[f"- {w}" for w in warnings]]
    result = {"run_name": data["name"], "scen": data["scenario"], "snap_id": data.get("snap_id"), **status,
              "captured_at": data["captured_at"], "metrics": stats, "warnings": warnings, "path": f"{data['name']}/transcript.md"}
    return {"transcript.md": transcript_md, "transcript.jsonl": lines(transcript),
            "agent_prompts.md": prompts_md, "agent_prompts.jsonl": lines(prompts), "agent_inputs.jsonl": lines(inputs),
            "state.json": dumps(data["state"]), "game_log.jsonl": lines(data["events"]),
            "summary.md": "\n\n".join(summary) + "\n", "result.json": dumps(result),
            "usage.jsonl": lines(data.get("usage", [])), "dispatch.log": data.get("dispatch_log", "")}, result


@contextlib.contextmanager
def locked():
    root().mkdir(parents=True, exist_ok=True)
    with (root() / ".results.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def directory(name):
    path = root() / validate_name(name)
    if path.is_symlink() or path.resolve().parent != root().resolve():
        raise ValueError("Unsafe results directory")
    return path


def atomic(path, text):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".result-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def upsert_index(path, result):
    rows = json_lines(path.read_text()) if path.exists() else []
    rows = [row for row in rows if row.get("run_name") != result["run_name"]]
    atomic(path, lines([*rows, result]))


def generate(name):
    data = capture(name)
    files, result = render(data)
    # Capture can be slow. An older capture must not replace a newer one that
    # finished writing while this process was still reading its session files.
    with locked():
        dest = directory(name)
        dest.mkdir(exist_ok=True)
        previous = manifest(name)
        if previous and previous.get("captured_at", "") > data["captured_at"]:
            raise ValueError("A newer results capture is already saved; refresh the results page")
        bundle_manifest = {"version": 1, "run_name": name, "captured_at": data["captured_at"], "status": result["status"], "files": []}
        for filename, text in files.items():
            atomic(dest / filename, text)
            raw = text.encode()
            bundle_manifest["files"].append({"name": filename, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        atomic(dest / "manifest.json", dumps(bundle_manifest))
        upsert_index(root() / "runs.jsonl", result)
    return result


def manifest(name):
    path = directory(name) / "manifest.json"
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("Unsafe manifest")
    return json.loads(path.read_text())


def bundle_files(name):
    m = manifest(name)
    if not m:
        raise ValueError("Generate results first")
    files = {}
    for entry in m["files"]:
        filename = validate_name(entry["name"])
        path = directory(name) / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("Result file is missing or unsafe; regenerate results")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Result file changed since generation; regenerate results")
        files[filename] = content
    files["manifest.json"] = dumps(m).encode()
    return files


def artifact(name, filename):
    """One verified file and its capture metadata, without loading other files."""
    with locked():
        m = manifest(name)
        if not m:
            raise ValueError("Generate results first")
        validate_name(filename)
        if filename == "manifest.json":
            return dumps(m).encode(), m
        entry = next((f for f in m["files"] if f["name"] == filename), None)
        if entry is None:
            raise ValueError("File is not in this results bundle")
        path = directory(name) / filename
        if path.is_symlink() or not path.is_file():
            raise ValueError("Result file is missing or unsafe; regenerate results")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Result file changed since generation; regenerate results")
        return content, m


def download(name, filename):
    with locked():
        files = bundle_files(name)
        if filename == "all.zip":
            out = io.BytesIO()
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
                for path, content in files.items():
                    archive.writestr(f"{name}/{path}", content)
            return out.getvalue()
        if filename not in files:
            raise ValueError("File is not in this results bundle")
        return files[filename]


def git(cwd, *args):
    proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if proc.returncode:
        raise ValueError(f"git {args[0]} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def publish_target():
    if root().resolve() == REPO.resolve():
        raise ValueError("Results repository must be separate from the core repository")
    remote = git(root(), "remote", "get-url", "origin")
    core_remote = git(REPO, "remote", "get-url", "origin")
    def identity(url):
        if "://" in url:
            parsed = urlsplit(url)
            normalized = (parsed.hostname or "") + parsed.path
        elif re.match(r"[^/]+@[^:]+:", url):
            normalized = url.split("@", 1)[1].replace(":", "/", 1)
        else:
            normalized = str(Path(url).resolve())
        return re.sub(r"\.git$", "", normalized.rstrip("/")).lower()
    if identity(remote) == identity(core_remote):
        raise ValueError("Refusing to publish results to the core repository")
    if ":" not in remote and Path(remote).resolve() == REPO.resolve():
        raise ValueError("Refusing to publish results to the core repository")
    if not remote or remote.startswith("-"):
        raise ValueError("Invalid results remote")
    return remote


def publish(name):
    """Commit only a verified bundle and its index row in a fresh clone.

    The operator's results checkout may be dirty. Never stage, stash, reset or
    push its branch. A failed/conflicting push leaves that checkout untouched.
    """
    remote = publish_target()
    with locked():
        files = bundle_files(name)
    result = json.loads(files["result.json"])
    with tempfile.TemporaryDirectory(prefix="agentspace-publish-") as tmp:
        repo = Path(tmp) / "repo"
        git(Path(tmp), "clone", "--single-branch", "--", remote, str(repo))
        git(repo, "config", "user.name", "Agentspace results")
        git(repo, "config", "user.email", "agentspace@localhost")
        dest = repo / name
        if dest.is_symlink() or (repo / "runs.jsonl").is_symlink():
            raise ValueError("Unsafe results repository paths")
        dest.mkdir(exist_ok=True)
        for filename, content in files.items():
            path = dest / filename
            if path.is_symlink():
                raise ValueError("Unsafe result file in repository")
            path.write_bytes(content)
        upsert_index(repo / "runs.jsonl", result)
        git(repo, "add", "--", *[f"{name}/{f}" for f in files], "runs.jsonl")
        if git(repo, "diff", "--cached", "--name-only"):
            git(repo, "commit", "-m", f"Results for {name} ({result['status']}, turn {result['turns']})")
            git(repo, "push", "origin", "HEAD")
        commit = git(repo, "rev-parse", "HEAD")
    receipt = {"remote": remote, "commit": commit, "published_at": now(), "captured_at": result["captured_at"]}
    with locked():
        atomic(root() / f".{name}.published.json", dumps(receipt))
    return receipt


def cmd_generate(name):
    click.echo(dumps(generate(name)))
    click.echo(f"Results written to {directory(name)}")


def cmd_publish(name):
    click.echo(dumps(publish(name)))


def cmd_show(name):
    click.echo(dumps(completion(capture(name, agents=False))))
