#!/usr/bin/env python3
"""Plain-mode gate asserts (inside the container after setup.sh). Zero tokens:
real gateway + dispatchd + real agentd, Pi replaced by fake_pi.py."""
import json
import subprocess
import sys

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


rc = subprocess.Popen(["python3", "/runtime_pi/dispatchd.py"], user="dispatch",
                      env={"HOME": "/dispatch", "GATEWAY_SOCKET": "/run/gateway/gateway.sock",
                           "PATH": "/usr/local/bin:/usr/bin:/bin"}).wait(timeout=120)
check("dispatchd exits clean", rc == 0, f"rc={rc}")
state = json.load(open("/dispatch/state.json"))
check("reply text arrived as the submission, both wakes",
      state["got"] == ["I look around.", "I walk north, whistling."], str(state["got"]))
calls = [json.loads(l) for l in open("/agents/a1/fake_pi.jsonl")]
check("two Pi turns", len(calls) == 2, str(len(calls)))
argv = calls[0]["argv"]
check("--no-tools passed", "--no-tools" in argv, str(argv))
sp = argv[argv.index("--system-prompt") + 1]
check("system prompt = md files only (no preamble/norms/scratchpad)",
      "How your world works" not in sp and "scratch" not in sp and "gateway" not in sp
      and "You are curious." in sp and "You are the player." in sp and "Everything you write" in sp, sp[:300])
check("first user prompt = FIRST_WAKE + payload, nothing else",
      calls[0]["prompt"] == "Welcome, this is your birth message.\n\nYou stand in a square. What do you do?", repr(calls[0]["prompt"]))
check("second user prompt = payload only",
      calls[1]["prompt"] == "You walk. Then what?", repr(calls[1]["prompt"]))
agentd_log = open("/agents/a1/agentd.log").read()
check("no submit refusals", "submit refused" not in agentd_log and "submit failed" not in agentd_log, agentd_log[-300:])
budget = [json.loads(l) for l in open("/data/gateway/budget.jsonl")]
check("two budget records, turn_ok, scratch_updated false",
      len(budget) == 2 and all(b.get("turn_ok") and not b.get("scratch_updated") for b in budget), str(budget)[:300])
audit = [json.loads(l) for l in open("/data/gateway/audit.jsonl")]
check("audit shows the agent's submits", sum(1 for e in audit if e.get("event") == "submit" and e.get("frm") == "a1") == 2)

print()
if FAIL:
    print(f"PLAIN GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print("PLAIN GATE: ALL PASS")
