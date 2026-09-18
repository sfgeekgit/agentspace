#!/usr/bin/env python3
"""Stand-in for the Pi binary in the plain-mode gate: speaks just enough of the
RPC protocol (one prompt in, message_end + agent_end out), records what agentd
handed it (argv incl. --system-prompt, the user prompt) to $HOME/fake_pi.jsonl,
and replies with the next line of $HOME/moves as its assistant text."""
import json
import os
import sys

home = os.environ["HOME"]
prompt = json.loads(sys.stdin.readline())["message"]
with open(os.path.join(home, "fake_pi.jsonl"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
moves = os.path.join(home, "moves")
lines = open(moves).read().splitlines()
reply = lines[0] if lines else ""
open(moves, "w").write("\n".join(lines[1:]) + ("\n" if lines[1:] else ""))
msg = {"role": "assistant", "stopReason": "stop",
       "content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": reply}],
       "usage": {"input": 10, "output": 5, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0.0}}}
print(json.dumps({"type": "message_end", "message": msg}), flush=True)
print(json.dumps({"type": "agent_end"}), flush=True)
