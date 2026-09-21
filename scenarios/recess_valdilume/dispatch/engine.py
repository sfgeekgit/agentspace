"""Scenario-local world model. Transactions are validated on a copy, then committed.

No runtime dependencies or Valdilume-specific branches. The model judges fiction;
this module owns identity, geography, ownership, reveal gates, and turn limits.
"""
import copy
import json
import re
from pathlib import Path


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def obj(value, keys, label):
    require(isinstance(value, dict), f"{label} must be an object")
    require(not set(value) - set(keys), f"Unknown {label} fields: {sorted(set(value) - set(keys))}")
    return value


def seq(value, limit, label):
    require(isinstance(value, list) and len(value) <= limit, f"{label} must be a list of at most {limit}")
    return value


def text(value, limit=600):
    require(isinstance(value, str) and len(value.strip()) <= limit, f"Expected text of at most {limit} characters")
    return value.strip()


def ident(value):
    require(isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_]{0,47}", value)),
            "IDs must be lower_case_words, at most 48 characters")
    return value


def prose(value, limit=600):
    value = text(value, limit)
    require(not re.search(r'["“”`]|\b(says|said|asks|asked|replies|whispers|shouts|tells you|answers that)\b', value, re.I),
            "Scene/outcome must contain no dialogue or paraphrased NPC speech; use talk")
    return value


def decode(raw):
    require(isinstance(raw, str) and raw.strip(), "Empty reply")
    raw = raw.strip()
    if raw.startswith("```"):
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, re.S)
        require(match is not None, "Return one complete JSON object")
        raw = match.group(1)
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise Invalid("Return valid JSON, without surrounding prose") from exc
    require(isinstance(value, dict), "Return a JSON object")
    return value


def when(p, s, npc=None):
    if p == "always":
        return True
    if "all" in p:
        return all(when(q, s, npc) for q in p["all"])
    if "any" in p:
        return any(when(q, s, npc) for q in p["any"])
    if "flag" in p:
        return bool(s["flags"].get(p["flag"]))
    if "not_flag" in p:
        return not s["flags"].get(p["not_flag"])
    if "loc" in p:
        return s["loc"] == p["loc"]
    if "item" in p:
        return s["items"].get(p["item"], {}).get("holder") == "player"
    if "attribute" in p:
        return s["attributes"].get(p["attribute"], 0) >= p.get("min", 1)
    if "npc" in p:
        return p["npc"] in s["npcs"] and s["npcs"][p["npc"]].get("active", True)
    if "topic" in p:
        return npc is not None and p["topic"] in npc["topics"]
    if "bond_min" in p:
        return npc is not None and npc["bond"] >= p["bond_min"]
    if "visits_min" in p:
        return npc is not None and npc["visits"] >= p["visits_min"]
    return False


def load(root):
    root = Path(root)
    b = {"map": json.loads((root / "map.json").read_text()),
         "rules": json.loads((root / "rules.json").read_text()),
         "npcs": {p.stem: json.loads(p.read_text()) for p in sorted((root / "npcs").glob("*.json"))}}
    nodes, rules = b["map"]["nodes"], b["rules"]
    require(b["map"]["start"] in nodes, "Invalid starting place")

    def predicate(p):
        if p == "always":
            return
        require(isinstance(p, dict), f"Bad predicate: {p}")
        keys = set(p)
        if keys == {"all"} or keys == {"any"}:
            for q in seq(p[next(iter(keys))], 20, "conditions"):
                predicate(q)
            return
        if "attribute" in p:
            obj(p, ["attribute", "min"], "attribute condition")
            ident(p["attribute"])
            require(type(p.get("min", 1)) is int, "Attribute threshold must be integer")
            return
        require(len(keys) == 1, f"Bad predicate: {p}")
        k, v = next(iter(p.items()))
        require(k in {"flag", "not_flag", "loc", "item", "npc", "topic", "bond_min", "visits_min"}, f"Unknown condition {k}")
        refs = {"flag": rules["flags"], "not_flag": rules["flags"], "loc": nodes,
                "item": b["map"]["items"], "npc": b["npcs"]}
        if k in refs:
            require(v in refs[k], f"Unknown {k}: {v}")
        elif k in {"bond_min", "visits_min"}:
            require(type(v) is int, f"{k} must be integer")
        else:
            ident(v)

    names = []
    for nid, n in nodes.items():
        ident(nid)
        names.append(n["name"].casefold())
        require(all(t in nodes for t in n["exits"].values()), f"Broken exit in {nid}")
        predicate(n.get("gate", "always"))
    require(len(names) == len(set(names)), "Duplicate place names")
    for key, npc in b["npcs"].items():
        ident(key)
        require(npc["loc"] in nodes, f"Unknown location for {key}")
        seen = set()
        for block in npc["blocks"]:
            require(block["id"] not in seen, f"Duplicate block in {key}")
            seen.add(block["id"])
            predicate(block["when"])
        require("core" in seen, f"{key} has no core")
    for item, d in b["map"]["items"].items():
        ident(item)
        require(d["holder"] in nodes or d["holder"] == "player" or
                d["holder"].removeprefix("npc:") in b["npcs"], f"Invalid item owner: {item}")
        predicate(d.get("known_when", "always"))
    for d in list(rules["flags"].values()) + list(rules["endings"].values()) + rules["achievements"]:
        predicate(d["when"])
    for d in rules["schedules"]:
        require(type(d["turn"]) is int and 1 <= d["turn"] <= 80, "Schedule outside turn cap")
        require(d["npc"] in b["npcs"] and d["loc"] in nodes, "Bad scheduled character/place")
        predicate(d.get("unless", {"any": []}))
    return b


def new_state(b, roles, params):
    require(type(params["max_turns"]) is int and 1 <= params["max_turns"] <= 80, "Turn cap must be 1–80")
    require(list(roles.values()).count("player") == list(roles.values()).count("gm") == 1,
            "Exactly one player and one GM are required")
    by_role = {r: a for a, r in roles.items()}
    npcs = {}
    for name, d in b["npcs"].items():
        if f"npc_{name}" in by_role:
            npcs[name] = {"agent": by_role[f"npc_{name}"], "loc": d["loc"], "definition": copy.deepcopy(d),
                          "bond": 0, "visits": 0, "topics": [], "unlocked": [], "memory": [], "exchanges": []}
    items = copy.deepcopy(b["map"]["items"])
    items = {k: d for k, d in items.items() if not d["holder"].startswith("npc:") or d["holder"][4:] in npcs}
    return {"version": 1, "title": b["map"]["title"], "params": params, "turn": 0, "phase": "opening",
            "player": by_role["player"], "gm": by_role["gm"], "loc": b["map"]["start"],
            "map": copy.deepcopy(b["map"]["nodes"]), "items": items, "npcs": npcs,
            "reserves": sorted(a for a, r in roles.items() if r == "reserve"),
            "attributes": {a.strip(): 0 for a in params["attributes"].split(",") if a.strip()},
            "attribute_last": {}, "fixed_attributes": [a.strip() for a in params["attributes"].split(",") if a.strip()], "flags": {},
            "facts": {}, "achievements": [], "fired": [], "events": [], "transcript": [],
            "recent": [], "io": None, "work": {}, "ended": None}


def present(s):
    return [n for n, d in s["npcs"].items() if d["loc"] == s["loc"] and d.get("active", True)]


def accessible(s, node):
    if "open" in s["map"][node]:
        return s["map"][node]["open"]
    return when(s["map"][node].get("gate", "always"), s)


def available_flags(s, b):
    return {k: d["instruction"] for k, d in b["rules"]["flags"].items()
            if not s["flags"].get(k) and when(d["when"], s)}


def begin(s, b):
    """Rare, authored changes, saved before planning; never fabricate the player's presence."""
    s["work"] = {"number": s["turn"] + 1, "scheduled": [], "replies": [], "receipts": [], "notices": []}
    for i, d in enumerate(b["rules"]["schedules"]):
        if i in s["fired"] or d["turn"] != s["turn"] + 1:
            continue
        s["fired"].append(i)
        if d["npc"] not in s["npcs"] or not s["npcs"][d["npc"]].get("active", True) or when(d.get("unless", {"any": []}), s):
            continue
        s["npcs"][d["npc"]]["loc"] = d["loc"]
        s["facts"][f"schedule_{i}"] = d["fact"]
        s["work"]["scheduled"].append({"npc": d["npc"], "topic": None, "scheduled": d["hears"]})


def planning_context(s, b, action):
    return {"job": "PLAN", "move_number": s["turn"] + 1, "limit": s["params"]["max_turns"],
            "player_attempt": action, "location": s["loc"],
            "places": {k: {**d, "accessible": accessible(s, k)} for k, d in s["map"].items()},
            "people": {k: {"name": d["definition"]["name"], "public": d["definition"]["public"],
                            "loc": d["loc"], "active": d.get("active", True), "topics": d["definition"].get("topics", []), "bond": d["bond"]}
                       for k, d in s["npcs"].items()},
            "items": s["items"], "attributes": s["attributes"],
            "attribute_meanings": b["rules"]["attributes"], "flags_already_true": s["flags"],
            "flag_rules": b["rules"]["flags"], "facts": s["facts"], "recent": s["recent"][-3:],
            "reserves_left": len(s["reserves"]), "endings": b["rules"]["endings"],
            "reply_shape": {"path": [], "effects": [], "attributes": [], "talk": [], "outcome": "", "end": None},
            "allowed_reply_keys": ["path", "effects", "attributes", "talk", "outcome", "end"],
            "path_rule": "path lists up to THREE successive adjacent DESTINATIONS from location. "
                "Normally omit your starting location. A leading current location is tolerated as a route origin and makes no move. "
                "To stay here use []. Only follow exits in places; never jump or repeat a node to wait.",
            "reply_example": {"effects": [{"op": "remember", "id": "afternoon_plan", "text": "The visitor plans a drawing."}], "outcome": "You settle on an idea."},
            "schema_reminder": "Return ONLY the reply object. Operations below are examples of entries INSIDE effects. "
                "Never return effect_catalog, operations, relate, job, reply_shape or reply_example as a top-level key. "
                "Example relationship effect: {\"op\":\"relate\",\"npc\":\"a present ID\",\"delta\":1,\"reason\":\"specific evidence\"} belongs INSIDE effects. "
                "No introduction, markdown or trailing text. Omit unused keys. {} is valid.",
            "effect_catalog": [
                {"op": "take", "item": "item_id"}, {"op": "drop", "item": "item_id"},
                {"op": "consume", "item": "held material used up by this action"},
                {"op": "give", "item": "item_id", "npc": "present_npc_id"},
                {"op": "create_item", "id": "new_id", "name": "name", "desc": "description"},
                {"op": "change_place", "node": "current_id", "desc": "complete new persistent description"},
                {"op": "access", "node": "current_or_adjacent_id", "open": False, "reason": "physical change caused by this action"},
                {"op": "presence", "npc": "character_at_this_location", "active": False, "reason": "why this character disappears or returns"},
                {"op": "remember", "id": "fact_id", "text": "persistent consequence; replaces same id"},
                {"op": "flag", "id": "authored_flag_id"},
                {"op": "relate", "npc": "present_npc", "delta": 1, "reason": "what happened between you"},
                {"op": "add_place", "id": "new_id", "name": "new name", "desc": "description", "via": "new exit label"},
                {"op": "recruit", "id": "new_id", "name": "name", "public": "appearance", "brief": "character and present desire"},
                {"op": "move_npc", "npc": "npc_id", "to": "adjacent_node_id"}],
            "execution_order": "Path, then effects in order, then attributes, then talk. New places link back. "
                "Every field is optional. Omit unused fields or use empty lists; {} is a valid quiet turn. "
                "Recruit/add at current place. Talk sees changes from this action. Max 2 talks, 2 attribute changes; "
                "deltas +/-1. Fixed attributes bounded -8..8 and change at most every 3 actions. "
                "Only choose end if the player's intent matches its instruction. Never invent speaker replies."}


def apply_plan(original, b, p):
    """All-or-nothing transaction. Errors return to the GM before any narration."""
    require(isinstance(p, dict), "Plan must be a JSON object")
    legal = {"path", "effects", "attributes", "talk", "outcome", "end"}
    require(not set(p) - legal, f"Unknown top-level keys: {sorted(set(p) - legal)}. "
            "Allowed keys: path, effects, attributes, talk, outcome, end. Put all op objects, including relate, inside effects; do not copy effect_catalog")
    s = copy.deepcopy(original)
    receipts, notices = [], []
    path = seq(p.get("path", []), 4, "path")
    # Some small models describe a route as [origin, destination, ...]. The
    # origin is not a move; tolerate it without relaxing any edge/access check.
    if path and path[0] == s["loc"]:
        path = path[1:]
    seq(path, 3, "path destinations")
    for dest in path:
        require(isinstance(dest, str) and dest in s["map"], f"Unknown destination {dest!r}; use a place ID")
        require(dest in s["map"][s["loc"]]["exits"].values(), f"{dest} is not adjacent to {s['loc']}")
        require(accessible(s, dest), s["map"][dest].get("blocked", "That way is not open yet"))
        s["loc"] = dest
        receipts.append(f"You reach {s['map'][dest]['name']}.")
    related = set()
    for effect in seq(p.get("effects", []), 8, "effects"):
        require(isinstance(effect, dict), "Every effect must be an object")
        op = effect.get("op")
        require(isinstance(op, str), "Effect op must be a string")
        if op in {"take", "drop", "give", "consume"}:
            obj(effect, ["op", "item", "npc"], op)
            item = effect.get("item")
            require(isinstance(item, str) and item in s["items"], f"Unknown item {item!r}")
            d = s["items"][item]
            require(d["holder"] == (s["loc"] if op == "take" else "player"), f"You cannot {op} {item}: holder is {d['holder']}")
            if op == "give":
                require(effect.get("npc") in present(s), "Recipient must be present")
                d["holder"] = "npc:" + effect["npc"]
                receipts.append(f"You give {d['name']} to {s['npcs'][effect['npc']]['definition']['name']}.")
            elif op == "consume":
                d["holder"] = "used"
                receipts.append(f"You use up {d['name']}.")
            else:
                d["holder"] = "player" if op == "take" else s["loc"]
                receipts.append(f"You {'pick up' if op == 'take' else 'put down'} {d['name']}.")
        elif op == "create_item":
            obj(effect, ["op", "id", "name", "desc"], op)
            key, name = ident(effect.get("id")), text(effect.get("name"), 100)
            require(name and key not in s["items"] and name.casefold() not in {v['name'].casefold() for v in s['items'].values()}, "That item already exists or has no name")
            s["items"][key] = {"name": name, "desc": text(effect.get("desc"), 300), "holder": "player"}
            receipts.append(f"You now have {name}.")
        elif op == "change_place":
            obj(effect, ["op", "node", "desc"], op)
            require(effect.get("node") == s["loc"], "Change the place where the action happens")
            s["map"][s["loc"]]["desc"] = prose(effect.get("desc"))
        elif op == "remember":
            obj(effect, ["op", "id", "text"], op)
            key = ident(effect.get("id"))
            require(key in s["facts"] or len(s["facts"]) < 60, "Update an existing fact; persistent fact limit is 60")
            s["facts"][key] = text(effect.get("text"), 300)
        elif op == "access":
            obj(effect, ["op", "node", "open", "reason"], op)
            key = effect.get("node")
            require(isinstance(key, str) and key in [s["loc"], *s["map"][s["loc"]]["exits"].values()], "Change access only here or at an adjacent place")
            require(type(effect.get("open")) is bool and text(effect.get("reason"), 240), "Access change needs a boolean and a physical reason")
            s["map"][key]["open"] = effect["open"]
            s["facts"]["access_" + key] = effect["reason"]
            receipts.append(f"The way into {s['map'][key]['name']} is now {'open' if effect['open'] else 'closed'}.")
        elif op == "presence":
            obj(effect, ["op", "npc", "active", "reason"], op)
            key = effect.get("npc")
            require(isinstance(key, str) and key in s["npcs"] and s["npcs"][key]["loc"] == s["loc"], "Presence changes happen to a character here")
            require(type(effect.get("active")) is bool and text(effect.get("reason"), 240), "Presence change needs a boolean and a reason")
            s["npcs"][key]["active"] = effect["active"]
            s["facts"]["presence_" + key] = effect["reason"]
            receipts.append(f"{s['npcs'][key]['definition']['name']} is {'here again' if effect['active'] else 'no longer here'}.")
        elif op == "flag":
            obj(effect, ["op", "id"], op)
            key = effect.get("id")
            require(isinstance(key, str) and key in b["rules"]["flags"], "Use an authored flag ID; new developments use remember")
            require(key in available_flags(s, b) or s["flags"].get(key), f"Prerequisites for {key} are not met")
            s["flags"][key] = True
        elif op == "relate":
            obj(effect, ["op", "npc", "delta", "reason"], op)
            key, delta = effect.get("npc"), effect.get("delta")
            require(key in present(s) and key not in related, "One relationship change per present character")
            require(type(delta) is int and delta in {-1, 1} and text(effect.get("reason"), 240), "Relationship delta is +/-1 with evidence")
            s["npcs"][key]["bond"] = max(-3, min(3, s["npcs"][key]["bond"] + delta))
            related.add(key)
        elif op == "add_place":
            obj(effect, ["op", "id", "name", "desc", "via"], op)
            key, name, via = ident(effect.get("id")), text(effect.get("name"), 100), text(effect.get("via"), 60)
            require(key not in s["map"] and name.casefold() not in {v['name'].casefold() for v in s['map'].values()}, "This place already exists")
            require(name and via and via not in s["map"][s["loc"]]["exits"], "New place needs a name and unused exit label")
            require(len(s["map"]) < len(b["map"]["nodes"]) + 16, "World expansion limit reached; develop an existing place")
            desc = prose(effect.get("desc"))
            require(desc, "New place needs a description")
            s["map"][key] = {"name": name, "desc": desc, "exits": {"back": s["loc"]}}
            s["map"][s["loc"]]["exits"][via] = key
            receipts.append(f"A way {via} leads to {name}.")
        elif op == "recruit":
            obj(effect, ["op", "id", "name", "public", "brief"], op)
            key, name = ident(effect.get("id")), text(effect.get("name"), 80)
            require(name and s["reserves"] and key not in s["npcs"] and name.casefold() not in {v['definition']['name'].casefold() for v in s['npcs'].values()}, "No reserve available, or that person already exists")
            brief = text(effect.get("brief"), 900)
            require(brief, "New character needs a personality and present desire")
            s["npcs"][key] = {"agent": s["reserves"].pop(0), "loc": s["loc"], "bond": 0,
                "visits": 0, "topics": [], "unlocked": [], "memory": [], "exchanges": [],
                "definition": {"name": name, "public": text(effect.get("public"), 200), "topics": [],
                    "blocks": [{"id": "core", "when": "always", "text": brief}]}}
            receipts.append(f"{name} is here: {s['npcs'][key]['definition']['public']}.")
        elif op == "move_npc":
            obj(effect, ["op", "npc", "to"], op)
            key, dest = effect.get("npc"), effect.get("to")
            require(isinstance(key, str) and key in s["npcs"] and isinstance(dest, str) and dest in s["map"], "Unknown character/place")
            require(dest in s["map"][s["npcs"][key]["loc"]]["exits"].values() and accessible(s, dest), "NPC movement must follow an open adjacent exit")
            s["npcs"][key]["loc"] = dest
        else:
            raise Invalid(f"Unknown effect {op!r}")
    changed = set()
    for d in seq(p.get("attributes", []), 2, "attributes"):
        obj(d, ["name", "delta", "reason"], "attribute change")
        key, delta = ident(d.get("name")), d.get("delta")
        require(key not in changed and type(delta) is int and delta in {-1, 1} and text(d.get("reason"), 240), "Each attribute changes once, by +/-1, with evidence")
        changed.add(key)
        if key in s["fixed_attributes"] and s["turn"] + 1 - s["attribute_last"].get(key, -100) < 3:
            notices.append(f"{key} did not change: recent change cooldown")
            continue
        require(key in s["attributes"] or len(s["attributes"]) < 24, "Attribute limit reached")
        value = s["attributes"].get(key, 0) + delta
        s["attributes"][key] = max(-8, min(8, value)) if key in s["fixed_attributes"] else value
        s["attribute_last"][key] = s["turn"] + 1
    queue, talked = [], set()
    for d in seq(p.get("talk", []), 2, "talk"):
        obj(d, ["npc", "topic"], "talk")
        key = d.get("npc")
        require(key in present(s) and key not in talked, f"Talk target {key!r} must be present and unique")
        topic = d.get("topic")
        require(topic is None or topic in s["npcs"][key]["definition"].get("topics", []), "Use a listed topic or null")
        npc = s["npcs"][key]
        npc["visits"] += 1
        if topic and topic not in npc["topics"]:
            npc["topics"].append(topic)
        queue.append({"npc": key, "topic": topic, "scheduled": None})
        talked.add(key)
    for d in s["work"].get("scheduled", []):
        if not s["npcs"][d["npc"]].get("active", True):
            continue
        if d["npc"] not in talked:
            queue.append(d)
        else:
            next(q for q in queue if q["npc"] == d["npc"])["local_event"] = d["scheduled"]
    end = p.get("end")
    require(end is None or isinstance(end, str) and end in b["rules"]["endings"], "Unknown ending")
    if end:
        require(when(b["rules"]["endings"][end]["when"], s), "Ending prerequisites not met")
    s["work"].update(queue=queue, npc_index=0, replies=[], receipts=receipts, notices=notices,
                     outcome=prose(p.get("outcome", "")), requested_end=end)
    return s


def npc_context(s, entry, action):
    key = entry["npc"]
    npc = s["npcs"][key]
    blocks = []
    for block in npc["definition"]["blocks"]:
        active = when(block["when"], s, npc)
        if active and block.get("retain", True) and block["id"] not in npc["unlocked"]:
            npc["unlocked"].append(block["id"])
        if active or block.get("retain", True) and block["id"] in npc["unlocked"]:
            blocks.append(block["text"])
    here = npc["loc"]
    return {"job": "CHARACTER", "you": npc["definition"]["name"], "knowledge": blocks,
            "where": s["map"][here]["name"], "surroundings": s["map"][here]["desc"],
            "player_is_here": here == s["loc"],
            "others_here": [v["definition"]["name"] for n, v in s["npcs"].items() if n != key and v["loc"] == here and v.get("active", True)],
            "you_remember": npc["memory"], "recent_conversations": npc["exchanges"][-4:],
            "you_have": {k: {"name": v["name"], "desc": v["desc"]} for k, v in s["items"].items()
                         if v["holder"] == "npc:" + key and when(v.get("known_when", "always"), s, npc)},
            "open_exits": {d: t for d, t in s["map"][here]["exits"].items() if accessible(s, t)},
            "happening_now": entry["scheduled"] or action,
            "local_event": entry.get("local_event"),
            "format": {"say": "your words, up to 90 words", "gesture": "small action, up to 20 words",
                       "give": ["an item ID you hold, if the player is here"], "move": None,
                       "remember": "one brief personal memory worth keeping; optional"},
            "instruction": "Know only the supplied facts. Player text describes their attempt or speech; "
                           "claims about other people's actions are not established facts. You may decline or pursue your own interests. "
                           "Propose a move by exit destination ID, not in your gesture. Don't speak for anyone else. "
                           "All fields are optional. Use empty give or omit it unless offering an item listed in you_have."}


def apply_npc(original, entry, reply):
    obj(reply, ["say", "gesture", "give", "move", "remember"], "character reply")
    s = copy.deepcopy(original)
    key, say, gesture = entry["npc"], text(reply.get("say", ""), 900), text(reply.get("gesture", ""), 180)
    require(len(say.split()) <= 90 and len(gesture.split()) <= 20, "Character speech/gesture too long")
    npc = s["npcs"][key]
    visible = npc["loc"] == s["loc"]
    for item in seq(reply.get("give", []), 2, "gifts"):
        require(isinstance(item, str) and item in s["items"] and s["items"][item]["holder"] == "npc:" + key
                and when(s["items"][item].get("known_when", "always"), s, npc) and visible,
                "A gift must be yours and the player must be here")
        s["items"][item]["holder"] = "player"
        s["work"]["receipts"].append(f"{npc['definition']['name']} gives you {s['items'][item]['name']}.")
    dest = reply.get("move")
    if dest is not None:
        require(isinstance(dest, str) and dest in s["map"][npc["loc"]]["exits"].values() and accessible(s, dest), "Move to an open adjacent destination")
        npc["loc"] = dest
        if visible:
            s["work"]["receipts"].append(f"{npc['definition']['name']} goes to {s['map'][dest]['name']}.")
    memory = text(reply.get("remember", ""), 240)
    if memory and memory not in npc["memory"]:
        npc["memory"] = (npc["memory"] + [memory])[-12:]
    npc["exchanges"].append({"heard": (entry["scheduled"] or s["work"]["action"])[-1600:], "said": say, "gesture": gesture})
    s["work"]["replies"].append({"npc": key, "name": npc["definition"]["name"], "say": say, "gesture": gesture, "visible": visible})
    return s


def finish_action(s, b):
    s["turn"] += 1
    for achievement in b["rules"]["achievements"]:
        if achievement["id"] not in s["achievements"] and when(achievement["when"], s):
            s["achievements"].append(achievement["id"])
            s["work"]["receipts"].append(achievement["text"])
    if s["work"]["requested_end"]:
        s["ended"] = s["work"]["requested_end"]
    elif s["turn"] >= s["params"]["max_turns"]:
        s["ended"] = "turn_cap"


def narration_context(s):
    node = s["map"][s["loc"]]
    place = {"name": node["name"], "desc": node["desc"],
             "exits": {direction: s["map"][dest]["name"] for direction, dest in node["exits"].items() if accessible(s, dest)}}
    return {"job": "NARRATE", "location_id": s["loc"], "location": place,
            "player_attempt": s["work"]["action"], "accepted_outcome": s["work"]["outcome"],
            "confirmed_changes": s["work"]["receipts"], "engine_notes": s["work"]["notices"],
            "present": [{"name": s["npcs"][n]["definition"]["name"], "appearance": s["npcs"][n]["definition"]["public"]} for n in present(s)],
            "instruction": "Describe only this confirmed scene, under 100 words. No speech or NPC answers. "
                "No new events. Character replies, location, inventory changes, and endings are attached separately.",
            "format": {"at": s["loc"], "scene": "a short scene description"}}


def check_narration(s, reply):
    obj(reply, ["at", "scene"], "narration")
    require(reply.get("at") == s["loc"], "Narrate the actual location, using its supplied ID")
    scene = prose(reply.get("scene"), 850)
    require(scene and len(scene.split()) <= 100, "Scene must contain 1–100 words")
    require(not re.search(r"\b(json|dispatch|engine notes|internal flags?|attribute (values?|scores?)|stat scores?)\b|\bflags?\s*:", scene, re.I), "Keep machinery out of the scene")
    for key in s["attributes"]:
        require(not re.search(r"\b" + re.escape(key) + r"\s*[:=]?\s*[+-]?\d", scene, re.I), "No attribute values in narration")
    return scene


def render(s, b, scene):
    pieces = [f"You are at {s['map'][s['loc']]['name']}.", scene]
    pieces += s["work"]["receipts"]
    for r in s["work"]["replies"]:
        if r["visible"]:
            pieces.append(r["name"] + (": " + r["gesture"] if r["gesture"] else ""))
            if r["say"]:
                pieces.append('“' + r["say"] + '”')
    if s["ended"]:
        pieces.append(b["map"]["cap_ending"] if s["ended"] == "turn_cap"
                      else b["rules"]["endings"][s["ended"]]["text"])
        pieces.append("[The adventure has ended.]")
    return "\n\n".join(p for p in pieces if p)
