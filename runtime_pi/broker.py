#!/usr/bin/env python3
"""pi-runtime broker — the only privileged piece of the PI runtime.

Docs: docs/runtime_pi.md (single source of truth — protocol, policy format,
wake contract, checklist). Design history in the working notes:
/home/cc/2026-07-03_plan_pi_runtime.md §3, /home/cc/PLAN_C_no_openclaw.md §3a.

MVP surface:

- unix-socket daemon; sender identity from SO_PEERCRED (never from the
  request body — spoofing is impossible by construction).
- ops: send (PM: policy check -> inbox spool -> audit -> wake recipient),
  post_public (append, wakes NOBODY), read_public (pull).
- wake = spawn /agents/<id>/on_wake as that agent's own user, serialized
  per agent (one wake at a time; messages arriving mid-wake trigger a
  follow-up wake, never a concurrent one).
- policy (allow/deny pairs, rate cap, size cap) is re-read from disk on
  every request: LIVE policy changes, no restart — the OC pain, fixed.
- everything appends to one audit JSONL: every send (incl. denials),
  every post, every wake with its cause.

Runs as root inside the env container. All broker state lives under
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
from collections import defaultdict, deque

SOCKET_PATH = os.environ.get("BROKER_SOCKET", "/run/broker/broker.sock")
STATE_DIR = os.environ.get("BROKER_STATE", "/data/broker")
AGENTS_DIR = os.environ.get("BROKER_AGENTS_DIR", "/agents")
USER_PREFIX = "u_"
WAKE_TIMEOUT_S = 120

AUDIT = os.path.join(STATE_DIR, "audit.jsonl")
PUBLIC = os.path.join(STATE_DIR, "public.jsonl")
POLICY = os.path.join(STATE_DIR, "policy.json")

DEFAULT_POLICY = {
    "max_msg_bytes": 16384,
    "rate_limit_per_min": 30,
    "allow": None,   # null = allow all pairs except "deny"; or list of [from, to]
    "deny": [],      # list of [from, to] pairs
}

_audit_lock = threading.Lock()
_public_lock = threading.Lock()
_seq_lock = threading.Lock()
_seq = 0
_send_times = defaultdict(deque)  # sender -> deque of monotonic times
_send_times_lock = threading.Lock()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"


def next_seq():
    global _seq
    with _seq_lock:
        _seq += 1
        return _seq


def audit(event, **fields):
    rec = {"ts": now_iso(), "event": event, **fields}
    line = json.dumps(rec, sort_keys=True)
    with _audit_lock:
        with open(AUDIT, "a") as f:
            f.write(line + "\n")
    return rec


def load_policy():
    try:
        with open(POLICY) as f:
            pol = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        audit("policy_load_error", error=str(e))
        return dict(DEFAULT_POLICY)
    merged = dict(DEFAULT_POLICY)
    merged.update(pol)
    return merged


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
    """Map the connecting process's uid to an identity. Root = operator."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", creds)
    if uid == 0:
        return "operator", uid
    try:
        name = pwd.getpwuid(uid).pw_name
    except KeyError:
        return None, uid
    if name.startswith(USER_PREFIX):
        return name[len(USER_PREFIX):], uid
    return None, uid


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
            "BROKER_SOCKET": SOCKET_PATH,
            "WAKE_CAUSES": json.dumps(causes),
        }
        try:
            # extra_groups=[] drops root's supplementary groups — without it
            # the child would keep root's group memberships and could read
            # peers' files. Load-bearing for isolation.
            r = subprocess.run(
                [prog], user=pw.pw_uid, group=pw.pw_gid, extra_groups=[],
                env=env, cwd=home, timeout=WAKE_TIMEOUT_S,
                capture_output=True, text=True,
            )
            audit("wake_end", agent=agent, rc=r.returncode,
                  dur_s=round(time.monotonic() - t0, 3),
                  stderr=r.stderr[-500:] if r.stderr else "")
        except Exception as e:
            audit("wake_error", agent=agent, error=str(e)[:500])


WAKES = WakeManager()


def op_send(sender, req):
    to = req.get("to")
    text = req.get("text")
    if not isinstance(to, str) or not isinstance(text, str):
        return {"ok": False, "error": "send needs string 'to' and 'text'"}
    if to not in agent_ids():
        audit("send_denied", frm=sender, to=to, reason="no_such_agent")
        return {"ok": False, "error": f"no such agent: {to}"}

    policy = load_policy()
    nbytes = len(text.encode())
    if nbytes > policy["max_msg_bytes"]:
        audit("send_denied", frm=sender, to=to, reason="size_cap",
              bytes=nbytes, cap=policy["max_msg_bytes"])
        return {"ok": False, "error": "message exceeds size cap"}
    if sender != "operator":
        if not pair_allowed(policy, sender, to):
            audit("send_denied", frm=sender, to=to, reason="policy")
            return {"ok": False, "error": "policy: not allowed to message this agent"}
        cap = policy["rate_limit_per_min"]
        with _send_times_lock:
            q = _send_times[sender]
            now = time.monotonic()
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= cap:
                audit("send_denied", frm=sender, to=to, reason="rate_cap", cap=cap)
                return {"ok": False, "error": "rate cap exceeded"}
            q.append(now)

    seq = next_seq()
    msg = {"seq": seq, "ts": now_iso(), "from": sender, "to": to, "text": text}
    pw = pwd.getpwnam(USER_PREFIX + to)
    inbox = os.path.join(pw.pw_dir, "inbox")
    os.makedirs(inbox, mode=0o700, exist_ok=True)
    os.chown(inbox, pw.pw_uid, pw.pw_gid)
    path = os.path.join(inbox, f"{seq:08d}.json")
    with open(path, "w") as f:
        f.write(json.dumps(msg, sort_keys=True) + "\n")
    os.chmod(path, 0o600)
    os.chown(path, pw.pw_uid, pw.pw_gid)
    audit("send", frm=sender, to=to, seq=seq, bytes=nbytes)
    WAKES.wake(to, {"type": "pm", "from": sender, "seq": seq})
    return {"ok": True, "seq": seq}


def op_post_public(sender, req):
    text = req.get("text")
    if not isinstance(text, str):
        return {"ok": False, "error": "post_public needs string 'text'"}
    policy = load_policy()
    if len(text.encode()) > policy["max_msg_bytes"]:
        audit("post_denied", frm=sender, reason="size_cap")
        return {"ok": False, "error": "message exceeds size cap"}
    seq = next_seq()
    entry = {"seq": seq, "ts": now_iso(), "from": sender, "text": text}
    with _public_lock:
        with open(PUBLIC, "a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    audit("post_public", frm=sender, seq=seq)
    # Posting wakes NOBODY — pull-only surface by design.
    return {"ok": True, "seq": seq}


def op_read_public(sender, req):
    since = req.get("since", 0)
    if not isinstance(since, int):
        return {"ok": False, "error": "'since' must be an integer seq"}
    entries = []
    with _public_lock:
        try:
            with open(PUBLIC) as f:
                for line in f:
                    e = json.loads(line)
                    if e["seq"] > since:
                        entries.append(e)
        except FileNotFoundError:
            pass
    audit("read_public", frm=sender, since=since, returned=len(entries))
    return {"ok": True, "entries": entries}


OPS = {"send": op_send, "post_public": op_post_public, "read_public": op_read_public}


def handle(conn):
    try:
        sender, uid = peer_identity(conn)
        if sender is None:
            conn.sendall(b'{"ok": false, "error": "unknown peer"}\n')
            audit("denied_peer", uid=uid)
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
            resp = op(sender, req)
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
        sys.exit("broker must run as root")
    os.umask(0o077)
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)
    if not os.path.exists(POLICY):
        with open(POLICY, "w") as f:
            json.dump(DEFAULT_POLICY, f, indent=2)
    os.makedirs(os.path.dirname(SOCKET_PATH), mode=0o755, exist_ok=True)
    os.chmod(os.path.dirname(SOCKET_PATH), 0o755)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o666)  # world-connectable; identity via SO_PEERCRED
    srv.listen(64)
    audit("broker_start", agents=agent_ids(), socket=SOCKET_PATH)
    print(f"pi-runtime broker listening on {SOCKET_PATH}", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
