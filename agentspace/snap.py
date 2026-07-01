"""Snap verbs: list, show, tree, note, take, fork, pull, push, rebuild-index.

Snaps are the primary research primitive. OCI labels on the image are the canonical
metadata; SQLite is a local cache. Notes are local-only until `snap push` is run.
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from . import audit, db, docker_host, oci, openrouter, versioning
from .runtimes import openclaw

REPO_ROOT = Path(__file__).resolve().parent.parent  # /opt/agentspace-ctl
GHCR_REPO_DEFAULT = versioning.GHCR_REPO_DEFAULT

# Default OpenRouter credit limit (USD) for a forked env when none is given.
# Single source of truth — the menu/CLI reference this so prompts stay in sync.
DEFAULT_BUDGET_USD = 2.00

console = Console()


# ---- ref resolution ----

def resolve_snap_ref(ref: str) -> dict[str, Any]:
    """Accept scenario:version, snap_id prefix, or full ghcr tag."""
    parsed = versioning.parse_tag(ref)
    if parsed:
        scenario, version = parsed
        snap = db.get_snap_by_ref(scenario, version)
        if snap:
            return snap

    if ":" in ref and not ref.startswith("ghcr.io"):
        scenario, version = ref.split(":", 1)
        snap = db.get_snap_by_ref(scenario, version)
        if snap:
            return snap

    matches = db.get_snap_by_id_prefix(ref)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise click.ClickException(
            f"ambiguous snap_id prefix {ref!r}: {len(matches)} matches"
        )

    raise click.ClickException(
        f"no snap found matching {ref!r}. "
        f"Try 'agentspace snap list' or 'agentspace snap rebuild-index'."
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return iso


# ---- list ----

def cmd_list(scenario: str | None = None, as_json: bool = False):
    snaps = db.list_snaps(scenario=scenario)
    if as_json:
        click.echo(json.dumps(snaps, indent=2, default=str))
        return

    if not snaps:
        console.print("[dim]no snaps indexed. Try 'agentspace snap rebuild-index'.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    for col in ("SCENARIO", "VERSION", "RUNTIME", "MODEL", "PARENT", "CREATED", "NOTES"):
        table.add_column(col)

    for s in snaps:
        notes_count = len(s.get("notes") or [])
        notes_cell = "—" if not notes_count else f"{notes_count}"
        if s.get("notes_dirty"):
            notes_cell += "*"
        table.add_row(
            s.get("scenario") or "",
            s.get("version") or "",
            s.get("runtime") or "",
            (s.get("model") or "").rsplit("/", 1)[-1],
            s.get("parent_version") or "—",
            _fmt_dt(s.get("created_at")),
            notes_cell,
        )

    console.print(table)
    _print_dirty_warning()


def _print_dirty_warning():
    dirty = db.dirty_snaps()
    if not dirty:
        return
    refs = ", ".join(f"{s['scenario']}:{s['version']}" for s in dirty)
    console.print(
        f"\n[yellow]⚠  {len(dirty)} snap(s) have unpushed notes: {refs}[/yellow]"
    )
    console.print(
        "[yellow]   Run 'agentspace snap push <snap_ref>' to sync to ghcr.io.[/yellow]"
    )


# ---- show ----

def cmd_show(ref: str):
    snap = resolve_snap_ref(ref)
    title = f"{snap['scenario']}:{snap['version']}"

    if snap.get("notes_dirty"):
        console.print(
            Panel(
                f"[bold yellow]⚠  UNPUSHED NOTES[/bold yellow] — run "
                f"[bold]agentspace snap push {title}[/bold] to sync to ghcr.io",
                style="yellow",
            )
        )

    children = db.get_snap_children(snap["snap_id"])
    children_str = (
        ", ".join(f"{c['scenario']}:{c['version']}" for c in children) or "—"
    )
    parent_str = (
        f"{snap['scenario']}:{snap['parent_version']}" if snap.get("parent_version") else "—"
    )

    flags = snap.get("feature_flags") or {}
    flags_str = " ".join(f"{k}={v}" for k, v in flags.items()) or "—"

    souls = snap.get("soul_files") or {}
    souls_str = (
        "\n  Souls:     " + "\n             ".join(f"{a} → {p}" for a, p in souls.items())
        if souls else ""
    )

    body = (
        f"  Snap ID:   {snap['snap_id']}\n"
        f"  Tag:       {snap['ghcr_tag']}\n"
        f"  Created:   {snap.get('created_at') or '—'}  (env: {snap.get('env_name') or '—'})\n"
        f"  Parent:    {parent_str}\n"
        f"  Children:  {children_str}\n\n"
        f"  \"{snap.get('creation_message') or '—'}\"\n\n"
        f"  Runtime:   {snap.get('runtime') or '—'} {snap.get('runtime_version') or ''}\n"
        f"  Model:     {snap.get('model') or '—'}\n"
        f"  Agents:    {', '.join(snap.get('agents') or [])}{souls_str}\n"
        f"  Flags:     {flags_str}\n\n"
        f"  Budget at snap:  ${float(snap.get('budget_usd') or 0):.2f} limit "
        f"/ ${float(snap.get('budget_used') or 0):.2f} used"
    )
    console.print(Panel(body, title=title, expand=False))

    notes = snap.get("notes") or []
    if notes:
        marker = " ⚠ local only" if snap.get("notes_dirty") else ""
        lines = []
        for n in notes:
            ts = _fmt_dt(n.get("ts"))
            lines.append(f"  {ts} — {n.get('text', '')}")
        console.print(Panel("\n".join(lines), title=f"NOTES{marker}", expand=False))


# ---- tree ----

def cmd_tree(scenario: str | None = None):
    snaps = db.list_snaps(scenario=scenario)
    if not snaps:
        console.print("[dim]no snaps indexed.[/dim]")
        return

    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for s in snaps:
        by_scenario.setdefault(s["scenario"], []).append(s)

    for scen, items in by_scenario.items():
        root = Tree(f"[bold]{scen}[/bold]")
        items_sorted = sorted(items, key=lambda s: versioning._version_key(s["version"]))
        nodes: dict[str, Tree] = {}
        for s in items_sorted:
            label = _tree_label(s)
            parent_v = s.get("parent_version")
            if parent_v and parent_v in nodes:
                node = nodes[parent_v].add(label)
            else:
                node = root.add(label)
            nodes[s["version"]] = node
        console.print(root)

    _print_dirty_warning()


def _tree_label(snap: dict[str, Any]) -> str:
    version = snap["version"]
    created = _fmt_dt(snap.get("created_at"))
    msg = snap.get("creation_message") or ""
    notes = snap.get("notes") or []
    marker = ""
    if notes:
        marker = "  ★"
        if snap.get("notes_dirty"):
            marker += "⚠"
        marker += f" {len(notes)}"
    msg_str = f'  "{msg}"' if msg else ""
    return f"[bold]{version}[/bold]  [{created}]{msg_str}{marker}"


# ---- note ----

def cmd_note(ref: str, text: str):
    snap = resolve_snap_ref(ref)
    notes = snap.get("notes") or []
    notes.append({"ts": _now(), "text": text})
    db.update_snap_notes(snap["snap_id"], notes, dirty=True)
    audit.log("snap.note", f"{snap['scenario']}:{snap['version']}", args={"text": text})

    console.print("[green]Note saved locally.[/green]")
    console.print(
        f"[yellow]⚠  Not yet on ghcr.io. Run "
        f"'agentspace snap push {snap['scenario']}:{snap['version']}' to sync.[/yellow]"
    )


# ---- take ----

def cmd_take(
    env_name: str,
    message: str,
    note: str | None = None,
    version: str | None = None,
):
    """Snapshot a running env: docker commit + push to ghcr.io with OCI labels."""
    env = db.get_env(env_name)
    if env is None:
        raise click.ClickException(f"env {env_name!r} not found. Try 'agentspace env list'.")

    host = env["host"] or "localhost"
    container = env["container_id"] or env_name

    parent_snap = db.get_snap_by_id(env["snap_id"])
    if parent_snap is None:
        raise click.ClickException(
            f"env {env_name!r} references snap_id {env['snap_id']!r} not in index"
        )

    scenario = parent_snap["scenario"]
    parent_version = parent_snap["version"]
    if version is None:
        version = versioning.next_child_version(scenario, parent_version)

    if db.get_snap_by_ref(scenario, version) is not None:
        raise click.ClickException(f"snap {scenario}:{version} already exists")

    snap_id = str(uuid.uuid4())
    ghcr_tag = versioning.ghcr_tag(scenario, version)
    now = _now()

    budget_info: dict[str, Any] = {}
    if env.get("openrouter_key"):
        try:
            info = openrouter.get_key_info(env["openrouter_key"])
            data = info.get("data") or info
            budget_info = {
                "budget_usd": float(data.get("limit") or env.get("budget_usd") or 0),
                "budget_used": float(data.get("usage") or 0),
            }
        except Exception as e:
            console.print(f"[dim]budget query failed: {e} — using last-known values[/dim]")
            budget_info = {
                "budget_usd": float(env.get("budget_usd") or 0),
                "budget_used": 0.0,
            }

    notes_arr: list[dict[str, Any]] = []
    if note:
        notes_arr.append({"ts": now, "text": note})

    snap: dict[str, Any] = {
        "snap_id": snap_id,
        "scenario": scenario,
        "version": version,
        "parent_snap_id": parent_snap["snap_id"],
        "parent_version": parent_version,
        "ghcr_tag": ghcr_tag,
        "created_at": now,
        "env_name": env_name,
        "creation_message": message,
        "runtime": parent_snap.get("runtime") or "openclaw",
        "runtime_version": parent_snap.get("runtime_version"),
        "model": parent_snap.get("model"),
        "agents": parent_snap.get("agents") or [],
        "soul_files": parent_snap.get("soul_files") or {},
        "feature_flags": parent_snap.get("feature_flags") or {},
        "budget_usd": budget_info.get("budget_usd"),
        "budget_used": budget_info.get("budget_used"),
        "agentspace_ver": _agentspace_version(),
        "notes": notes_arr,
    }

    # Sandboxed envs keep workspaces on the host; tar them INTO the container
    # pre-commit so the pushed image is the complete env. The env container
    # itself does the (root) tar through its own mount — start it briefly if
    # stopped (CMD is `sleep infinity`; the gateway is NOT started, no spend).
    if (parent_snap.get("feature_flags") or {}).get("fs_isolation") == "sandbox":
        console.print(f"[dim]capturing host workspace tree into container …[/dim]")
        # Use env_name (the container NAME): container_running matches names,
        # and env["container_id"] is the raw hex id.
        was_running = docker_host.container_running(host, env_name)
        if not was_running:
            docker_host.run(host, "start", env_name)
        openclaw.capture_env_fs(host, env_name, env_name)
        if not was_running:
            docker_host.run(host, "stop", env_name, check=False)

    labels = oci.make_labels(snap)
    console.print(f"[dim]committing {container} → {ghcr_tag}[/dim]")
    oci.commit_with_labels(host, container, ghcr_tag, labels)

    console.print(f"[dim]pushing to ghcr.io …[/dim]")
    try:
        docker_host.run(host, "push", ghcr_tag)
    except docker_host.DockerError as e:
        audit.log_error("snap.take", f"{scenario}:{version}", str(e), args={"env": env_name})
        raise click.ClickException(f"docker push failed: {e}")

    snap["indexed_at"] = now
    snap["notes_dirty"] = 0
    db.upsert_snap(snap)
    audit.log(
        "snap.take",
        f"{scenario}:{version}",
        args={"env": env_name, "message": message, "snap_id": snap_id},
    )

    console.print(f"[green]✓[/green] {scenario}:{version} created and pushed.")
    console.print(f"  Snap ID:  {snap_id}")
    console.print(f"  Tag:      {ghcr_tag}")


def _agentspace_version() -> str:
    from . import __version__
    return __version__


# ---- push (re-commit with updated labels, push to ghcr) ----

def cmd_push(ref: str):
    snap = resolve_snap_ref(ref)
    host = "localhost"  # snap push always runs against the local image cache
    ghcr_tag = snap["ghcr_tag"]

    if not snap.get("notes_dirty"):
        console.print(
            f"[dim]{snap['scenario']}:{snap['version']} has no local changes; nothing to push.[/dim]"
        )
        return

    # Pull to ensure local image exists, then re-commit a placeholder container with new labels.
    console.print(f"[dim]ensuring local image is present …[/dim]")
    docker_host.run(host, "pull", ghcr_tag, check=False)

    container = f"agentspace-push-{uuid.uuid4().hex[:8]}"
    docker_host.run(host, "create", "--name", container, ghcr_tag)
    try:
        snap["indexed_at"] = _now()
        labels = oci.make_labels(snap)
        oci.commit_with_labels(host, container, ghcr_tag, labels)
        docker_host.run(host, "push", ghcr_tag)
    finally:
        docker_host.run(host, "rm", container, check=False)

    db.set_notes_dirty(snap["snap_id"], False)
    audit.log("snap.push", f"{snap['scenario']}:{snap['version']}")
    console.print(f"[green]✓[/green] {snap['scenario']}:{snap['version']} pushed to ghcr.io.")


# ---- pull (import a snap created elsewhere) ----

def cmd_pull(ghcr_tag: str):
    host = "localhost"
    console.print(f"[dim]pulling {ghcr_tag} …[/dim]")
    docker_host.run(host, "pull", ghcr_tag)
    labels = oci.inspect_image_labels(host, ghcr_tag)
    snap = oci.parse_labels(labels)
    if not snap.get("snap_id"):
        raise click.ClickException(
            f"image {ghcr_tag} has no agentspace labels — not a recognized snap."
        )
    snap["indexed_at"] = _now()
    snap["notes_dirty"] = 0
    db.upsert_snap(snap)
    audit.log("snap.pull", f"{snap['scenario']}:{snap['version']}", args={"tag": ghcr_tag})
    console.print(f"[green]✓[/green] indexed {snap['scenario']}:{snap['version']}")


# ---- rebuild-index (read all ghcr labels, replace SQLite) ----

def cmd_rebuild_index(repo: str | None = None):
    repo = repo or GHCR_REPO_DEFAULT
    console.print(f"[dim]listing tags from ghcr.io/{repo} …[/dim]")
    try:
        tags = oci.list_registry_tags(repo)
    except Exception as e:
        raise click.ClickException(f"could not list tags: {e}")

    snap_tags = [t for t in tags if t.startswith(versioning.SNAP_TAG_PREFIX)]
    console.print(f"[dim]found {len(snap_tags)} snap tag(s); fetching labels …[/dim]")

    snaps: list[dict[str, Any]] = []
    for t in snap_tags:
        try:
            labels = oci.fetch_registry_labels(repo, t)
            snap = oci.parse_labels(labels)
            if not snap.get("snap_id"):
                console.print(f"[yellow]skipping {t} — no agentspace labels[/yellow]")
                continue
            snap["indexed_at"] = _now()
            snap["notes_dirty"] = 0
            snaps.append(snap)
        except Exception as e:
            console.print(f"[yellow]skipping {t} — {e}[/yellow]")

    db.reconcile_snaps(snaps)
    audit.log("snap.rebuild_index", repo, args={"count": len(snaps)})
    console.print(f"[green]✓[/green] indexed {len(snaps)} snap(s).")


# ---- fork (create a new env from a snap) ----

def cmd_fork(
    snap_ref: str,
    new_env_name: str,
    souls: tuple[str, ...] = (),
    model: str | None = None,
    budget_usd: float | None = None,
    host: str = "localhost",
    kick: bool | None = None,
    existing_key: str | None = None,
):
    snap = resolve_snap_ref(snap_ref)
    ghcr_tag = snap["ghcr_tag"]

    if db.get_env(new_env_name) is not None:
        raise click.ClickException(
            f"env {new_env_name!r} already exists. Use a new name or 'env kill' first."
        )
    if docker_host.container_exists(host, new_env_name):
        raise click.ClickException(
            f"container named {new_env_name!r} already exists on {host}."
        )

    if budget_usd is None:
        budget_usd = DEFAULT_BUDGET_USD
        console.print(f"[dim]no budget given; using default ${budget_usd:.2f}[/dim]")

    # Default kick: on for world-snap forks, off otherwise.
    is_world = versioning.is_world_root(snap["version"])
    if kick is None:
        kick = is_world

    flags = snap.get("feature_flags") or {}
    agents = snap.get("agents") or []
    sandboxed = flags.get("fs_isolation") == "sandbox"
    env_root = openclaw.env_fs_root(new_env_name)

    if sandboxed and host != "localhost":
        raise click.ClickException(
            "fs_isolation=sandbox envs are localhost-only for now (host-side "
            "workspace dirs need mkdir/rmdir on the remote host)."
        )

    console.print(f"[dim]ensuring local image …[/dim]")
    docker_host.run(host, "pull", ghcr_tag, check=False)

    if sandboxed:
        # The sandbox image is local-only (not on any registry; OC won't pull it).
        result = docker_host.run(host, "image", "inspect", openclaw.SANDBOX_IMAGE, check=False)
        if result.returncode != 0:
            raise click.ClickException(
                f"sandbox image {openclaw.SANDBOX_IMAGE!r} not found on {host}. "
                f"Build it from OpenClaw's official sandbox Dockerfile first "
                f"(see docs/runtime_openclaw.md §4)."
            )
        # Host-side workspace tree, owned by the operator. Contents written by
        # sandboxes are root-owned; those are cleaned through the container.
        console.print(f"[dim]creating workspace tree {env_root} …[/dim]")
        try:
            for agent_id in agents:
                os.makedirs(f"{env_root}/{agent_id}/workspace", exist_ok=True)
                os.makedirs(f"{env_root}/{agent_id}/agent", exist_ok=True)
        except PermissionError:
            raise click.ClickException(
                f"cannot create {env_root}. One-time setup: "
                f"sudo install -d -o $USER {openclaw.WORKSPACE_ROOT}"
            )

    if existing_key:
        console.print(
            f"[yellow]using provided OpenRouter key (no mint, no per-env budget cap).[/yellow]"
        )
        inference_key = existing_key
    else:
        console.print(f"[dim]minting OpenRouter key (budget ${budget_usd:.2f}) …[/dim]")
        try:
            key_resp = openrouter.mint_key(new_env_name, budget_usd)
        except openrouter.OpenRouterError as e:
            raise click.ClickException(f"could not mint OpenRouter key: {e}")
        inference_key = (
            key_resp.get("key")
            or (key_resp.get("data") or {}).get("key")
            or key_resp.get("api_key")
        )
        if not inference_key:
            audit.log_error("snap.fork", new_env_name, "no key in response", args=key_resp)
            raise click.ClickException(
                f"OpenRouter response missing key field. Inspect audit log for details."
            )

    now = _now()
    container_started = False
    try:
        console.print(f"[dim]starting container {new_env_name} …[/dim]")
        run_args = [
            "run", "-d",
            "-e", f"OPENROUTER_API_KEY={inference_key}",
            "--name", new_env_name,
        ]
        if sandboxed:
            # DooD: gateway drives the HOST daemon; workspace tree mounted at
            # the IDENTICAL absolute path (host daemon resolves mount paths in
            # the host namespace — see docs/runtime_openclaw.md §4).
            run_args += [
                "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-v", f"{env_root}:{env_root}",
            ]
        run_args.append(ghcr_tag)
        docker_host.run(host, *run_args)
        container_started = True

        if sandboxed:
            console.print(f"[dim]rewriting workspace paths → {env_root} …[/dim]")
            openclaw.rewrite_workspace_paths(host, new_env_name, new_env_name)
            console.print(f"[dim]populating workspaces (seed or snapshot tar) …[/dim]")
            openclaw.restore_env_fs(host, new_env_name, new_env_name)

        # Inject souls.
        for soul_spec in souls:
            if "=" not in soul_spec:
                raise click.ClickException(
                    f"--soul must be agentId=path, got {soul_spec!r}"
                )
            agent_id, soul_path = soul_spec.split("=", 1)
            soul_file = Path(soul_path)
            if not soul_file.is_absolute():
                soul_file = REPO_ROOT / soul_file
            if not soul_file.is_file():
                raise click.ClickException(f"soul file not found: {soul_file}")
            # Sandboxed envs keep workspaces on the host tree; docker cp into
            # the container writes through the bind mount either way.
            if sandboxed:
                dest = f"{env_root}/{agent_id}/workspace/SOUL.md"
            else:
                dest = f"/data/openclaw/agents/{agent_id}/workspace/SOUL.md"
            console.print(f"[dim]injecting soul {agent_id} ← {soul_file}[/dim]")
            docker_host.run(host, "cp", str(soul_file), f"{new_env_name}:{dest}")

        if model:
            console.print(f"[dim]patching model = {model}[/dim]")
            openclaw.patch_model(host, new_env_name, model)

        if flags:
            console.print(f"[dim]translating feature flags → openclaw config …[/dim]")
            openclaw.translate_flags(host, new_env_name, flags, agents)

        console.print(f"[dim]starting gateway …[/dim]")
        openclaw.start_gateway(host, new_env_name)
        openclaw.wait_for_gateway(host, new_env_name)

        if kick:
            kick_text = openclaw.read_kick_message(host, new_env_name)
            console.print(f"[dim]kicking agents with message {kick_text!r} …[/dim]")
            for agent_id in agents:
                openclaw.kick_agent(host, new_env_name, agent_id, kick_text)

        container_id = docker_host.stdout(
            host, "inspect", "--format", "{{.Id}}", new_env_name
        ).strip()
        db.upsert_env({
            "name": new_env_name,
            "snap_id": snap["snap_id"],
            "container_id": container_id,
            "openrouter_key": inference_key,
            "budget_usd": budget_usd,
            "host": host,
            "status": "running",
            "created_at": now,
        })
        audit.log(
            "snap.fork",
            new_env_name,
            args={"from": f"{snap['scenario']}:{snap['version']}", "budget_usd": budget_usd,
                  "kick": kick, "host": host},
        )
        audit.log("env.start", new_env_name)  # first session start, for runtime tracking
        console.print(f"[green]✓[/green] env {new_env_name} is running (forked from "
                      f"{snap['scenario']}:{snap['version']}).")

        # Hand the operator everything needed to inspect / enter the new container.
        # On a remote host the docker CLI must be pointed at it over SSH.
        console.print(f"  Container:  {container_id[:12]}   image: {ghcr_tag}")
        console.print(f"  Host:       {host}   budget: ${budget_usd:.2f}")
        console.print(f"  Enter it:   [bold]{docker_host.enter_command(host, new_env_name)}[/bold]")
        console.print(f"  Gateway log: python3 zookeeper.py env logs {new_env_name} -f")
        if kick:
            console.print(f"  Agents:     kicked (running).")
        else:
            console.print(
                f"  Agents:     dormant — begin with "
                f"[bold]python3 zookeeper.py env kick {new_env_name}[/bold] "
                f"(menu: Envs → 'Wake agents')."
            )

    except Exception as e:
        # Partial-failure path: container may be up, key was minted. Log loudly and stop.
        if container_started:
            audit.log_error(
                "snap.fork",
                new_env_name,
                f"partial failure after container start: {e}",
                args={"orphan_container": new_env_name, "orphan_key_name": f"agentspace-{new_env_name}"},
            )
            console.print(
                f"[red]✗ partial failure:[/red] container {new_env_name} is running but bootstrap "
                f"did not complete. Inspect and either complete manually or run 'env kill'."
            )
        else:
            audit.log_error(
                "snap.fork",
                new_env_name,
                f"failed before container start: {e}",
                args={"orphan_key_name": f"agentspace-{new_env_name}"},
            )
            console.print(
                f"[red]✗ OpenRouter key 'agentspace-{new_env_name}' was minted but no container "
                f"was started. Disable it via the OpenRouter dashboard.[/red]"
            )
        raise
