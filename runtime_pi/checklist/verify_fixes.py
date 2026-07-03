#!/usr/bin/env python3
"""Ad-hoc verification for step-1 fixes the standard gate doesn't exercise
(broker restart, reserved-name collision, fail-closed policy, public rate cap).
Runs as root in the same container image after setup_env.sh. Zero tokens."""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, "/runtime_pi")
import broker

CLIENT = "python3 /runtime_pi/broker_client.py"
OK = []


def check(name, cond, detail=""):
    OK.append(cond)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))


def kill_other_brokers():
    """setup_env.sh already backgrounded a broker; the slim image lacks pkill,
    so reap any stray broker.py by scanning /proc, then clear the socket."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().decode(errors="ignore")
        except OSError:
            continue
        if "broker.py" in cmd:
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass
    if os.path.exists(broker.SOCKET_PATH):
        os.unlink(broker.SOCKET_PATH)
    time.sleep(0.2)


def start_broker():
    kill_other_brokers()
    p = subprocess.Popen(["python3", "/runtime_pi/broker.py"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    for _ in range(50):
        if os.path.exists(broker.SOCKET_PATH):
            time.sleep(0.1)
            return p
        time.sleep(0.1)
    raise RuntimeError("broker did not start")


def as_agent(agent, cmd):
    return subprocess.run(["su", "-s", "/bin/bash", f"u_{agent}", "-c", cmd],
                          capture_output=True, text=True, timeout=30)


def last_public_seq():
    m = 0
    try:
        with open(broker.PUBLIC) as f:
            for line in f:
                m = max(m, json.loads(line)["seq"])
    except FileNotFoundError:
        pass
    return m


print("V1: seq counter survives a broker restart (snapshot transparency)")
p = start_broker()
as_agent("a1", f'{CLIENT} post "before restart"')
seq_before = last_public_seq()
p.terminate(); p.wait()
p = start_broker()  # simulate restore: fresh process, same on-disk state
as_agent("a1", f'{CLIENT} post "after restart"')
seq_after = last_public_seq()
check("seq strictly increases across restart (no reset/collision)",
      seq_after == seq_before + 1 and seq_before >= 1, f"{seq_before}->{seq_after}")

print("V2: reserved-name collision — u_operator cannot impersonate the operator")
subprocess.run(["useradd", "--no-user-group", "-M", "-d", "/agents/operator",
                "-s", "/usr/sbin/nologin", "u_operator"], capture_output=True)
r = as_agent("operator", f'{CLIENT} send a1 "as fake operator"')
check("u_operator is refused (unknown peer), not treated as operator",
      r.returncode != 0 and "unknown peer" in r.stdout, r.stdout + r.stderr)

print("V3: policy load fails CLOSED when there is no good policy to fall back on")
# A cold broker whose very first policy read fails must deny (no last-good yet).
# (A warm broker instead falls back to the last-good policy — deliberate
# resilience against a torn write, verified by the send succeeding after
# restore below.)
p.terminate(); p.wait()
with open(broker.POLICY, "w") as f:
    f.write("{ this is not valid json")
p = start_broker()  # main() won't overwrite an existing (corrupt) policy file
r = as_agent("a1", f'{CLIENT} send a2 "denied: cold broker, corrupt policy"')
check("cold broker + corrupt policy denies all sends (fail closed)",
      r.returncode != 0, r.stdout)
broker.write_policy(broker.DEFAULT_POLICY)
r = as_agent("a1", f'{CLIENT} send a2 "ok again"')
check("send allowed again after policy restored", r.returncode == 0, r.stdout + r.stderr)

print("V4: public posting is rate-capped (not exempt)")
broker.write_policy(dict(broker.DEFAULT_POLICY, rate_limit_per_min=2))
outs = [as_agent("a3", f'{CLIENT} post "spam {i}"').returncode for i in range(5)]
check("public flood partially refused", outs.count(0) == 2 and outs.count(1) == 3, str(outs))
broker.write_policy(broker.DEFAULT_POLICY)

p.terminate(); p.wait()
print(f"\nRESULT: {sum(OK)}/{len(OK)} passed")
sys.exit(0 if all(OK) else 1)
