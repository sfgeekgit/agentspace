#!/usr/bin/env python3
"""dispatcher-machinery gate (run as root inside the gate container after setup_dispatch.sh).

Drives the REAL stack — pi_gateway + dispatchd + PD dispatch.py + dummy agents — with zero
tokens, and asserts the whole dispatcher contract: blocking dispatch_wake, submit→dispatch_collect,
truthful scoring, world announcements, isolation, dispatch_remove, and resume from
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
    """One gateway request AS ROOT → operator principal (may call dispatch ops)."""
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


def run_dispatch(timeout=120):
    """Start dispatchd as the dispatch user and wait for the game to finish."""
    p = subprocess.Popen(
        ["python3", "/runtime_pi/dispatchd.py"], user="dispatch",
        env={"HOME": "/dispatch", "GATEWAY_SOCKET": SOCK,
             "PATH": "/usr/local/bin:/usr/bin:/bin"})
    return p.wait(timeout=timeout)


def audit_len():
    try:
        return sum(1 for _ in open("/data/gateway/audit.jsonl"))
    except FileNotFoundError:
        return 0


# ── Scenario 1: full 3-round refereed PD ────────────────────────────────────
rc = run_dispatch()
check("dispatchd exits clean (game complete)", rc == 0, f"rc={rc}")
state = json.load(open("/dispatch/state.json"))
check("3 rounds completed", state["round"] == 3, str(state.get("round")))
check("truthful dispatcher score a1=0 a2=15", state["scores"] == {"a1": 0, "a2": 15}, str(state["scores"]))
check("scorelog has 3 rounds", len(state["log"]) == 3, str(len(state["log"])))
pub = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
worldposts = [e for e in pub if e["from"] == "dispatch"]
check("4 world announcements (3 rounds + game over)", len(worldposts) == 4, str(len(worldposts)))
check("submissions consumed by collect", os.listdir("/data/gateway/submissions") == [],
      str(os.listdir("/data/gateway/submissions")))
adt = [json.loads(l) for l in open("/data/gateway/audit.jsonl")]
check("audit dispatch_announce entries carry text (content enrichment)",
      all(e.get("text") for e in adt if e["event"] == "dispatch_announce"))
check("audit submit entries carry the action",
      all(e.get("action") for e in adt if e["event"] == "submit"))

# ── Isolation: agents can't read dispatcher state or call dispatcher ops ─────────────────────
check("agent cannot read /dispatch state", su("u_a1", "cat /dispatch/state.json").returncode != 0)
check("agent cannot read submission spool", su("u_a1", "ls /data/gateway/submissions").returncode != 0)
r = su("u_a1", "gateway raw '{\"op\":\"dispatch_collect\",\"agent\":\"a2\"}'")
check("agent dispatch_collect refused (role-gated)", '"ok": false' in r.stdout, r.stdout.strip())

# ── dispatch_remove: no send rights, no wakes ─────────────────────────────────────
check("dispatch_remove ok", raw("dispatch_remove", agent="a2").get("ok") is True)
check("removed agent cannot send", su("u_a2", "gateway send a1 hi").returncode != 0)
check("dispatch_wake on removed refused", raw("dispatch_wake", to="a2", payload="x").get("ok") is False)

# ── Scenario 2: resume from pre-seeded mid-game state ────────────────────────
for f in os.listdir("/data/gateway/submissions"):
    os.remove(f"/data/gateway/submissions/{f}")
os.remove("/data/gateway/removed.json")  # un-remove a2 for the resume run
json.dump({"round": 2, "scores": {"a1": 0, "a2": 10},
           "log": [{"round": 1}, {"round": 2}]}, open("/dispatch/state.json", "w"))
subprocess.run(["chown", "dispatch", "/dispatch/state.json"])
before = audit_len()
rc = run_dispatch()
st = json.load(open("/dispatch/state.json"))
check("resume reaches round 3", st["round"] == 3, str(st["round"]))
check("resume final score a1=0 a2=15", st["scores"] == {"a1": 0, "a2": 15}, str(st["scores"]))
new = [json.loads(l) for l in open("/data/gateway/audit.jsonl")][before:]
dwakes = [e for e in new if e.get("event") == "dispatch_wake"]
check("resume plays exactly 1 round (2 dispatch_wakes)", len(dwakes) == 2, str(len(dwakes)))

print()
if FAIL:
    print(f"dispatcher GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print("dispatcher GATE: ALL PASS")
