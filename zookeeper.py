#!/usr/bin/env python3
"""agentspace — control CLI for envs and snaps.

Thin entry point: loads secrets, sets up click groups, dispatches to module verbs.
The real work lives in agentspace/*.py.
"""

import os
import sys
from pathlib import Path

import click


# ---- secrets loading ----

SECRETS_PATH = Path(
    os.environ.get("AGENTSPACE_SECRETS", "/var/agentspace-ctl/secrets.env")
)


def _load_secrets():
    """Load KEY=VALUE pairs from secrets.env into os.environ (without overriding existing).

    Silent on missing or unreadable file — verbs that need a specific secret will raise
    their own informative errors when they reach for it.
    """
    if not SECRETS_PATH.is_file():
        return
    try:
        text = SECRETS_PATH.read_text()
    except (PermissionError, OSError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


_load_secrets()


# ---- top-level group ----

@click.group()
@click.version_option(prog_name="agentspace")
def cli():
    """Control CLI for agentspace envs and snaps."""


# ---- snap group ----

@cli.group()
def snap():
    """Manage snaps (frozen env images on ghcr.io)."""


@snap.command("list")
@click.option("--scenario", default=None, help="Filter to one scenario.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def snap_list(scenario, as_json):
    """List indexed snaps."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_list(scenario=scenario, as_json=as_json)


@snap.command("show")
@click.argument("snap_ref")
def snap_show(snap_ref):
    """Show full detail for one snap (scenario:version, snap_id prefix, or ghcr tag)."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_show(snap_ref)


@snap.command("tree")
@click.option("--scenario", default=None, help="Restrict to one scenario tree.")
def snap_tree(scenario):
    """Render the snap lineage tree."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_tree(scenario=scenario)


@snap.command("note")
@click.argument("snap_ref")
@click.argument("text")
def snap_note(snap_ref, text):
    """Append a note to a snap (local-only until `snap push`)."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_note(snap_ref, text)


@snap.command("take")
@click.argument("env_name")
@click.option("--message", "-m", required=True, help="One-line label baked into the snap.")
@click.option("--note", default=None, help="Initial entry for the notes array.")
@click.option("--version", default=None, help="Override auto-assigned version.")
def snap_take(env_name, message, note, version):
    """Snapshot a running env: docker commit + push to ghcr.io with OCI labels."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_take(env_name, message=message, note=note, version=version)


@snap.command("fork")
@click.argument("snap_ref")
@click.argument("new_env_name")
@click.option(
    "--soul",
    "souls",
    multiple=True,
    help="Inject a soul file: --soul <agentId>=<path>. Repeatable.",
)
@click.option("--model", default=None, help="Override model in openclaw.json before gateway start.")
@click.option("--budget", "budget_usd", type=float, default=None, help="Credit limit for the new OpenRouter key.")
@click.option("--host", "host", default="localhost", help="Host droplet (default: localhost).")
@click.option("--kick/--no-kick", default=None, help="Override default kick behavior.")
def snap_fork(snap_ref, new_env_name, souls, model, budget_usd, host, kick):
    """Pull a snap and start it as a new env."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_fork(
        snap_ref,
        new_env_name,
        souls=souls,
        model=model,
        budget_usd=budget_usd,
        host=host,
        kick=kick,
    )


@snap.command("pull")
@click.argument("ghcr_tag")
def snap_pull(ghcr_tag):
    """Pull a snap from ghcr.io and index it locally."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_pull(ghcr_tag)


@snap.command("push")
@click.argument("snap_ref")
def snap_push(snap_ref):
    """Push a snap's current metadata (notes etc.) to ghcr.io."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_push(snap_ref)


@snap.command("rebuild-index")
@click.option("--repo", default=None, help="ghcr.io repo (default from spec).")
def snap_rebuild_index(repo):
    """Rebuild SQLite cache from ghcr.io OCI labels."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_rebuild_index(repo=repo)


# ---- env group ----

@cli.group()
def env():
    """Manage envs (running Docker containers)."""


@env.command("list")
def env_list():
    """List envs."""
    from agentspace import env as env_mod
    env_mod.cmd_list()


@env.command("show")
@click.argument("name")
def env_show(name):
    """Show full detail for one env."""
    from agentspace import env as env_mod
    env_mod.cmd_show(name)


@env.command("start")
@click.argument("name")
def env_start(name):
    """Start a stopped env (re-runs flag→config translate from snap labels)."""
    from agentspace import env as env_mod
    env_mod.cmd_start(name)


@env.command("stop")
@click.argument("name")
def env_stop(name):
    """Stop a running env. Container filesystem is preserved."""
    from agentspace import env as env_mod
    env_mod.cmd_stop(name)


@env.command("kick")
@click.argument("name")
@click.option("--message", default=None, help="Override the per-scenario kick message.")
def env_kick(name, message):
    """Send the bootstrap message to every agent in the env."""
    from agentspace import env as env_mod
    env_mod.cmd_kick(name, message=message)


@env.command("kill")
@click.argument("name")
@click.option("--force", is_flag=True, help="Skip confirmation.")
def env_kill(name, force):
    """Stop and remove a env's container. Snap state on ghcr.io is unaffected."""
    from agentspace import env as env_mod
    env_mod.cmd_kill(name, force=force)


@env.command("logs")
@click.argument("name")
@click.option("--agent", default=None, help="Tail a specific agent's session log instead of the gateway.")
@click.option("-f", "--follow", is_flag=True)
def env_logs(name, agent, follow):
    """Tail gateway or agent session logs."""
    from agentspace import env as env_mod
    env_mod.cmd_logs(name, agent=agent, follow=follow)


@env.command("exec")
@click.argument("name")
@click.argument("cmd", nargs=-1, required=True)
def env_exec(name, cmd):
    """Run a command inside the env via `docker exec`."""
    from agentspace import env as env_mod
    env_mod.cmd_exec(name, list(cmd))


# ---- budget group ----

@cli.group()
def budget():
    """OpenRouter budget commands."""


@budget.command("show")
@click.argument("env_name", required=False)
def budget_show(env_name):
    """Show budget for one env (or all if none given)."""
    from agentspace import budget as budget_mod
    budget_mod.cmd_show(env_name)


@budget.command("topup")
@click.argument("env_name")
@click.argument("amount_usd", type=float)
def budget_topup(env_name, amount_usd):
    """Increase an env's OpenRouter credit limit."""
    from agentspace import budget as budget_mod
    budget_mod.cmd_topup(env_name, amount_usd)


# ---- entry ----

if __name__ == "__main__":
    cli()
