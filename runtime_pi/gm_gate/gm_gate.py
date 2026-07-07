#!/usr/bin/env python3
"""GM-machinery gate (run as root inside the gate container after setup_gm.sh).

Drives the REAL stack — pi_gateway + gmd + PD gm.py + dummy agents — with zero
tokens, and asserts the whole GM contract: blocking gm_wake, submit→gm_collect,
truthful scoring, world announcements, isolation, gm_remove, and resume from
on-disk state. Exits nonzero on any failure.
"""
import json
import os
import socket
import subprocess
import sys

SOCK = "/run/gateway/gateway.sock"
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def raw(op, **kw):
    """One gateway request AS ROOT → operator principal (may call gm ops)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(400)
    s.connect(SOCK)
    s.sendall((json.dumps({"op": op, **kw}) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        c = s.recv(65536)
        if not c:
            break
        buf += c
    s.close()
    return json.loads(buf.split(b"\n", 1)[0])


def su(user, cmd):
    return subprocess.run(["su", user, "-s", "/bin/sh", "-c", cmd],
                          capture_output=True, text=True)


def run_gm(timeout=120):
    """Start gmd as the gm user and wait for the game to finish."""
    p = subprocess.Popen(
        ["python3", "/runtime_pi/gmd.py"], user="gm",
        env={"HOME": "/gm", "GATEWAY_SOCKET": SOCK,
             "PATH": "/usr/local/bin:/usr/bin:/bin"})
    return p.wait(timeout=timeout)


def audit_len():
    try:
        return sum(1 for _ in open("/data/gateway/audit.jsonl"))
    except FileNotFoundError:
        return 0


# ── Scenario 1: full 3-round refereed PD ────────────────────────────────────
rc = run_gm()
check("gmd exits clean (game complete)", rc == 0, f"rc={rc}")
state = json.load(open("/gm/state.json"))
check("3 rounds completed", state["round"] == 3, str(state.get("round")))
check("truthful GM score a1=0 a2=15", state["scores"] == {"a1": 0, "a2": 15}, str(state["scores"]))
check("scorelog has 3 rounds", len(state["log"]) == 3, str(len(state["log"])))
pub = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
worldposts = [e for e in pub if e["from"] == "world"]
check("4 world announcements (3 rounds + game over)", len(worldposts) == 4, str(len(worldposts)))
check("submissions consumed by collect", os.listdir("/data/gateway/submissions") == [],
      str(os.listdir("/data/gateway/submissions")))
adt = [json.loads(l) for l in open("/data/gateway/audit.jsonl")]
check("audit gm_announce entries carry text (content enrichment)",
      all(e.get("text") for e in adt if e["event"] == "gm_announce"))
check("audit submit entries carry the action",
      all(e.get("action") for e in adt if e["event"] == "submit"))

# ── Isolation: agents can't read GM state or call GM ops ─────────────────────
check("agent cannot read /gm state", su("u_a1", "cat /gm/state.json").returncode != 0)
check("agent cannot read submission spool", su("u_a1", "ls /data/gateway/submissions").returncode != 0)
r = su("u_a1", "gateway raw '{\"op\":\"gm_collect\",\"agent\":\"a2\"}'")
check("agent gm_collect refused (role-gated)", '"ok": false' in r.stdout, r.stdout.strip())

# ── gm_remove: no send rights, no wakes ─────────────────────────────────────
check("gm_remove ok", raw("gm_remove", agent="a2").get("ok") is True)
check("removed agent cannot send", su("u_a2", "gateway send a1 hi").returncode != 0)
check("gm_wake on removed refused", raw("gm_wake", to="a2", payload="x").get("ok") is False)

# ── Scenario 2: resume from pre-seeded mid-game state ────────────────────────
for f in os.listdir("/data/gateway/submissions"):
    os.remove(f"/data/gateway/submissions/{f}")
os.remove("/data/gateway/removed.json")  # un-remove a2 for the resume run
json.dump({"round": 2, "scores": {"a1": 0, "a2": 10},
           "log": [{"round": 1}, {"round": 2}]}, open("/gm/state.json", "w"))
subprocess.run(["chown", "gm", "/gm/state.json"])
before = audit_len()
rc = run_gm()
st = json.load(open("/gm/state.json"))
check("resume reaches round 3", st["round"] == 3, str(st["round"]))
check("resume final score a1=0 a2=15", st["scores"] == {"a1": 0, "a2": 15}, str(st["scores"]))
new = [json.loads(l) for l in open("/data/gateway/audit.jsonl")][before:]
gmwakes = [e for e in new if e.get("event") == "gm_wake"]
check("resume plays exactly 1 round (2 gm_wakes)", len(gmwakes) == 2, str(len(gmwakes)))

print()
if FAIL:
    print(f"GM GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print("GM GATE: ALL PASS")
