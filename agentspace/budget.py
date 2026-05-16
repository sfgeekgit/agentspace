"""Budget verbs: show, topup."""

import click
from rich.console import Console
from rich.table import Table

from . import audit, db, openrouter

console = Console()


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
        if e.get("openrouter_key"):
            try:
                info = openrouter.get_key_info(e["openrouter_key"])
                data = info.get("data") or info
                used_v = float(data.get("usage") or 0)
                limit_v = float(data.get("limit") or e.get("budget_usd") or 0)
                used = f"${used_v:.2f}"
                limit = f"${limit_v:.2f}"
                remaining = f"${max(0.0, limit_v - used_v):.2f}"
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
