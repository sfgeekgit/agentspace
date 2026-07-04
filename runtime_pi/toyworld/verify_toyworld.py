#!/usr/bin/env python3
"""Step-2 gate verifier — runs as root inside the toy-world container AFTER
the world has gone quiet. Checks the plan §4 done-criteria from the logs the
world actually produced (audit, public, budget, homes). Exit 1 on any FAIL.
"""
import json
import os
import sys

AGENTS = ["a48291", "a73056", "a19467"]  # random 5-digit ids by convention
GREETING_PAIRS = {("a48291", "a73056"), ("a73056", "a19467"), ("a19467", "a48291")}
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def jsonl(path):
    out = []
    try:
        with open(path) as f:
            for line in f:
                if line.strip():
                    out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


audit = jsonl("/data/gateway/audit.jsonl")
public = jsonl("/data/gateway/public.jsonl")
budget = jsonl("/data/gateway/budget.jsonl")

print("G1: every agent woke, starting from the operator wake")
wakes = [e for e in audit if e["event"] == "wake"]
for a in AGENTS:
    mine = [w for w in wakes if w["agent"] == a]
    check(f"g1 {a} woke at least once", len(mine) >= 1)
    check(f"g1 {a} first wake cause = operator",
          mine and mine[0]["causes"][0]["type"] == "operator",
          json.dumps(mine[0]["causes"]) if mine else "no wakes")

print("G2: introductions on the public board")
for a in AGENTS:
    posts = [p for p in public if p["from"] == a]
    check(f"g2 {a} posted an introduction", len(posts) >= 1)

print("G3: private greetings sent (the FIRST_WAKE cycle)")
sends = [e for e in audit if e["event"] == "send" and e["frm"] != "operator"]
pairs = {(s["frm"], s["to"]) for s in sends}
for f, t in sorted(GREETING_PAIRS):
    check(f"g3 greeting {f}->{t} sent", (f, t) in pairs, str(pairs))

print("G4: recipients woke on delivery (pm cause)")
pm_woken = {w["agent"] for w in wakes
            if any(c.get("type") == "pm" for c in w["causes"])}
for a in AGENTS:
    check(f"g4 {a} woke on a pm delivery", a in pm_woken)

print("G5: no-ack norm — greetings did not ping-pong")
check("g5 total agent sends <= 4 (3 greetings + slack, no ack storm)",
      len(sends) <= 4, f"sends={[(s['frm'], s['to']) for s in sends]}")
extra = {(s["frm"], s["to"]) for s in sends} - GREETING_PAIRS
check("g5 no reply-to-greeting pairs", not (
    {(t, f) for f, t in GREETING_PAIRS} & extra), str(extra))

print("G6: per-agent spend attribution in budget.jsonl")
for a in AGENTS:
    mine = [b for b in budget if b["agent"] == a]
    ok_turns = [b for b in mine if b.get("turn_ok")]
    cost = sum(b.get("cost_total") or 0 for b in mine)
    check(f"g6 {a} logged usage with real cost",
          len(ok_turns) >= 1 and cost > 0,
          f"entries={len(mine)} cost={cost}")

print("G7: homes carry full state (scaffolding + sessions + memory)")
for a in AGENTS:
    home = f"/agents/{a}"
    for fn in ("SOUL.md", "MEMORY.md", "ROLE.md"):
        check(f"g7 {a}/{fn} exists", os.path.exists(f"{home}/{fn}"))
    check(f"g7 {a} FIRST_WAKE consumed (archived, not re-injected)",
          os.path.exists(f"{home}/.FIRST_WAKE.md.done")
          and not os.path.exists(f"{home}/FIRST_WAKE.md"))
    check(f"g7 {a} frozen session sandwich exists",
          os.path.exists(f"{home}/sessions/.sysprompt"))
    sess = [f for f in os.listdir(f"{home}/sessions")
            if f.endswith(".jsonl")] if os.path.isdir(f"{home}/sessions") else []
    check(f"g7 {a} has a session transcript", len(sess) >= 1)
    done = len(os.listdir(f"{home}/inbox_done")) if os.path.isdir(f"{home}/inbox_done") else 0
    inbox = len([f for f in os.listdir(f"{home}/inbox")
                 if f.endswith(".json")]) if os.path.isdir(f"{home}/inbox") else 0
    check(f"g7 {a} drained its inbox (inbox empty, archive populated)",
          inbox == 0 and done >= 1, f"inbox={inbox} done={done}")

print("G7b: required scratchpad was used")
for a in AGENTS:
    mine = [b for b in budget if b["agent"] == a]
    check(f"g7b {a} updated scratch/ in every completed turn",
          mine and all(b.get("scratch_updated") for b in mine if b.get("turn_ok")),
          json.dumps([b.get("scratch_updated") for b in mine]))

print("G8: every wake ended cleanly")
ends = [e for e in audit if e["event"] == "wake_end"]
bad = [e for e in ends if e["rc"] != 0]
check("g8 all wake_end rc=0", len(ends) >= 3 and not bad,
      json.dumps(bad)[:300])
errors = [e for e in audit if e["event"] == "wake_error"]
check("g8 no wake_error events", not errors, json.dumps(errors)[:300])

print()
failed = [r for r in RESULTS if not r[1]]
print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:")
    for name, _, detail in failed:
        print(f"  - {name}: {detail[:300]}")
    sys.exit(1)
print("STEP 2 GATE: ALL GREEN")
