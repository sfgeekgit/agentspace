#!/usr/bin/env python3
"""Step-1 gate: functional shakeout + isolation checklist (no LLM, no tokens).

Runs as root inside the step-1 container AFTER setup_env.sh. Agent-identity
actions are performed via `su` so they carry real kernel credentials — the
same way a compromised/curious agent would act.

Docs: docs/runtime_pi.md §7. Ported from the OC-era 8-item sandbox checklist
(see learnings_2026-06-12.md): the "sandbox siblings" items become su tests
here.
"""
import json
import subprocess
import sys
import time

CLIENT = "python3 /runtime_pi/broker_client.py"
AUDIT = "/data/broker/audit.jsonl"
POLICY = "/data/broker/policy.json"
RESULTS = []


def as_agent(agent, cmd, **kw):
    return subprocess.run(
        ["su", "-s", "/bin/bash", f"u_{agent}", "-c", cmd],
        capture_output=True, text=True, timeout=30, **kw)


def as_operator(cmd):
    return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=30)


def audit_events(event=None):
    out = []
    with open(AUDIT) as f:
        for line in f:
            e = json.loads(line)
            if event is None or e["event"] == event:
                out.append(e)
    return out


def agent_log(agent):
    try:
        with open(f"/agents/{agent}/log.txt") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def wait_for(pred, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.2)
    return False


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


def set_policy(**overrides):
    pol = {"max_msg_bytes": 16384, "rate_limit_per_min": 30, "allow": None, "deny": []}
    pol.update(overrides)
    with open(POLICY, "w") as f:
        json.dump(pol, f)


def main():
    set_policy()

    print("T1: PM round-trip, auto-wake, no-ack norm")
    r = as_agent("a1", f'{CLIENT} send a2 "hello please-reply"')
    check("t1 send accepted", r.returncode == 0, r.stdout + r.stderr)
    ok = wait_for(lambda: "reply-from-a2" in agent_log("a1"))
    check("t1 a2 woke, logged, replied; a1 woke on the reply", ok, agent_log("a2"))
    time.sleep(2)  # would-be ack window
    n_sends = len(audit_events("send"))
    check("t1 no ack ping-pong (exactly 2 sends in audit)", n_sends == 2, f"sends={n_sends}")
    wakes = audit_events("wake")
    causes_ok = (len(wakes) == 2
                 and wakes[0]["agent"] == "a2" and wakes[0]["causes"][0]["from"] == "a1"
                 and wakes[1]["agent"] == "a1" and wakes[1]["causes"][0]["from"] == "a2")
    check("t1 every wake logged with its cause", causes_ok, json.dumps(wakes))

    print("T2: public chat is pull-only")
    wakes_before = len(audit_events("wake"))
    r = as_agent("a1", f'{CLIENT} post "public hello from a1"')
    check("t2 post accepted", r.returncode == 0, r.stdout + r.stderr)
    time.sleep(2)
    check("t2 posting woke NOBODY", len(audit_events("wake")) == wakes_before)
    r = as_agent("a2", f"{CLIENT} read-public")
    got = r.returncode == 0 and "public hello from a1" in r.stdout and '"from": "a1"' in r.stdout.replace('"from":"a1"', '"from": "a1"')
    check("t2 peer can pull the post with true author", got, r.stdout + r.stderr)

    print("T3: filesystem isolation (0700 homes)")
    r = as_agent("a1", "cat /agents/a2/log.txt")
    check("t3 a1 cannot read a2's files", r.returncode != 0, r.stdout)
    r = as_agent("a1", "ls /agents/a2/")
    check("t3 a1 cannot list a2's home", r.returncode != 0, r.stdout)
    r = as_agent("a1", "touch /agents/a2/inbox/evil.json")
    check("t3 a1 cannot write into a2's inbox directly", r.returncode != 0, r.stdout)

    print("T4: broker private state unreachable by agents")
    for path in [AUDIT, POLICY, "/data/broker/public.jsonl"]:
        r = as_agent("a1", f"cat {path}")
        check(f"t4 a1 cannot read {path}", r.returncode != 0, r.stdout[:100])
    r = as_agent("a1", "ls /data/broker/")
    check("t4 a1 cannot list broker state dir", r.returncode != 0, r.stdout)

    print("T5: sender identity cannot be spoofed")
    spoof = json.dumps({"op": "send", "to": "a3", "from": "a2", "text": "spoofed"})
    r = as_agent("a1", f"{CLIENT} raw '{spoof}'")
    check("t5 spoofed send accepted (but rewritten)", r.returncode == 0, r.stdout + r.stderr)
    ok = wait_for(lambda: "RECV from=a1 text=spoofed" in agent_log("a3"))
    check("t5 delivered message carries TRUE sender a1, not a2", ok, agent_log("a3"))
    last_send = audit_events("send")[-1]
    check("t5 audit records true sender", last_send["frm"] == "a1", json.dumps(last_send))

    print("T6: LIVE policy change (no restart)")
    set_policy(deny=[["a1", "a3"]])
    r = as_agent("a1", f'{CLIENT} send a3 "should be blocked"')
    check("t6 denied pair refused without broker restart", r.returncode != 0, r.stdout)
    denied = audit_events("send_denied")
    check("t6 denial audited", any(d["reason"] == "policy" for d in denied))
    set_policy()
    r = as_agent("a1", f'{CLIENT} send a3 "allowed again"')
    check("t6 re-allowed after policy revert", r.returncode == 0, r.stdout + r.stderr)

    print("T7: rate cap")
    set_policy(rate_limit_per_min=3)
    outs = [as_agent("a1", f'{CLIENT} send a3 "flood {i}"').returncode for i in range(6)]
    check("t7 flood partially refused", outs.count(0) <= 3 and 1 in outs, str(outs))
    check("t7 rate denial audited",
          any(d["reason"] == "rate_cap" for d in audit_events("send_denied")))
    set_policy()

    print("T8: size cap")
    r = as_agent("a1", f'{CLIENT} send a2 "{"x" * 20000}"')
    check("t8 oversized message refused", r.returncode != 0, r.stdout)
    check("t8 size denial audited",
          any(d["reason"] == "size_cap" for d in audit_events("send_denied")))

    print("T9: wakes are serialized per agent")
    log_before = agent_log("a2").count("RECV")
    for i in range(4):
        as_operator(f'{CLIENT} send a2 "burst {i}"')
    ok = wait_for(lambda: agent_log("a2").count("RECV") >= log_before + 4)
    check("t9 all 4 burst messages processed", ok, agent_log("a2"))
    check("t9 zero overlapping wakes", "OVERLAP" not in agent_log("a2"))
    check("t9 operator sends audited as operator",
          audit_events("send")[-1]["frm"] == "operator")

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail[:300]}")
        sys.exit(1)
    print("STEP 1 GATE: ALL GREEN")


if __name__ == "__main__":
    main()
