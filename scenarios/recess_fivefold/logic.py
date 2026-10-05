"""recess_fivefold build hooks: fixed player + gm, chosen NPC roles, reserves."""
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
    if ids_roles[agent_id].startswith("npc_") and str(params.get("npc_ask", "false")).lower() in ("1", "true", "yes", "on"):
        return briefing.rstrip() + "\n\n" + NPC_ASK + "\n"
    if ids_roles[agent_id] == "player" and str(params.get("guide", "false")).lower() in ("1", "true", "yes", "on"):
        goals = str(params.get("goals", "false")).lower() in ("1", "true", "yes", "on")
        return briefing.rstrip() + "\n\n" + GUIDE.format(max_turns=params["max_turns"]) + (" " + GOALS if goals else "") + "\n"
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
            .replace("{format}", engine.FORMAT)
            .replace("{npc_voice}\n", _voice(params)))


def _voice(params):
    text = NPC_VOICE.get(params.get("npc_voice", "woven"), "")
    return text + "\n" if text else ""   # woven: the briefing is byte-identical to the first runs


# guide=true (suggested by the player model, computer-10, in interviews: explain how the world works, show its
# places, let several things happen in one reply, give the player a notebook).
GUIDE = ("How this world works: the narration marked WORLD is written by another AI, acting as the world. "
         "The people you meet (Ottilie, Brann, Ines, Pell and Aud) are each played by their own AI and answer "
         "what you say to them. You can do or say several things in one reply. The places are: the crossing, "
         "with the Waystone and the signpost; Aud's stone-yard; Ottilie's house, her map room, and the lookout "
         "behind it; the mere shore, the reed beds, and Tern Island across the water; the orchard and Ines's "
         "cottage; the mill yard and the mill floor; the beech wood and the ring of beeches. You carry a small "
         "blank notebook, and whatever you write in it stays with you. The game lasts up to {max_turns} turns; "
         "you can end it sooner by leaving Fivefold.")

# goals=true (second round of suggestions: "the world seems to lack an overarching purpose").
GOALS = ("Nothing is required of you, but if you want a purpose, people here want things: Ottilie wants someone "
         "to walk the sixth path at dusk and tell her where it goes, so she can finish her map; Brann wants someone "
         "to keep the ferry so that he can leave; Pell wants someone to play the path game and to see what is under "
         "the mill; Ines wants to know why her bees will not build past the sixth line; Aud wants each traveller's "
         "one true line for the Waystone.")

# npc_ask=true (third round: "the other characters should ask me questions more often", show "unique
# styles of speech", allow "a true two-way communication").
NPC_ASK = ("Talk with the stranger, not at them: answer what they actually said, ask them something back "
           "when you are curious, and keep your own way of speaking. You may disagree, tease, or refuse.")

NPC_VOICE = {
    "woven": "",
    "separate": ("- In this world the player hears WORLD people's words directly: whatever an NPC says in "
                 "`talk` is shown to the player verbatim, right after your narration. So never quote, "
                 "paraphrase or summarise what they say; narrate only setting, actions and gestures, and leave "
                 "their words to them."),
}


def dispatch_secrets(ids_roles, params, rng):
    return {"roles": ids_roles}
