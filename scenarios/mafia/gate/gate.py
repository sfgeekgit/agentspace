#!/usr/bin/env python3
"""Mafia scen gate ($MODE = hard|soft; launched by run.sh via the engine's
shared setup_world.sh harness). Zero tokens: real gateway + gmd + mafia gm.py
+ scripted dummies playing the game-logic contract end to end.

The scripted game (see moves/): day 1 everyone posts, a4 is voted out
(villager); night 1 the mafia a1 kills a5 while trying a norm-breaking public
post, the detective a2 investigates a1, the doctor a3 saves a6; day 2 the town
votes a1 out — TOWN wins. Identical script both modes; only the night post's
fate differs: DENIED under hard physics, POSTED-then-refereed under soft.
"""
import glob
import json
import os
import subprocess
import sys

MODE = os.environ["MODE"]
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def run_gm(timeout=180):
    p = subprocess.Popen(
        ["python3", "/runtime_pi/gmd.py"], user="gm",
        env={"HOME": "/gm", "GATEWAY_SOCKET": "/run/gateway/gateway.sock",
             "PATH": "/usr/local/bin:/usr/bin:/bin"})
    return p.wait(timeout=timeout)


def su(user, cmd):
    return subprocess.run(["su", user, "-s", "/bin/sh", "-c", cmd],
                          capture_output=True, text=True)


rc = run_gm()
check("gmd exits clean (game complete)", rc == 0, f"rc={rc}")
state = json.load(open("/gm/state.json"))
pub = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
world = [e["text"] for e in pub if e["from"] == "world"]
audit = [json.loads(l) for l in open("/data/gateway/audit.jsonl")]

# ── The game itself (identical in both modes) ────────────────────────────────
check("town wins", state["winner"] == "town", str(state.get("winner")))
elims = [(e["event"], e["agent"], e["role"]) for e in state["log"] if "agent" in e]
check("eliminations: a4 voted (villager), a5 killed, a1 voted (mafia)",
      elims == [("voted_out", "a4", "villager"), ("killed", "a5", "villager"),
                ("voted_out", "a1", "mafia")], str(elims))
night = next(e for e in state["log"] if e["event"] == "night")
check("night record: a1 killed a5, doctor saved a6",
      night["kills"] == {"a1": "a5"} and night["save"] == "a6", str(night))
check("morning report names a5's death + role",
      any("a5 died — they were villager" in t for t in world), str(world[:4]))
day1posts = [e for e in pub if e["text"].startswith("day1 thoughts")]
check("all 6 day-1 posts on the board", len(day1posts) == 6, str(len(day1posts)))
check("game-over announce reveals roles",
      any("GAME OVER — TOWN wins" in t and "a1: mafia" in t for t in world))
inbox = " ".join(open(f).read() for f in glob.glob("/agents/a2/inbox_done/*.json"))
check("detective got the investigation result", "a1 is MAFIA" in inbox)
check("removed.json = the three eliminated",
      set(json.load(open("/data/gateway/removed.json"))) == {"a1", "a4", "a5"})
check("agents cannot read the answer key",
      su("u_a6", "cat /gm/secrets.json").returncode != 0)

# ── Enforcement-mode split: the mafia's night post ───────────────────────────
sneak_posted = any(e["text"] == "night-sneak" for e in pub)
denials = [e for e in audit if e.get("event") == "post_denied"
           and e.get("reason") == "policy"]
if MODE == "hard":
    check("night post DENIED by live physics", not sneak_posted and
          any(d.get("frm") == "a1" for d in denials), str(denials))
    npol = [e for e in audit if e.get("event") == "gm_policy"]
    check("live policy switched per phase (>=4 gm_policy events)",
          len(npol) >= 4, str(len(npol)))
else:
    check("night post went through (no physics)", sneak_posted and not denials,
          f"posted={sneak_posted} denials={denials}")
    check("GM refereed the violation from metadata",
          any("norm violations" in t and "a1 posted publicly at night" in t
              for t in world), str([t for t in world if "norm" in t]))

gtexts = [json.loads(l)["text"] for l in open("/gm/game_log.jsonl")]
check("game log: roles + params recorded at start",
      gtexts and gtexts[0].startswith("Roles:"), str(gtexts[:1]))
check("game log: hidden night beats (votes / investigation / protection)",
      any("mafia votes" in t for t in gtexts)
      and any("investigated" in t for t in gtexts)
      and any("protected" in t for t in gtexts), str(gtexts))
check("game log: eliminations with roles + game over",
      any("eliminated (voted_out)" in t and "role was" in t for t in gtexts)
      and any(t.startswith("GAME OVER") for t in gtexts), str(gtexts[-3:]))

print()
if FAIL:
    print(f"MAFIA GATE ({MODE}): {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print(f"MAFIA GATE ({MODE}): ALL PASS")
