"""Build-time hooks: one player, one GM, selected characters, then reserves."""
from pathlib import Path
import re

ROOT = Path(__file__).parent


def split(value):
    return [s.strip() for s in str(value).split(",") if s.strip()]


def validate(n, params):
    if type(params.get("max_turns")) is not int or not 1 <= params["max_turns"] <= 80:
        return "max_turns must be an integer from 1 to 80"
    chosen = split(params.get("npcs", ""))
    known = {p.stem for p in (ROOT / "dispatch/world/npcs").glob("*.json")}
    if len(chosen) != len(set(chosen)) or set(chosen) - known:
        return f"Choose distinct NPC names from: {', '.join(sorted(known))}"
    if n < len(chosen) + 2:
        return f"Need {len(chosen) + 2} agents for this cast; extras become reserves"
    attrs = split(params.get("attributes", ""))
    if not attrs or len(attrs) > 24 or len(attrs) != len(set(attrs)) or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,47}", a) for a in attrs):
        return "Choose 1–24 distinct lower_case attribute names"
    return None


def assign_roles(n, params, rng):
    error = validate(n, params)
    if error:
        raise ValueError(error)
    roles = ["player", "gm"] + [f"npc_{name}" for name in split(params["npcs"])]
    return roles + ["reserve"] * (n - len(roles))


def dispatch_secrets(ids_roles, params, rng):
    return {"roles": ids_roles}
