#!/usr/bin/env python3
"""Real installed Pi + agentd, local fake provider, no network or paid calls.

docker run --rm --network none -v /opt/agentspace-ctl:/repo:ro pi-world:base \
    python3 /repo/runtime_pi/prompt_capture_gate.py
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


requests = []


class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": "A complete reply."}, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}]
        for chunk in chunks:
            self.wfile.write(("data: " + json.dumps({"id": "gate", "object": "chat.completion.chunk", "created": 1, "model": "gate-model", **chunk}) + "\n\n").encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_):
        pass


spec = importlib.util.spec_from_file_location("agentd", "/repo/runtime_pi/agentd.py")
agentd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agentd)
server = HTTPServer(("127.0.0.1", 0), Provider)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    # This gate runs only in its disposable container; no real credentials exist.
    settings = Path.home() / ".pi/agent"
    settings.mkdir(parents=True, exist_ok=True)
    (settings / "models.json").write_text(json.dumps({"providers": {"openrouter": {
        "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "apiKey": "fixture-only",
        "api": "openai-completions", "models": [{"id": "gate-model", "name": "Gate", "reasoning": False,
        "input": ["text"], "contextWindow": 8192, "maxTokens": 256,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}]}}}))
    agentd.KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    agentd.KEY_FILE.write_text("fixture-only")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        (home / "sessions").mkdir()
        cfg = {"model": "gate-model", "plain": True, "thinking": "off"}
        for index in range(2):
            ok, _, _, reply = agentd.run_pi_turn(home, "Full system\n  spacing\n", f"Scene {index}\n\nAll input.", cfg, bool(index))
            assert ok and reply == "A complete reply.", (ok, reply)
        logs = [json.loads(line) for line in (home / "prompt_log.jsonl").read_text().splitlines()]
        assert len(logs) == len(requests) == 2, (len(logs), len(requests))
        for log, request in zip(logs, requests):
            actual = [m for m in request["messages"] if m["role"] in ("system", "developer")]
            assert log["messages"] == actual
            assert "Full system\n  spacing" in actual[0]["content"]
            assert "Current date:" in actual[0]["content"]
            assert "Current working directory:" in actual[0]["content"]
            assert log["session"] and Path(log["session"]).is_file()
            assert "fixture-only" not in json.dumps(log)
        assert logs[0]["session"] == logs[1]["session"]
        assert sum(m["role"] == "user" for m in requests[1]["messages"]) == 2
        print("PROMPT CAPTURE GATE: ALL PASS (real Pi; two local fake-provider calls; zero paid tokens)")
finally:
    server.shutdown()
    server.server_close()
