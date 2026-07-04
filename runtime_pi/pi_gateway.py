#!/usr/bin/env python3
"""The PI gateway — the only privileged piece of the PI runtime.

Named for its role parallel with the OpenClaw gateway: the single privileged
daemon every agent talks through. The parallel is ROLE-ONLY — this one is
deliberately thin (deliver, wake, audit; no LLM sessions, no containers, no
heartbeats, no TUI) and it never initiates anything on its own: every wake it
performs was caused by a message, a GM call, or an operator command. Game
logic never lives here — it belongs in scen code (GM, step 4).

Docs: docs/runtime_pi.md (single source of truth — protocol, policy format,
wake contract, checklist). Design history in the working notes:
/home/cc/2026-07-03_plan_pi_runtime.md §3, /home/cc/PLAN_C_no_openclaw.md §3a.

MVP surface:

- unix-socket daemon; sender identity + privilege from SO_PEERCRED (never
  from the request body, never from a name string — spoofing and role
  escalation are impossible by construction; see peer_identity/Principal).
- ops: send (PM: policy check -> inbox spool -> audit -> wake recipient),
  post_public (append, wakes NOBODY), read_public (pull), wake (operator-only:
  wake an agent/all with no message — the explicit "restart and wake" primitive),
  log_usage (agentd reports per-turn model usage/cost -> budget.jsonl, agent id
  from peercred so spend attribution cannot be forged).
- inbox delivery never follows a symlink the recipient could plant, and
  publishes each message atomically (temp + rename) so a concurrent drain
  never sees a half-written or root-owned file. See _deliver().
- wake = spawn /agents/<id>/on_wake as that agent's own user, serialized
  per agent (one wake at a time; messages arriving mid-wake trigger a
  follow-up wake, never a concurrent one).
- policy (allow/deny pairs, rate cap, size cap) is re-read from disk on
  every request: LIVE policy changes, no restart — the OC pain, fixed.
  Reads fail CLOSED (last-good policy, else deny-all); writes are atomic.
- everything appends to one audit JSONL: every send (incl. denials),
  every post, every wake with its cause.

Restart transparency: a snapshot/restore is invisible to agents. All durable
state (inboxes, public.jsonl, audit.jsonl, policy.json) is on disk; the only
cross-restart in-memory state that agents could observe — the sequence
counter — is rebuilt from disk at startup (recover_seq). A restart does NOT
wake agents (the DEFAULT): mail waits in the inbox for the next legitimate
wake, which drains the whole inbox. Waking on restart is a separate, explicit
opt-in owned by the operator/zookeeper — the `wake` op (op_wake) — never
something the gateway forces because mail happens to be present.

Runs as root inside the env container. All gateway state lives under
STATE_DIR (mode 0700) — unreadable by agents by kernel permission bits.
"""
import json
import os
import pwd
import socket
import struct
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque, namedtuple
from datetime import datetime, timezone

SOCKET_PATH = os.environ.get("GATEWAY_SOCKET", "/run/gateway/gateway.sock")
STATE_DIR = os.environ.get("GATEWAY_STATE", "/data/gateway")
AGENTS_DIR = os.environ.get("GATEWAY_AGENTS_DIR", "/agents")
USER_PREFIX = "u_"
# Real Pi turns (LLM + tool calls) need more than the 120s the dummy agents
# did; a turn that overruns is killed and audited as wake_error.
WAKE_TIMEOUT_S = int(os.environ.get("GATEWAY_WAKE_TIMEOUT_S", "300"))

AUDIT = os.path.join(STATE_DIR, "audit.jsonl")
PUBLIC = os.path.join(STATE_DIR, "public.jsonl")
POLICY = os.path.join(STATE_DIR, "policy.json")
BUDGET = os.path.join(STATE_DIR, "budget.jsonl")

DEFAULT_POLICY = {
    "max_msg_bytes": 16384,
    "rate_limit_per_min": 30,
    "allow": None,   # null = allow all pairs except "deny"; or list of [from, to]
    "deny": [],      # list of [from, to] pairs
}

# What load_policy returns when the file is unreadable AND there is no last-good
# policy to fall back on: deny everything. allow=[] means pair_allowed() is
# False for every non-operator pair; the zero caps block sends/posts outright.
FAILCLOSED_POLICY = {
    "max_msg_bytes": 0,
    "rate_limit_per_min": 0,
    "allow": [],
    "deny": [],
}

# Identity strings reserved for the gateway/operator; a Linux user u_<name> whose
# name collides with one of these is refused rather than allowed to impersonate.
RESERVED_IDS = {"operator"}

# A connected peer: its agent id (or "operator"), whether it holds operator
# privilege, and its uid. Privilege rides on is_operator (derived from uid==0),
# NEVER on the identity string — so an agent named "operator" cannot escalate.
Principal = namedtuple("Principal", "identity is_operator uid")

_audit_lock = threading.Lock()
_public_lock = threading.Lock()
_seq_lock = threading.Lock()
_seq = 0
_send_times = defaultdict(deque)  # sender -> deque of monotonic times
_send_times_lock = threading.Lock()
_policy_lock = threading.Lock()
_last_good_policy = None


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def next_seq():
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def _max_seq_in_jsonl(path):
    m = 0
    try:
        with open(path) as f:
            for line in f:
                try:
                    s = json.loads(line).get("seq")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if isinstance(s, int) and s > m:
                    m = s
    except FileNotFoundError:
        pass
    return m


def recover_seq():
    """Rebuild the sequence counter after a restart so it never goes backwards.

    audit.jsonl is append-only and records every seq the gateway ever issued
    (send + post_public), so its max is a hard floor; public.jsonl and the
    inbox/inbox_done spool filenames are belt-and-suspenders.
    """
    m = max(_max_seq_in_jsonl(AUDIT), _max_seq_in_jsonl(PUBLIC))
    for aid in agent_ids():
        try:
            home = pwd.getpwnam(USER_PREFIX + aid).pw_dir
        except KeyError:
            continue
        for sub in ("inbox", "inbox_done"):
            try:
                names = os.listdir(os.path.join(home, sub))
            except OSError:
                continue
            for n in names:
                stem = n[:-5] if n.endswith(".json") else ""
                if stem.isdigit():
                    m = max(m, int(stem))
    return m


def audit(event, **fields):
    rec = {"ts": now_iso(), "event": event, **fields}
    line = json.dumps(rec, sort_keys=True)
    with _audit_lock:
        with open(AUDIT, "a") as f:
            f.write(line + "\n")
    return rec


def write_policy(pol, path=None):
    """Write policy.json atomically (temp + rename) so a concurrent load never
    sees a half-written file. Used by the gateway and by scen/GM phase switches."""
    path = path or POLICY
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(pol, f, indent=2)
    os.replace(tmp, path)


def load_policy():
    """Re-read policy on every request (live changes, no restart). Fail CLOSED:
    a transient half-written file (e.g. a GM rewriting phase allowlists) returns
    the last-good policy; if there is no last-good, deny everything."""
    global _last_good_policy
    try:
        with open(POLICY) as f:
            pol = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        audit("policy_load_error", error=str(e))
        with _policy_lock:
            if _last_good_policy is not None:
                return dict(_last_good_policy)
        return dict(FAILCLOSED_POLICY)
    merged = dict(DEFAULT_POLICY)
    merged.update(pol)
    with _policy_lock:
        _last_good_policy = dict(merged)
    return merged


def _rate_ok(sender, cap):
    """Sliding 60s window per sender. cap<=0 blocks all (fail-closed policy)."""
    with _send_times_lock:
        q = _send_times[sender]
        now = time.monotonic()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= cap:
            return False
        q.append(now)
        return True


def pair_allowed(policy, sender, to):
    for f, t in policy.get("deny") or []:
        if (f in ("*", sender)) and (t in ("*", to)):
            return False
    allow = policy.get("allow")
    if allow is None:
        return True
    for f, t in allow:
        if (f in ("*", sender)) and (t in ("*", to)):
            return True
    return False


def agent_ids():
    return sorted(
        p.pw_name[len(USER_PREFIX):] for p in pwd.getpwall()
        if p.pw_name.startswith(USER_PREFIX)
    )


def peer_identity(conn):
    """Derive the caller's Principal from SO_PEERCRED. Operator privilege comes
    from uid==0 alone; agent identity comes from the u_<id> username. A uid that
    is neither, or whose id lands in RESERVED_IDS, is rejected (identity=None)."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", creds)
    if uid == 0:
        return Principal("operator", True, uid)
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return Principal(None, False, uid)
    if name.startswith(USER_PREFIX):
        aid = name[len(USER_PREFIX):]
        if aid in RESERVED_IDS:
            return Principal(None, False, uid)
        return Principal(aid, False, uid)
    return Principal(None, False, uid)


class WakeManager:
    """Serialized wakes: at most one on_wake process per agent at a time.

    Causes arriving while a wake runs are queued and coalesced into one
    follow-up wake after the current one exits.
    """

    def __init__(self):
        self.mu = threading.Lock()
        self.pending = {}   # agent -> [cause, ...]
        self.running = set()

    def wake(self, agent, cause):
        with self.mu:
            self.pending.setdefault(agent, []).append(cause)
            if agent in self.running:
                return
            self.running.add(agent)
        threading.Thread(target=self._runner, args=(agent,), daemon=True).start()

    def _runner(self, agent):
        while True:
            with self.mu:
                causes = self.pending.pop(agent, [])
                if not causes:
                    self.running.discard(agent)
                    return
            self._spawn(agent, causes)

    def _spawn(self, agent, causes):
        try:
            pw = pwd.getpwnam(USER_PREFIX + agent)
        except KeyError:
            audit("wake_error", agent=agent, error="no such user")
            return
        home = pw.pw_dir
        prog = os.path.join(home, "on_wake")
        audit("wake", agent=agent, causes=causes)
        t0 = time.monotonic()
        env = {
            "HOME": home,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "USER": pw.pw_name,
            "AGENT_ID": agent,
            "GATEWAY_SOCKET": SOCKET_PATH,
            "WAKE_CAUSES": json.dumps(causes),
        }
        try:
            # extra_groups=[] drops root's supplementary groups — without it
            # the child would keep root's group memberships and could read
            # peers' files. Load-bearing for isolation.
            # stdout -> DEVNULL: a chatty on_wake (e.g. a Pi agent streaming
            # model output) must not accumulate in the long-lived root gateway's
            # memory. Only the bounded stderr tail is kept for the audit record.
            r = subprocess.run(
                [prog], user=pw.pw_uid, group=pw.pw_gid, extra_groups=[],
                env=env, cwd=home, timeout=WAKE_TIMEOUT_S,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
            audit("wake_end", agent=agent, rc=r.returncode,
                  dur_s=round(time.monotonic() - t0, 3),
                  stderr=r.stderr[-500:] if r.stderr else "")
        except Exception as e:
            audit("wake_error", agent=agent, error=str(e)[:500])


WAKES = WakeManager()


def _deliver(pw, seq, msg):
    """Write msg into the recipient's inbox as an agent-owned 0600 file without
    ever following a symlink the recipient could have planted, and publish it
    atomically so a concurrent inbox drain never sees a partial or root-owned
    file.

    Trust model: /agents is root-owned, so the recipient cannot swap the
    /agents/<id> home-dir entry itself; but everything INSIDE the home is
    agent-controlled. So we open the home with O_NOFOLLOW, then create/verify
    the inbox and write the message using single-component paths relative to
    dir fds with O_NOFOLLOW|O_EXCL — no path component the agent can redirect.
    """
    uid, gid = pw.pw_uid, pw.pw_gid
    home_fd = os.open(pw.pw_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            inbox_fd = os.open("inbox", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=home_fd)
        except FileNotFoundError:
            os.mkdir("inbox", mode=0o700, dir_fd=home_fd)
            os.chown("inbox", uid, gid, dir_fd=home_fd, follow_symlinks=False)
            inbox_fd = os.open("inbox", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=home_fd)
        try:
            if os.fstat(inbox_fd).st_uid != uid:
                raise PermissionError("inbox not owned by recipient")
            data = (json.dumps(msg, sort_keys=True) + "\n").encode()
            tmp = f".tmp.{seq:08d}.{os.getpid()}"
            final = f"{seq:08d}.json"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=inbox_fd)
            try:
                os.write(fd, data)
                os.fchown(fd, uid, gid)
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            # Atomic reveal: the final name appears only fully-written + owned.
            # rename does not follow a symlink at either name and stays within
            # the verified inbox dir fd, so the agent cannot redirect it out.
            os.rename(tmp, final, src_dir_fd=inbox_fd, dst_dir_fd=inbox_fd)
        finally:
            os.close(inbox_fd)
    finally:
        os.close(home_fd)


def op_send(pr, req):
    sender = pr.identity
    to = req.get("to")
    text = req.get("text")
    if not isinstance(to, str) or not isinstance(text, str):
        return {"ok": False, "error": "send needs string 'to' and 'text'"}
    if to in RESERVED_IDS:
        audit("send_denied", frm=sender, to=to, reason="reserved")
        return {"ok": False, "error": f"reserved recipient: {to}"}
    try:
        pw = pwd.getpwnam(USER_PREFIX + to)
    except KeyError:
        audit("send_denied", frm=sender, to=to, reason="no_such_agent")
        return {"ok": False, "error": f"no such agent: {to}"}

    policy = load_policy()
    nbytes = len(text.encode())
    if nbytes > policy["max_msg_bytes"]:
        audit("send_denied", frm=sender, to=to, reason="size_cap",
              bytes=nbytes, cap=policy["max_msg_bytes"])
        return {"ok": False, "error": "message exceeds size cap"}
    if not pr.is_operator:
        if not pair_allowed(policy, sender, to):
            audit("send_denied", frm=sender, to=to, reason="policy")
            return {"ok": False, "error": "policy: not allowed to message this agent"}
        if not _rate_ok(sender, policy["rate_limit_per_min"]):
            audit("send_denied", frm=sender, to=to, reason="rate_cap",
                  cap=policy["rate_limit_per_min"])
            return {"ok": False, "error": "rate cap exceeded"}

    seq = next_seq()
    msg = {"seq": seq, "ts": now_iso(), "from": sender, "to": to, "text": text}
    try:
        _deliver(pw, seq, msg)
    except Exception as e:
        audit("send_failed", frm=sender, to=to, seq=seq, error=str(e)[:200])
        return {"ok": False, "error": "delivery failed"}
    audit("send", frm=sender, to=to, seq=seq, bytes=nbytes)
    WAKES.wake(to, {"type": "pm", "from": sender, "seq": seq})
    return {"ok": True, "seq": seq}


def op_post_public(pr, req):
    sender = pr.identity
    text = req.get("text")
    if not isinstance(text, str):
        return {"ok": False, "error": "post_public needs string 'text'"}
    policy = load_policy()
    if len(text.encode()) > policy["max_msg_bytes"]:
        audit("post_denied", frm=sender, reason="size_cap")
        return {"ok": False, "error": "message exceeds size cap"}
    if not pr.is_operator and not _rate_ok(sender, policy["rate_limit_per_min"]):
        # Same hard anti-flood backstop as send — the public surface is not exempt.
        audit("post_denied", frm=sender, reason="rate_cap",
              cap=policy["rate_limit_per_min"])
        return {"ok": False, "error": "rate cap exceeded"}
    seq = next_seq()
    entry = {"seq": seq, "ts": now_iso(), "from": sender, "text": text}
    with _public_lock:
        with open(PUBLIC, "a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    audit("post_public", frm=sender, seq=seq)
    # Posting wakes NOBODY — pull-only surface by design.
    return {"ok": True, "seq": seq}


def op_read_public(pr, req):
    since = req.get("since", 0)
    if not isinstance(since, int):
        return {"ok": False, "error": "'since' must be an integer seq"}
    entries = []
    skipped = 0
    with _public_lock:
        try:
            with open(PUBLIC) as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        # One torn/partial line (e.g. a crash mid-append) must
                        # not permanently break all public reads — skip it.
                        skipped += 1
                        continue
                    if e.get("seq", 0) > since:
                        entries.append(e)
        except FileNotFoundError:
            pass
    audit("read_public", frm=pr.identity, since=since,
          returned=len(entries), skipped=skipped)
    return {"ok": True, "entries": entries}


def op_wake(pr, req):
    """Operator-only: wake an agent (or all with '*') WITHOUT delivering a
    message. This is the explicit primitive for "restart and wake" — the gateway
    never auto-wakes on restart (see the module docstring), so zookeeper/the
    operator uses this to opt in. The wake carries cause {"type":"operator"};
    the agent runs and drains whatever is already in its inbox, nothing new is
    injected. (Step 4's gm_wake generalizes this with a payload.)"""
    if not pr.is_operator:
        audit("wake_denied", frm=pr.identity, reason="not_operator")
        return {"ok": False, "error": "wake is operator-only"}
    to = req.get("to")
    if not isinstance(to, str):
        return {"ok": False, "error": "wake needs string 'to' (agent id or '*')"}
    if to == "*":
        targets = agent_ids()
    else:
        if to in RESERVED_IDS:
            return {"ok": False, "error": f"reserved target: {to}"}
        try:
            pwd.getpwnam(USER_PREFIX + to)
        except KeyError:
            audit("wake_denied", frm=pr.identity, to=to, reason="no_such_agent")
            return {"ok": False, "error": f"no such agent: {to}"}
        targets = [to]
    for aid in targets:
        WAKES.wake(aid, {"type": "operator"})
    audit("wake_requested", frm=pr.identity, targets=targets)
    return {"ok": True, "woke": targets}


def op_who(pr, req):
    """Discovery: list the agent ids that exist in this world. Read-only,
    derived from the user database — always true, nothing to maintain. This
    (not a PEERS file) is how agents learn who they can message."""
    return {"ok": True, "agents": agent_ids()}


_budget_lock = threading.Lock()


def op_log_usage(pr, req):
    """agentd reports one turn's model usage/cost; appended to budget.jsonl
    with the PEERCRED-derived agent id — an agent cannot attribute spend to a
    peer. Values are shallow-validated (small flat dict of scalars) so a
    hostile agent cannot bloat root-owned state; flooding is rate-capped on a
    separate counter from send (usage logging must not compete with mail)."""
    usage = req.get("usage")
    if not isinstance(usage, dict) or len(usage) > 24:
        return {"ok": False, "error": "log_usage needs a small dict 'usage'"}
    clean = {}
    for k, v in usage.items():
        if not isinstance(k, str) or len(k) > 64:
            return {"ok": False, "error": "bad usage key"}
        if not isinstance(v, (bool, int, float, str, type(None))):
            return {"ok": False, "error": f"usage[{k!r}] must be a scalar"}
        if isinstance(v, str) and len(v) > 200:
            return {"ok": False, "error": f"usage[{k!r}] too long"}
        clean[k] = v
    if not pr.is_operator and not _rate_ok(f"usage:{pr.identity}",
                                           load_policy()["rate_limit_per_min"]):
        audit("usage_denied", frm=pr.identity, reason="rate_cap")
        return {"ok": False, "error": "rate cap exceeded"}
    # clean first, protected fields last — a usage dict containing "agent"
    # or "ts" must not override the peercred-derived values.
    rec = {**clean, "ts": now_iso(), "agent": pr.identity}
    with _budget_lock:
        with open(BUDGET, "a") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")
    return {"ok": True}


OPS = {"send": op_send, "post_public": op_post_public,
       "read_public": op_read_public, "wake": op_wake,
       "log_usage": op_log_usage, "who": op_who}


def handle(conn):
    try:
        pr = peer_identity(conn)
        if pr.identity is None:
            conn.sendall(b'{"ok": false, "error": "unknown peer"}\n')
            audit("denied_peer", uid=pr.uid)
            return
        buf = b""
        conn.settimeout(10)
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
            if len(buf) > 1 << 20:
                conn.sendall(b'{"ok": false, "error": "request too large"}\n')
                return
        try:
            req = json.loads(buf.split(b"\n", 1)[0])
        except json.JSONDecodeError:
            conn.sendall(b'{"ok": false, "error": "bad json"}\n')
            return
        op = OPS.get(req.get("op"))
        if op is None:
            resp = {"ok": False, "error": f"unknown op: {req.get('op')!r}"}
        else:
            resp = op(pr, req)
        conn.sendall((json.dumps(resp) + "\n").encode())
    except Exception as e:
        audit("handler_error", error=str(e)[:500])
        try:
            conn.sendall(b'{"ok": false, "error": "internal error"}\n')
        except OSError:
            pass
    finally:
        conn.close()


def main():
    if os.geteuid() != 0:
        sys.exit("gateway must run as root")
    os.umask(0o077)
    global _seq
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if not os.path.exists(POLICY):
        write_policy(DEFAULT_POLICY)
    os.makedirs(os.path.dirname(SOCKET_PATH), mode=0o755, exist_ok=True)
    os.chmod(os.path.dirname(SOCKET_PATH), 0o755)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    # Resume the sequence counter from disk so a snapshot/restore is invisible
    # to agents (no backwards seqs, no inbox-filename collisions).
    _seq = recover_seq()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # world-connectable; identity via SO_PEERCRED
    srv.listen(64)
    audit("gateway_start", agents=agent_ids(), socket=SOCKET_PATH, seq=_seq)
    print(f"pi-runtime gateway listening on {SOCKET_PATH}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
