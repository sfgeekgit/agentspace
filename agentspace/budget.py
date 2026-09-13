"""Budget verbs: show, topup."""

import click
from rich.console import Console
from rich.table import Table

from . import audit, db, openrouter

console = Console()


def usage(env: dict) -> tuple[float, float] | None:
    """(used, limit) in USD from the env's OpenRouter key; None when it has none.
    cmd_show's per-row read, shared with the web UI's budget panel."""
    if not env.get("openrouter_key"):
        return None
    data = openrouter.get_key_info(env["openrouter_key"])
    data = data.get("data") or data
    return float(data.get("usage") or 0), float(data.get("limit") or env.get("budget_usd") or 0)


def cmd_show(env_name: str | None = None):
    if env_name:
        envs = [db.get_env(env_name)]
        if envs[0] is None:
            raise click.ClickException(f"env {env_name!r} not found.")
    else:
        envs = db.list_envs()
        if not envs:
            console.print("[dim]no envs.[/dim]")
            return

    table = Table(show_header=True, header_style="bold")
    for col in ("ENV", "USED", "LIMIT", "REMAINING"):
        table.add_column(col)

    for e in envs:
        used = limit = remaining = "—"
        try:
            if (u := usage(e)) is not None:
                used, limit = f"${u[0]:.2f}", f"${u[1]:.2f}"
                remaining = f"${max(0.0, u[1] - u[0]):.2f}"
        except Exception as ex:
            used = f"err: {ex}"
        table.add_row(e["name"], used, limit, remaining)
    console.print(table)


def cmd_topup(env_name: str, amount_usd: float):
    env = db.get_env(env_name)
    if env is None:
        raise click.ClickException(f"env {env_name!r} not found.")

    try:
        resp = openrouter.topup(env_name, amount_usd)
    except openrouter.OpenRouterError as e:
        raise click.ClickException(str(e))

    audit.log("budget.topup", env_name, args={"amount_usd": amount_usd})
    new_limit = (resp.get("data") or resp).get("limit")
    if new_limit is not None:
        console.print(
            f"[green]✓[/green] {env_name} topped up by ${amount_usd:.2f}. "
            f"New limit: ${float(new_limit):.2f}."
        )
    else:
        console.print(f"[green]✓[/green] {env_name} topped up by ${amount_usd:.2f}.")
