#!/usr/bin/env python3
"""commons_vote scen gate, in-container half (launched by run.sh via the
engine's shared setup_world.sh harness). Zero tokens: real gateway + dispatchd +
the scen dispatcher + scripted dummies vote a fixed 3-agent, 6-round PDD game
(moves/: round 3 has a2 submitting invalid "9" and a3 submitting nothing —
both must default). Asserts announcements, the dispatcher log, clean post-game
re-entry, then exports /dispatch/physics.pkl for run.sh's host-side bit-exact
twin replay (twin_check.py)."""
import json
import os
import shutil
import subprocess
import sys

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def run_dispatch(timeout=300):
    p = subprocess.Popen(
        ["python3", "/runtime_pi/dispatchd.py"], user="dispatch",
        env={"HOME": "/dispatch", "GATEWAY_SOCKET": "/run/gateway/gateway.sock",
             "PATH": "/usr/local/bin:/usr/bin:/bin"})
    return p.wait(timeout=timeout)


rc = run_dispatch()
check("dispatchd exits clean (game complete)", rc == 0, f"rc={rc}")

pub = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
world = [e["text"] for e in pub if e["from"] == "dispatch"]
check("6 round announcements + final", len(world) == 7, str(world))
check("rounds announce adopted option + level",
      all("adopted. Reservoir level:" in t for t in world[:6]), str(world[:6]))
check("final announcement", "Proceedings complete after 6 rounds" in world[-1], world[-1])

log = [json.loads(l)["text"] for l in open("/dispatch/game_log.jsonl")]
check("log: created + 6 rounds + complete",
      len(log) == 8 and log[0].startswith("world created") and log[-1].startswith("complete"),
      str(log[:2] + log[-1:]))
check("round-1 votes recorded", "a1:1" in log[1] and "a2:2" in log[1] and "a3:3" in log[1], log[1])
check("invalid vote defaulted (a2, round 3)", "a2:1" in log[3], log[3])
check("missing vote defaulted (a3, round 3)", "a3:1" in log[3], log[3])
check("moves exhausted", all(open(f"/agents/{a}/moves").read().strip() == ""
                             for a in ("a1", "a2", "a3")))

# Post-game re-entry (a restart after completion): exits clean, re-announces
# the final state once, wakes nobody, plays no rounds.
rc2 = run_dispatch()
pub2 = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
check("post-game re-entry clean",
      rc2 == 0 and len(pub2) == len(pub) + 1
      and "Proceedings complete" in pub2[-1]["text"], f"rc={rc2} posts={len(pub2)}")

for f in ("physics.pkl", "game_log.jsonl"):
    shutil.copy(f"/dispatch/{f}", f"/out/{f}")
    os.chmod(f"/out/{f}", 0o644)

print("IN-CONTAINER: ALL PASS" if not FAIL else f"FAILURES: {FAIL}")
sys.exit(1 if FAIL else 0)
