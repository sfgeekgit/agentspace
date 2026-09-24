#!/usr/bin/env python3
"""The public mirror's refresh trigger: the one thing a visitor can make this box do.

Caddy proxies `POST /publicview/refresh` here as `POST /refresh`. No query, no body, no arguments:
if the current build is older than its own min_refresh_seconds, run `zookeeper.py mirror publish`
once and answer with the new build's time; otherwise answer with the current one. Requests are
served one at a time, so a press during a publish waits for it and gets the same new build.

`POST /generate/<env>` is the one action beyond a refresh: `zookeeper.py results generate <env>`,
then a publish. The name must be one the current build itself marked `generate` (a finished recess
game whose results are missing or partial), so nothing a visitor sends is an argument the mirror
did not write first; one attempt per env per interval. Imports nothing from agentspace, so this
process holds no secrets. Loopback only, 127.0.0.1:7787, run as
the `agentspace-mirror-refresh` service. Docs: docs/mirror.md.
"""
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SITE = Path(os.environ.get("AGENTSPACE_MIRROR_DIR", "/srv/agentworldmaker-public")) / "current" / "site.json"
PUBLISH = [sys.executable, str(Path(__file__).resolve().parent / "zookeeper.py"), "mirror", "publish"]   # the gate swaps these
GENERATE = [sys.executable, str(Path(__file__).resolve().parent / "zookeeper.py"), "results", "generate"]
LOCK = "/run/lock/agentspace-mirror"
PORT = int(os.environ.get("AGENTSPACE_MIRROR_REFRESH_PORT", 7787))
last_attempt = 0.0            # a failing publish is debounced too
generated = {}                # env -> last generate attempt


def site():
    try:
        return json.loads(SITE.read_text())
    except (OSError, ValueError):
        return {}


def run(argv, timeout):
    """One child, its output to the journal, never to the client. False on failure."""
    try:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=sys.stderr, timeout=timeout, check=True)
        return True
    except (subprocess.SubprocessError, OSError) as e:
        print(f"{argv[-2]} {argv[-1]} failed: {e}", file=sys.stderr, flush=True)
        return False


def refresh(env=None):
    global last_attempt
    before, failed = site().get("generated_at"), False
    with open(LOCK, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        s = site()
        wait = s.get("min_refresh_seconds", 20)
        built = datetime.fromisoformat(s["generated_at"]).timestamp() if s.get("generated_at") else 0
        if env is not None:
            if not any(r.get("id") == env and r.get("generate") for r in s.get("runs", [])):
                return None                                    # not an env the build offers the button for
            if time.time() - generated.get(env, 0) < wait:
                return {"as_of": before, "generated": False}
            generated[env] = last_attempt = time.time()
            if not run(GENERATE + [env], 300):
                return {"as_of": before, "generated": False, "error": "generate failed"}
            failed = not run(PUBLISH, 120)
        elif time.time() - max(built, last_attempt) >= wait:
            last_attempt = time.time()
            failed = not run(PUBLISH, 120)
    now = site().get("generated_at")
    return {"as_of": now, "refreshed" if env is None else "generated": now != before} | ({"error": "publish failed"} if failed else {})


class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def reply(self, code, body=b""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:                                  # the client gave up waiting for a long publish
            pass

    def do_GET(self):
        self.reply(404)

    def do_POST(self):
        m = re.fullmatch(r"/refresh|/generate/([A-Za-z0-9][A-Za-z0-9_.-]{0,100})", self.path)
        if not m:
            return self.reply(404)
        if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0").strip() != "0":
            return self.reply(400)
        out = refresh(m.group(1))
        self.reply(200, json.dumps(out).encode()) if out else self.reply(404)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
