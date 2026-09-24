#!/usr/bin/env python3
"""End-of-run results for a recess_borgo_mean env (run on the HOST):

    python3 scenarios/recess_borgo_mean/results.py <env>     # -> $AGENTSPACE_RESULTS_DIR/<env>/

Writes transcript.md (the transcript and nothing else), state.json (the engine's
final state, verbatim), game_log.jsonl, and summary.md (end state in words +
notable events, all from state/log, no LLM). Appends to runs.jsonl. Does not commit."""
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "dispatch"))
import engine  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    env = sys.argv[1]
    out = Path(os.environ.get("AGENTSPACE_RESULTS_DIR", "/opt/agentspace-results")) / env
    cat = lambda p: subprocess.run(["docker", "exec", env, "cat", p], capture_output=True, text=True, check=True).stdout
    state = json.loads(cat("/dispatch/state.json"))
    log = [json.loads(l) for l in cat("/dispatch/game_log.jsonl").splitlines() if l.strip()]

    out.mkdir(parents=True, exist_ok=True)
    (out / "transcript.md").write_text(engine.transcript_md(state, env))
    (out / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    (out / "game_log.jsonl").write_text("\n".join(json.dumps(e) for e in log) + "\n")

    pl = state["player"]
    s = [f"# {env} — summary\n",
         f"Turns: {state['turn']}. Ended: {state['ended'] or 'turn cap'}.",
         f"Final location: {state['map'][pl['loc']]['name']} ({pl['loc']}).",
         f"Inventory: {', '.join(pl['inventory']) or 'nothing'}.",
         "\n## Attributes\n"] + [f"- {k}: {v} ({engine.words(pl['stats']).get(k, 'unremarkable')})" for k, v in pl["stats"].items()]
    s += ["\n## Flags\n"] + ([f"- {k} = {v}" for k, v in state["flags"].items()] or ["- none"])
    s += ["\n## NPCs\n"] + [f"- {n}: {'met' if v['met'] else 'never met'}, at {v['loc']}, unlocked {v['unlocked']}"
                            for n, v in state["npcs"].items()]
    s += [f"\nReserves unassigned: {state['reserves'] or 'none'}.", "\n## Notable events\n"]
    kinds = ("new_stat", "unlock", "map", "reserve", "schedule", "end", "bad_move", "gm_parse_error", "move_npc")
    s += [f"- turn {e['turn']}: {e['text']}" for e in log if e.get("kind") in kinds] or ["- none"]
    (out / "summary.md").write_text("\n".join(s) + "\n")

    runs = out.parent / "runs.jsonl"
    old = [l for l in runs.read_text().splitlines() if l.strip() and json.loads(l).get("run_name") != env] \
        if runs.exists() else []
    line = {"run_name": env, "scen": "recess_borgo_mean", "status": state["ended"] or "capped",
            "turns": state["turn"], "path": f"{env}/transcript.md"}
    runs.write_text("\n".join(old + [json.dumps(line)]) + "\n")
    print(out)


if __name__ == "__main__":
    main()
