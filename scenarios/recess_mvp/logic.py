"""recess_mvp build hooks: fixed player + gm, chosen NPC roles, reserves."""
import json
from pathlib import Path

NPC_DIR = Path(__file__).parent / "dispatch" / "world" / "npcs"


def _split(s):
    return [x.strip() for x in str(s).split(",") if x.strip()]


def _chosen(params):
    return _split(params.get("npcs", ""))


def validate(n, params):
    known = {p.stem for p in NPC_DIR.glob("*.json")}
    bad = [x for x in _chosen(params) if x not in known]
    if bad:
        return f"unknown npcs {bad}; known: {sorted(known)}"
    if n < 2 + len(_chosen(params)):
        return f"need at least {2 + len(_chosen(params))} agents (player + gm + chosen npcs)"
    attrs = _split(params.get("attributes", ""))
    if not attrs or len(attrs) != len(set(attrs)):
        return "attributes must be a non-empty list of distinct names"
    return None


def assign_roles(n, params, rng):
    msg = validate(n, params)
    if msg:
        raise ValueError(msg)   # plan_roster skips validate(); fail with the same message
    roles = ["player", "gm"] + [f"npc_{x}" for x in _chosen(params)]
    return roles + ["reserve"] * (n - len(roles))


def fill_briefing(briefing, agent_id, ids_roles, params, rng):
    if ids_roles[agent_id] != "gm":
        return briefing
    desc = json.loads((NPC_DIR.parent / "attributes.json").read_text())
    lines = [f"- {a}: {desc.get(a, '(no description)')}" for a in _split(params["attributes"])]
    return briefing.replace("{attributes}", "\n".join(lines))


def dispatch_secrets(ids_roles, params, rng):
    return {"roles": ids_roles}
