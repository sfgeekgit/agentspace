"""recess_mvp dispatcher — the turn loop. One turn = the player's submitted action ->
GM wake (context) -> optional NPC wakes + GM re-wake -> apply -> deliver
narration to the player. Resumable: state is saved before every delivery."""
import json
import random
from datetime import datetime, timezone
from pathlib import Path

import bundle as bundle_mod
import engine

HOME = Path.home()


def glog(kind, text, turn):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with (HOME / "game_log.jsonl").open("a") as f:
        f.write(json.dumps({"ts": ts, "turn": turn, "kind": kind, "text": f"[{kind}] {text}"}) + "\n")


def run(api, params):
    b = bundle_mod.load(HOME / "code" / "world")
    roles = json.loads((HOME / "secrets.json").read_text())["roles"]
    state = api.load_state(default=None)
    if state is None:
        state = engine.new_state(b, roles, params, random.getrandbits(32))
        api.save_state(state)
        glog("start", f"npcs={sorted(state['npcs'])} reserves={state['reserves']} params={params}", 0)
    log = lambda kind, text: glog(kind, text, state["turn"])
    player, gm = state["player"]["agent"], state["gm"]
    max_turns = int(params["max_turns"])

    def ask_gm(ctx):
        api.wake(gm, ctx)
        parsed = engine.parse_gm(api.collect(gm))
        if parsed is None:
            log("gm_parse_error", "retrying once")
            api.wake(gm, "Your last reply was empty or its ```json block was not valid JSON. "
                         "Reply again: narration, then the block.\n\n" + ctx)
            parsed = engine.parse_gm(api.collect(gm))
        return parsed or {"narration": engine.FALLBACK}

    def deliver():
        out = state["pending_out"]
        api.wake(player, out + ("\n\n[The game has ended.]" if state["ended"] else ""))
        state["pending_out"] = None
        api.save_state(state)

    for a in [gm] + [n["agent"] for n in state["npcs"].values()]:
        api.collect(a)   # drain strays
    if state["pending_out"]:
        deliver()   # resumed between save and delivery

    while not state["ended"] and state["turn"] < max_turns:
        action = None if state["turn"] == 0 else api.collect(player, default="")
        log("player_in", action or "")
        parsed = ask_gm(engine.gm_context(state, b, action, params))
        lines = []
        for t in parsed.get("talk") or []:
            n = t.get("npc")
            if n in engine.present(state) and t.get("hears"):
                npc = state["npcs"][n]
                api.wake(npc["agent"], engine.npc_payload(state, n, t["hears"]))
                line = api.collect(npc["agent"], default="(says nothing)")
                api.roll_session(npc["agent"])
                npc["met"] = True
                npc["notes"] += [f"heard: {t['hears'][:300]}", f"you said: {line[:300]}"]
                lines.append((n, line))
                log("npc", f"{n}: {line}")
        if lines:
            parsed = ask_gm(engine.gm_context(state, b, action, params, npc_lines=lines))
        narration = engine.apply(state, parsed, log, b["quests"]["ends"])
        state["turn"] += 1
        engine.unlock(state, log)
        for i, s in engine.due_schedules(state, b):
            state["fired"].append(i)
            npc = state["npcs"][s["npc"]]
            api.wake(npc["agent"], engine.npc_payload(state, s["npc"], s["hears"]))
            line = api.collect(npc["agent"], default="(acts silently)")
            api.roll_session(npc["agent"])
            log("schedule", f"{s['npc']}: {line}")
            patch = ask_gm(engine.gm_context(
                state, b, "(continue the scene)", params,
                scheduled=f"{s['npc']} says/does: {line}. {s['gm_note']} Narrate only what "
                          f"the player perceives now, as a short addition."))
            narration += "\n\n" + engine.apply(state, patch, log, b["quests"]["ends"])
        engine.check_end(state, b, log)
        state["transcript"].append({"turn": state["turn"], "in": action, "out": narration})
        state["pending_out"] = narration
        api.save_state(state)
        log("gm_out", narration)
        deliver()
        if state["turn"] % 30 == 0:
            api.roll_session(gm)

    (HOME / "transcript.md").write_text(engine.transcript_md(state))
    log("game_over", f"turn {state['turn']}, ended={state['ended'] or 'cap'}")
    api.save_state(state)
