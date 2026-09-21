"""The public mirror's publisher: `mirror publish`, `mirror show`.

Exports the runs listed in the operator's manifest to a directory of static
files that Caddy serves with no password. One fixed `docker exec` per run pulls
the log files out as a tar stream; everything else (the logwatch parsers, the
facts, the scenario text, the results bundle) happens on the host. The publisher
reads only: it never wakes, starts, stops or messages an environment.

Docs: docs/mirror.md.
"""

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import click
from rich.console import Console
from rich.text import Text

from . import audit, budget, db, docker_host, logwatch, registry, results, versioning

console = Console()

MANIFEST = db.STATE_DIR / "mirror.toml"
WEBROOT = Path(os.environ.get("AGENTSPACE_MIRROR_DIR", "/srv/agentworldmaker-public"))
CAP = 50 * 1024 * 1024                       # bytes of log files per run
RUN_ID = re.compile(r"[a-z0-9][a-z0-9-]*\Z")
ENV_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
DEFAULT_VIEWS = ["feed", "board", "announcements"]
GITHUB = "https://github.com/sfgeekgit/agentspace/tree/main/scenarios/"

# Runs in the container, the only command the publisher ever runs there: exactly the files the
# views read (logwatch.tree's patterns plus the scenario's declared files), as a tar on stdout.
# mirror/gateway is a marker for "the gateway is up" (pi.agent_state's probe, folded in).
EXTRACTOR = r"""
import glob, io, json, os, sys, tarfile
pats = ["/world/world.json", "/agents/*", "/data/gateway/*.jsonl", "/agents/*/sessions/*.jsonl", "/agents/*/scratch/*.md"]
try:
    pats += [str(w["file"]) for w in json.load(open("/world/world.json")).get("watch", []) if isinstance(w, dict) and w.get("file")]
except (OSError, ValueError):
    pass
def gateway(p):
    try:
        return b"pi_gateway.py" in open(p, "rb").read()
    except OSError:
        return False
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as t:
    if any(gateway(p) for p in glob.glob("/proc/[0-9]*/cmdline") if p != "/proc/%d/cmdline" % os.getpid()):
        t.addfile(tarfile.TarInfo("mirror/gateway"), io.BytesIO())
    for p in sorted({f for pat in pats for f in glob.glob(pat)}):
        t.add(p, recursive=False)
"""


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise click.ClickException(f"no manifest at {path} (see mirror.toml.example)")
    try:
        m = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise click.ClickException(f"{path}: {e}")
    seen = set()
    for run in m.get("run", []):
        rid, env = run.get("id"), run.get("env")
        if not isinstance(rid, str) or not RUN_ID.match(rid):
            raise click.ClickException(f"run id {rid!r}: lowercase letters, digits and hyphens only")
        if rid in seen:
            raise click.ClickException(f"run id {rid!r} is listed twice")
        seen.add(rid)
        if not isinstance(env, str) or not ENV_NAME.match(env):
            raise click.ClickException(f"run {rid}: env {env!r} is not an environment name")
        views = run.setdefault("views", DEFAULT_VIEWS)
        if not isinstance(views, list) or not all(isinstance(v, str) for v in views):
            raise click.ClickException(f"run {rid}: views must be a list of view names")
    return m


def extract(env: dict) -> dict[str, bytes]:
    """The run's log files as {in-container path: bytes}; a directory is an empty entry. Regular
    files only and nothing touches the host filesystem, so a symlink an agent left in its scratch
    directory is never followed here."""
    proc = subprocess.Popen([*docker_host._base_cmd(env["host"] or "localhost"), "exec", env["name"],
                             "python3", "-c", EXTRACTOR],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    files, total = {}, 0
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
            for member in tar:
                if member.isdir():
                    files["/" + member.name.strip("/")] = b""
                if not member.isfile():
                    continue
                total += member.size
                if total > CAP:
                    raise ValueError(f"logs exceed {CAP // 2**20} MB")
                files["/" + member.name] = tar.extractfile(member).read()
    finally:
        proc.kill()
        proc.wait()
    return files


def _events(files: dict[str, bytes], view: logwatch.View, secret: str) -> list[dict]:
    """One view's events as the STREAMER + Watcher pair would produce them, sorted by time. Each is
    built field by field from the Event dataclass, so nothing else in a log record can be published."""
    keyed, last = [], 0.0
    for path in sorted(p for p in files if any(fnmatch(p, pat) for pat in view.patterns)):
        for raw in files[path].split(b"\n")[:-1]:                 # complete lines only, as the streamer
            try:
                ev = view.parse(path, raw.decode("utf-8", "replace"))
            except (KeyError, TypeError, AttributeError):         # a malformed record is not worth the build
                continue
            if not ev:
                continue
            t = logwatch._epoch(str(ev.ts))
            last = t if t is not None else last                   # an undated line stays after its predecessor
            keyed.append((last, {k: _scrub(str(getattr(ev, k)), secret) for k in ("ts", "who", "kind", "text")}))
    return [e for _, e in sorted(keyed, key=lambda x: x[0])]      # stable: file order breaks ties


def _scrub(text: str, secret: str) -> str:
    """The env's OpenRouter key is readable inside the container; never let a log that quotes it out."""
    return text.replace(secret, "[redacted]") if secret else text


def _iso(epoch: float | None) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def _slug(name: str, used: set) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "view"
    while slug in used:
        slug += "-x"
    used.add(slug)
    return slug


def _views(run: dict, files: dict[str, bytes]) -> list[tuple[logwatch.View, str | None]]:
    """The (view, agent id) pairs this run publishes. Manifest names match the world and scenario
    views exactly; an unknown name is reported and skipped."""
    aids = sorted({p.split("/")[2] for p in files if p.startswith("/agents/")} - {"lost+found"})
    nodes = logwatch.tree(logwatch.declared_views(files.get("/world/world.json", b"").decode("utf-8", "replace")), aids)
    world = {v.name: v for v, kids in nodes if not kids}
    for name in run["views"]:
        if name not in world:
            console.print(f"[yellow]{run['id']}: no view named {name!r}, skipped[/yellow] (views: {', '.join(world)})")
    out = [(world[n], None) for n in run["views"] if n in world]
    for v, kids in nodes:
        if kids and run.get("agents"):
            if run.get("thoughts"):      # the whole session and its facets
                out += [(x, v.name) for x in (v, *kids)]
            else:                        # only what the agent said: no reasoning, tool traffic or scratchpad
                out.append((logwatch.View(v.name, v.patterns, logwatch.parse_session("says")), v.name))
    return out


def _lineage(env: dict, snap: dict | None) -> tuple[str, list[dict]]:
    chain, seen = [], set()
    while snap and snap["snap_id"] not in seen:
        seen.add(snap["snap_id"])
        chain.append(snap)
        snap = db.get_snap_by_id(snap["parent_snap_id"]) if snap.get("parent_snap_id") else None
    scen = results.scenario(chain[-1]) if chain else ""
    steps = [{"kind": "root" if versioning.is_world_root(s["version"]) else "snapshot",
              "ref": f"{s['scenario']}:{s['version']}", "message": s.get("creation_message") or ""} for s in reversed(chain)]
    return scen, [{"kind": "scenario", "ref": scen, "message": ""}, *steps, {"kind": "env", "ref": env["name"], "message": ""}]


def _inspect(env: dict) -> tuple[str, bool, str] | None:
    try:
        cid, running, started = docker_host.inspect(env["host"] or "localhost", env["name"],
                                                    format="{{.Id}} {{.State.Running}} {{.State.StartedAt}}").split()
    except (docker_host.DockerError, ValueError):
        return None
    return cid, running == "true", started[:19] + "Z"


def _write(path: Path, data: bytes | str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def _publish_run(run: dict, out: Path, prev: Path | None, now: str) -> dict | None:
    """Write runs/<id>/ into the build and return its run.json, or None when there is nothing to show."""
    rid, dest = run["id"], out / "runs" / run["id"]
    old = prev / "runs" / rid if prev else None
    old_run = json.loads((old / "run.json").read_text()) if old and (old / "run.json").is_file() else None
    env = db.get_env(run["env"])
    seen = _inspect(env) if env else None
    files, why = None, None
    if not seen:
        why = "the environment or its container no longer exists"
    elif run.get("container") and not seen[0].startswith(run["container"]):
        why = f"container {seen[0][:12]} is not the pinned {run['container']}"
    elif not run.get("container") and old_run and old_run["container"] != seen[0][:12]:
        why = (f"environment {run['env']!r} is now container {seen[0][:12]}, was {old_run['container']}: "
               "a new run under an old name is not published until the manifest changes its id or pins container=")
    elif not seen[1]:
        why = "the container is not running"
    else:
        try:
            files = extract(env)
        except (tarfile.TarError, ValueError, OSError) as e:
            why = f"the logs could not be read ({e})"
    if files is None:
        console.print(f"[yellow]{rid}: {why}[/yellow]" + ("; keeping the last published copy" if old_run else "; nothing published"))
        if not old_run:
            return None
        shutil.copytree(old, dest)
        facts = old_run | {"stale": True, "coverage": f"Not refreshed: {why}. Showing the copy from {old_run['as_of']}."}
        if seen and not seen[1]:
            facts["status"] = "stopped"
        _write(dest / "run.json", json.dumps(facts))
        return facts

    secret = env.get("openrouter_key") or ""
    used, views, texts = set(), [], {}
    for view, agent in _views(run, files):
        evs, slug = _events(files, view, secret), _slug(view.name, used)
        _write(dest / "views" / f"{slug}.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))
        texts[f"views/{slug}.txt"] = "".join(          # render() minus colour; a bare-seconds ts shown as a clock time like the rest
            Text.from_markup(logwatch.render(logwatch.Event(**e | {"ts": _iso(logwatch._epoch(e["ts"])) or ""}))).plain + "\n" for e in evs).encode()
        stamps = [t for t in (logwatch._epoch(e["ts"]) for e in evs) if t is not None]
        views.append({"name": view.name, "file": slug, "events": len(evs), "last_ts": _iso(max(stamps, default=None)), "agent": agent})
    if run.get("results"):
        try:
            with results.locked():
                bundle = results.bundle_files(run["env"])
        except ValueError as e:
            console.print(f"[dim]{rid}: no results bundle ({e})[/dim]")
            bundle = {}
        texts |= {f"results/{name}": data.replace(secret.encode(), b"[redacted]") if secret else data for name, data in bundle.items()}
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in texts.items():
            _write(dest / name, data)
            z.writestr(f"{rid}/{name}", data)
    _write(dest / "all.zip", zipped.getvalue())

    snap = db.get_snap_by_id(env["snap_id"])
    scen, lineage = _lineage(env, snap)
    try:
        world = json.loads(files.get("/world/world.json", b"{}"))
    except ValueError:
        world = {}
    roster = (snap or {}).get("roster") or [{"id": a} for a in (snap or {}).get("agents") or []]
    timing = audit.env_runtime_intervals(since_by_name={env["name"]: env["created_at"]}).get(env["name"]) or {}
    runtime = timing.get("closed", 0.0) + ((datetime.now(timezone.utc) - timing["open_since"]).total_seconds() if timing.get("open_since") else 0)
    spend = None
    if run.get("budget"):
        try:
            spend = (budget.usage(env) or (None,))[0]
        except Exception as e:                                   # the mirror is still worth publishing without it
            console.print(f"[dim]{rid}: budget lookup failed ({e})[/dim]")
    facts = {
        "id": rid, "env": env["name"], "title": run.get("title") or env["name"], "blurb": run.get("blurb") or "",
        "scenario": scen, "container": seen[0][:12], "as_of": now, "stale": False,
        "status": "active" if "/mirror/gateway" in files and any(fnmatch(p, "/agents/*/sessions/*.jsonl") for p in files) else "dormant",
        "started": seen[2], "created": env.get("created_at"), "runtime_seconds": int(runtime),
        "coverage": "Everything the operator chose to publish for this run, complete as of the time above.",
        "budget_used": spend, "budget_usd": float(env.get("budget_usd") or 0),
        "lineage": lineage,
        "roster": [{"id": r["id"], "role": r.get("role") or "", "persona": r.get("persona") or "",
                    "model": (world.get("models") or {}).get(r["id"]) or r.get("model") or world.get("model") or ""} for r in roster],
        "views": views,
        "results": [{"name": n[8:], "size": len(d), "file": n} for n, d in texts.items() if n.startswith("results/")],
    }
    _write(dest / "run.json", json.dumps(facts))
    return facts


def _row(f: dict) -> dict:
    """A run's line on the board (site.json), from its run.json."""
    models = {}
    for r in f["roster"]:
        models[r["model"]] = models.get(r["model"], 0) + 1
    world = [v for v in f["views"] if not v["agent"]] or f["views"]
    return {k: f[k] for k in ("id", "env", "title", "scenario", "status", "started", "runtime_seconds",
                              "budget_used", "budget_usd", "as_of", "stale")} | {
        "agents": len(f["roster"]), "models": models, "results": bool(f["results"]),
        "events": max((v["events"] for v in world), default=0),
        "last_event_ts": max((v["last_ts"] for v in f["views"] if v["last_ts"]), default=None),
        "views": [v["name"] for v in f["views"] if not v["agent"]],
        "agent_views": len({v["agent"] for v in f["views"] if v["agent"]})}


def _scenario(name: str, run_ids: list[str]) -> dict | None:
    """What the demo's scenario page shows: description, world briefing, role prompts."""
    try:
        s = registry.load_scen(name)
    except registry.RegistryError:
        return None
    world = s["dir"] / "world.md"
    roles = sorted(s["roles_dir"].glob("*.md")) if s.get("roles_dir") else []
    return {"name": name, "description": s.get("description") or "", "world": world.read_text() if world.is_file() else "",
            "roles": [{"name": p.stem, "text": p.read_text()} for p in roles],
            "agents": f"{s.get('min_agents', '?')}–{s.get('max_agents', '?')}", "runs": run_ids, "github": GITHUB + name}


def cmd_publish(manifest: str | None = None):
    m = load_manifest(Path(manifest) if manifest else MANIFEST)
    os.umask(0o022)                                              # everything here is public: 0755 / 0644
    now = datetime.now(timezone.utc)
    current = WEBROOT / "current"
    prev = current.resolve() if current.exists() else None
    out = WEBROOT / "builds" / now.strftime("%Y%m%dT%H%M%S.%fZ")
    out.mkdir(parents=True)
    try:
        runs = [f for run in m.get("run", []) if (f := _publish_run(run, out, prev, now.isoformat(timespec="seconds")))]
        scens = {}
        for f in runs:
            if re.fullmatch(r"[A-Za-z0-9_]+", f["scenario"] or ""):
                scens.setdefault(f["scenario"], []).append(f["id"])
        pages = [s for name, ids in scens.items() if (s := _scenario(name, ids))]
        for s in pages:
            _write(out / "scenarios" / f"{s['name']}.json", json.dumps(s))
        for asset in (*sorted((registry.REPO_ROOT / "mirror").iterdir()), registry.REPO_ROOT / "web.css"):
            shutil.copyfile(asset, out / asset.name)
        _write(out / "site.json", json.dumps({
            "generated_at": now.isoformat(timespec="seconds"), "title": m.get("title") or "Agent World Maker",
            "min_refresh_seconds": int(m.get("min_refresh_seconds", 20)), "runs": [_row(f) for f in runs],
            "scenarios": [{"name": s["name"], "description": s["description"], "roles": len(s["roles"]), "runs": s["runs"]} for s in pages]}))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)                   # `current` still points at the last good build
        raise
    swap = WEBROOT / ".current-new"
    swap.unlink(missing_ok=True)
    swap.symlink_to(Path("builds") / out.name)
    swap.replace(current)
    for old in sorted(p for p in (WEBROOT / "builds").iterdir() if p != out)[:-max(int(m.get("keep_builds", 3)) - 1, 0) or None]:
        shutil.rmtree(old, ignore_errors=True)
    console.print(f"[green]published[/green] {len(runs)} run(s) to {out}")


def cmd_show():
    site = WEBROOT / "current" / "site.json"
    if not site.is_file():
        console.print("[dim]nothing published yet. Try 'agentspace mirror publish'.[/dim]")
        return
    data = json.loads(site.read_text())
    console.print(f"build {(WEBROOT / 'current').resolve().name}, generated {data['generated_at']}")
    for r in data["runs"]:
        console.print(f"  {r['id']:<24} {r['status']:<8} as of {r['as_of']}{'  STALE' if r['stale'] else ''}  {r['events']} events")
