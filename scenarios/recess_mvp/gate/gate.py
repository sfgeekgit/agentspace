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

check("8 turns, ended left_village (by end, under the 9-turn cap)", s["turn"] == 8 and s["ended"] == "left_village", f"{s['turn']} {s['ended']}")
check("player back in square", s["player"]["loc"] == "square", s["player"]["loc"])
check("stats incl. GM-created one", s["player"]["stats"] == {"honesty": 3, "curiosity": 1, "mischief": 3, "things_set_on_fire": 1}, str(s["player"]["stats"]))
check("flag set by GM", s["flags"] == {"asked_about_son": True}, str(s["flags"]))
m = s["npcs"]["merrow"]
check("merrow met, grief+trust unlocked", m["met"] and {"core", "grief", "trust"} <= set(m["unlocked"]), str(m["unlocked"]))
check("merrow remembers the exchange", any("Nails" in n for n in m["notes"]), str(m["notes"]))
check("tobin never met, but 'wary' unlocked by rank+floor (mischief 3, top-2)",
      not s["npcs"]["tobin"]["met"] and s["npcs"]["tobin"]["unlocked"] == ["core", "wary"], str(s["npcs"]["tobin"]["unlocked"]))
check("no 'pedlar' unlock: curiosity 1 is under the floor", "pedlar" not in s["npcs"]["tobin"]["unlocked"])
check("reserve assigned as a new NPC", s["reserves"] == [] and s["npcs"]["bowling_alley_keeper"]["agent"] == "r1", str(s["reserves"]))
check("map add landed", "smoking_pit" in s["map"] and s["map"]["square"]["exits"]["into the ashes"] == "smoking_pit")
t = s["transcript"]
check("transcript: 8 entries, opening has no input",
      len(t) == 8 and t[0]["in"] is None and t[1]["in"] == "look around" and t[-1]["in"] == "climb down the well",
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
gm_in = inbox("g1")
check("engine notes fed back: absent-NPC talk and duplicate add",
      "talk to 'tobin' ignored: they are at inn_common" in gm_in and "already exists as 'forge'" in gm_in, gm_in[:0])
check("GM context lists world places, people, and listened-for flags",
      "WORLD places" in gm_in and "merrow (Merrow) at forge" in gm_in and "asked_about_son" in gm_in)
check("no stray node from the duplicate add", "the_forge" not in s["map"])
check("multi-hop move walked the graph (church_yard > fields > fen_edge) and reported the route",
      "via church_yard > fields > fen_edge" in " ".join(e["text"] for e in log if e["kind"] == "move") and "walked church_yard > fields > fen_edge" in gm_in, str([e["text"] for e in log if e["kind"]=="move"]))
check("premature ending flag refused with a note",
      "mill_restored" not in s["flags"] and "bad_flag" in kinds and "far too early" in gm_in)
check("GM-voiced NPC line flagged", "voiced" in kinds and "gave Tobin lines to say" in gm_in)
check("GM context names present NPC and hides numbers",
      "Present: merrow" in gm_in and "honesty very high" in gm_in and '"honesty": 3' not in gm_in)
check("static format/endings text not sent per turn", "Possible endings" not in gm_in and '"assign_reserve": {' not in gm_in)
check("agents cannot read engine state",
      subprocess.run(["su", "u_p1", "-s", "/bin/sh", "-c", "cat /dispatch/state.json"], capture_output=True).returncode != 0)

print()
if FAIL:
    print(f"VILLAGE GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
