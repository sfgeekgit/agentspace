#!/usr/bin/env python3
"""pi-runtime CLI — the bash-to-broker shim agents (and the operator) use.

Docs: docs/runtime_pi.md §2.

Usage:
  broker_client.py send <to> <text...>
  broker_client.py post <text...>
  broker_client.py read-public [--since N]
  broker_client.py wake <to|*>           # operator-only: wake without a message
  broker_client.py raw '<json>'          # send an arbitrary request (tests)

Identity is NEVER an argument: the broker derives it from SO_PEERCRED.
Prints the broker's JSON response; exits 1 if ok=false.
"""
import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("BROKER_SOCKET", "/run/broker/broker.sock")


def request(obj):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    s.connect(SOCKET_PATH)
    s.sendall((json.dumps(obj) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.split(b"\n", 1)[0])


def main(argv):
    if len(argv) < 1:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "send" and len(args) >= 2:
        req = {"op": "send", "to": args[0], "text": " ".join(args[1:])}
    elif cmd == "post" and args:
        req = {"op": "post_public", "text": " ".join(args)}
    elif cmd == "read-public":
        since = 0
        if len(args) == 2 and args[0] == "--since":
            since = int(args[1])
        req = {"op": "read_public", "since": since}
    elif cmd == "wake" and len(args) == 1:
        req = {"op": "wake", "to": args[0]}
    elif cmd == "raw" and len(args) == 1:
        req = json.loads(args[0])
    else:
        sys.exit(__doc__)
    resp = request(req)
    print(json.dumps(resp))
    sys.exit(0 if resp.get("ok") else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
