#!/usr/bin/env python3
"""Build gate (engine, host-side): the builder's hidden-information hooks,
end to end through a REAL throwaway build (~30s, zero tokens). Builds an
8-agent mafia world root with a fixed seed, then inspects the image:
role briefings instantiated (each mafia's ROLE.md names its partner, no
'{partners}' placeholder left), /gm/secrets.json baked + gm-owned + agent-
unreadable. Cleans up the image and the local snap index row afterwards.
"""
import json
import subprocess
import sys
import uuid

sys.path.insert(0, "/opt/agentspace-ctl")
from agentspace import db                      # noqa: E402
from agentspace.builder import build_world_root  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def img(tag, *args, user=None):
    """Run one command in a fresh container from the image, return (rc, out)."""
    cmd = ["docker", "run", "--rm", "--network", "none"]
    if user:
        cmd += ["--user", user]
    r = subprocess.run(cmd + ["--entrypoint", args[0], tag, *args[1:]],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


name = f"buildgate_{uuid.uuid4().hex[:6]}"
snap = build_world_root(
    "mafia", [{"model": "gate/none", "persona": "minimal"}] * 8,
    runtime="pi", world_name=name, seed=42,
    params={"hard_enforcement": True})
tag = snap["ghcr_tag"]
try:
    rc, out = img(tag, "cat", "/gm/secrets.json")
    roles = json.loads(out)["roles"]
    check("secrets.json baked with 8 roles", rc == 0 and len(roles) == 8, out)
    counts = {r: list(roles.values()).count(r) for r in set(roles.values())}
    check("role mix at N=8: 2 mafia, 1 detective, 1 doctor, 4 villagers",
          counts == {"mafia": 2, "detective": 1, "doctor": 1, "villager": 4},
          str(counts))

    m1, m2 = sorted(a for a, r in roles.items() if r == "mafia")
    _, brief = img(tag, "cat", f"/agents/{m1}/ROLE.md")
    check("mafia briefing instantiated: partner named, placeholder gone",
          m2 in brief and "{partners}" not in brief, brief[:200])
    villager = next(a for a, r in roles.items() if r == "villager")
    _, vbrief = img(tag, "cat", f"/agents/{villager}/ROLE.md")
    check("villager briefing untouched (no partner leak)",
          m1 not in vbrief and m2 not in vbrief)

    _, perms = img(tag, "stat", "-c", "%U %a", "/gm", "/gm/secrets.json")
    lines = perms.split("\n")
    check("/gm is gm-owned 0700", lines[0] == "gm 700", perms)
    check("secrets.json is gm-owned", lines[1].startswith("gm "), perms)
    rc, _ = img(tag, "cat", "/gm/secrets.json", user=f"u_{villager}")
    check("agents cannot read the baked answer key", rc != 0)
finally:
    subprocess.run(["docker", "rmi", tag], capture_output=True)
    db.delete_snap(snap["snap_id"])
    print(f"(cleaned up throwaway build {name})")

print()
if FAIL:
    print(f"BUILD GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print("BUILD GATE: ALL PASS")
