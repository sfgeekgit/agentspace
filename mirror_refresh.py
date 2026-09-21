#!/usr/bin/env python3
"""The public mirror's refresh trigger: the one thing a visitor can make this box do.

Caddy proxies `POST /publicview/refresh` here as `POST /refresh`. No query, no body, no arguments:
if the current build is older than its own min_refresh_seconds, run `zookeeper.py mirror publish`
once and answer with the new build's time; otherwise answer with the current one. Requests are
served one at a time, so a press during a publish waits for it and gets the same new build. Imports
nothing from agentspace, so this process holds no secrets. Loopback only, 127.0.0.1:7787, run as
the `agentspace-mirror-refresh` service. Docs: docs/mirror.md.
"""
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SITE = Path(os.environ.get("AGENTSPACE_MIRROR_DIR", "/srv/agentworldmaker-public")) / "current" / "site.json"
PUBLISH = [sys.executable, str(Path(__file__).resolve().parent / "zookeeper.py"), "mirror", "publish"]   # the gate swaps this
LOCK = "/run/lock/agentspace-mirror"
PORT = int(os.environ.get("AGENTSPACE_MIRROR_REFRESH_PORT", 7787))
last_attempt = 0.0            # a failing publish is debounced too


def site():
    try:
        return json.loads(SITE.read_text())
    except (OSError, ValueError):
        return {}


def refresh():
    global last_attempt
    before, failed = site().get("generated_at"), False
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        s = site()
        wait = s.get("min_refresh_seconds", 20)
        built = datetime.fromisoformat(s["generated_at"]).timestamp() if s.get("generated_at") else 0
        if time.time() - max(built, last_attempt) >= wait:
            last_attempt = time.time()
            try:                                              # the publisher's output goes to the journal, never to the client
                subprocess.run(PUBLISH, stdin=subprocess.DEVNULL, stdout=sys.stderr, timeout=120, check=True)
            except (subprocess.SubprocessError, OSError) as e:
                print(f"publish failed: {e}", file=sys.stderr, flush=True)
                failed = True
    now = site().get("generated_at")
    return {"as_of": now, "refreshed": now != before} | ({"error": "publish failed"} if failed else {})


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(404)

    def do_POST(self):
        if self.path != "/refresh":
            return self.reply(404)
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0").strip() != "0":
            return self.reply(400)
        self.reply(200, json.dumps(refresh()).encode())


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
