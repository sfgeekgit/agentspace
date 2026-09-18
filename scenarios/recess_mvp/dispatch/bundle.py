"""Load and validate the content bundle (dispatch/world/). Generic: the engine
knows nothing about any particular village; everything world-specific is here."""
import json
from pathlib import Path

PRED_KEYS = {"stat", "min", "max", "flag", "not_flag", "turn_min", "met", "loc", "all", "any"}


def load(root):
    root = Path(root)
    b = {
        "map": json.loads((root / "map.json").read_text()),
        "attributes": json.loads((root / "attributes.json").read_text()),
        "npcs": {p.stem: json.loads(p.read_text()) for p in sorted((root / "npcs").glob("*.json"))},
        "quests": json.loads((root / "quests.json").read_text()),
        "schedule": json.loads((root / "schedule.json").read_text()),
    }
    validate(b)
    return b


def _check_pred(p, where):
    if p == "always":
        return
    if not isinstance(p, dict) or not set(p) <= PRED_KEYS:
        raise ValueError(f"{where}: bad predicate {p!r}")
    for sub in p.get("all", []) + p.get("any", []):
        _check_pred(sub, where)


def validate(b):
    nodes = b["map"]["nodes"]
    if b["map"]["start"] not in nodes:
        raise ValueError("map.start is not a node")
    for nid, n in nodes.items():
        for d, t in n.get("exits", {}).items():
            if t not in nodes:
                raise ValueError(f"map node {nid}: exit {d!r} -> unknown node {t!r}")
    for name, npc in b["npcs"].items():
        if npc["loc"] not in nodes:
            raise ValueError(f"npc {name}: loc {npc['loc']!r} is not a node")
        for blk in npc.get("blocks", []):
            _check_pred(blk["unlock"], f"npc {name} block {blk['id']}")
        for note in npc.get("gm_notes", []):
            _check_pred(note["unlock"], f"npc {name} gm_note")
    for end, q in b["quests"].get("ends", {}).items():
        _check_pred(q["when"], f"end {end}")
    for arc in b["quests"].get("arcs", []):
        for note in arc.get("gm_notes", []):
            _check_pred(note["unlock"], f"arc {arc['name']}")
    for i, s in enumerate(b["schedule"]):
        if "unless" in s:
            _check_pred(s["unless"], f"schedule {i}")
        if s["npc"] not in b["npcs"]:
            raise ValueError(f"schedule {i}: unknown npc {s['npc']!r}")
