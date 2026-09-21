"""The public mirror's publisher: `mirror publish`, `mirror show`.

Exports every environment (minus the manifest's exclusions), every world root and
snapshot, and every scenario to a directory of static files that Caddy serves with
no password. One fixed `docker exec` per running env (`docker cp` for a stopped
one) pulls the log files out as a tar stream; everything else (the logwatch
parsers, the facts, the library, the results bundle and its reader pages) happens
on the host. The publisher reads only: it never wakes, starts, stops or messages
an environment.

Docs: docs/mirror.md.
"""

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

import click
from rich.console import Console

from . import audit, budget, db, docker_host, logwatch, registry, result_view, results, versioning

console = Console()

MANIFEST = db.STATE_DIR / "mirror.toml"
WEBROOT = Path(os.environ.get("AGENTSPACE_MIRROR_DIR", "/srv/agentworldmaker-public"))
CAP = 25 * 1024 * 1024                       # bytes of log files per run
WORKERS = 4                                  # runs read at once; with CAP, bounds the publisher's memory
ENV_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")   # also the run's id: a file name and a URL parameter
SNAP_ID = re.compile(r"[0-9a-f][0-9a-f-]*\Z")            # 32 hex, or the dashed uuids of older snapshots
SCEN_NAME = re.compile(r"[A-Za-z0-9_]+\Z")
# Everything is public unless the manifest says otherwise. `views` defaults to every world and
# scenario view except `raw` (whole audit records, which would bypass the four-field event).
DEFAULTS = {"title": "", "blurb": "", "views": None, "agents": True, "thoughts": True, "results": True,
            "budget": False, "container": ""}
TEXT = (".md", ".txt", ".json", ".jsonl", ".log")    # results files that get a reader page
LOG_PATTERNS = ["/data/gateway/*.jsonl", "/agents/*/sessions/*.jsonl", "/agents/*/scratch/*.md"]
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
    """The manifest is optional: with none, everything is published with the defaults."""
    if not path.is_file():
        if path != MANIFEST:
            raise click.ClickException(f"no manifest at {path} (see mirror.toml.example)")
        return {}
    try:
        m = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise click.ClickException(f"{path}: {e}")
    exclude, envs = m.setdefault("exclude", []), m.setdefault("env", {})
    if not isinstance(exclude, list) or not all(isinstance(x, str) for x in exclude):
        raise click.ClickException("exclude must be a list of environment names")
    if not isinstance(envs, dict):
        raise click.ClickException("[env.<name>] tables hold the per-environment settings")
    for name, opts in envs.items():
        if not ENV_NAME.match(name) or not isinstance(opts, dict):
            raise click.ClickException(f"[env.{name}]: not an environment name")
        for key, value in opts.items():
            if key not in DEFAULTS:
                raise click.ClickException(f"[env.{name}]: unknown setting {key!r} (known: {', '.join(DEFAULTS)})")
            want = list if key == "views" else type(DEFAULTS[key])
            if not isinstance(value, want) or (key == "views" and not all(isinstance(v, str) for v in value)):
                raise click.ClickException(f"[env.{name}]: {key} must be a {'list of view names' if key == 'views' else want.__name__}")
    return m


def _untar(stream, prefix: str, files: dict[str, bytes]):
    """Add a tar stream's regular files (and directories, as empty entries) to `files`, held in
    memory and never unpacked: a symlink an agent left in its scratch directory is never followed
    on the host."""
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            path = prefix + member.name.strip("/")
            if member.isdir():
                files[path] = b""
            if not member.isfile():
                continue
            if sum(map(len, files.values())) + member.size > CAP:
                raise ValueError(f"logs exceed {CAP // 2**20} MB")
            files[path] = tar.extractfile(member).read()


def _docker(env: dict, *args: str) -> subprocess.Popen:
    return subprocess.Popen([*docker_host._base_cmd(env["host"] or "localhost"), *args],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def extract(env: dict) -> dict[str, bytes]:
    """A running env's log files as {in-container path: bytes}, from the one fixed exec."""
    proc, files = _docker(env, "exec", env["name"], "python3", "-c", EXTRACTOR), {}
    try:
        _untar(proc.stdout, "/", files)
    finally:
        proc.kill()
        proc.wait()
    return files


def extract_stopped(env: dict) -> dict[str, bytes]:
    """The same for a stopped container, where nothing can run: `docker cp` of the same places,
    filtered to the same patterns on the host."""
    files = {}
    def cp(path):
        proc = _docker(env, "cp", f"{env['name']}:{path}", "-")
        try:
            _untar(proc.stdout, path.rsplit("/", 1)[0] + "/", files)
        except tarfile.ReadError:        # the path does not exist in this container
            pass
        finally:
            proc.kill()
            proc.wait()
    cp("/world/world.json")
    declared = [v.patterns[0] for v in logwatch.declared_views(files.get("/world/world.json", b"").decode("utf-8", "replace"))]
    for path in ["/data/gateway", "/agents", *(p for p in declared if p.startswith("/") and "*" not in p)]:
        cp(path)
    keep = [*LOG_PATTERNS, "/world/world.json", "/agents/*", *declared]       # glob semantics: * does not cross a /
    return {p: d for p, d in files.items() if any(p.count("/") == pat.count("/") and fnmatch(p, pat) for pat in keep)}


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


def _views(opts: dict, files: dict[str, bytes], rid: str) -> list[tuple[logwatch.View, str | None]]:
    """The (view, agent id) pairs this run publishes. `views` names match the world and scenario
    views exactly; an unknown name is reported and skipped."""
    aids = sorted({p.split("/")[2] for p in files if p.startswith("/agents/")} - {"lost+found"})
    nodes = logwatch.tree(logwatch.declared_views(files.get("/world/world.json", b"").decode("utf-8", "replace")), aids)
    world = {v.name: v for v, kids in nodes if not kids}
    wanted = opts["views"] if opts["views"] is not None else [n for n in world if n != "raw"]
    for name in wanted:
        if name not in world:
            console.print(f"[yellow]{rid}: no view named {name!r}, skipped[/yellow] (views: {', '.join(world)})")
    out = [(world[n], None) for n in wanted if n in world]
    for v, kids in nodes:
        if kids and opts["agents"]:
            if opts["thoughts"]:         # the whole session and its facets
                out += [(x, v.name) for x in (v, *kids)]
            else:                        # only what the agent said: no reasoning, tool traffic or scratchpad
                out.append((logwatch.View(v.name, v.patterns, logwatch.parse_session("says")), v.name))
    return out


def _chain(snap: dict | None, snaps: dict[str, dict]) -> list[dict]:
    """A snapshot and its ancestors, nearest first."""
    chain, seen = [], set()
    while snap and snap["snap_id"] not in seen:
        seen.add(snap["snap_id"])
        chain.append(snap)
        snap = snaps.get(snap.get("parent_snap_id") or "")
    return chain


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


def _fetch(env: dict, opts: dict, old_run: dict | None):
    """The docker half of a run, safe to run in a worker thread: (inspect, files, why-not).
    A stopped container never changes, so one already published with the same settings is not
    read again (files None, why None); a running one whose logs hash the same as last time
    comes back as that hash instead of its files."""
    seen = _inspect(env)
    if not seen:
        return None, None, "its container no longer exists"
    if opts["container"] and not seen[0].startswith(opts["container"]):
        return seen, None, f"container {seen[0][:12]} is not the pinned {opts['container']}"
    if not seen[1] and old_run and not old_run["stale"] and (old_run["container"], old_run["status"], old_run.get("options")) == (seen[0][:12], "stopped", opts):
        return seen, None, None
    try:
        files = (extract if seen[1] else extract_stopped)(env)
    except (tarfile.TarError, ValueError, OSError) as e:
        return seen, None, f"its logs could not be read ({e})"
    digest = hashlib.sha256(repr(sorted(files.items())).encode()).hexdigest()
    if old_run and not old_run["stale"] and (old_run["container"], old_run.get("digest"), old_run.get("options")) == (seen[0][:12], digest, opts):
        return seen, digest, None        # nothing in the logs changed since the last build: its views are reused
    return seen, files | {"": digest.encode()}, None


def _reader(rid: str, name: str, data: bytes, note: str) -> str:
    """A static page for one results file, from the app's own results reader: every byte of the
    file is treated as untrusted there (escaped; Markdown with raw HTML off, no images fetched)."""
    body, sections, remark = result_view.render(name, data.decode("utf-8", "replace"))
    e, up = html.escape, "../../../"
    jump = ("<details><summary>Jump to a section</summary><p>" + " · ".join(f'<a class="text-link" href="#{e(a)}">{e(t)}</a>' for a, t in sections)
            + "</p></details>") if sections else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow"><title>{e(name)} · {e(rid)}</title><link rel="icon" href="data:,">'
            f'<link rel="stylesheet" href="{up}web.css"><link rel="stylesheet" href="{up}mirror.css"></head><body data-reader="1">'
            f'<div class="workspace mirror"><main id="content"><div class="breadcrumbs"><a href="{up}run.html?id={e(rid)}#results">← {e(rid)}</a><strong>{e(name)}</strong></div>'
            f'<div class="page-heading"><div><div class="eyebrow">RESULT FILE</div><h1>{e(name)}</h1><p class="muted">{e(result_view.size_label(len(data)))} · {e(note)}</p></div>'
            f'<a class="button secondary" href="{e(name)}" download>Download file</a></div>{jump}'
            + (f'<p class="notice">{e(remark)}</p>' if remark else "")
            + f'<div class="result-reader">{body}</div></main></div></body></html>')


def _results(env: dict, opts: dict, dest: Path, old: Path | None) -> list[dict]:
    """Copy the env's generated results bundle (every file re-checked against its sha256, as
    results.download does) with a reader page per text file. An unchanged bundle is carried over
    from the previous build instead of being rendered again."""
    if not opts["results"]:
        return []
    try:
        with results.locked():
            bundle = results.bundle_files(env["name"])
    except ValueError:
        return []
    secret = (env.get("openrouter_key") or "").encode()
    if old and (old / "results/manifest.json").is_file() and (old / "results/manifest.json").read_bytes() == bundle["manifest.json"]:
        shutil.copytree(old / "results", dest / "results", copy_function=os.link)
    else:
        m = json.loads(bundle["manifest.json"])
        note = f"captured {str(m.get('captured_at', ''))[:19].replace('T', ' ')} UTC" + ("" if m.get("status") == "complete" else " · generated before the run completed")
        for name, data in bundle.items():
            data = data.replace(secret, b"[redacted]") if secret and name != "manifest.json" else data
            _write(dest / "results" / name, data)
            if name.endswith(TEXT) and len(data) <= result_view.FULL_VIEW_LIMIT:
                _write(dest / "results" / f"{name}.html", _reader(env["name"], name, data, note))
    return [{"name": n, "size": (dest / "results" / n).stat().st_size, "file": f"results/{n}", "page": (dest / "results" / f"{n}.html").is_file()}
            for n in bundle]


def _publish_run(env: dict, opts: dict, fetched, dest: Path, old: Path | None, old_run: dict | None, snaps: dict, timing: dict, now: str) -> dict | None:
    """Write runs/<env>/ into the build and return its run.json, or None when there is nothing to show."""
    rid, (seen, files, why) = env["name"], fetched
    if not isinstance(files, dict):      # reuse the last published views: unchanged, final (a stopped run), or stale (why)
        if why:
            console.print(f"[yellow]{rid}: {why}[/yellow]" + ("; keeping the last published copy" if old_run else "; nothing to publish"))
        if not old_run:
            return None
        if (old / "views").is_dir():
            shutil.copytree(old / "views", dest / "views", copy_function=os.link)   # builds are immutable: link, don't copy
        facts = old_run | ({"stale": True, "coverage": f"Not refreshed: {why}. Showing the copy from {old_run['as_of']}."} if why else {})
        if files:                        # unchanged logs, but the facts around them move
            ran = timing.get("closed", 0.0) + ((datetime.now(timezone.utc) - timing["open_since"]).total_seconds() if timing.get("open_since") else 0)
            facts |= {"as_of": now, "started": seen[2], "runtime_seconds": int(ran)}
        elif seen and not seen[1]:
            facts["status"] = "stopped"
    else:
        digest = files.pop("").decode()
        secret, used, views = env.get("openrouter_key") or "", set(), []
        for view, agent in _views(opts, files, rid):
            evs, slug = _events(files, view, secret), _slug(view.name, used)
            _write(dest / "views" / f"{slug}.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs))
            _write(dest / "views" / f"{slug}.txt", "".join(          # logwatch.render() minus colour: clock time, who, text
                " ".join(x for x in ((_iso(logwatch._epoch(e["ts"])) or "")[11:19], e["who"], e["text"]) if x) + "\n" for e in evs))
            stamps = [t for t in (logwatch._epoch(e["ts"]) for e in evs) if t is not None]
            views.append({"name": view.name, "file": slug, "events": len(evs), "last_ts": _iso(max(stamps, default=None)), "agent": agent})
        chain = _chain(snaps.get(env["snap_id"]), snaps)
        snap, scen = (chain[0] if chain else {}), (results.scenario(chain[-1]) if chain else "")
        try:
            world = json.loads(files.get("/world/world.json", b"{}"))
        except ValueError:
            world = {}
        roster = snap.get("roster") or [{"id": a} for a in snap.get("agents") or []]
        ran = timing.get("closed", 0.0) + ((datetime.now(timezone.utc) - timing["open_since"]).total_seconds() if seen[1] and timing.get("open_since") else 0)
        spend = None
        if opts["budget"]:
            try:
                spend = (budget.usage(env) or (None,))[0]
            except Exception as e:                               # the mirror is still worth publishing without it
                console.print(f"[dim]{rid}: budget lookup failed ({e})[/dim]")
        facts = {
            "id": rid, "env": rid, "title": opts["title"], "blurb": opts["blurb"], "options": opts, "digest": digest,
            "scenario": scen, "container": seen[0][:12], "as_of": now, "stale": False,
            "status": "stopped" if not seen[1] else "active" if "/mirror/gateway" in files and any(fnmatch(p, LOG_PATTERNS[1]) for p in files) else "dormant",
            "started": seen[2] if seen[1] else None, "created": env.get("created_at"), "runtime_seconds": int(ran),
            "coverage": "Complete as of the time above." if seen[1] else "This environment is stopped; this is its final state.",
            "budget_used": spend, "budget_usd": float(env.get("budget_usd") or 0),
            "lineage": [{"kind": "scenario", "ref": scen, "id": None, "message": ""},
                        *({"kind": "root" if versioning.is_world_root(s["version"]) else "snapshot", "ref": f"{s['scenario']}:{s['version']}",
                           "id": s["snap_id"], "message": s.get("creation_message") or ""} for s in reversed(chain)),
                        {"kind": "env", "ref": rid, "id": None, "message": ""}],
            "roster": [{"id": r["id"], "role": r.get("role") or "", "persona": r.get("persona") or "",
                        "model": (world.get("models") or {}).get(r["id"]) or r.get("model") or world.get("model") or snap.get("model") or ""} for r in roster],
            "views": views,
        }
    facts["results"] = _results(env, opts, dest, old)
    linked = lambda d: all(f.stat().st_nlink > 1 for f in (dest / d).rglob("*") if f.is_file())
    if old_run and (old / "all.zip").is_file() and linked("views") and linked("results") and facts["results"] == old_run.get("results"):
        os.link(old / "all.zip", dest / "all.zip")               # nothing in it changed
    else:
        with zipfile.ZipFile(dest / "all.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(dest.rglob("*")):
                if f.is_file() and f.suffix in TEXT and (f.parent.name == "results" or f.suffix == ".txt"):
                    z.write(f, f"{rid}/{f.relative_to(dest)}")
    _write(dest / "run.json", json.dumps(facts))
    return facts


def _row(f: dict) -> dict:
    """A run's line on the board (site.json), from its run.json."""
    models = {}
    for r in f["roster"]:
        models[r["model"]] = models.get(r["model"], 0) + 1
    world = [v for v in f["views"] if not v["agent"]] or f["views"]
    return {k: f[k] for k in ("id", "env", "title", "blurb", "scenario", "status", "started", "created", "runtime_seconds",
                              "budget_used", "budget_usd", "as_of", "stale")} | {
        "agents": len(f["roster"]), "models": models, "results": len(f["results"]),
        "events": max((v["events"] for v in world), default=0),
        "last_event_ts": max((v["last_ts"] for v in f["views"] if v["last_ts"]), default=None),
        "views": [v["name"] for v in f["views"] if not v["agent"]],
        "agent_views": len({v["agent"] for v in f["views"] if v["agent"]}),
        "snap": next((l["id"] for l in reversed(f["lineage"]) if l["id"]), None)}


def _scenario(name: str) -> dict | None:
    """What the demo's scenario page shows: description, world briefing, role prompts; plus its README."""
    try:
        s = registry.load_scen(name)
    except registry.RegistryError:
        return None
    text = lambda p: p.read_text(errors="replace") if p.is_file() else ""
    roles = sorted(s["roles_dir"].glob("*.md")) if s.get("roles_dir") else []
    return {"name": name, "description": s.get("description") or "", "active": bool(s.get("active")), "world": text(s["dir"] / "world.md"),
            "readme": text(s["dir"] / "README.md"), "roles": [{"name": p.stem, "text": text(p)} for p in roles],
            "agents": f"{s.get('min_agents', '?')}–{s.get('max_agents', '?')}", "runtime": s.get("runtime") or "",
            "dispatcher": bool(s.get("has_dispatch")), "github": GITHUB + name}


def _library(out: Path, snaps: dict[str, dict], runs: list[dict]) -> tuple[list[dict], list[dict]]:
    """worlds.json (every world root and snapshot, as the demo's library shows them) and a page of
    text per scenario. Returns the two summaries site.json carries."""
    envs_on = {}
    for f in runs:
        envs_on.setdefault(next((l["id"] for l in reversed(f["lineage"]) if l["id"]), None), []).append(f["id"])
    listed = []
    for s in sorted(snaps.values(), key=lambda s: s.get("created_at") or "", reverse=True):
        if not SNAP_ID.match(s["snap_id"]):
            continue
        chain = _chain(s, snaps)
        notes = [{"ts": str(n.get("ts") or ""), "text": str(n.get("text") or "")} for n in s.get("notes") or [] if isinstance(n, dict)]
        listed.append({
            "id": s["snap_id"], "ref": f"{s['scenario']}:{s['version']}", "world": s["scenario"], "version": s["version"],
            "root": versioning.is_world_root(s["version"]), "root_id": chain[-1]["snap_id"], "parent": s.get("parent_snap_id"),
            "scenario": results.scenario(chain[-1]), "created": s.get("created_at"), "message": s.get("creation_message") or "",
            "taken_from": s.get("env_name") or "", "runtime": s.get("runtime") or "", "model": s.get("model") or "",
            "flags": s.get("feature_flags") or {}, "tag": s.get("ghcr_tag") or "",
            "roster": [{"id": r["id"], "role": r.get("role") or "", "persona": r.get("persona") or "", "model": r.get("model") or ""}
                       for r in s.get("roster") or [{"id": a} for a in s.get("agents") or []]],
            "notes": notes, "files": {str(k): str(v)[:200_000] for k, v in (s.get("files") or {}).items()},
            "envs": envs_on.get(s["snap_id"], [])})
    _write(out / "worlds.json", json.dumps({"snaps": listed}))
    names = sorted({s["name"] for s in registry.list_scens()} | {x["scenario"] for x in [*runs, *listed] if SCEN_NAME.match(x["scenario"] or "")})
    pages = [s for name in names if (s := _scenario(name))]
    for s in pages:
        _write(out / "scenarios" / f"{s['name']}.json", json.dumps(s))
    worlds = [{k: s[k] for k in ("id", "ref", "scenario", "created", "message", "model")}
              | {"agents": len(s["roster"]), "snapshots": sum(x["root_id"] == s["id"] for x in listed) - 1,
                 "envs": sum(len(x["envs"]) for x in listed if x["root_id"] == s["id"])} for s in listed if s["root"]]
    scens = [{"name": s["name"], "description": s["description"], "active": s["active"], "agents": s["agents"], "roles": len(s["roles"]),
              "runs": [f["id"] for f in runs if f["scenario"] == s["name"]], "worlds": sum(w["scenario"] == s["name"] for w in worlds)} for s in pages]
    return worlds, scens


def cmd_publish(manifest: str | None = None):
    m = load_manifest(Path(manifest) if manifest else MANIFEST)
    os.umask(0o022)                                              # everything here is public: 0755 / 0644
    now = datetime.now(timezone.utc)
    current = WEBROOT / "current"
    prev = current.resolve() if current.exists() else None
    out = WEBROOT / "builds" / now.strftime("%Y%m%dT%H%M%S.%fZ")
    out.mkdir(parents=True)
    try:
        envs = [e for e in db.list_envs() if e["name"] not in m.get("exclude", []) and ENV_NAME.match(e["name"])]
        snaps = {s["snap_id"]: s for s in db.list_snaps()}
        timing = audit.env_runtime_intervals(since_by_name={e["name"]: e["created_at"] for e in envs})
        for name in [*m.get("exclude", []), *m.get("env", {})]:
            if not db.get_env(name):
                console.print(f"[yellow]manifest names {name!r}, which is not an environment[/yellow]")
        plans = []
        for e in envs:
            opts = DEFAULTS | m.get("env", {}).get(e["name"], {})
            old = prev / "runs" / e["name"] if prev else None
            old_run = json.loads((old / "run.json").read_text()) if old and (old / "run.json").is_file() else None
            plans.append((e, opts, old, old_run))
        runs = []
        with ThreadPoolExecutor(WORKERS) as pool:                # WORKERS runs in memory at a time, written in manifest order
            for i in range(0, len(plans), WORKERS):
                batch = plans[i:i + WORKERS]
                for (e, opts, old, old_run), fetched in zip(batch, pool.map(lambda p: _fetch(p[0], p[1], p[3]), batch)):
                    if facts := _publish_run(e, opts, fetched, out / "runs" / e["name"], old, old_run, snaps, timing.get(e["name"]) or {},
                                             now.isoformat(timespec="seconds")):
                        runs.append(facts)
        worlds, scens = _library(out, snaps, runs)
        for asset in (*sorted((registry.REPO_ROOT / "mirror").iterdir()), registry.REPO_ROOT / "web.css"):
            shutil.copyfile(asset, out / asset.name)
        _write(out / "site.json", json.dumps({
            "generated_at": now.isoformat(timespec="seconds"), "title": m.get("title") or "Agent World Maker",
            "min_refresh_seconds": int(m.get("min_refresh_seconds", 20)), "runs": [_row(f) for f in runs],
            "worlds": worlds, "scenarios": scens, "snapshots": len(snaps)}))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)                   # `current` still points at the last good build
        raise
    swap = WEBROOT / ".current-new"
    swap.unlink(missing_ok=True)
    swap.symlink_to(Path("builds") / out.name)
    swap.replace(current)
    for old in sorted(p for p in (WEBROOT / "builds").iterdir() if p != out)[:-max(int(m.get("keep_builds", 3)) - 1, 0) or None]:
        shutil.rmtree(old, ignore_errors=True)
    console.print(f"[green]published[/green] {len(runs)} environment(s), {len(worlds)} world(s), {len(scens)} scenario(s) to {out}")


def cmd_show():
    site = WEBROOT / "current" / "site.json"
    if not site.is_file():
        console.print("[dim]nothing published yet. Try 'agentspace mirror publish'.[/dim]")
        return
    data = json.loads(site.read_text())
    console.print(f"build {(WEBROOT / 'current').resolve().name}, generated {data['generated_at']}: "
                  f"{len(data['runs'])} environments, {len(data.get('worlds', []))} worlds, {len(data.get('scenarios', []))} scenarios")
    for r in data["runs"]:
        console.print(f"  {r['id']:<28} {r['status']:<8} as of {r['as_of']}{'  STALE' if r['stale'] else ''}  {r['events']} events  {r['results']} result files")
