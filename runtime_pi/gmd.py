#!/usr/bin/env python3
"""gmd — the PI runtime's GM launcher + gateway adapter.

Started BY THE RUNTIME as the dedicated `gm` user (never root) when a world is
run and its scen ships a `gm.py`. This is the ONLY PI-specific GM code: it wraps
the pi_gateway socket as the `adapter` gmlib expects, then hands control to the
scen. Swap this file for an OC equivalent and gmlib + every scen run unchanged
(plan decision 10).

Lifecycle (plan decision 13): the runtime owns start/stop — this process is the
GM's whole life. It is RESUMABLE: on (re)start it just runs the scen's `run`
again, which re-reads on-disk state and continues (decision 14). It never wakes
agents on its own beyond what the scen's game logic asks for.
"""
import importlib.util
import json
import os
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, "/runtime_pi")  # baked alongside this file
import gmlib  # noqa: E402

SOCKET_PATH = os.environ.get("GATEWAY_SOCKET", "/run/gateway/gateway.sock")
WORLD_DIR = Path(os.environ.get("GMD_WORLD_DIR", "/world"))
# Must exceed the gateway's gm_wake block (WAKE_TIMEOUT_S + 30 = 330s default).
REQUEST_TIMEOUT_S = 400


def log(msg):
    with open(Path.home() / "gmd.log", "a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}\n")


class PiAdapter:
    """gmlib transport over the pi_gateway unix socket, as the `gm` user. One
    JSON line per op; a fresh connection per call (thread-safe: gmlib.round
    fans wakes out across threads)."""

    def _req(self, obj):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(REQUEST_TIMEOUT_S)
        s.connect(SOCKET_PATH)
        s.sendall((json.dumps(obj) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.split(b"\n", 1)[0])
        if not resp.get("ok"):
            log(f"gateway refused {obj.get('op')}: {resp.get('error')}")
        return resp

    def who(self):
        return self._req({"op": "who"}).get("agents", [])

    def wake(self, agent, payload):
        return self._req({"op": "gm_wake", "to": agent, "payload": payload})

    def collect(self, agent):
        return self._req({"op": "gm_collect", "agent": agent}).get("submission")

    def announce(self, text):
        return self._req({"op": "gm_announce", "text": text})

    def policy(self, pol):
        return self._req({"op": "gm_policy", "policy": pol})

    def remove(self, agent):
        return self._req({"op": "gm_remove", "agent": agent})

    def roll_session(self, agent):
        return self._req({"op": "gm_roll_session", "agent": agent})


def load_scen_gm():
    spec = importlib.util.spec_from_file_location("scen_gm", WORLD_DIR / "gm.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    cfg = json.loads((WORLD_DIR / "world.json").read_text())
    params = cfg.get("params", {})
    log(f"gm start: params={params}")
    try:
        gmlib.run(PiAdapter(), load_scen_gm().run, params,
                  str(Path.home() / "state.json"))
        log("gm run() returned (game complete)")
    except Exception as e:
        log(f"gm crashed: {e!r}")
        raise


if __name__ == "__main__":
    main()
