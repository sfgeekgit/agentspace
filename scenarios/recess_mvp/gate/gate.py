#!/usr/bin/env python3
"""recess_mvp scen gate asserts (run inside the harness container, see run.sh)."""
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

check("7 turns, ended left_village", s["turn"] == 7 and s["ended"] == "left_village", f"{s['turn']} {s['ended']}")
check("player back in square", s["player"]["loc"] == "square", s["player"]["loc"])
check("stats incl. GM-created one", s["player"]["stats"] == {"honesty": 2, "curiosity": 1, "mischief": 3, "things_set_on_fire": 1}, str(s["player"]["stats"]))
check("flag set by GM", s["flags"] == {"asked_about_son": True}, str(s["flags"]))
m = s["npcs"]["merrow"]
check("merrow met, grief+trust unlocked", m["met"] and {"core", "grief", "trust"} <= set(m["unlocked"]), str(m["unlocked"]))
check("merrow remembers the exchange", any("Nails" in n for n in m["notes"]), str(m["notes"]))
check("tobin untouched", not s["npcs"]["tobin"]["met"] and s["npcs"]["tobin"]["unlocked"] == ["core"])
check("reserve assigned as a new NPC", s["reserves"] == [] and s["npcs"]["bowling_alley_keeper"]["agent"] == "r1", str(s["reserves"]))
check("map add landed", "smoking_pit" in s["map"] and s["map"]["square"]["exits"]["into the ashes"] == "smoking_pit")
t = s["transcript"]
check("transcript: 7 entries, opening has no input",
      len(t) == 7 and t[0]["in"] is None and t[1]["in"] == "look around" and t[-1]["in"] == "climb down the well",
      str([e["in"] for e in t]))
check("transcript.md written", "You are gone." in open("/dispatch/transcript.md").read())
check("log has parse error, new stat, unlock, reserve, map, end",
      {"gm_parse_error", "new_stat", "unlock", "reserve", "map", "end", "game_over"} <= set(kinds), str(sorted(set(kinds))))
check("player received every narration + end marker",
      "Dust, hens" in inbox("p1") and "The game has ended" in inbox("p1"))
npc_in = inbox("n1")
check("NPC payload has core block", "YOU ARE Merrow" in npc_in and "making nails" in npc_in)
check("NPC payload has NO locked depth (need-to-know)", "Col" not in npc_in and "boar-spear" not in npc_in)
check("NPC payload carries what it hears", "asks what you are making" in npc_in)
check("narration stripped of the json block", all("```" not in e["out"] for e in t), str(t[1]["out"]))
check("GM context names present NPC and hides numbers",
      "Present: merrow" in inbox("g1") and "curiosity high" in inbox("g1") and '"honesty": 2' not in inbox("g1").split("RESPOND")[0])
check("agents cannot read engine state",
      subprocess.run(["su", "u_p1", "-s", "/bin/sh", "-c", "cat /dispatch/state.json"], capture_output=True).returncode != 0)

print()
if FAIL:
    print(f"VILLAGE GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
