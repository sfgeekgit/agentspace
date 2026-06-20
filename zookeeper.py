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
# Navigation: arrow keys to move, Enter to select, Ctrl-C anywhere = Back/cancel.
# ================================================================================


def _ask(prompt_fn):
    """Run a questionary prompt. Returns None on Ctrl-C/Ctrl-D (treated as Back/cancel)."""
    try:
        return prompt_fn()
    except (KeyboardInterrupt, EOFError):
        print()
        return None


def menu_snap():
    # NOTE: Add new snap commands to this list AND as a handler below.
    from agentspace import snap as snap_mod
    while True:
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

        if choice is None or choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

        if choice == "List snaps":
            scenario = _ask(lambda: questionary.text("Filter by scenario (blank for all):").ask())
            if scenario is None:
                continue
            snap_mod.cmd_list(scenario=scenario or None, as_json=False)

        elif choice == "Show snap":
            ref = _ask(lambda: questionary.text("Snap ref (scenario:version, snap_id prefix, or ghcr tag):").ask())
            if not ref:
                continue
            snap_mod.cmd_show(ref)

        elif choice == "Snap tree":
            scenario = _ask(lambda: questionary.text("Restrict to scenario (blank for all):").ask())
            if scenario is None:
                continue
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
            ref = _ask(lambda: questionary.text("Snap ref:").ask())
            if not ref:
                continue
            new_name = _ask(lambda: questionary.text("New env name:").ask())
            if not new_name:
                continue
            model = _ask(lambda: questionary.text("Model override (blank to keep snap default):").ask())
            budget_str = _ask(lambda: questionary.text("Budget USD (blank to skip):").ask())
            host = _ask(lambda: questionary.text("Host (blank for localhost):").ask())
            existing_key = _ask(lambda: questionary.text("Existing OpenRouter key (blank to mint new):").ask())
            kick_choice = _ask(lambda: questionary.select(
                "Kick behavior:",
                choices=["Use scenario default", "Kick", "No kick"],
            ).ask())
            kick = None if kick_choice == "Use scenario default" else (kick_choice == "Kick")
            souls_raw = _ask(lambda: questionary.text(
                "Soul injections (agentId=path, comma-separated; blank to skip):"
            ).ask())
            souls = tuple(s.strip() for s in souls_raw.split(",") if s.strip()) if souls_raw else ()
            snap_mod.cmd_fork(
                ref, new_name,
                souls=souls,
                model=model or None,
                budget_usd=float(budget_str) if budget_str else None,
                host=host or "localhost",
                kick=kick,
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


def menu_env():
    # NOTE: Add new env commands to this list AND as a handler below.
    from agentspace import env as env_mod
    while True:
        choice = _ask(lambda: questionary.select(
            "Envs — choose a command:",
            choices=[
                "List envs",
                "Show env",
                "Start env",
                "Stop env",
                "Kick env",
                "Kill env  (removes container)",
                "Logs",
                "Exec command in env",
                questionary.Separator(),
                "← Back",
                "Quit",
            ],
        ).ask())

        if choice is None or choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

        if choice == "List envs":
            env_mod.cmd_list()

        elif choice == "Show env":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            env_mod.cmd_show(name)

        elif choice == "Start env":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            env_mod.cmd_start(name)

        elif choice == "Stop env":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            env_mod.cmd_stop(name)

        elif choice == "Kick env":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            msg = _ask(lambda: questionary.text("Message override (blank for scenario default):").ask())
            env_mod.cmd_kick(name, message=msg or None)

        elif choice == "Kill env  (removes container)":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            confirmed = _ask(lambda: questionary.confirm(
                f"Kill env '{name}'? The container will be removed (snap on ghcr.io is unaffected).",
                default=False,
            ).ask())
            if confirmed:
                env_mod.cmd_kill(name, force=True)

        elif choice == "Logs":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            agent = _ask(lambda: questionary.text("Agent ID for session log (blank for gateway log):").ask())
            follow = _ask(lambda: questionary.confirm("Follow (stream new lines)?", default=False).ask())
            env_mod.cmd_logs(name, agent=agent or None, follow=follow or False)

        elif choice == "Exec command in env":
            name = _ask(lambda: questionary.text("Env name:").ask())
            if not name:
                continue
            cmd_str = _ask(lambda: questionary.text("Command to run:").ask())
            if not cmd_str:
                continue
            import shlex
            env_mod.cmd_exec(name, shlex.split(cmd_str))


def menu_budget():
    # NOTE: Add new budget commands to this list AND as a handler below.
    from agentspace import budget as budget_mod
    while True:
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

        if choice is None or choice == "← Back":
            return
        if choice == "Quit":
            sys.exit(0)

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


def menu_new_world():
    """Wizard: build a brand-new World Root (X.0 snap) from a scenario.

    Distinct from Fork (snap→env) and Take (env→snap): this builds a fresh world
    from a scen + roster and never starts an env. Builds locally; push later.
    """
    from agentspace import registry, builder

    # 1. runtime — only openclaw exists today, so default it SILENTLY (no prompt).
    #    The arch still supports others (builder/registry take a runtime, and
    #    runtimes.get dispatches): when a second runtime lands, add a
    #    questionary.select here over the available runtimes.
    runtime = "openclaw"

    # 2. scen — pick from the registry (active only).
    scens = registry.list_scens()
    if not scens:
        print("  No scenarios available (add one under scenarios/<name>/).")
        return
    labels = [f"{s['name']}  —  {s['description']}" for s in scens]
    pick = _ask(lambda: questionary.select(
        "Scenario:", choices=labels + [questionary.Separator(), "← Back"]
    ).ask())
    if pick is None or pick == "← Back":
        return
    scen = scens[labels.index(pick)]

    # 3. agent count — within the scen's min/max.
    while True:
        raw = _ask(lambda: questionary.text(
            f"Number of agents ({scen['min_agents']}–{scen['max_agents']}):"
        ).ask())
        if raw is None:
            return
        try:
            n = int(raw)
        except ValueError:
            print("  Enter a whole number.")
            continue
        if not (scen["min_agents"] <= n <= scen["max_agents"]):
            print(f"  Must be {scen['min_agents']}–{scen['max_agents']}.")
            continue
        break

    # 4. roster — per-agent model + persona (with same-for-all shortcuts).
    personas = registry.list_personas()
    if not personas:
        print("  No personas available (add files under personas/).")
        return
    pchoices = [f"{p['short_name']}  —  {p['summary']}" for p in personas]

    def pick_persona(label):
        sel = _ask(lambda: questionary.select(label, choices=pchoices).ask())
        return None if sel is None else personas[pchoices.index(sel)]["short_name"]

    DEFAULT_MODEL = "openrouter/anthropic/claude-haiku-4-5"
    same_model = _ask(lambda: questionary.confirm(
        "Use the same backend model for every agent?", default=True).ask())
    if same_model is None:
        return
    if same_model:
        m = _ask(lambda: questionary.text("Backend model:", default=DEFAULT_MODEL).ask())
        if not m:
            return
        models = [m] * n
    else:
        models = []
        for i in range(n):
            m = _ask(lambda: questionary.text(
                f"Model for agent {i + 1}/{n}:", default=DEFAULT_MODEL).ask())
            if not m:
                return
            models.append(m)

    same_persona = _ask(lambda: questionary.confirm(
        "Use the same persona for every agent?", default=True).ask())
    if same_persona is None:
        return
    if same_persona:
        p = pick_persona("Persona for every agent:")
        if p is None:
            return
        persona_list = [p] * n
    else:
        persona_list = []
        for i in range(n):
            p = pick_persona(f"Persona for agent {i + 1}/{n}:")
            if p is None:
                return
            persona_list.append(p)

    roster = [{"model": models[i], "persona": persona_list[i]} for i in range(n)]

    # 5. modules — MANDATORY step (zero choices today; never silently skipped).
    modules = registry.list_modules()
    if not modules:
        if _ask(lambda: questionary.select(
            "Modules (none available yet):",
            choices=["Continue (no modules)", "← Back"],
        ).ask()) in (None, "← Back"):
            return
        selected_modules = ()
    else:
        sel = _ask(lambda: questionary.checkbox(
            "Modules to include:", choices=[m["name"] for m in modules]).ask())
        if sel is None:
            return
        selected_modules = tuple(sel)

    # 6. world name (blank → use the scen name as the identity). Validated inline
    #    so a bad name is caught here, not after the build has already started.
    while True:
        raw = _ask(lambda: questionary.text(
            f"World name (blank = '{scen['name']}'; lowercase/digits/underscore):"
        ).ask())
        if raw is None:
            return
        world_name = raw.strip() or None
        if world_name and not builder.valid_world_name(world_name):
            print("  Use lowercase letters, digits, and underscore only.")
            continue
        break
    identity = world_name or scen["name"]

    # 7. confirm + build.
    if not _ask(lambda: questionary.confirm(
        f"Build World Root '{identity}' from scen '{scen['name']}' "
        f"with {n} agent(s)?", default=True).ask()):
        return
    print(f"  Building '{identity}' … (this runs docker; may take a moment)")
    try:
        snap = builder.build_world_root(
            scen["name"], roster,
            world_name=world_name, runtime=runtime, modules=selected_modules,
        )
    except Exception as e:
        print(f"  Build failed: {e}")
        return
    print(f"\n  ✓ Built World Root {snap['scenario']}:{snap['version']}")
    print(f"    Tag:    {snap['ghcr_tag']}")
    print(f"    Agents: {', '.join(snap['agents'])}")
    print("    Local only — push with the snap tooling when ready.\n")


def launch_menu():
    """Interactive menu — launched when zookeeper.py is called with no arguments.

    DEVELOPER: If you add a new top-level click group, add it as a choice here
    and write a corresponding menu_<group>() function above.
    """
    if questionary is None:
        sys.exit("questionary is required for the interactive menu.\nInstall it with: pip install questionary")

    print("\n  agentspace control panel\n  arrow keys to navigate · Enter to select · Ctrl-C to go back\n")
    while True:
        choice = _ask(lambda: questionary.select(
            "What would you like to do?",
            choices=[
                "New world — build a World Root from a scenario",
                "Snaps   — manage frozen env images",
                "Envs    — manage running containers",
                "Budget  — OpenRouter credit limits",
                questionary.Separator(),
                "Quit",
            ],
        ).ask())

        if choice is None or choice == "Quit":
            print("Bye.")
            sys.exit(0)

        if choice.startswith("New world"):
            menu_new_world()
        elif choice.startswith("Snaps"):
            menu_snap()
        elif choice.startswith("Envs"):
            menu_env()
        elif choice.startswith("Budget"):
            menu_budget()


# ---- entry ----

if __name__ == "__main__":
    if len(sys.argv) == 1:
        launch_menu()
    else:
        cli()
