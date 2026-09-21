"""Inside the throwaway gate container: exercise the real blocking API and spools."""
import json
import os
from pathlib import Path
import subprocess

env = {"HOME": "/dispatch", "GATEWAY_SOCKET": "/run/gateway/gateway.sock", "PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"}
subprocess.run(["python3", "-B", "/runtime_pi/dispatchd.py"], user="dispatch", env=env, check=True, timeout=120)
state = json.loads(Path("/dispatch/state.json").read_text())
assert state["phase"] == "done" and state["turn"] == 3 and state["ended"] == "turn_cap"
assert state["loc"] == "tower" and state["achievements"] == ["a_clear_note"]
assert state["items"]["brace"]["holder"] == "player"
assert state["npcs"]["mara"]["visits"] == 1
assert len(state["transcript"]) == 8
assert state["transcript"][-1]["text"] == "Goodbye, Bellweather."
assert sum(e["kind"] == "rejected_plan" for e in state["events"]) == 1
assert not any(e["kind"] == "fallback" for e in state["events"])
transcript = Path("/dispatch/results/transcript.md").read_text()
assert "Take this brace. Two slots beside the clapper." in transcript
assert "[The adventure has ended.]" in transcript and "missing_place" not in transcript
assert "Goodbye, Bellweather." in transcript
mail = [json.loads(p.read_text())["text"] for p in sorted(Path("/agents/p/inbox_done").glob("*.json"))]
assert mail == [m["text"] for m in state["transcript"] if m["speaker"] == "Game master"]
npc_mail = "\n".join(p.read_text() for p in Path("/agents/m/inbox_done").glob("*.json"))
assert "clapper" in npc_mail and "Jun" not in npc_mail
assert subprocess.run(["su", "u_p", "-s", "/bin/sh", "-c", "cat /dispatch/state.json"], capture_output=True).returncode != 0
# Re-entering a finished dispatcher makes no additional player deliveries.
subprocess.run(["python3", "-B", "/runtime_pi/dispatchd.py"], user="dispatch", env=env, check=True, timeout=30)
assert json.loads(Path("/dispatch/state.json").read_text())["transcript"] == state["transcript"]
print("BELLWEATHER INTEGRATION: PASS (turns, correction, gated NPC, gift, ending, transcript, isolation, finished resume)")
