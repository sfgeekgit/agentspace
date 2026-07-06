#!/usr/bin/env python3
"""Isolation checklist gate: functional shakeout + isolation checklist (no LLM, no tokens).

Runs as root inside the gate container AFTER setup_env.sh. Agent-identity
actions are performed via `su` so they carry real kernel credentials — the
same way a compromised/curious agent would act.

Docs: docs/runtime_pi.md §7. Ported from the OC-era 8-item sandbox checklist
(see learnings_2026-06-12.md): the "sandbox siblings" items become su tests
here.
"""
import json
import os
import pwd
import subprocess
import sys
import time

sys.path.insert(0, "/runtime_pi")
import pi_gateway as gateway  # same deployed dir; single source of defaults + state paths

CLIENT = "python3 /runtime_pi/pi_gateway_client.py"
AUDIT = gateway.AUDIT
POLICY = gateway.POLICY
PUBLIC = gateway.PUBLIC
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
    pol = dict(gateway.DEFAULT_POLICY)
    pol.update(overrides)
    gateway.write_policy(pol)   # atomic, and the same defaults the gateway ships


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
    try:
        resp = json.loads(r.stdout)
    except json.JSONDecodeError:
        resp = {}
    got = (r.returncode == 0 and resp.get("ok")
           and any(e.get("text") == "public hello from a1" and e.get("from") == "a1"
                   for e in resp.get("entries", [])))
    check("t2 peer can pull the post with true author", got, r.stdout + r.stderr)

    print("T3: filesystem isolation (0700 homes)")
    r = as_agent("a1", "cat /agents/a2/log.txt")
    check("t3 a1 cannot read a2's files", r.returncode != 0, r.stdout)
    r = as_agent("a1", "ls /agents/a2/")
    check("t3 a1 cannot list a2's home", r.returncode != 0, r.stdout)
    r = as_agent("a1", "touch /agents/a2/inbox/evil.json")
    check("t3 a1 cannot write into a2's inbox directly", r.returncode != 0, r.stdout)

    print("T4: gateway private state unreachable by agents")
    for path in [AUDIT, POLICY, PUBLIC]:
        r = as_agent("a1", f"cat {path}")
        check(f"t4 a1 cannot read {path}", r.returncode != 0, r.stdout[:100])
    r = as_agent("a1", f"ls {gateway.STATE_DIR}/")
    check("t4 a1 cannot list gateway state dir", r.returncode != 0, r.stdout)

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
    check("t6 denied pair refused without gateway restart", r.returncode != 0, r.stdout)
    denied = audit_events("send_denied")
    check("t6 denial audited", any(d["reason"] == "policy" for d in denied))
    set_policy()
    r = as_agent("a1", f'{CLIENT} send a3 "allowed again"')
    check("t6 re-allowed after policy revert", r.returncode == 0, r.stdout + r.stderr)

    print("T7: rate cap")
    # a3 has sent nothing yet, so its 60s window is empty — with cap=3 exactly
    # the first 3 sends must succeed and the next 3 must be refused. (Using a
    # sender already at the cap would let this pass even for a gateway that
    # denies every send.)
    set_policy(rate_limit_per_min=3)
    outs = [as_agent("a3", f'{CLIENT} send a1 "flood {i}"').returncode for i in range(6)]
    check("t7 exactly cap sends allowed, rest refused",
          outs == [0, 0, 0, 1, 1, 1], str(outs))
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

    print("T10: operator wake primitive (wake without delivering a message)")
    wakes_before = len(audit_events("wake"))
    sends_before = len(audit_events("send"))
    r = as_operator(f"{CLIENT} wake a2")
    check("t10 operator wake accepted", r.returncode == 0, r.stdout + r.stderr)
    ok = wait_for(lambda: len(audit_events("wake")) > wakes_before)
    last_wake = audit_events("wake")[-1]
    check("t10 wake ran with operator cause", ok
          and last_wake["agent"] == "a2"
          and last_wake["causes"] == [{"type": "operator"}], json.dumps(last_wake))
    check("t10 wake delivered no message", len(audit_events("send")) == sends_before)
    r = as_agent("a1", f"{CLIENT} wake a2")
    check("t10 agents cannot wake (operator-only)", r.returncode != 0, r.stdout)
    check("t10 agent wake attempt audited", any(
        e.get("reason") == "not_operator" for e in audit_events("wake_denied")))
    r = as_operator(f"{CLIENT} wake '*'")
    check("t10 wake-all accepted", r.returncode == 0, r.stdout + r.stderr)
    # let the wake-all's on_wake processes finish before the destructive test
    wait_for(lambda: not os.path.exists("/agents/a2/.wake_running"))

    print("T12: log_usage — spend attribution cannot be forged")
    spoof_usage = json.dumps({"op": "log_usage",
                              "usage": {"cost_total": 0.5, "agent": "a2", "model": "x"}})
    r = as_agent("a1", f"{CLIENT} raw '{spoof_usage}'")
    check("t12 log_usage accepted", r.returncode == 0, r.stdout + r.stderr)
    with open(gateway.BUDGET) as f:
        last = json.loads(f.readlines()[-1])
    check("t12 budget entry carries TRUE agent id (spoofed 'agent' overridden)",
          last["agent"] == "a1" and last["cost_total"] == 0.5, json.dumps(last))
    bad = json.dumps({"op": "log_usage", "usage": {"nested": {"a": 1}}})
    r = as_agent("a1", f"{CLIENT} raw '{bad}'")
    check("t12 non-scalar usage refused", r.returncode != 0, r.stdout)
    r = as_agent("a1", f"cat {gateway.BUDGET}")
    check("t12 budget log unreachable by agents", r.returncode != 0, r.stdout[:100])

    print("T13: who — agents can discover the roster")
    r = as_agent("a1", f"{CLIENT} who")
    try:
        who = json.loads(r.stdout)
    except json.JSONDecodeError:
        who = {}
    check("t13 who returns the full agent roster",
          r.returncode == 0 and who.get("agents") == ["a1", "a2", "a3"],
          r.stdout + r.stderr)

    print("T11: inbox symlink attack is refused (gateway never follows it)")
    # a2 redirects its own inbox at a victim dir; the gateway must refuse rather
    # than chown/write through the symlink. (Runs last: it breaks a2's inbox.)
    victim_uid_before = os.stat("/agents/a1").st_uid
    as_agent("a2", 'rm -rf "$HOME/inbox" && ln -s /agents/a1 "$HOME/inbox"')
    r = as_operator(f'{CLIENT} send a2 "attack"')
    check("t11 send through symlinked inbox refused", r.returncode != 0, r.stdout)
    check("t11 refusal audited as send_failed", len(audit_events("send_failed")) >= 1)
    check("t11 victim home ownership unchanged (no chown escape)",
          os.stat("/agents/a1").st_uid == victim_uid_before == pwd.getpwnam("u_a1").pw_uid)

    print()
    failed = [r for r in RESULTS if not r[1]]
    print(f"RESULT: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail[:300]}")
        sys.exit(1)
    print("CHECKLIST GATE: ALL GREEN")


if __name__ == "__main__":
    main()
