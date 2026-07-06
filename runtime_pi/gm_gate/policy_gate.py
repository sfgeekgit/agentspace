#!/usr/bin/env python3
"""Policy gate (engine): live phase physics + observation at N=5, zero tokens.
Run inside the container after setup_world.sh with policy_driver.py.

Script: phase A everyone posts (open board). Phase B the driver closes the
board allowing only a2→a5 PMs: a1/a4 try posts (denied), a3 tries a3→a4
(denied), a2 PMs a5 (delivered; the PM auto-wake just burns one of a5's spare
`skip` lines, so ordering can't skew the script). Phase C reopens and fans out
a submit round. The driver saves its gm_activity view + collected subs to
state.json; we cross-check against the gateway's own records.
"""
import glob
import json
import subprocess
import sys

FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def su(user, cmd):
    return subprocess.run(["su", user, "-s", "/bin/sh", "-c", cmd],
                          capture_output=True, text=True)


p = subprocess.Popen(["python3", "/runtime_pi/gmd.py"], user="gm",
                     env={"HOME": "/gm", "GATEWAY_SOCKET": "/run/gateway/gateway.sock",
                          "PATH": "/usr/local/bin:/usr/bin:/bin"})
rc = p.wait(timeout=120)
check("driver exits clean", rc == 0, f"rc={rc}")

state = json.load(open("/gm/state.json"))
pub = [json.loads(l) for l in open("/data/gateway/public.jsonl")]
audit = [json.loads(l) for l in open("/data/gateway/audit.jsonl")]
texts = [e["text"] for e in pub]

check("open board: all 5 phase-A posts landed",
      sum(t.startswith("open-") for t in texts) == 5, str(texts))
check("closed board: no phase-B post landed",
      not any(t.startswith("closed-") for t in texts), str(texts))
pd = [e for e in audit if e.get("event") == "post_denied" and e.get("reason") == "policy"]
check("closed board: both posts denied by policy",
      sorted(e["frm"] for e in pd) == ["a1", "a4"], str(pd))
sd = [e for e in audit if e.get("event") == "send_denied" and e.get("reason") == "policy"]
check("PM outside the allowlist denied (a3->a4)",
      [(e["frm"], e["to"]) for e in sd] == [("a3", "a4")], str(sd))
inbox = " ".join(open(f).read() for f in glob.glob("/agents/a5/inbox_done/*.json"))
check("allowlisted PM delivered (a2->a5)", "hello-a5" in inbox)

acts = state["activity"]
check("gm_activity: 5 posts + 1 send, denials excluded",
      sorted(e["event"] for e in acts) == ["post_public"] * 5 + ["send"], str(acts))
check("gm_activity metadata only (no content field)",
      all("text" not in e for e in acts))
check("fan-out at N=5: all collected, silent agent defaulted",
      state["subs"] == {f"a{i}": f"s-a{i}" for i in range(1, 5)} | {"a5": "none"},
      str(state["subs"]))
wakes = [e for e in audit if e.get("event") == "gm_wake"]
check("15 gm_wakes (3 phases x 5)", len(wakes) == 15, str(len(wakes)))
check("agents cannot read /gm/secrets.json",
      su("u_a1", "cat /gm/secrets.json").returncode != 0)

print()
if FAIL:
    print(f"POLICY GATE: {len(FAIL)} FAILURE(S): {FAIL}")
    sys.exit(1)
print("POLICY GATE: ALL PASS")
