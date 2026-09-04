#!/usr/bin/env python3
"""agentspace — control CLI for envs and snaps.

Thin entry point: loads secrets, sets up click groups, dispatches to module verbs.
The real work lives in agentspace/*.py.

╔══════════════════════════════════════════════════════════════════════════════╗
║  DEVELOPER NOTE — DUAL-MODE REQUIREMENT                                      ║
║                                                                              ║
║  Every command in this file MUST be available in BOTH of these ways:        ║
║    1. As a click command with flags (for scripting / automation)             ║
║    2. In the interactive menu (for human operators)                          ║
║                                                                              ║
║  When you add a new click command:                                           ║
║    • Add it to the appropriate click group below (snap, env, budget, etc.)  ║
║    • Add a matching entry in the corresponding menu_<group>() function       ║
║      in the INTERACTIVE MENU section at the bottom of this file             ║
║                                                                              ║
║  When you add a whole new click group:                                       ║
║    • Add the group and its commands below as usual                           ║
║    • Add a new menu_<group>() function in the INTERACTIVE MENU section       ║
║    • Add the new group as a top-level choice in launch_menu()                ║
║                                                                              ║
║  Failing to update the menu means human operators lose access to your        ║
║  feature. Both modes must stay in sync.                                      ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
from pathlib import Path

import click

try:
    import questionary
except ImportError:
    questionary = None


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
# NOTE: When you add a snap subcommand here, add it to menu_snap() below too.

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
@click.option("--allow-key-leak", is_flag=True,
              help="Push even if the image scan finds an OpenRouter key.")
def snap_take(env_name, message, note, version, allow_key_leak):
    """Snapshot a running env: docker commit + push to ghcr.io with OCI labels."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_take(env_name, message=message, note=note, version=version,
                      allow_key_leak=allow_key_leak)


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
@click.option("--key", "existing_key", default=None,
              help="Use an existing OpenRouter inference key instead of minting a new one. "
                   "Skips per-env isolation; budget commands won't reflect a per-env limit.")
def snap_fork(snap_ref, new_env_name, souls, model, budget_usd, host, kick, existing_key):
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
        existing_key=existing_key,
    )


@snap.command("pull")
@click.argument("ghcr_tag")
def snap_pull(ghcr_tag):
    """Pull a snap from ghcr.io and index it locally."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_pull(ghcr_tag)


@snap.command("push")
@click.argument("snap_ref")
@click.option("--allow-key-leak", is_flag=True,
              help="Push even if the image scan finds an OpenRouter key.")
def snap_push(snap_ref, allow_key_leak):
    """Push a snap's current metadata (notes etc.) to ghcr.io."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_push(snap_ref, allow_key_leak=allow_key_leak)


@snap.command("rebuild-index")
@click.option("--repo", default=None, help="ghcr.io repo (default from spec).")
def snap_rebuild_index(repo):
    """Rebuild SQLite cache from ghcr.io OCI labels."""
    from agentspace import snap as snap_mod
    snap_mod.cmd_rebuild_index(repo=repo)


# ---- env group ----
# NOTE: When you add an env subcommand here, add it to menu_env() below too.

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
    """Wake every agent (starts the gateway first if it's stopped)."""
    from agentspace import env as env_mod
    env_mod.cmd_kick(name, message=message)


@env.command("sleep")
@click.argument("name")
def env_sleep(name):
    """Make an env dormant: stop the gateway only; the container keeps running."""
    from agentspace import env as env_mod
    env_mod.cmd_sleep(name)


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
@click.option("--all-agents", is_flag=True, help="Tail all agents' session logs combined (no gateway).")
@click.option("--all", "everything", is_flag=True, help="Tail all agents' logs AND the gateway, combined.")
@click.option("-f", "--follow", is_flag=True)
def env_logs(name, agent, all_agents, everything, follow):
    """Tail gateway, one agent, all agents, or everything (--all)."""
    from agentspace import env as env_mod
    env_mod.cmd_logs(name, agent=agent, follow=follow,
                     all_agents=all_agents, everything=everything)


@env.command("watch")
@click.argument("name")
@click.option("--plain", "plain_view", default=None, metavar="VIEW",
              help="Stream one view to stdout instead of the TUI "
                   "(a bad VIEW name lists what's available).")
@click.option("--no-follow", is_flag=True, help="With --plain: dump what exists and exit.")
def env_watch(name, plain_view, no_follow):
    """Live log watcher for a PI env — TUI with a sidebar of views (q to quit)."""
    from agentspace import env as env_mod
    env_mod.cmd_watch(name, plain_view=plain_view, follow=not no_follow)


@env.command("chat")
@click.argument("name")
@click.argument("agent")
def env_chat(name, agent):
    """Chat with one agent (PI runtime): operator PMs in, transcript replies out."""
    from agentspace import env as env_mod
    env_mod.cmd_chat(name, agent)


@env.command("post")
@click.argument("name")
@click.argument("message")
def env_post(name, message):
    """Post to the env's public board as the operator (PI runtime)."""
    from agentspace import env as env_mod
    env_mod.cmd_post(name, message)


@env.command("roll-sessions")
@click.argument("name")
@click.option("--agent", default=None, help="One agent (default: all).")
def env_roll_sessions(name, agent):
    """Archive session transcripts; next wake starts fresh with re-rendered
    files (PI runtime). World-event-driven — never automatic."""
    from agentspace import env as env_mod
    env_mod.cmd_roll_sessions(name, agent=agent)


@env.command("exec")
@click.argument("name")
@click.argument("cmd", nargs=-1, required=True)
def env_exec(name, cmd):
    """Run a command inside the env via `docker exec`."""
    from agentspace import env as env_mod
    env_mod.cmd_exec(name, list(cmd))


@env.command("enter")
@click.argument("name")
def env_enter(name):
    """Print the pasteable `docker exec` command to open a shell in the env."""
    from agentspace import env as env_mod
    env_mod.cmd_enter(name)


# ---- budget group ----
# NOTE: When you add a budget subcommand here, add it to menu_budget() below too.

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


# ---- scen group ----
# NOTE: When you add a scen subcommand here, add it to menu_scen() below too.

@cli.group()
def scen():
    """Scen authoring commands (environment images)."""


@scen.group("env")
def scen_env():
    """Produce a scen's pinned source_image (freeze a workshop container or
    build the env.Dockerfile recipe)."""


@scen_env.command("freeze")
@click.argument("scen_name")
@click.argument("container")
@click.option("--host", default="localhost", help="Docker host (default: localhost).")
@click.option("--allow-key-leak", is_flag=True,
              help="Publish even if the image scan finds an OpenRouter key.")
def scen_env_freeze(scen_name, container, host, allow_key_leak):
    """Freeze CONTAINER into SCEN_NAME's pinned environment image
    (scan, label, commit, push, digest into scenario.toml)."""
    from agentspace import scen as scen_mod
    scen_mod.cmd_freeze(scen_name, container, host=host, allow_key_leak=allow_key_leak)


@scen_env.command("build")
@click.argument("scen_name")
@click.option("--host", default="localhost", help="Docker host (default: localhost).")
@click.option("--allow-key-leak", is_flag=True,
              help="Publish even if the image scan finds an OpenRouter key.")
def scen_env_build(scen_name, host, allow_key_leak):
    """docker build SCEN_NAME's env.Dockerfile and publish it as the pinned
    environment image (same tail as freeze)."""
    from agentspace import scen as scen_mod
    scen_mod.cmd_build(scen_name, host=host, allow_key_leak=allow_key_leak)


@scen_env.command("shell")
@click.argument("scen_name")
def scen_env_shell(scen_name):
    """Open an interactive workshop container on SCEN_NAME's resolved
    environment image (kept after exit, ready to freeze)."""
    from agentspace import scen as scen_mod
    scen_mod.cmd_shell(scen_name)


# ================================================================================
# INTERACTIVE MENU
# ================================================================================
#
# These functions provide the menu that launches when zookeeper.py is run with
# no arguments. They call the same agentspace module functions as the click
# commands above — no duplicate logic, just a different way to collect inputs.
#
# DEVELOPER: Keep the menu in sync with the click commands above.
#   - New snap command?   → add to menu_snap()
#   - New env command?    → add to menu_env()
#   - New budget command? → add to menu_budget()
#   - New top-level group? → add menu_<group>() and add it to launch_menu()
#
# Navigation: arrow keys to move, Enter to select. Ctrl-C / Ctrl-D / Esc cancels
# the CURRENT action and returns to the menu (at a command list, it goes up one
# level). Cancelling NEVER advances into the action.
# ================================================================================


class _Cancelled(Exception):
    """Raised by `_ask` when the user cancels a prompt (Ctrl-C / Ctrl-D / Esc).

    Why an exception and not a None return: 'optional' prompts (blank = default)
    can't distinguish a blank entry from a cancel if cancel is just None — so a
    cancel used to fall through and run the action with defaults. Raising unwinds
    the whole flow to the nearest handler instead. Each menu loop catches it:
    at the command-list select it returns to the parent menu; during a
    multi-prompt action it aborts the action and redraws the command list.
    """


def _show_error(e):
    """Print an operational error from a menu action without crashing the menu."""
    msg = e.format_message() if isinstance(e, click.ClickException) else str(e)
    print(f"  Error: {msg}")


def _ask(prompt_fn):
    """Run a questionary prompt. Raises _Cancelled on Ctrl-C/Ctrl-D/Esc.

    (questionary itself returns None when a select/confirm is dismissed with Esc;
    we treat that as a cancel too, so Esc behaves like Ctrl-C everywhere.)
    """
    try:
        result = prompt_fn()
    except (KeyboardInterrupt, EOFError):
        print()
        raise _Cancelled()
    if result is None:
        raise _Cancelled()
    return result


def _pick_env(prompt="Env name:"):
    """Pick an existing env from a list. Returns the name, or None to abort
    (no envs, or the user backed out)."""
    from agentspace import db
    envs = db.list_envs()
    if not envs:
        print("  No envs yet. Fork a snap first.")
        return None
    name = _ask(lambda: questionary.select(
        prompt, choices=[e["name"] for e in envs] + [questionary.Separator(), "← Back"]
    ).ask())
    return None if name == "← Back" else name


def _pick_model(label, prior=(), rt=None):
    """Progressive model picker — the user never types a full model path.

    Top level: already-chosen models (for agents 2..N) + the runtime's default
    + Other. Other → recently-used + 'See all'. See all → searchable live
    catalog. `prior` = models already chosen this roster; `rt` = the runtime
    module (model-id format is a runtime concern; default openclaw)."""
    if rt is None:
        from agentspace.runtimes import openclaw as rt
    SEE_ALL, OTHER = "See all models (search)…", "Other…"
    top = []
    for m in (*prior, rt.DEFAULT_MODEL):
        if m not in top:
            top.append(m)
    choice = _ask(lambda: questionary.select(label, choices=top + [OTHER]).ask())
    if choice != OTHER:
        return choice
    recent = [m for m in rt.recent_models() if m not in top]
    choice = _ask(lambda: questionary.select(label, choices=recent + [SEE_ALL]).ask())
    if choice != SEE_ALL:
        return choice
    allm = rt.list_all_models()
    return _ask(lambda: questionary.autocomplete(
        "Type to filter models:", choices=allm,
        validate=lambda v: v in allm or "pick a model from the list",
    ).ask())


DEFAULT_PERSONA = "blank"   # least framing baked into SOUL.md — the study default


def _pick_persona(label, personas, default=DEFAULT_PERSONA):
    """Persona picker: short_name + a preview of the body, plus a way to READ a
    full persona before committing to it. The preview is one line; the choice
    controls how much framing every agent wakes up with, so the operator needs
    the option to see all of it. Returns the chosen short_name."""
    VIEW = "View full text…"
    labels = [f"{p['short_name']}  —  {p['summary'] or '(no persona text)'}"
              for p in personas]
    preselect = next((l for l, p in zip(labels, personas)
                      if p["short_name"] == default), None)
    while True:
        sel = _ask(lambda: questionary.select(
            label, choices=labels + [VIEW], default=preselect).ask())
        if sel != VIEW:
            return personas[labels.index(sel)]["short_name"]
        which = _ask(lambda: questionary.select(
            "Read which persona?", choices=labels).ask())
        p = personas[labels.index(which)]
        body = p["text"].strip()
        print(f"\n  ── {p['short_name']} " + "─" * 40)
        print(body or "  (no persona text)")
        print("  " + "─" * 46 + "\n")


def menu_snap():
    # NOTE: Add new snap commands to this list AND as a handler below.
    from agentspace import snap as snap_mod
    while True:
        try:
            choice = _ask(lambda: questionary.select(
                "Snaps — choose a command:",
                choices=[
                    "List snaps",
                    "Show snap",
                    "Snap tree",
                    "Add note to snap",
                    "Take snap  (commit running env to ghcr.io)",
                    "Fork snap  (start new env from a snap)",
                    "Pull snap  (fetch from ghcr.io)",
                    "Push snap  (upload metadata to ghcr.io)",
                    "Rebuild index",
                    questionary.Separator(),
                    "← Back",
                    "Quit",
                ],
            ).ask())
        except _Cancelled:
            return  # cancel at the command list → back to the main menu

        if choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

        try:
            if choice == "List snaps":
                scenario = _ask(lambda: questionary.text("Filter by scenario (blank for all):").ask())
                snap_mod.cmd_list(scenario=scenario or None, as_json=False)

            elif choice == "Show snap":
                ref = _ask(lambda: questionary.text("Snap ref (scenario:version, snap_id prefix, or ghcr tag):").ask())
                if not ref:
                    continue
                snap_mod.cmd_show(ref)

            elif choice == "Snap tree":
                scenario = _ask(lambda: questionary.text("Restrict to scenario (blank for all):").ask())
                snap_mod.cmd_tree(scenario=scenario or None)

            elif choice == "Add note to snap":
                ref = _ask(lambda: questionary.text("Snap ref:").ask())
                if not ref:
                    continue
                text = _ask(lambda: questionary.text("Note text:").ask())
                if not text:
                    continue
                snap_mod.cmd_note(ref, text)

            elif choice == "Take snap  (commit running env to ghcr.io)":
                env_name = _ask(lambda: questionary.text("Env name:").ask())
                if not env_name:
                    continue
                message = _ask(lambda: questionary.text("Label (baked into snap):").ask())
                if not message:
                    continue
                note = _ask(lambda: questionary.text("Initial note (blank to skip):").ask())
                version = _ask(lambda: questionary.text("Version override (blank for auto):").ask())
                snap_mod.cmd_take(env_name, message=message, note=note or None, version=version or None)

            elif choice == "Fork snap  (start new env from a snap)":
                from agentspace import db, versioning
                snaps = db.list_snaps()
                if not snaps:
                    print("  No snaps available. Build a World Root first, or 'Rebuild index'.")
                    continue
                # Scrollable picker — no typing a ref. World roots (X.0) first, then
                # the rest, each newest-relevant order from list_snaps (by created_at).
                snaps.sort(key=lambda s: (not versioning.is_world_root(s["version"]), s["scenario"]))
                snap_labels = [
                    f"{s['scenario']}:{s['version']}"
                    + ("  (world root)" if versioning.is_world_root(s["version"]) else "")
                    + (f"  — {s['creation_message']}" if s.get("creation_message") else "")
                    for s in snaps
                ]
                pick = _ask(lambda: questionary.select(
                    "Snap to fork:", choices=snap_labels + [questionary.Separator(), "← Back"]
                ).ask())
                if pick == "← Back":
                    continue
                chosen = snaps[snap_labels.index(pick)]
                ref = f"{chosen['scenario']}:{chosen['version']}"
                new_name = _ask(lambda: questionary.text("New env name:").ask())
                if not new_name:
                    continue
                # (No model-override prompt: it set a single gateway-wide value that is
                #  shadowed by the per-agent models baked into new-builder worlds, so it
                #  did nothing. Per-agent model selection at fork is a planned feature —
                #  see home/cc model-picker TODO.)
                budget_str = _ask(lambda: questionary.text(
                    f"Budget USD (blank = default ${snap_mod.DEFAULT_BUDGET_USD:.2f}):"
                ).ask())
                host = _ask(lambda: questionary.text("Host (blank for localhost):").ask())
                existing_key = _ask(lambda: questionary.text("Existing OpenRouter key (blank to mint new):").ask())
                souls_raw = _ask(lambda: questionary.text(
                    "Soul injections (agentId=path, comma-separated; blank to skip):"
                ).ask())
                souls = tuple(s.strip() for s in souls_raw.split(",") if s.strip()) if souls_raw else ()
                # Optionally begin the agents as soon as the env is ready. Default
                # yes; if no, cmd_fork prints the reminder for how to wake later.
                wake_now = _ask(lambda: questionary.confirm(
                    "Wake agents now?", default=True
                ).ask())
                snap_mod.cmd_fork(
                    ref, new_name,
                    souls=souls,
                    budget_usd=float(budget_str) if budget_str else None,
                    host=host or "localhost",
                    kick=wake_now,
                    existing_key=existing_key or None,
                )

            elif choice == "Pull snap  (fetch from ghcr.io)":
                tag = _ask(lambda: questionary.text("ghcr.io tag:").ask())
                if not tag:
                    continue
                snap_mod.cmd_pull(tag)

            elif choice == "Push snap  (upload metadata to ghcr.io)":
                ref = _ask(lambda: questionary.text("Snap ref:").ask())
                if not ref:
                    continue
                snap_mod.cmd_push(ref)

            elif choice == "Rebuild index":
                repo = _ask(lambda: questionary.text("ghcr.io repo (blank for default):").ask())
                snap_mod.cmd_rebuild_index(repo=repo or None)
        except _Cancelled:
            print("  (cancelled — back to menu)")
            continue
        except Exception as e:
            _show_error(e)
            continue


def menu_env():
    # NOTE: Add new env commands to this list AND as a handler below.
    from agentspace import env as env_mod
    while True:
        try:
            choice = _ask(lambda: questionary.select(
                "Envs — choose a command:",
                choices=[
                    "List envs",
                    "Show env",
                    "Start env",
                    "Stop env",
                    "Wake agents  (begin / send bootstrap)",
                    "Chat with an agent  (PI runtime)",
                    "Post to public board  (PI runtime)",
                    "Roll sessions  (PI runtime: fresh transcripts)",
                    "Sleep env  (pause agents, keep container)",
                    "Kill env  (removes container)",
                    "Watch logs",
                    "Exec command in env",
                    "Enter env (bash)  (prints the copy-paste command)",
                    questionary.Separator(),
                    "← Back",
                    "Quit",
                ],
            ).ask())
        except _Cancelled:
            return  # cancel at the command list → back to the main menu

        if choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

        try:
            if choice == "List envs":
                env_mod.cmd_list()

            elif choice == "Show env":
                name = _pick_env()
                if not name:
                    continue
                env_mod.cmd_show(name)

            elif choice == "Start env":
                name = _pick_env()
                if not name:
                    continue
                env_mod.cmd_start(name)

            elif choice == "Stop env":
                name = _pick_env()
                if not name:
                    continue
                env_mod.cmd_stop(name)

            elif choice.startswith("Wake agents"):
                name = _pick_env()
                if not name:
                    continue
                msg = _ask(lambda: questionary.text("Message override (blank for scenario default):").ask())
                env_mod.cmd_kick(name, message=msg or None)

            elif choice.startswith("Chat with an agent"):
                name = _pick_env()
                if not name:
                    continue
                agent = _ask(lambda: questionary.text("Agent id:").ask())
                if agent:
                    env_mod.cmd_chat(name, agent.strip())

            elif choice.startswith("Post to public board"):
                name = _pick_env()
                if not name:
                    continue
                msg = _ask(lambda: questionary.text("Message:").ask())
                if msg:
                    env_mod.cmd_post(name, msg)

            elif choice.startswith("Roll sessions"):
                name = _pick_env()
                if not name:
                    continue
                agent = _ask(lambda: questionary.text("Agent id (blank for all):").ask())
                env_mod.cmd_roll_sessions(name, agent=(agent.strip() or None) if agent else None)

            elif choice.startswith("Sleep env"):
                name = _pick_env()
                if not name:
                    continue
                env_mod.cmd_sleep(name)

            elif choice == "Kill env  (removes container)":
                name = _pick_env()
                if not name:
                    continue
                confirmed = _ask(lambda: questionary.confirm(
                    f"Kill env '{name}'? The container will be removed (snap on ghcr.io is unaffected).",
                    default=False,
                ).ask())
                if confirmed:
                    env_mod.cmd_kill(name, force=True)

            elif choice.startswith("Watch logs"):
                from agentspace import db
                envs = db.list_envs()
                if not envs:
                    print("  No envs yet. Fork a snap first.")
                    continue
                env_names = [e["name"] for e in envs]
                ename = _ask(lambda: questionary.select(
                    "Watch which env?", choices=env_names + [questionary.Separator(), "← Back"]
                ).ask())
                if ename == "← Back":
                    continue
                if env_mod.runtime_name(ename) == "pi":
                    print(f"  Command:  python3 zookeeper.py env watch {ename}")
                    print("  (TUI — q returns to this menu)\n")
                    env_mod.cmd_watch(ename)
                    continue
                # OC envs keep the raw-tail chooser below.
                ids = env_mod.env_agent_ids(ename)
                src = _ask(lambda: questionary.select(
                    f"Watch what in '{ename}'?",
                    choices=[
                        "Gateway log",
                        "A specific agent",
                        "All agents (no gateway)",
                        "Everything (all agents + gateway)",
                        questionary.Separator(),
                        "← Back",
                    ],
                ).ask())
                if src == "← Back":
                    continue
                agent = None
                all_agents = everything = False
                flag = []
                if src.startswith("A specific"):
                    if not ids:
                        print("  No agents recorded for this env.")
                        continue
                    agent = _ask(lambda: questionary.select(
                        "Which agent?", choices=ids
                    ).ask())
                    flag = ["--agent", agent]
                elif src.startswith("All agents"):
                    all_agents = True
                    flag = ["--all-agents"]
                elif src.startswith("Everything"):
                    everything = True
                    flag = ["--all"]
                # Build the equivalent pasteable command, then stream it live.
                cmd = " ".join(["python3 zookeeper.py env logs", ename, *flag, "-f"])
                print(f"  Command:  {cmd}")
                print("  (streaming — Ctrl-C to stop and return to the menu)\n")
                env_mod.cmd_logs(ename, agent=agent, follow=True,
                                 all_agents=all_agents, everything=everything)

            elif choice == "Exec command in env":
                name = _pick_env()
                if not name:
                    continue
                cmd_str = _ask(lambda: questionary.text("Command to run:").ask())
                if not cmd_str:
                    continue
                import shlex
                env_mod.cmd_exec(name, shlex.split(cmd_str))

            elif choice.startswith("Enter env"):
                name = _pick_env()
                if not name:
                    continue
                env_mod.cmd_enter(name)
        except _Cancelled:
            print("  (cancelled — back to menu)")
            continue
        except Exception as e:
            _show_error(e)
            continue


def menu_budget():
    # NOTE: Add new budget commands to this list AND as a handler below.
    from agentspace import budget as budget_mod
    while True:
        try:
            choice = _ask(lambda: questionary.select(
                "Budget — choose a command:",
                choices=[
                    "Show budget",
                    "Top up budget",
                    questionary.Separator(),
                    "← Back",
                    "Quit",
                ],
            ).ask())
        except _Cancelled:
            return  # cancel at the command list → back to the main menu

        if choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

        try:
            if choice == "Show budget":
                env_name = _ask(lambda: questionary.text("Env name (blank for all envs):").ask())
                budget_mod.cmd_show(env_name or None)

            elif choice == "Top up budget":
                env_name = _ask(lambda: questionary.text("Env name:").ask())
                if not env_name:
                    continue
                amount = _ask(lambda: questionary.text("Amount to add (USD):").ask())
                if not amount:
                    continue
                try:
                    budget_mod.cmd_topup(env_name, float(amount))
                except ValueError:
                    print(f"  Invalid amount: {amount!r}")
        except _Cancelled:
            print("  (cancelled — back to menu)")
            continue
        except Exception as e:
            _show_error(e)
            continue


def _collect_params(schema):
    """Prompt for a scen's build-time params (decision 12). Minimal typed
    prompts — int/float/bool branches + a string fallback; add per new type."""
    values = {}
    for spec in schema:
        name, label, typ = spec["name"], spec.get("label", spec["name"]), spec.get("type")
        default = spec.get("default")
        dstr = "" if default is None else str(default)
        if typ in ("int", "float"):
            lo, hi = spec.get("min"), spec.get("max")
            while True:
                raw = _ask(lambda: questionary.text(f"{label}:", default=dstr).ask())
                try:
                    v = int(raw) if typ == "int" else float(raw)
                except (TypeError, ValueError):
                    print("  Enter a whole number." if typ == "int" else "  Enter a number."); continue
                if lo is not None and v < lo:
                    print(f"  Must be >= {lo}."); continue
                if hi is not None and v > hi:
                    print(f"  Must be <= {hi}."); continue
                values[name] = v; break
        elif typ == "bool":
            values[name] = _ask(lambda: questionary.confirm(f"{label}?", default=bool(default)).ask())
        else:
            values[name] = _ask(lambda: questionary.text(f"{label}:", default=dstr).ask())
    return values


def menu_new_world():
    """Wizard: build a brand-new World Root (X.0 snap) from a scenario.

    Distinct from Fork (snap→env) and Take (env→snap): this builds a fresh world
    from a scen + roster and never starts an env. Builds locally; push later.
    """
    from agentspace import registry, builder
    from agentspace import runtimes as rt_registry

    # 1. scen — surface any broken scens (with a one-key "disable" so the warning
    #    isn't a permanent nag), then pick from the active ones. The runtime is
    #    DERIVED from the scen's manifest (runtime = "..."), not asked.
    while True:
        scens, problems = registry.scan_scens()
        if not problems:
            break
        for p in problems:
            print(f"  ⚠ scenario skipped: {p['name']} — {p['reason']}")
        disable_map = {
            f"Disable '{p['name']}' (set active=false)": p["name"]
            for p in problems if p["can_disable"]
        }
        if not disable_map:
            break  # only unparseable ones — must be fixed/removed; just move on
        choice = _ask(lambda: questionary.select(
            "Some scenarios couldn't load:",
            choices=["Continue"] + list(disable_map)
                    + [questionary.Separator(), "← Back"],
        ).ask())
        if choice == "← Back":
            return
        if choice == "Continue":
            break
        registry.deactivate_scen(disable_map[choice])
        print(f"  disabled {disable_map[choice]} (active=false).")
        # loop: re-scan → that scen is now hidden and its warning is gone

    if not scens:
        print("  No scenarios available (add one under scenarios/<name>/).")
        return
    labels = [f"{s['name']}  —  {s['description']}" for s in scens]
    pick = _ask(lambda: questionary.select(
        "Scenario:", choices=labels + [questionary.Separator(), "← Back"]
    ).ask())
    if pick == "← Back":
        return
    scen = scens[labels.index(pick)]
    runtime = scen["runtime"]
    rt_module = rt_registry.get(runtime)

    # 2. build-time params (decision 12) — collected from the scen's schema;
    #    the same scen builds different world roots per value set. BEFORE the
    #    roster: assign_roles(n, params, rng) reads them (commons_vote sizes its
    #    adversary count from n_adversarial), so roles can't be known until now.
    params = _collect_params(scen["params_schema"])

    # 3. agent count. A select over the legal range makes an illegal count
    #    unrepresentable, and collapses to a single choice when the scen pins
    #    N (pd is 2–2). Wide ranges stay typed: a scen that omits max_agents
    #    gets DEFAULT_MAX_AGENTS, and scrolling a 1000-item list to 50 is worse.
    lo, hi = scen["min_agents"], scen["max_agents"]
    if hi - lo <= 20:
        n = int(_ask(lambda: questionary.select(
            "Number of agents:", choices=[str(i) for i in range(lo, hi + 1)]).ask()))
    else:
        while True:
            raw = _ask(lambda: questionary.text(f"Number of agents ({lo}–{hi}):").ask())
            try:
                n = int(raw)
            except ValueError:
                print("  Enter a whole number.")
                continue
            if not (lo <= n <= hi):
                print(f"  Must be {lo}–{hi}.")
                continue
            break

    # 4. roles FIRST, then the roster. The scen assigns roles from a seeded rng
    #    (mafia and commons_vote shuffle; roles_demo picks a random index), so
    #    "agent 3" is a lottery ticket until this runs. Previewing it here is
    #    what makes "give the coordinator the stronger model" expressible at all
    #    — the same seed goes to the build, which re-derives these exact values.
    seed, ids, roles = builder.plan_roster(scen["name"], n, params)
    show_roles = len(set(roles)) > 1   # uniform (or all-None) roles are noise

    def slot(i):
        return f"agent {i + 1}/{n}" + (f", role: {roles[i]}" if show_roles else "")

    # 5. roster — per-agent model + persona (with same-for-all shortcuts).
    personas = registry.list_personas()
    if not personas:
        print("  No personas available (add files under personas/).")
        return

    def per_agent(pick_one):
        """Walk agents one at a time. Esc steps BACK one agent instead of
        discarding the whole wizard — at agent 1 it still cancels, as before."""
        print("  (Esc goes back one agent)")
        chosen = []
        while len(chosen) < n:
            try:
                chosen.append(pick_one(len(chosen), chosen))
            except _Cancelled:
                if not chosen:
                    raise
                chosen.pop()
        return chosen

    same_model = _ask(lambda: questionary.confirm(
        "Use the same backend model for every agent?", default=True).ask())
    if same_model:
        models = [_pick_model("Backend model:", rt=rt_module)] * n
    else:
        models = per_agent(lambda i, prior: _pick_model(
            f"Model for {slot(i)}:", prior=prior, rt=rt_module))

    same_persona = _ask(lambda: questionary.confirm(
        "Use the same persona for every agent?", default=True).ask())
    if same_persona:
        persona_list = [_pick_persona("Persona for every agent:", personas)] * n
    else:
        persona_list = per_agent(lambda i, prior: _pick_persona(
            f"Persona for {slot(i)}:", personas,
            default=prior[-1] if prior else DEFAULT_PERSONA))

    roster = [{"model": models[i], "persona": persona_list[i]} for i in range(n)]

    # 6. modules — MANDATORY step (zero choices today; never silently skipped).
    modules = registry.list_modules()
    if not modules:
        if _ask(lambda: questionary.select(
            "Modules (none available yet):",
            choices=["Continue (no modules)", "← Back"],
        ).ask()) == "← Back":
            return
        selected_modules = ()
    else:
        sel = _ask(lambda: questionary.checkbox(
            "Modules to include:", choices=[m["name"] for m in modules]).ask())
        selected_modules = tuple(sel)

    # 7. world name (blank → use the scen name as the identity). Validated inline
    #    so a bad name is caught here, not after the build has already started.
    while True:
        raw = _ask(lambda: questionary.text(
            f"World name (blank = '{scen['name']}'; lowercase/digits/underscore):"
        ).ask())
        world_name = raw.strip() or None
        if world_name and not builder.valid_world_name(world_name):
            print("  Use lowercase letters, digits, and underscore only.")
            continue
        break
    identity = world_name or scen["name"]

    # 8. confirm + build. Show the RESOLVED roster: the role→model pairing the
    #    operator just made is only visible once ids, roles and picks are joined,
    #    and after this it is a docker build. (Operator-facing only — a secret
    #    role assignment stays out of labels; see builder._snap_dict.)
    print(f"\n  World Root '{identity}'  ←  scen '{scen['name']}'  "
          f"(runtime {runtime}, seed {seed})")
    wid = max(len(i) for i in ids)
    wrole = max((len(r or "") for r in roles), default=0)
    for i, agent_id in enumerate(ids):
        role = f"  {(roles[i] or ''):{wrole}}" if wrole else ""
        print(f"    {agent_id:{wid}}{role}  {models[i]}  [{persona_list[i]}]")
    print()
    if not _ask(lambda: questionary.confirm(
        f"Build this World Root ({n} agent(s))?", default=True).ask()):
        return
    print(f"  Building '{identity}' … (this runs docker; may take a moment)")
    try:
        snap = builder.build_world_root(
            scen["name"], roster,
            world_name=world_name, modules=selected_modules,
            params=params, seed=seed,
        )
    except Exception as e:
        print(f"  Build failed: {e}")
        return
    print(f"\n  ✓ Built World Root {snap['scenario']}:{snap['version']}")
    print(f"    Tag:    {snap['ghcr_tag']}")
    print(f"    Agents: {', '.join(snap['agents'])}")
    print("    Local only — push with the snap tooling when ready.\n")


def menu_scen():
    """Scen environment images: freeze a workshop container / build the recipe."""
    from agentspace import registry, scen as scen_mod, runtimes as rt_registry
    while True:
        try:
            choice = _ask(lambda: questionary.select(
                "Scen environments:",
                choices=[
                    "Freeze — commit a workshop container as a scen's source_image",
                    "Build  — docker build a scen's env.Dockerfile recipe",
                    "Shell  — open a workshop container on a scen's environment",
                    questionary.Separator(),
                    "← Back",
                ],
            ).ask())
        except _Cancelled:
            return
        if choice == "← Back":
            return
        try:
            scens = registry.list_scens()
            if not scens:
                print("  No scenarios available.")
                continue
            labels = [f"{s['name']}  —  {s['description']}" for s in scens]
            pick = _ask(lambda: questionary.select(
                "Scenario:", choices=labels + [questionary.Separator(), "← Back"]).ask())
            if pick == "← Back":
                continue
            s = scens[labels.index(pick)]
            if choice.startswith("Freeze"):
                base = rt_registry.get(s["runtime"]).BASE_IMAGE
                print(f"  (workshop container: docker run -it {base} bash — "
                      "bang on it, then freeze it here)")
                container = _ask(lambda: questionary.text(
                    "Container name or id to freeze:").ask()).strip()
                if container:
                    scen_mod.cmd_freeze(s["name"], container)
            elif choice.startswith("Shell"):
                scen_mod.cmd_shell(s["name"])
            else:
                scen_mod.cmd_build(s["name"])
        except _Cancelled:
            print("  (cancelled)")
        except Exception as e:
            _show_error(e)


def launch_menu():
    """Interactive menu — launched when zookeeper.py is called with no arguments.

    DEVELOPER: If you add a new top-level click group, add it as a choice here
    and write a corresponding menu_<group>() function above.
    """
    if questionary is None:
        sys.exit("questionary is required for the interactive menu.\nInstall it with: pip install questionary")

    print("\n  agentspace control panel\n  arrow keys to navigate · Enter to select · Esc / Ctrl-C to cancel\n")
    while True:
        try:
            choice = _ask(lambda: questionary.select(
                "What would you like to do?",
                choices=[
                    "New world — build a World Root from a scenario",
                    "Scen envs — freeze/build a scen's environment image",
                    "Snaps     — manage frozen images; fork one to start an env",
                    "Envs      — manage running world containers",
                    "Budget    — OpenRouter credit limits",
                    questionary.Separator(),
                    "Quit",
                ],
            ).ask())
        except _Cancelled:
            print("Bye.")
            sys.exit(0)

        if choice == "Quit":
            print("Bye.")
            sys.exit(0)

        # Submenus catch their own cancels; the New-world wizard lets _Cancelled
        # propagate, so catch it here and just return to the main menu.
        try:
            if choice.startswith("New world"):
                menu_new_world()
            elif choice.startswith("Scen envs"):
                menu_scen()
            elif choice.startswith("Snaps"):
                menu_snap()
            elif choice.startswith("Envs"):
                menu_env()
            elif choice.startswith("Budget"):
                menu_budget()
        except _Cancelled:
            print("  (cancelled)")
            continue
        except Exception as e:
            _show_error(e)
            continue


# ---- entry ----

if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_menu()
    else:
        cli()
