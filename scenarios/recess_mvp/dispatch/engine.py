"""The game engine: pure functions over the state dict. Owns the map, positions,
attributes, inventory, flags, unlocks, schedules, and the transcript. Knows no
village facts — everything specific comes from the bundle or the GM."""
import json

BANDS = [(-5, "very low"), (-1, "low"), (0, "neutral"), (4, "high")]
FALLBACK = "The world is quiet for a moment. What do you do?"


def band(v):
    for top, name in BANDS:
        if v <= top:
            return name
    return "very high"


# ---- predicates ----

def pred(p, state, npc=None):
    if p == "always":
        return True
    if "all" in p:
        return all(pred(q, state, npc) for q in p["all"])
    if "any" in p:
        return any(pred(q, state, npc) for q in p["any"])
    pl = state["player"]
    if "stat" in p:
        v = pl["stats"].get(p["stat"], 0)
        return p.get("min", v) <= v <= p.get("max", v)
    if "flag" in p:
        return bool(state["flags"].get(p["flag"]))
    if "not_flag" in p:
        return not state["flags"].get(p["not_flag"])
    if "turn_min" in p:
        return state["turn"] >= p["turn_min"]
    if "met" in p:
        return bool(npc and npc["met"]) == p["met"]
    if "loc" in p:
        return pl["loc"] == p["loc"]
    return False


# ---- state ----

def new_state(bundle, roles, params, seed):
    by_role = {r: a for a, r in roles.items()}
    attrs = [x.strip() for x in params["attributes"].split(",") if x.strip()]
    chosen = [x.strip() for x in params["npcs"].split(",") if x.strip()]
    npcs = {}
    for name in chosen:
        d = bundle["npcs"][name]
        npcs[name] = {"agent": by_role[f"npc_{name}"], "loc": d["loc"], "met": False,
                      "notes": [], "unlocked": [], "def": d}
    return {
        "seed": seed, "turn": 0, "ended": None, "pending_out": None,
        "player": {"agent": by_role["player"], "loc": bundle["map"]["start"],
                   "inventory": [], "stats": {a: 0 for a in attrs}},
        "gm": by_role["gm"],
        "npcs": npcs,
        "reserves": sorted(a for a, r in roles.items() if r == "reserve"),
        "map": json.loads(json.dumps(bundle["map"]["nodes"])),
        "flags": {}, "fired": [], "transcript": [], "created_stats": [],
    }


def present(state, loc=None):
    loc = loc or state["player"]["loc"]
    return [n for n, v in state["npcs"].items() if v["loc"] == loc]


def exits(state):
    node = state["map"][state["player"]["loc"]]
    return {d: t for d, t in node.get("exits", {}).items() if not state["map"][t].get("hidden")}


# ---- GM context ----

def _notes(state, bundle):
    out = []
    node = state["map"][state["player"]["loc"]]
    if node.get("gm_notes"):
        out.append(node["gm_notes"])
    for n in present(state):
        npc = state["npcs"][n]
        for note in npc["def"].get("gm_notes", []):
            if pred(note["unlock"], state, npc):
                out.append(f"{npc['def']['name']}: {note['text']}")
    for arc in bundle["quests"].get("arcs", []):
        for note in arc.get("gm_notes", []):
            if pred(note["unlock"], state):
                out.append(f"({arc['name']}) {note['text']}")
    return out


FORMAT = ('RESPOND WITH one `submit \'<json>\'`: {"narration": "...", "stats": {"name": delta}, '
          '"move": "<exit or null>", "items": {"take": [], "drop": [], "create": []}, '
          '"flags": {"name": true}, "map": [{"op": "open|close", "node": "<id>"} or '
          '{"op": "add", "id": "<new id>", "name": "...", "desc": "...", "via": "<direction from here>"}], '
          '"move_npc": {"<npc>": "<node>"}, "talk": [{"npc": "<npc>", "hears": "..."}], '
          '"assign_reserve": {"name": "...", "brief": "...", "loc": "<node>"} or null, '
          '"end": "<end name>" or null}. Only "narration" is required. Keep it under 3500 bytes.')


def gm_context(state, bundle, action, params, npc_lines=None, scheduled=None):
    pl = state["player"]
    node = state["map"][pl["loc"]]
    sec = [f"TURN {state['turn'] + 1} of {params['max_turns']}."]
    who = [f"{n} ({state['npcs'][n]['def']['public']})" for n in present(state)]
    sec.append(f"LOCATION {pl['loc']} — {node['name']}. {node['desc']}\n"
               f"Exits: " + (", ".join(f"{d} -> {state['map'][t]['name']}" for d, t in exits(state).items()) or "none")
               + f"\nItems here: {', '.join(node.get('items', [])) or 'none'}"
               + f"\nPresent: {', '.join(who) or 'nobody'}")
    stats = [f"{k} {band(v)}" for k, v in pl["stats"].items() if band(v) != "neutral"]
    sec.append(f"PLAYER carries: {', '.join(pl['inventory']) or 'nothing'}. "
               f"Notable: {', '.join(stats) or 'nothing yet'}.")
    recent = state["transcript"][-3:]
    if recent:
        sec.append("RECENT:\n" + "\n".join(
            (f"> {t['in']}\n" if t["in"] else "") + t["out"] for t in recent))
    ends = [f"{e}: {q['gm_note']}" for e, q in bundle["quests"].get("ends", {}).items()]
    notes = _notes(state, bundle) + [f"Possible endings (use `end` only when truly reached): " + "; ".join(ends)]
    sec.append("CONTENT NOTES:\n- " + "\n- ".join(notes))
    if scheduled:
        sec.append("SCHEDULED EVENT (weave into this turn): " + scheduled)
    sec.append("PLAYER SAYS: " + (action if action else "(the player says and does nothing)"))
    if npc_lines:
        sec.append("NPC REPLIES (now give your final response; no more `talk`):\n" +
                   "\n".join(f"{n}: {line}" for n, line in npc_lines))
    sec.append(FORMAT)
    return "\n\n".join(sec)


def parse_gm(raw):
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except ValueError:
        return None
    return d if isinstance(d, dict) and isinstance(d.get("narration"), str) else None


# ---- applying a GM turn ----

def apply(state, parsed, log, ends=()):
    """Apply every proposal in a parsed GM document. Bad pieces are dropped with a
    log line, never a failed turn. Returns the narration."""
    pl = state["player"]
    for k, dv in (parsed.get("stats") or {}).items():
        if isinstance(k, str) and isinstance(dv, int):
            if k not in pl["stats"]:
                pl["stats"][k] = 0
                state["created_stats"].append({"turn": state["turn"], "stat": k})
                log("new_stat", k)
            pl["stats"][k] += dv
            log("stat", f"{k} {dv:+d} -> {pl['stats'][k]}")
    items = parsed.get("items") or {}
    node = state["map"][pl["loc"]]
    for it in items.get("take", []):
        if it in node.get("items", []):
            node["items"].remove(it)
        if it not in pl["inventory"]:
            pl["inventory"].append(it)
    for it in items.get("create", []):
        if it not in pl["inventory"]:
            pl["inventory"].append(it)
    for it in items.get("drop", []):
        if it in pl["inventory"]:
            pl["inventory"].remove(it)
            node.setdefault("items", []).append(it)
    for k, v in (parsed.get("flags") or {}).items():
        state["flags"][k] = v
    for n, loc in (parsed.get("move_npc") or {}).items():
        if n in state["npcs"] and loc in state["map"]:
            state["npcs"][n]["loc"] = loc
            log("move_npc", f"{n} -> {loc}")
    mv = parsed.get("move")
    if mv:
        if mv in exits(state):
            pl["loc"] = exits(state)[mv]
            log("move", f"{mv} -> {pl['loc']}")
        else:
            log("bad_move", mv)
    for op in parsed.get("map") or []:
        _map_op(state, op, log)
    r = parsed.get("assign_reserve")
    if r and state["reserves"] and r.get("name") and r.get("brief"):
        _assign_reserve(state, r, log)
    end = parsed.get("end")
    if end in ends:
        state["ended"] = end
        log("end", end)
    elif end:
        log("bad_end", str(end))
    return parsed["narration"]


def _map_op(state, op, log):
    m = state["map"]
    if op.get("op") in ("open", "close") and op.get("node") in m:
        m[op["node"]]["hidden"] = op["op"] == "close"
        log("map", f"{op['op']} {op['node']}")
    elif op.get("op") == "add" and op.get("id") and op["id"] not in m and op.get("via"):
        here = state["player"]["loc"]
        m[op["id"]] = {"name": op.get("name", op["id"]), "desc": op.get("desc", ""),
                       "exits": {"back": here}, "items": []}
        m[here].setdefault("exits", {})[op["via"]] = op["id"]
        log("map", f"add {op['id']} via {op['via']} from {here}")


def _assign_reserve(state, r, log):
    agent = state["reserves"].pop(0)
    name = "".join(c for c in r["name"].lower().replace(" ", "_") if c.isalnum() or c == "_")
    base = name or "stranger"
    name, i = base, 2
    while name in state["npcs"]:
        name, i = f"{base}{i}", i + 1
    loc = r.get("loc") if r.get("loc") in state["map"] else state["player"]["loc"]
    state["npcs"][name] = {
        "agent": agent, "loc": loc, "met": False, "notes": [], "unlocked": [],
        "def": {"name": r["name"], "public": r.get("public", r["name"]),
                "blocks": [{"id": "core", "unlock": "always", "text": r["brief"]}], "gm_notes": []}}
    log("reserve", f"{agent} becomes {name} at {loc}")


def unlock(state, log):
    for n, npc in state["npcs"].items():
        for blk in npc["def"].get("blocks", []):
            if blk["id"] not in npc["unlocked"] and pred(blk["unlock"], state, npc):
                npc["unlocked"].append(blk["id"])
                log("unlock", f"{n}:{blk['id']}")


def due_schedules(state, bundle):
    out = []
    for i, s in enumerate(bundle["schedule"]):
        if s["turn"] == state["turn"] and i not in state["fired"] and s["npc"] in state["npcs"] \
                and not (s.get("unless") and pred(s["unless"], state)):
            out.append((i, s))
    return out


def check_end(state, bundle, log):
    if state["ended"]:
        return
    for e, q in bundle["quests"].get("ends", {}).items():
        if pred(q["when"], state):
            state["ended"] = e
            log("end", e)
            return


# ---- NPC payload ----

def npc_payload(state, name, hears):
    npc = state["npcs"][name]
    d = npc["def"]
    blocks = [b["text"] for b in d.get("blocks", []) if b["id"] in npc["unlocked"]]
    others = [state["npcs"][o]["def"]["name"] for o in present(state, npc["loc"]) if o != name]
    node = state["map"][npc["loc"]]
    sec = [f"YOU ARE {d['name']}.\n" + "\n\n".join(blocks),
           f"WHERE: {node['name']}. {node['desc']} Also here: a stranger (the player)"
           + (", " + ", ".join(others) if others else "") + "."]
    if npc["notes"]:
        sec.append("YOU REMEMBER:\n" + "\n".join(npc["notes"][-6:]))
    sec.append("YOU HEAR: " + hears)
    sec.append('RESPOND WITH one `submit "<what you say or do>"`, under 120 words.')
    return "\n\n".join(sec)


def transcript_md(state, env=""):
    t = state["transcript"]
    head = f"# Transcript{' — ' + env if env else ''}\n\n{len(t)} turns; ended: {state['ended'] or 'turn cap'}.\n"
    body = "\n\n".join((f"> {e['in']}\n\n" if e["in"] else "") + e["out"] for e in t)
    return head + "\n" + body + "\n"
