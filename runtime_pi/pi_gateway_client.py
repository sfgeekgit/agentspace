#!/usr/bin/env python3
"""pi-runtime CLI — the bash-to-gateway shim agents (and the operator) use.

Docs: docs/runtime_pi.md §2.

Usage:
  pi_gateway_client.py send <to> <text...>
  pi_gateway_client.py post <text...>
  pi_gateway_client.py submit <action...>     # hand the GM a structured action (GM scens)
  pi_gateway_client.py read-public [--since N]
  pi_gateway_client.py wake <to|*>           # operator-only: wake without a message
  pi_gateway_client.py who                   # list agent ids in this world
  pi_gateway_client.py raw '<json>'          # send an arbitrary request (tests)

Identity is NEVER an argument: the gateway derives it from SO_PEERCRED.
Prints the gateway's JSON response; exits 1 if ok=false.
"""
import json
import os
import socket
import sys

SOCKET_PATH = os.environ.get("GATEWAY_SOCKET", "/run/gateway/gateway.sock")


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
    elif cmd == "submit" and args:
        req = {"op": "submit", "action": " ".join(args)}
    elif cmd == "read-public":
        since = 0
        if len(args) == 2 and args[0] == "--since":
            since = int(args[1])
        req = {"op": "read_public", "since": since}
    elif cmd == "wake" and len(args) == 1:
        req = {"op": "wake", "to": args[0]}
    elif cmd == "who":
        req = {"op": "who"}
    elif cmd == "raw" and len(args) == 1:
        req = json.loads(args[0])
    else:
        sys.exit(__doc__)
    resp = request(req)
    print(json.dumps(resp))
    sys.exit(0 if resp.get("ok") else 1)


if __name__ == "__main__":
    main(sys.argv[1:])
