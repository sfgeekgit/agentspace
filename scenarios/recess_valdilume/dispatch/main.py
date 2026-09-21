"""Resumable plan -> commit -> character -> narration -> player loop.

Only uses dispatchlib's public API. Nothing reads agent homes or runtime logs.
Model replies and each phase are checkpointed; the opening is not a player move.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import engine
import report


def stamp():
    return datetime.now(timezone.utc).isoformat()


def event(s, kind, message):
    s["events"].append({"ts": stamp(), "turn": s["work"].get("number", s["turn"]), "kind": kind, "text": message})


def exchange(api, s, agent, payload, player=False):
    """Checkpoint a request/reply. Recover a spooled answer before repeating a wake.

The public API has no atomic collect-and-save or delivery receipt. A crash in that
small external-call window can require redelivery; record it, never claim exactly-once.
"""
    if s["io"] is None:
        s["io"] = {"agent": agent, "payload": payload, "started": False, "raw": None, "message": None}
        api.save_state(s)
    io = s["io"]
    if io["raw"] is not None:
        return io["raw"]
    raw = api.collect(agent) if io["started"] else None
    if raw is None:
        if io["started"]:
            event(s, "redelivery", f"Resuming an unanswered {s['phase']} request to {agent}")
            if player and io["message"] is not None:
                s["transcript"][io["message"]]["delivery"] = "uncertain"
        if not player:
            api.roll_session(agent)  # Fresh prompts prevent old location/secret context from winning.
        io["started"] = True
        if player:
            io["message"] = len(s["transcript"])
            s["transcript"].append({"ts": stamp(), "turn": s["turn"], "speaker": "Game master",
                                    "text": io["payload"], "delivery": "pending"})
        api.save_state(s)
        completed = api.wake(agent, io["payload"])
        raw = api.collect(agent)
        if raw is None:
            raise RuntimeError(f"{agent} produced no submission in {s['phase']} (wake completed={completed}); resume to retry")
    io["raw"] = raw
    if player:
        s["transcript"][io["message"]]["delivery"] = "delivered"
        s["transcript"].append({"ts": stamp(), "turn": s["turn"], "speaker": "Player", "text": raw, "delivery": "delivered"})
    api.save_state(s)
    return raw


def prompt(context, error=None):
    if error:
        context = {**context, "CORRECTION": error + ". Nothing in that invalid reply was applied. Return the corrected object."}
    return json.dumps(context, ensure_ascii=False)


def run(api, params, home=None):
    home = Path(home) if home else Path.home()
    b = engine.load(home / "code" / "world")
    s = api.load_state(default=None)
    if s is None:
        roles = json.loads((home / "secrets.json").read_text())["roles"]
        s = engine.new_state(b, roles, params)
        api.save_state(s)
    # A resumed world uses its own captured parameters, never a new cap.
    s.pop("paused_reason", None)
    api.save_state(s)
    try:
        while s["phase"] != "done":
            phase = s["phase"]
            if phase == "opening":
                s["outgoing"] = b["map"]["opening"]
                s["phase"] = "receive"
            elif phase == "receive":
                action = exchange(api, s, s["player"], s["outgoing"], player=True)
                s["io"] = None
                if s["ended"]:
                    s["phase"] = "done"  # Final farewell is captured, but is not an 81st action.
                else:
                    engine.begin(s, b)
                    s["work"]["action"] = action
                    s["phase"] = "plan"
            elif phase == "plan":
                ctx = engine.planning_context(s, b, s["work"]["action"])
                raw = exchange(api, s, s["gm"], prompt(ctx, s["work"].get("plan_error")))
                try:
                    plan = engine.decode(raw)
                    updated = engine.apply_plan(s, b, plan)
                except engine.Invalid as exc:
                    event(s, "rejected_plan", str(exc))
                    s["io"] = None
                    if s["work"].get("plan_error") is None:
                        s["work"]["plan_error"] = str(exc)
                        api.save_state(s)
                        continue
                    updated = engine.apply_plan(s, b, {"outcome": "For a moment, nothing happens."})
                    event(updated, "fallback", "Two invalid plans; no player consequences applied")
                    plan = {}
                s = updated
                event(s, "accepted_plan", json.dumps(plan, ensure_ascii=False))
                for effect in plan.get("effects", []):
                    if effect["op"] in {"add_place", "recruit"}:
                        event(s, "expansion" if effect["op"] == "add_place" else "recruit", effect["name"])
                for notice in s["work"]["notices"]:
                    event(s, "bounded_attribute", notice)
                s["io"] = None
                s["phase"] = "characters"
            elif phase == "characters":
                w = s["work"]
                if w["npc_index"] < len(w["queue"]):
                    entry = w["queue"][w["npc_index"]]
                    ctx = engine.npc_context(s, entry, w["action"])
                    raw = exchange(api, s, s["npcs"][entry["npc"]]["agent"], prompt(ctx, w.get("npc_error")))
                    try:
                        updated = engine.apply_npc(s, entry, engine.decode(raw))
                    except engine.Invalid as exc:
                        event(s, "rejected_character", str(exc))
                        s["io"] = None
                        if not w.get("npc_error"):
                            w["npc_error"] = str(exc)
                            api.save_state(s)
                            continue
                        updated = engine.apply_npc(s, entry, {"gesture": "pauses for a moment."})
                        event(updated, "fallback", f"No valid reply from {entry['npc']}; character stays quiet")
                    s = updated
                    event(s, "scheduled_character" if entry["scheduled"] else "conversation", entry["npc"])
                    s["work"]["npc_index"] += 1
                    s["work"].pop("npc_error", None)
                    s["io"] = None
                else:
                    before = set(s["achievements"])
                    engine.finish_action(s, b)
                    for key in set(s["achievements"]) - before:
                        event(s, "achievement", key)
                    if s["ended"]:
                        event(s, "ending", s["ended"])
                    s["phase"] = "narrate"
            elif phase == "narrate":
                raw = exchange(api, s, s["gm"], prompt(engine.narration_context(s), s["work"].get("narration_error")))
                try:
                    scene = engine.check_narration(s, engine.decode(raw))
                except engine.Invalid as exc:
                    event(s, "rejected_narration", str(exc))
                    s["io"] = None
                    if not s["work"].get("narration_error"):
                        s["work"]["narration_error"] = str(exc)
                        api.save_state(s)
                        continue
                    scene = s["map"][s["loc"]]["desc"]
                    event(s, "fallback", "Used the saved place description after two invalid narrations")
                s["outgoing"] = engine.render(s, b, scene)
                s["recent"] = (s["recent"] + [{"action": s["work"]["action"], "result": s["outgoing"]}])[-3:]
                s["io"] = None
                s["phase"] = "receive"
            else:
                raise RuntimeError(f"Unknown saved phase: {phase}")
            api.save_state(s)
            if s["phase"] in {"receive", "done"}:
                report.export(s, home / "results")
    except Exception as exc:
        s["paused_reason"] = str(exc)
        event(s, "transport_error", str(exc))
        api.save_state(s)
        raise
    finally:
        report.export(s, home / "results")
