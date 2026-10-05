"""The game engine: pure functions over the state dict. Owns the map, positions,
attributes, inventory, flags, unlocks, schedules, and the transcript. Knows no
village facts — everything specific comes from the bundle or the GM."""
import json
import re

FALLBACK = "The world is quiet for a moment. What do you do?"
WORD_MIN = 2   # an attribute needs |value| >= this before it gets a word at all


def rank(stats, name, low=False):
    """1-based rank of `name` among all attributes by value (ties share the
    better rank): 1 = highest, or 1 = lowest with low=True."""
    v = stats.get(name, 0)
    return 1 + sum(1 for x in stats.values() if (x < v if low else x > v))


def words(stats):
    """Relative bands: among attributes that have moved at least WORD_MIN, the
    top one is 'very high' and the next two 'high'; among those at or below
    -WORD_MIN, the lowest is 'very low' and the next two 'low'. Everything else
    gets no word. Inflation cannot saturate this: only three can be high."""
    out = {}
    hi = sorted((k for k, v in stats.items() if v >= WORD_MIN), key=lambda k: -stats[k])
    lo = sorted((k for k, v in stats.items() if v <= -WORD_MIN), key=lambda k: stats[k])
    for i, k in enumerate(hi[:3]):
        out[k] = "very high" if i == 0 else "high"
    for i, k in enumerate(lo[:3]):
        out[k] = "very low" if i == 0 else "low"
    return out


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
        ok = p.get("min", v) <= v <= p.get("max", v)
        if "rank" in p:        # must be one of the player's top-N attributes
            ok = ok and rank(pl["stats"], p["stat"]) <= p["rank"]
        if "rank_low" in p:    # must be one of the bottom-N
            ok = ok and rank(pl["stats"], p["stat"], low=True) <= p["rank_low"]
        return ok
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


def notice(state, text):
    """Feedback for the GM's next wake: what the engine could not apply and why."""
    state.setdefault("notices", []).append(text)


def _norm(x):
    return str(x).strip().lower().replace("_", " ")


def resolve_move(state, mv):
    """A move may name the exit, the target node id, or the target node name."""
    ex = exits(state)
    for d, t in ex.items():
        if _norm(mv) in (_norm(d), _norm(t), _norm(state["map"][t]["name"])):
            return d
    return None


def path_to(state, target):
    """Shortest route (list of node ids, excluding the start) from the player to
    `target` over non-hidden nodes, or None. Lets the GM name any known place as
    a destination; the engine walks the graph (run 3: the GM named 'fields' and
    'mill_yard' from two hops away and the player never moved)."""
    start = state["player"]["loc"]
    prev, queue = {start: None}, [start]
    while queue:
        cur = queue.pop(0)
        if cur == target:
            path = []
            while cur != start:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        for t in state["map"][cur].get("exits", {}).values():
            if t not in prev and not state["map"][t].get("hidden"):
                prev[t] = cur
                queue.append(t)
    return None


def resolve_npc(state, name):
    """NPC keys are lower-case ids; the GM may use the display name or any case."""
    for n, v in state["npcs"].items():
        if _norm(name) in (_norm(n), _norm(v["def"]["name"])):
            return n
    return None


def find_node(state, ref):
    for nid, v in state["map"].items():
        if _norm(ref) in (_norm(nid), _norm(v["name"])):
            return nid
    return None


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


# Baked into the GM's ROLE.md at build (logic.fill_briefing), not sent every turn.
FORMAT = ('Reply with the narration (prose only), then a ```json block: {"stats": {"name": delta}, '
          '"move": "<exit or null>", "items": {"take": [], "drop": [], "create": []}, '
          '"flags": {"name": true}, "map": [{"op": "open|close", "node": "<id>"} or '
          '{"op": "add", "id": "<new id>", "name": "...", "desc": "...", "via": "<direction from here>"}], '
          '"move_npc": {"<npc>": "<node>"}, "talk": [{"npc": "<npc>", "hears": "..."}], '
          '"assign_reserve": {"name": "...", "brief": "...", "loc": "<node>"} or null, '
          '"end": "<end name>" or null}. Every key is optional; omit the block if nothing changes.')


def gm_context(state, bundle, action, params, npc_lines=None, scheduled=None):
    pl = state["player"]
    node = state["map"][pl["loc"]]
    sec = [f"TURN {state['turn'] + 1} of {params['max_turns']}."]
    who = [f"{n} ({state['npcs'][n]['def']['public']})" for n in present(state)]
    sec.append(f"LOCATION {pl['loc']} — {node['name']}. {node['desc']}\n"
               f"Exits: " + (", ".join(f"{d} -> {state['map'][t]['name']}" for d, t in exits(state).items()) or "none")
               + f"\nItems here: {', '.join(node.get('items', [])) or 'none'}"
               + f"\nPresent: {', '.join(who) or 'nobody'}")
    places = ", ".join(f"{nid} ({v['name']})" for nid, v in state["map"].items() if not v.get("hidden"))
    people = ", ".join(f"{n} ({v['def']['name']}) at {v['loc']}" for n, v in state["npcs"].items())
    sec.append(f"WORLD places (use these; do not add a place that already exists): {places}\n"
               f"WORLD people (the only characters with voices; bring one here with move_npc, or assign a reserve — "
               f"{len(state['reserves'])} left): {people or 'nobody'}")
    if state.get("notices"):
        sec.append("ENGINE NOTES (what the world did with your last reply; correct anything it could not apply):\n- " + "\n- ".join(state["notices"]))
        state["notices"] = []
    stats = [f"{k} {w}" for k, w in words(pl["stats"]).items()]
    sec.append(f"PLAYER carries: {', '.join(pl['inventory']) or 'nothing'}. "
               f"Notable: {', '.join(stats) or 'nothing yet'}.")
    recent = state["transcript"][-3:]
    if recent:
        sec.append("RECENT:\n" + "\n".join(
            (f"> {t['in']}\n" if t["in"] else "") + t["out"] for t in recent))
    notes = _notes(state, bundle) + [
        "Flags the world listens for (set exactly these names when they become true; other flags are ignored by the world): "
        + ", ".join(sorted(bundle["flags"] - bundle["ending_flags"]))]
    sec.append("CONTENT NOTES:\n- " + "\n- ".join(notes))
    if scheduled:
        sec.append("SCHEDULED EVENT (weave into this turn): " + scheduled)
    sec.append("PLAYER SAYS: " + (action if action else "(the player says and does nothing)"))
    if npc_lines:
        sec.append("NPC REPLIES (now give your final response; no more `talk`):\n" +
                   "\n".join(f"{n}: {line}" for n, line in npc_lines))
    sec.append("Reply: narration, then the ```json block (format and endings are in your briefing).")
    return "\n\n".join(sec)


FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_gm(raw):
    """Narration prose + optional fenced JSON block -> proposal dict with
    "narration". None (= ask again) only if empty or the block is malformed."""
    if not raw or not raw.strip():
        return None
    m = FENCE.search(raw)
    if not m:
        return {"narration": raw.strip()}
    try:
        d = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    d["narration"] = (raw[:m.start()] + raw[m.end():]).strip()
    return d


# ---- applying a GM turn ----

def apply(state, parsed, log, bundle):
    """Apply every proposal in a parsed GM document. Bad pieces are dropped with a
    log line, never a failed turn. Returns the narration."""
    pl = state["player"]
    ends = bundle["quests"].get("ends", {})
    ending_flags = {q["when"].get("flag") for q in ends.values() if "flag" in q["when"]}
    for k, dv in (parsed.get("stats") or {}).items():
        if isinstance(k, str) and isinstance(dv, int):
            if k not in pl["stats"]:
                pl["stats"][k] = 0
                state["created_stats"].append({"turn": state["turn"], "stat": k})
                log("new_stat", k)
            pl["stats"][k] += dv
            log("stat", f"{k} {dv:+d} -> {pl['stats'][k]}")
    items = {k: names(v) for k, v in (parsed.get("items") or {}).items()}
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
        if k in ending_flags and v:
            why = _ending_blocked(state, bundle, k)
            if why:
                notice(state, f"flag {k!r} ignored: {why}")
                log("bad_flag", k)
                continue
        state["flags"][k] = v
    for n, loc in (parsed.get("move_npc") or {}).items():
        rn, rl = resolve_npc(state, n), find_node(state, loc)
        if rn and rl:
            state["npcs"][rn]["loc"] = rl
            log("move_npc", f"{rn} -> {rl}")
        else:
            notice(state, f"move_npc {n!r} -> {loc!r} ignored: " + ("no such person" if not rn else "no such place"))
    apply_move(state, parsed.get("move"), log)
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


def names(lst):
    """Item lists must hold strings; the GM sometimes sends {"name", "desc"} objects."""
    out = []
    for it in lst if isinstance(lst, list) else []:
        if isinstance(it, dict) and isinstance(it.get("name"), str):
            it = it["name"]
        if isinstance(it, str) and it.strip():
            out.append(it.strip())
    return out


def _ending_blocked(state, bundle, flag):
    """An ending flag may only be set once (a) the run is past the ending's
    min_turn and (b) some currently-unlocked content note names the flag — i.e.
    the world itself says this ending is on the table. Run 3: the GM, shown the
    flag names, railroaded the inheritance ending by turn 21."""
    ends = bundle["quests"].get("ends", {})
    for e, q in ends.items():
        if q["when"].get("flag") == flag and state["turn"] < q.get("min_turn", 0):
            return f"it is far too early for that ending (not before turn {q['min_turn']})"
    if not any(flag in t for t in _notes(state, bundle)):
        return "nothing in the world's current notes makes that ending possible yet; let it be earned"
    return None


def voiced_without_talk(state, narration, talked):
    """Names of WORLD people who appear next to quoted speech in the narration
    although the GM did not `talk` to them this turn (heuristic)."""
    out = []
    for n, v in state["npcs"].items():
        if n in talked:
            continue
        name = v["def"]["name"].split()[-1]
        for sent in re.split(r"(?<=[.!?])\s+", narration):
            if name in sent and ('"' in sent or "\u201c" in sent) and re.search(r"\b(say|says|said|asks|asked|answers|answered|calls|called|mutters|replies|replied)\b", sent):
                out.append(n)
                break
    return out


def apply_move(state, mv, log):
    """Move the player: by exit, by adjacent place, or by path to any known place.
    Applied BEFORE any `talk` in the same reply, so 'you push the shop door and
    Nunzia says…' finds Nunzia present."""
    if not mv:
        return
    pl = state["player"]
    d = resolve_move(state, mv)
    target = find_node(state, mv)
    if d:
        pl["loc"] = exits(state)[d]
        log("move", f"{mv} -> {pl['loc']}")
        return
    if target == pl["loc"]:
        return   # already there
    route = path_to(state, target) if target else None
    if route:
        pl["loc"] = route[-1]
        log("move", f"{mv} -> {pl['loc']} via {' > '.join(route)}")
        if len(route) > 1:
            notice(state, f"move {mv!r}: the player walked {' > '.join(route)} to get there; "
                          f"narrate the way if it matters. The player is now at {pl['loc']}.")
    else:
        log("bad_move", str(mv))
        notice(state, f"move {mv!r} is not a known place reachable from {pl['loc']}; the player did NOT move. "
                      f"Exits: {', '.join(f'{k} -> {v}' for k, v in exits(state).items())}")


def _map_op(state, op, log):
    m = state["map"]
    if op.get("op") in ("open", "close") and find_node(state, op.get("node", "")):
        nid = find_node(state, op["node"])
        m[nid]["hidden"] = op["op"] == "close"
        log("map", f"{op['op']} {nid}")
    elif op.get("op") == "add" and op.get("id") and op.get("via"):
        dup = find_node(state, op["id"]) or (op.get("name") and find_node(state, op["name"]))
        if dup:
            notice(state, f"map add {op['id']!r} ignored: that place already exists as {dup!r}; move there instead")
            return
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
    sec.append("Reply with only what you say or do, under 120 words.")
    return "\n\n".join(sec)


def transcript_md(state, env=""):
    t = state["transcript"]
    head = f"# Transcript{' — ' + env if env else ''}\n\n{len(t)} turns; ended: {state['ended'] or 'turn cap'}.\n"
    body = "\n\n".join((f"> {e['in']}\n\n" if e["in"] else "") + e["out"] for e in t)
    return head + "\n" + body + "\n"
