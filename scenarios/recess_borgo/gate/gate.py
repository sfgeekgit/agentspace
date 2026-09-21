#!/usr/bin/env python3
"""recess_borgo scen gate asserts (run inside the harness container, see run.sh)."""
import glob
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
                           "PATH": "/usr/local/bin:/usr/bin:/bin"}).wait(timeout=180)
check("dispatchd exits clean", rc == 0, f"rc={rc}")
s = json.load(open("/dispatch/state.json"))
log = [json.loads(l) for l in open("/dispatch/game_log.jsonl")]
kinds = [e["kind"] for e in log]
inbox = lambda a: " ".join(open(f).read() for f in glob.glob(f"/agents/{a}/inbox_done/*.json"))
gm_in = inbox("g1")
moves = [e["text"] for e in log if e["kind"] == "move"]

check("ran to the 7-turn cap (both ending flags refused as too early)", s["turn"] == 7 and s["ended"] is None, f"{s['turn']} {s['ended']}")
check("nunzia met by display name, remembers the exchange", s["npcs"]["nunzia"]["met"] and any("Pradello" in n for n in s["npcs"]["nunzia"]["notes"]))
check("multi-hop to the ridge walked the graph",
      any("crinale via" in m and "alpe_corte" in m for m in moves) and "walked" in gm_in, str(moves))
check("both premature ending flags refused with notes",
      "truth_spoken" not in s["flags"] and "left_on_the_bus" not in s["flags"] and kinds.count("bad_flag") == 2 and "far too early" in gm_in, str(s["flags"]))
check("ordinary flags kept", s["flags"].get("asked_about_sale") and s["flags"].get("saw_ridge"))
check("ending flags not in the listened-for list sent to the GM",
      "Flags the world listens for" in gm_in and "truth_spoken" not in gm_in.split("Flags the world listens for")[1].split("\n")[0])
check("GM-voiced Dario line flagged", "voiced" in kinds and "gave Dario lines" in gm_in)
check("world places/people listed (50 nodes, 2 npcs, 1 reserve)",
      "casermetta" in gm_in and "dario (Dario) at bar" in gm_in and "1 left" in gm_in)
check("relative words: curiosity very high, honesty high", "curiosity very high" in gm_in and "honesty high" in gm_in)
check("nunzia unlock by rank+floor: 'credit' needs honesty>=3 (not yet)", "credit" not in s["npcs"]["nunzia"]["unlocked"] and "sale" in s["npcs"]["nunzia"]["unlocked"], str(s["npcs"]["nunzia"]["unlocked"]))
check("player got every narration", "Late morning" in inbox("p1") and "17:40" in inbox("p1"))
check("agents cannot read engine state",
      subprocess.run(["su", "u_p1", "-s", "/bin/sh", "-c", "cat /dispatch/state.json"], capture_output=True).returncode != 0)

print()
if FAIL:
    print(f"RECESS_BORGO GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
