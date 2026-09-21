"""Deterministic, spoiler-separated exports from one consistent state snapshot."""
import collections
import json
import os
from pathlib import Path


def atomic(path, value):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def export(state, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    messages = state["transcript"]
    # Text is preserved, not summarized or blockquoted (multiline replies matter).
    transcript = "\n\n".join(f"### {m['speaker']}\n\n{m['text']}" for m in messages
                              if m.get("delivery", "delivered") == "delivered") + "\n"
    atomic(directory / "transcript.md", transcript)
    atomic(directory / "transcript.jsonl", "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages))
    atomic(directory / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    atomic(directory / "game_log.jsonl", "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in state["events"]))
    counts = collections.Counter(e["kind"] for e in state["events"])
    status = "complete" if state["phase"] == "done" else "paused" if state.get("paused_reason") else "in progress"
    summary = [f"# {state['title']} — results", f"Status: {status}. Completed player moves: {state['turn']}/{state['params']['max_turns']}.",
               f"Ending: {state['ended'] or 'none'}. Location: {state['map'][state['loc']]['name']}.",
               "## Characters"]
    for name, npc in state["npcs"].items():
        summary.append(f"- {name}: {npc['visits']} direct conversations; relationship {npc['bond']}; at {npc['loc']}; revealed {', '.join(npc['unlocked']) or 'nothing'}.")
    summary += ["## Attributes"] + [f"- {k}: {v}" for k, v in state["attributes"].items()]
    summary += ["## Accomplishments", ", ".join(state["achievements"]) or "None.", "## Persistent developments"]
    summary += [f"- {v}" for v in state["facts"].values()] or ["None."]
    summary += ["## Engine health"] + [f"- {k}: {v}" for k, v in sorted(counts.items())]
    uncertain = sum(m.get("delivery") != "delivered" for m in messages)
    if uncertain:
        summary.append(f"- {uncertain} player deliveries are pending or uncertain; see transcript.jsonl and the runtime audit.")
    if state.get("paused_reason"):
        summary.append(f"Paused: {state['paused_reason']}")
    summary += ["## Notable events"] + [f"- move {e['turn']}: {e['text']}" for e in state["events"]
        if e["kind"] in {"rejected_plan", "fallback", "expansion", "recruit", "achievement", "ending", "transport_error"}]
    atomic(directory / "summary.md", "\n\n".join(summary) + "\n")

