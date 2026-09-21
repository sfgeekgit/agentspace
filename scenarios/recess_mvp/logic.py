"""recess_mvp build hooks: fixed player + gm, chosen NPC roles, reserves."""
import json
import sys
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
    world = NPC_DIR.parent
    desc = json.loads((world / "attributes.json").read_text())
    lines = [f"- {a}: {desc.get(a, '(no description)')}" for a in _split(params["attributes"])]
    ends = json.loads((world / "quests.json").read_text()).get("ends", {})
    endings = [f"- {e}: {q['gm_note']}" for e, q in ends.items()]
    sys.path.insert(0, str(world.parent))
    import engine   # the reply format is the engine's; bake it once instead of sending it every turn
    return (briefing.replace("{attributes}", "\n".join(lines))
            .replace("{endings}", "\n".join(endings))
            .replace("{format}", engine.FORMAT))


def dispatch_secrets(ids_roles, params, rng):
    return {"roles": ids_roles}
