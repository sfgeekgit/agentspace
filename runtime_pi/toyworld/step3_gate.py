#!/usr/bin/env python3
"""Step-3 end-to-end gate: build → fork → wake → chat → roll → snapshot,
all through the real zookeeper module code paths. Live Pi turns (~$0.05).

    python3 runtime_pi/toyworld/step3_gate.py <openrouter-key-file>

Run from the repo root with a funded OpenRouter key file as argv[1]. Builds a
fresh hello_pi world root each run (auto-versioned), forks it as env
`step3test`, exercises the operator surface, verifies snapshot completeness,
then tears the env down. Rebuild the PI base image first if agentd/gateway
changed (docs/agentspace_cli.md prerequisites)."""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/agentspace-ctl")
from agentspace import builder, snap as snap_mod, env as env_mod, db, docker_host
from agentspace.runtimes import pi

KEY = Path(sys.argv[1]).read_text().strip()
ENV = "step3test"
R = []


def chk(name, ok, detail=""):
    R.append((name, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def dexec(c, *a, **k):
    return docker_host.stdout("localhost", "exec", c, *a, check=False, **k)


# clean any prior run
docker_host.run("localhost", "rm", "-f", ENV, check=False)
try:
    db.delete_env(ENV)
except Exception:
    pass

print("BUILD: hello_pi world root (runtime=pi)")
sp = builder.build_world_root(
    "hello_pi",
    [{"model": "anthropic/claude-haiku-4.5", "persona": "minimal"},
     {"model": "anthropic/claude-haiku-4.5", "persona": "minimal"}],
    runtime="pi",
)
ref = f"hello_pi:{sp['version']}"
ids = sp["agents"]
chk("build produced a pi world root", sp["runtime"] == "pi" and len(ids) == 2)
chk("no fs_isolation flag (structural isolation)", sp["feature_flags"] == {})

print(f"FORK: {ref} → env {ENV} (kick=on → first wake)")
snap_mod.cmd_fork(ref, ENV, existing_key=KEY, budget_usd=None, kick=True)
chk("env row recorded", db.get_env(ENV) is not None)
chk("gateway socket live", pi.gateway_running("localhost", ENV))

print("WAKE: waiting for both first-wakes to finish")
deadline = time.monotonic() + 240
while time.monotonic() < deadline:
    n = sum(pi.wake_ended_since("localhost", ENV, a, 0) for a in ids)
    if n >= 2:
        break
    time.sleep(3)
audit = dexec(ENV, "cat", "/data/gateway/audit.jsonl")
budget = dexec(ENV, "cat", "/data/gateway/budget.jsonl")
wake_ends = [json.loads(l) for l in audit.splitlines() if '"wake_end"' in l]
chk("both agents woke and ended cleanly",
    len(wake_ends) >= 2 and all(w["rc"] == 0 for w in wake_ends),
    audit[-300:])
chk("per-agent budget logged with real cost",
    all(any(json.loads(l)["agent"] == a and (json.loads(l).get("cost_total") or 0) > 0
            for l in budget.splitlines()) for a in ids))
for a in ids:
    home = dexec(ENV, "sh", "-c", f"ls -a /agents/{a} /agents/{a}/sessions /agents/{a}/scratch 2>&1")
    chk(f"{a} home has memory+session+scratch",
        "MEMORY.md" in home and ".sysprompt" in home, home[:200])

print("CHAT: operator PM round-trip via the transcript read path")
mark = pi.audit_line_count("localhost", ENV)
pi.kick_agent("localhost", ENV, ids[0], "In one short sentence, what do you enjoy?")
deadline = time.monotonic() + 240
while time.monotonic() < deadline:
    if pi.wake_ended_since("localhost", ENV, ids[0], mark):
        break
    time.sleep(3)
reply = pi.last_assistant_text("localhost", ENV, ids[0])
chk("chat elicited an assistant reply", bool(reply), f"reply={reply[:80]!r}")

print("ROLL-SESSIONS: archive + fresh sysprompt")
sysprompt_before = dexec(ENV, "sh", "-c", f"cat /agents/{ids[0]}/sessions/.sysprompt 2>/dev/null | wc -c")
pi.roll_sessions("localhost", ENV, [ids[0]])
after = dexec(ENV, "sh", "-c",
              f"ls /agents/{ids[0]}/sessions/archive/*.jsonl 2>/dev/null | wc -l; "
              f"test -f /agents/{ids[0]}/sessions/.sysprompt && echo HASSP || echo NOSP")
chk("session archived + sysprompt cleared",
    after.split()[0] != "0" and "NOSP" in after, after)

print("SNAPSHOT: docker commit captures ALL state (local; no push — test hygiene)")
docker_host.run("localhost", "commit", ENV, "pi-step3-snap:test")
try:
    probe = subprocess.run(
        ["docker", "run", "--rm", "pi-step3-snap:test", "bash", "-c",
         "test -s /data/gateway/audit.jsonl && test -s /data/gateway/budget.jsonl && "
         f"ls /agents/{ids[0]}/sessions/archive/*.jsonl >/dev/null && "
         f"test -s /agents/{ids[1]}/MEMORY.md && echo OK"],
        capture_output=True, text=True, timeout=60)
    chk("snapshot carries audit+budget+archived-sessions+memory",
        "OK" in probe.stdout, probe.stdout + probe.stderr)
finally:
    docker_host.run("localhost", "rmi", "pi-step3-snap:test", check=False)

print()
docker_host.run("localhost", "rm", "-f", ENV, check=False)
try:
    db.delete_env(ENV)
except Exception:
    pass
failed = [n for n, ok in R if not ok]
print(f"RESULT: {len(R) - len(failed)}/{len(R)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("STEP 3 GATE: ALL GREEN")
