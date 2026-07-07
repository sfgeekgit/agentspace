"""Env verbs: list, show, start, stop, kick, kill, logs, exec."""

import os
import re
import shutil
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import audit, db, docker_host, openrouter, runtimes

console = Console()


def _rt(env: dict):
    """Runtime module for an env (via its snap's `runtime` label)."""
    return runtimes.for_snap(db.get_snap_by_id(env["snap_id"]))


def runtime_name(name: str) -> str:
    """An env's runtime name ('pi', 'openclaw'), for menu-level dispatch."""
    return _rt(_require_env(name)).NAME


def _require_env(name: str) -> dict:
    env = db.get_env(name)
    if env is None:
        raise click.ClickException(
            f"env {name!r} not found. Try 'agentspace env list'."
        )
    return env


def _live_status(env: dict) -> str:
    """Best-effort live status check; falls back to last-known on unreachable host."""
    host = env["host"] or "localhost"
    name = env["name"]
    # The agent-state exec doubles as the is-it-running probe (it fails if the
    # container is down), so running envs need just one docker call, not two.
    try:
        return _rt(env).agent_state(host, name)
    except docker_host.DockerError:
        try:
            return "stopped" if docker_host.container_exists(host, name) else "missing"
        except docker_host.DockerError:
            return f"unreachable (last: {env.get('status') or '?'})"


# Status values where the container is UP (vs stopped/missing). Agent-level:
#   active  = container up, gateway running, at least one agent has been kicked
#   dormant = container up, but gateway down OR no agent kicked yet
# (The user need not distinguish never-kicked from slept — both read "dormant".)
_CONTAINER_UP = ("active", "dormant")


def _is_up(status: str) -> bool:
    return status in _CONTAINER_UP


def _enter_line(env: dict, status: str) -> str:
    """Pasteable 'enter the container' command, with a hint if it isn't running."""
    cmd = docker_host.enter_command(env["host"] or "localhost", env["name"])
    return cmd if _is_up(status) else f"{cmd}   (start the env first)"


_STARTED_AT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def _started_at(env: dict, status: str) -> str:
    """Local-time 'YYYY-MM-DD HH:MM' the env was last started, or '—'.

    Reads Docker's own State.StartedAt — no extra tracking needed. Only meaningful
    while running. Docker reports UTC (RFC3339Nano); we truncate the fractional
    seconds and render in local time.
    """
    if not _is_up(status):
        return "—"
    host = env["host"] or "localhost"
    try:
        raw = docker_host.inspect(host, env["name"], format="{{.State.StartedAt}}").strip()
    except docker_host.DockerError:
        return "—"
    if not raw or raw.startswith("0001-01-01"):  # docker's zero value = never started
        return "—"
    m = _STARTED_AT_RE.match(raw)
    if not m:
        return "—"
    dt = datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_duration(seconds: float | None) -> str:
    """Human runtime, two units max: '4d 23h', '3h 14m', '14m', '9s'. Under an
    hour shows a single unit (minutes, or seconds if under a minute). '—' for
    none/negative."""
    if not seconds or seconds < 0:
        return "—"
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _total_runtime(intervals: dict, name: str, status: str, now: datetime) -> str:
    """Total time an env has run, from the audit log: completed sessions plus the
    live one if running. '—' when we have no recorded runtime (see audit.py)."""
    info = intervals.get(name) or {}
    total = info.get("closed", 0.0)
    if _is_up(status) and info.get("open_since") is not None:
        total += (now - info["open_since"]).total_seconds()
    return _fmt_duration(total)


# ---- list ----

def cmd_list():
    envs = db.list_envs()
    if not envs:
        console.print("[dim]no envs. Try 'agentspace snap fork <snap_ref> <name>'.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    for col in ("NAME", "SNAP", "HOST", "STATUS", "STARTED", "RUNTIME", "USED", "LIMIT"):
        # STARTED ('YYYY-MM-DD HH:MM') has a space; keep it on one line.
        table.add_column(col, no_wrap=(col == "STARTED"))

    now = datetime.now(timezone.utc)
    intervals = audit.env_runtime_intervals(
        since_by_name={e["name"]: e["created_at"] for e in envs}
    )

    for e in envs:
        snap = db.get_snap_by_id(e["snap_id"])
        snap_str = f"{snap['scenario']}:{snap['version']}" if snap else e["snap_id"][:8]
        status = _live_status(e)
        started_str = _started_at(e, status)
        runtime_str = _total_runtime(intervals, e["name"], status, now)
        used_str = "—"
        limit_str = f"${float(e.get('budget_usd') or 0):.2f}"
        if e.get("openrouter_key"):
            try:
                info = openrouter.get_key_info(e["openrouter_key"])
                data = info.get("data") or info
                used_str = f"${float(data.get('usage') or 0):.2f}"
                if data.get("limit") is not None:
                    limit_str = f"${float(data.get('limit')):.2f}"
            except Exception:
                pass
        table.add_row(
            e["name"],
            snap_str,
            e["host"] or "localhost",
            status,
            started_str,
            runtime_str,
            used_str,
            limit_str,
        )
        # Persist live status back for next time.
        if status in ("active", "dormant", "stopped", "missing"):
            db.set_env_status(e["name"], status)
    console.print(table)


# ---- show ----

def cmd_show(name: str):
    env = _require_env(name)
    snap = db.get_snap_by_id(env["snap_id"])
    snap_str = f"{snap['scenario']}:{snap['version']}" if snap else env["snap_id"]
    status = _live_status(env)
    started_str = _started_at(env, status)
    now = datetime.now(timezone.utc)
    intervals = audit.env_runtime_intervals(since_by_name={name: env["created_at"]})
    runtime_str = _total_runtime(intervals, name, status, now)

    used_str = "—"
    limit_str = f"${float(env.get('budget_usd') or 0):.2f}"
    if env.get("openrouter_key"):
        try:
            info = openrouter.get_key_info(env["openrouter_key"])
            data = info.get("data") or info
            used_str = f"${float(data.get('usage') or 0):.2f}"
            if data.get("limit") is not None:
                limit_str = f"${float(data.get('limit')):.2f}"
        except Exception as e:
            used_str = f"query failed: {e}"

    body = (
        f"  Snap:         {snap_str}\n"
        f"  Host:         {env['host'] or 'localhost'}\n"
        f"  Container:    {env.get('container_id') or '—'}\n"
        f"  Status:       {status}\n"
        f"  Started:      {started_str}\n"
        f"  Runtime:      {runtime_str}\n"
        f"  Created:      {env.get('created_at') or '—'}\n"
        f"  Budget used:  {used_str} / {limit_str}\n"
        f"  Enter:        {_enter_line(env, status)}\n"
    )
    if snap:
        agents = snap.get("agents") or []
        flags = snap.get("feature_flags") or {}
        body += (
            f"\n  Agents:       {', '.join(agents) or '—'}\n"
            f"  Flags:        {' '.join(f'{k}={v}' for k, v in flags.items()) or '—'}\n"
            f"  Model:        {snap.get('model') or '—'}\n"
        )
    console.print(Panel(body, title=name, expand=False))


def cmd_enter(name: str):
    """Print the pasteable shell command to enter the env's container."""
    env = _require_env(name)
    console.print("  " + _enter_line(env, _live_status(env)))


# ---- start / stop ----

def cmd_start(name: str):
    env = _require_env(name)
    host = env["host"] or "localhost"
    snap = db.get_snap_by_id(env["snap_id"])
    if snap is None:
        raise click.ClickException(
            f"env {name!r} references unknown snap_id {env['snap_id']!r}"
        )

    rt = runtimes.for_snap(snap)
    console.print(f"[dim]docker start {name} …[/dim]")
    docker_host.run(host, "start", name)

    flags = snap.get("feature_flags") or {}
    agents = snap.get("agents") or []
    if flags:
        console.print(f"[dim]re-translating flags from snap labels …[/dim]")
        rt.translate_flags(host, name, flags, agents)

    console.print(f"[dim]starting gateway …[/dim]")
    rt.start_gateway(host, name)
    rt.wait_for_gateway(host, name)

    audit.log("env.start", name)
    console.print(f"[green]✓[/green] env {name} container started, gateway up. "
                  f"Wake agents with 'agentspace env kick {name}' if needed.")


def cmd_stop(name: str):
    env = _require_env(name)
    host = env["host"] or "localhost"

    rt = _rt(env)
    rt.stop_gm(host, name)  # stop-all stops the GM too (decision 13; no-op if none)
    console.print(f"[dim]stopping gateway …[/dim]")
    rt.stop_gateway(host, name)

    console.print(f"[dim]docker stop {name} …[/dim]")
    docker_host.run(host, "stop", name)

    db.set_env_status(name, "stopped")
    audit.log("env.stop", name)
    console.print(f"[green]✓[/green] env {name} stopped. Container filesystem preserved.")


# ---- kick ----

def cmd_kick(name: str, message: str | None = None):
    env = _require_env(name)
    host = env["host"] or "localhost"
    snap = db.get_snap_by_id(env["snap_id"])
    agents = (snap.get("agents") if snap else []) or []
    if not agents:
        raise click.ClickException(f"env {name!r} has no agents recorded on its snap.")

    # A dormant or slept env may have its gateway stopped; waking must start it
    # first. (start_gateway truncates the log, so wait_for_gateway is reliable.)
    rt = runtimes.for_snap(snap)
    if not rt.gateway_running(host, name):
        console.print("[dim]gateway not running; starting it …[/dim]")
        rt.start_gateway(host, name)
        rt.wait_for_gateway(host, name)

    # "Run the world": in a GM world, that means start (or resume) the GM — it is
    # the sole driver and wakes its own agents, so we do NOT also blast per-agent
    # wakes (that would race the GM's setup). Non-GM worlds wake agents directly.
    # (Per-agent debug pokes still go through env chat / the gateway wake op.)
    if rt.world_has_gm(host, name):
        if rt.gm_running(host, name):
            console.print(f"[green]✓[/green] env {name}: GM already running.")
        else:
            console.print("[dim]starting game master …[/dim]")
            rt.start_gm(host, name)
            console.print(f"[green]✓[/green] env {name} is active — GM driving the world.")
        audit.log("env.kick", name, args={"gm": True})
        db.set_env_status(name, "active")
        return

    text = message or rt.read_kick_message(host, name)
    what = f"message {text!r}" if text else "a bare wake (no message)"
    console.print(f"[dim]waking {len(agents)} agent(s) with {what} …[/dim]")
    for agent_id in agents:
        rt.kick_agent(host, name, agent_id, text)
    audit.log("env.kick", name, args={"agents": agents, "message": text})
    db.set_env_status(name, "active")
    console.print(f"[green]✓[/green] env {name} is active — agents woken.")


def cmd_sleep(name: str):
    """Anti-wake: stop ONLY the gateway, leaving the container running. Agents go
    dormant — no turns, no messaging, and (crucially) no heartbeat polling/spend —
    but the filesystem and sessions are untouched. Wake again with cmd_kick."""
    env = _require_env(name)
    host = env["host"] or "localhost"
    if not docker_host.container_running(host, name):
        raise click.ClickException(
            f"env {name!r} is not running (nothing to sleep). Use 'env start' first."
        )
    rt = _rt(env)
    rt.stop_gm(host, name)  # sleep-all sleeps the GM too (decision 13; no-op if none)
    console.print(f"[dim]stopping gateway (container stays up) …[/dim]")
    rt.stop_gateway(host, name)
    db.set_env_status(name, "dormant")
    audit.log("env.sleep", name)
    console.print(
        f"[green]✓[/green] env {name} is dormant — agents paused, container still "
        f"running. Wake with 'agentspace env kick {name}'."
    )


# ---- kill ----

def cmd_kill(name: str, force: bool = False):
    env = _require_env(name)
    if not force:
        click.confirm(
            f"This will stop and remove container {name!r}. State on ghcr.io is unaffected. "
            f"Continue?",
            abort=True,
        )

    host = env["host"] or "localhost"
    snap = db.get_snap_by_id(env["snap_id"])
    rt = runtimes.for_snap(snap)
    sandboxed = bool(snap) and (snap.get("feature_flags") or {}).get("fs_isolation") == "sandbox"

    if sandboxed:
        # 1. Sandbox siblings OUTLIVE the env container — remove them explicitly
        #    (matched by mounted workspace path, not by OC's generated names).
        for sbx in rt.find_sandbox_containers(host, name):
            console.print(f"[dim]removing sandbox container {sbx} …[/dim]")
            docker_host.run(host, "rm", "-f", sbx, check=False)
        # 2. Workspace contents are root-owned (written by sandboxes) — delete
        #    them as root through the env container's own mount, BEFORE the
        #    container goes away. Start it briefly if stopped (no gateway,
        #    no spend).
        env_root = rt.env_fs_root(name)
        if docker_host.container_exists(host, name):
            if not docker_host.container_running(host, name):
                docker_host.run(host, "start", name, check=False)
            console.print(f"[dim]clearing workspace tree {env_root} …[/dim]")
            rt.clear_env_fs(host, name, name)

    docker_host.run(host, "stop", name, check=False)
    docker_host.run(host, "rm", name, check=False)

    if sandboxed:
        # 3. The now-empty dirs are operator-owned (created at fork) — plain rmdir.
        shutil.rmtree(rt.env_fs_root(name), ignore_errors=True)
        if os.path.isdir(rt.env_fs_root(name)):
            console.print(
                f"[yellow]could not fully remove {rt.env_fs_root(name)} "
                f"(root-owned leftovers?). Remove manually, e.g. with sudo.[/yellow]"
            )

    try:
        openrouter.disable_key(name)
    except openrouter.OpenRouterError as e:
        console.print(f"[yellow]could not disable OpenRouter key: {e}[/yellow]")
    db.delete_env(name)
    audit.log("env.kill", name)
    console.print(f"[green]✓[/green] env {name} killed.")


# ---- logs ----

def env_agent_ids(name: str) -> list[str]:
    """Agent ids for an env, from its snap's `agents` label (no docker call)."""
    env = db.get_env(name)
    if env is None:
        return []
    snap = db.get_snap_by_id(env["snap_id"])
    return list((snap or {}).get("agents") or [])


def cmd_logs(
    name: str,
    agent: str | None = None,
    follow: bool = False,
    all_agents: bool = False,
    everything: bool = False,
):
    """Tail logs for an env.

    Source (most specific wins): everything (all agents + gateway) > all_agents
    (agents only) > agent=<id> (one agent) > gateway (default).
    """
    env = _require_env(name)
    host = env["host"] or "localhost"
    rt = _rt(env)

    def _source(follow_: bool):
        if everything or all_agents:
            return rt.tail_combined(
                host, name, env_agent_ids(name),
                include_gateway=everything, follow=follow_,
            )
        if agent:
            return rt.tail_agent_log(host, name, agent, follow=follow_)
        return rt.tail_gateway_log(host, name, follow=follow_)

    if follow:
        proc = _source(True)
        try:
            for line in proc.stdout:
                click.echo(line, nl=False)
        except KeyboardInterrupt:
            pass
        finally:
            proc.terminate()   # logwatch.RawTail reaps its streamer here
            # docker exec doesn't propagate the kill to the in-container `tail`;
            # reap it so idle followers don't pile up (tail is only ours here).
            docker_host.run(host, "exec", name, "pkill", "-x", "tail", check=False)
    else:
        click.echo(_source(False))


# ---- exec ----

def cmd_exec(name: str, cmd: list[str]):
    env = _require_env(name)
    host = env["host"] or "localhost"
    audit.log("env.exec", name, args={"cmd": cmd})
    # Pass through stdin/stdout/stderr by NOT capturing.
    result = docker_host.run(host, "exec", name, *cmd, capture=False, check=False)
    raise SystemExit(result.returncode)


# ---- PI-runtime operator surface: chat / post / roll-sessions ----

def _require_pi(env: dict):
    rt = _rt(env)
    if rt.NAME != "pi":
        raise click.ClickException(
            f"this command is PI-runtime only (env runs {rt.NAME!r})."
        )
    return rt


def cmd_post(name: str, message: str):
    """Post to the env's public board as the operator."""
    env = _require_env(name)
    rt = _require_pi(env)
    host = env["host"] or "localhost"
    rt.post_public(host, name, message)
    audit.log("env.post", name, args={"message": message})
    console.print("[green]✓[/green] posted to the public board.")


def cmd_roll_sessions(name: str, agent: str | None = None):
    """Archive session transcripts + frozen sysprompt so the next wake starts a
    fresh session with re-rendered files. World-event-driven, never wall-clock
    (docs/runtime_pi.md §4a)."""
    env = _require_env(name)
    rt = _require_pi(env)
    host = env["host"] or "localhost"
    targets = [agent] if agent else env_agent_ids(name)
    if not targets:
        raise click.ClickException(f"env {name!r} has no agents recorded on its snap.")
    rt.roll_sessions(host, name, targets)
    audit.log("env.roll_sessions", name, args={"agents": targets})
    console.print(f"[green]✓[/green] rolled sessions for {', '.join(targets)} — "
                  f"fresh session (and re-rendered files) at each agent's next wake.")


def cmd_watch(name: str, plain_view: str | None = None, follow: bool = True):
    """Live log watcher: Textual TUI (default) or --plain single-view stream.
    Views/parsers live in logwatch.py; the TUI shell in watch_tui.py."""
    env = _require_env(name)
    _require_pi(env)
    host = env["host"] or "localhost"
    if not docker_host.container_running(host, name):
        raise click.ClickException(f"env {name!r} is not running — 'env start {name}' first.")
    audit.log("env.watch", name, args={"view": plain_view or "tui"})
    from . import logwatch
    if plain_view:
        logwatch.cmd_watch_plain(host, name, plain_view, follow)
    else:
        from .watch_tui import WatchApp
        WatchApp(host, name).run()


def cmd_chat(name: str, agent: str):
    """Minimal operator REPL: each line is sent as an operator PM; the agent's
    reply is read from its session transcript once the wake ends. Ctrl-D/empty
    line to leave. Shares the agent's ONE session with everything else."""
    import time as _time
    env = _require_env(name)
    rt = _require_pi(env)
    host = env["host"] or "localhost"
    if agent not in env_agent_ids(name):
        raise click.ClickException(f"no agent {agent!r} in env {name!r}.")
    if not rt.gateway_running(host, name):
        console.print("[dim]gateway not running; starting it …[/dim]")
        rt.start_gateway(host, name)
        rt.wait_for_gateway(host, name)
    console.print(f"[dim]chatting with {agent} — empty line to quit. Each turn "
                  f"costs tokens; replies can take ~10-60s.[/dim]")
    while True:
        try:
            line = input(f"you → {agent}: ").strip()
        except EOFError:
            break
        if not line:
            break
        mark = rt.audit_line_count(host, name)
        rt.kick_agent(host, name, agent, line)
        deadline = _time.monotonic() + 300
        while _time.monotonic() < deadline:
            if rt.wake_ended_since(host, name, agent, mark):
                break
            _time.sleep(2)
        else:
            console.print("[yellow]no wake_end within 300s — see env logs.[/yellow]")
            continue
        reply = rt.last_assistant_text(host, name, agent)
        console.print(f"[bold]{agent}[/bold]: {reply or '(no reply text — norms allow silence)'}")
    audit.log("env.chat", name, args={"agent": agent})
