#!/usr/bin/env python3
"""Results regression gate: no model calls; publication uses a local bare repo."""
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentspace import results as r
from agentspace import result_view as rv


def fixture():
    rows = [{"type": "session", "timestamp": "2026-09-20T08:00:00Z"}]
    for i, (role, text) in enumerate([("user", "First scene.\n\nWhole input."), ("assistant", "I look around.\nThen listen.")]):
        rows.append({"type": "message", "id": str(i), "timestamp": f"2026-09-20T08:00:0{i+1}Z",
                     "message": {"role": role, "content": [{"type": "thinking", "thinking": "DO NOT EXPORT THINKING"}, {"type": "text", "text": text}]}})
    return {"name": "run1", "scenario": "recess_fixture", "captured_at": r.now(), "snap_id": "fixture",
            "state": {"player": "p1", "gm": "g1", "turn": 1, "phase": "done", "ended": "left_town", "attributes": {"curiosity": 1},
                      "fixed_attributes": ["curiosity"], "npcs": {"lucia": {"agent": "n1"}}},
            "events": [{"kind": "accepted_plan", "turn": 1, "text": json.dumps({"attributes": [{"name": "curiosity", "delta": 1}]})},
                       {"kind": "conversation", "turn": 0, "text": "lucia"}],
            "audit": [{"event": "dispatch_wake", "to": "n1"}, {"event": "dispatch_wake", "to": "n1"}, {"event": "dispatch_wake", "to": "g1"}],
            "usage": [], "world": {"params": {"max_turns": 80}}, "dispatch_log": "", "dispatcher_running": False,
            "roster": [{"id": a, "role": role} for a, role in [("g1", "gm"), ("p1", "player"), ("n1", "npc")]],
            "agents": {"p1": {"sessions/one.jsonl": r.lines(rows), "sessions/.sysprompt": "# ROLE.md\n\nPlayer.\n\n# WORLD.md\n\nA world."},
                       "g1": {"ROLE.md": "GM."}, "n1": {"ROLE.md": "NPC."}}}


class ResultsGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="results-gate-")
        self.root = Path(self.tmp.name) / "results"
        self.env = patch.dict(os.environ, {"AGENTSPACE_RESULTS_DIR": str(self.root)})
        self.env.start()
        self.data = fixture()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def generate(self):
        with patch.object(r, "capture", return_value=self.data):
            return r.generate("run1")

    def test_completion_needs_evidence_not_turn_cap_or_container(self):
        data = self.data
        self.assertTrue(r.completion(data)["complete"])
        data["state"].update(phase="narrate", turn=80, ended="turn_cap")
        self.assertEqual(r.completion(data)["status"], "incomplete")
        data["dispatcher_running"] = True
        self.assertEqual(r.completion(data)["status"], "in_progress")
        data["dispatcher_running"] = False
        data["dispatch_log"] = "dispatch run() returned (game complete)\ndispatch start: resumed\ndispatch crashed: no reply"
        self.assertEqual(r.completion(data)["status"], "stalled")
        data["state"]["paused_reason"] = "Retry needed"
        self.assertEqual(r.completion(data)["status"], "paused")
        del data["state"]["paused_reason"]
        data["events"].append({"kind": "game_over"})
        self.assertTrue(r.completion(data)["complete"])

    def test_metrics_count_retries_and_only_applied_changes(self):
        data = self.data
        data["events"].extend([
            {"kind": "rejected_plan", "turn": 1, "text": "bad"},
            {"kind": "accepted_plan", "turn": 2, "text": '{"attributes":[{"name":"curiosity","delta":1}]}'},
            {"kind": "accepted_plan", "turn": 4, "text": '{"attributes":[{"name":"curiosity","delta":-1}]}'}])
        data["state"]["attributes"]["curiosity"] = 0
        stats = r.metrics(data)
        self.assertEqual((stats["npc_calls"], stats["npc_interactions"], stats["stat_updates"]), (2, 1, 2))
        self.assertTrue(stats["stat_replay_matches_state"])
        for turn in range(7, 37, 3):
            data["events"].append({"kind": "accepted_plan", "turn": turn, "text": '{"attributes":[{"name":"curiosity","delta":1}]}'})
        data["state"]["attributes"]["curiosity"] = 8
        self.assertEqual(r.metrics(data)["stat_updates"], 10)
        self.assertTrue(r.metrics(data)["stat_replay_matches_state"])

    def test_transcript_includes_archives_full_text_and_honest_prompt_sources(self):
        files, result = r.render(self.data)
        rows = r.json_lines(files["transcript.jsonl"])
        self.assertEqual(rows[0]["role"], "system")
        self.assertIn("# WORLD.md", rows[0]["text"])
        self.assertIn("Current date: 2026-09-20", rows[0]["text"])
        self.assertEqual(rows[1]["text"], "First scene.\n\nWhole input.")
        self.assertNotIn("DO NOT EXPORT THINKING", "".join(files.values()))
        self.assertNotIn("I look around", files["agent_prompts.md"])
        self.assertEqual([p["agent"] for p in r.json_lines(files["agent_prompts.jsonl"])], ["p1", "g1", "n1"])
        self.assertTrue(any("not recorded" in w for w in result["warnings"]))
        fileset = self.data["agents"]["p1"]
        fileset["sessions/archive/one.jsonl"] = fileset.pop("sessions/one.jsonl")
        fileset["sessions/archive/one.sysprompt"] = "Original archived instructions."
        self.assertIn("Original archived instructions.", r.render(self.data)[0]["transcript.md"])

    def test_exact_provider_prompt_is_first_and_unmodified(self):
        self.data["agents"]["p1"]["prompt_log.jsonl"] = r.lines([{
            "kind": "provider_system", "timestamp": "2026-09-20T08:00:01.500Z", "session": "/agents/p1/sessions/one.jsonl",
            "messages": [{"role": "system", "content": "Exact system.\n  Whitespace preserved.\n"}]}])
        transcript, systems, _, warnings = r.prompt_exports(self.data)
        self.assertEqual(transcript[0]["text"], "Exact system.\n  Whitespace preserved.\n")
        self.assertEqual(systems[0]["source"], "recorded provider system prompt")
        self.assertFalse(any("p1:" in w for w in warnings))

    def test_zip_files_are_verified_and_paths_restricted(self):
        self.generate()
        with zipfile.ZipFile(io.BytesIO(r.download("run1", "all.zip"))) as archive:
            self.assertIn("run1/agent_prompts.md", archive.namelist())
            self.assertEqual(archive.read("run1/transcript.md"), r.download("run1", "transcript.md"))
        for name in ("../run1", "/tmp/run1", "run1/../../x"):
            with self.assertRaises(ValueError):
                r.download(name, "all.zip")
        with self.assertRaises(ValueError):
            r.download("run1", "../../secret")
        path = self.root / "run1" / "summary.md"
        path.write_text("tampered")
        with self.assertRaises(ValueError):
            r.download("run1", "all.zip")
        path.unlink()
        path.symlink_to(self.root / "runs.jsonl")
        with self.assertRaises(ValueError):
            r.download("run1", "summary.md")

    def test_browser_artifact_reads_one_verified_file(self):
        self.generate()
        data, manifest = r.artifact("run1", "summary.md")
        self.assertEqual(data, r.download("run1", "summary.md"))
        self.assertEqual(manifest["run_name"], "run1")
        for name in ("../../secret", "all.zip", "not-generated.md"):
            with self.assertRaises(ValueError):
                r.artifact("run1", name)
        path = self.root / "run1" / "summary.md"
        path.write_text("changed after export")
        with self.assertRaises(ValueError):
            r.artifact("run1", "summary.md")
        path.unlink()
        path.symlink_to(self.root / "runs.jsonl")
        with self.assertRaises(ValueError):
            r.artifact("run1", "summary.md")

    def test_markdown_renders_without_executing_embedded_content(self):
        text = '# Report\n\n**Bold** and *emphasis*.\n\n- List item\n\n```json\n{"ok":true}\n```\n\n'
        text += '<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n![remote](https://example.com/pixel)\n\n'
        text += '| Attribute | Value |\n|---|---|\n| Curiosity | 3 |\n'
        body, sections, note = rv.render("report.md", text)
        self.assertIn('<h1 id="section-1">Report</h1>', body)
        self.assertIn('<strong>Bold</strong>', body)
        self.assertIn('<em>emphasis</em>', body)
        self.assertIn('<li>List item</li>', body)
        self.assertIn('<table>', body)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>', body)
        self.assertNotIn('href="javascript:', body)
        self.assertNotIn('<img', body)
        self.assertEqual(sections, [('section-1', 'Report')])
        source, _, _ = rv.render("report.md", text, source=True)
        self.assertIn('# Report', source)
        self.assertNotIn('<h1', source)

    def test_json_and_jsonl_are_readable_and_keep_all_fields(self):
        body, _, _ = rv.render('state.json', '{"stats":{"curiosity":3}}')
        self.assertIn('  &quot;stats&quot;: {\n    &quot;curiosity&quot;: 3', body)
        text = r.lines([{"agent": "p1", "role": "user", "text": "One line.\nSecond line.", "nested": {"safe": "<img onerror=alert(1)>"}},
                        {"text": "Final record."}])
        body, sections, note = rv.render('messages.jsonl', text)
        self.assertIn('Record 1 · p1 · user', body)
        self.assertIn('One line.\nSecond line.', body)
        self.assertIn('Final record.', body)
        self.assertIn('<dt>nested</dt>', body)
        self.assertNotIn('<img', body)
        self.assertEqual(len(sections), 2)
        self.assertFalse(note)
        body, _, note = rv.render('invalid.jsonl', '{broken}\nlast line')
        self.assertIn('last line', body)
        self.assertIn('Invalid JSONL', note)

    def test_large_preview_is_announced_before_content_and_can_show_full_file(self):
        import web_views as ui
        content = ("# Opening\n\n" + "A paragraph with café.\n" * 100 + "\n# The actual end\n").encode()
        manifest = {"captured_at": r.now(), "status": "complete"}
        with patch.object(rv, 'FULL_VIEW_LIMIT', 100), patch.object(rv, 'PREVIEW_BYTES', 91):
            text, truncated, shown = rv.preview(content)
            self.assertTrue(truncated)
            self.assertLessEqual(shown, 91)
            self.assertNotIn('\ufffd', text)
            page = ui.result_file_page('run1', 'long.md', content, manifest)
            self.assertLess(page.index('Truncated preview — this is not the whole file.'), page.index('id="result-document"'))
            self.assertIn('View full file', page)
            self.assertIn('/view/long.md?full=1', page)
            self.assertNotIn('The actual end', page)
            full_page = ui.result_file_page('run1', 'long.md', content, manifest, full=True)
            self.assertIn('Full file shown — nothing truncated.', full_page)
            self.assertIn('The actual end', full_page)
            self.assertNotIn('Truncated preview', full_page)
            # A giant first JSONL record still has a useful, labeled preview.
            partial, cut, _ = rv.preview(r.lines([{'text': 'z' * 500}]).encode())
            body, _, note = rv.render('long.jsonl', partial, truncated=cut)
            self.assertIn('zzzz', body)
            self.assertIn('within a JSON record', note)

    def test_publish_only_selected_bundle_preserves_dirty_checkout(self):
        self.root.mkdir()
        remote = Path(self.tmp.name) / "remote.git"
        r.git(Path(self.tmp.name), "init", "--bare", str(remote))
        r.git(self.root, "init", "-b", "main")
        r.git(self.root, "config", "user.name", "Gate")
        r.git(self.root, "config", "user.email", "gate@localhost")
        (self.root / "README.md").write_text("committed readme")
        (self.root / "runs.jsonl").write_text('{"run_name":"other","status":"complete"}\n')
        r.git(self.root, "add", ".")
        r.git(self.root, "commit", "-m", "initial")
        r.git(self.root, "remote", "add", "origin", str(remote))
        r.git(self.root, "push", "-u", "origin", "main")
        r.git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
        (self.root / "README.md").write_text("unrelated local edits")
        (self.root / "private.txt").write_text("do not publish")
        self.generate()
        index_before = (self.root / "runs.jsonl").read_bytes()
        receipt = r.publish("run1")
        self.assertEqual(len(receipt["commit"]), 40)
        self.assertEqual(r.git(remote, "show", "HEAD:README.md"), "committed readme")
        self.assertNotIn("private.txt", r.git(remote, "ls-tree", "-r", "--name-only", "HEAD"))
        self.assertEqual((self.root / "README.md").read_text(), "unrelated local edits")
        self.assertEqual((self.root / "runs.jsonl").read_bytes(), index_before)
        self.assertEqual([v["run_name"] for v in r.json_lines(r.git(remote, "show", "HEAD:runs.jsonl"))], ["other", "run1"])
        self.assertEqual(r.publish("run1")["commit"], receipt["commit"])

    def test_core_repo_is_never_a_publish_destination(self):
        self.root.mkdir()
        r.git(self.root, "init")
        remote = r.git(r.REPO, "remote", "get-url", "origin")
        r.git(self.root, "remote", "add", "origin", remote)
        with self.assertRaisesRegex(ValueError, "core repository"):
            r.publish_target()

    def test_web_results_downloads_and_public_policy(self):
        import web
        self.generate()
        server = web.make_server(0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with patch.object(r, "environment", return_value=({}, {})), patch.object(r, "capture", return_value=self.data):
                page = urllib.request.urlopen(base+"/results/run1").read().decode()
                self.assertIn("Download all (.zip)", page)
                self.assertIn("Upload to results GitHub", page)
                self.assertIn('data-action="results generate"', page)
                self.assertIn('/results/run1/view/transcript.md', page)
                preview = urllib.request.urlopen(base+"/results/run1/view/transcript.md")
                self.assertIn('text/html', preview.headers['Content-Type'])
                document = preview.read().decode()
                self.assertIn('Full file shown — nothing truncated.', document)
                self.assertIn('<article class="result-prose">', document)
                self.assertIn('Whole input.', document)
                source = urllib.request.urlopen(base+"/results/run1/view/transcript.md?source=1").read().decode()
                self.assertIn('# run1 — player transcript', source)
                state = json.load(urllib.request.urlopen(base+"/results/run1/status"))
                self.assertEqual(state["status"], "complete")
                response = urllib.request.urlopen(base+"/results/run1/files/all.zip")
                self.assertEqual(response.headers["Content-Type"], "application/zip")
                self.assertIn("attachment", response.headers["Content-Disposition"])
                self.assertTrue(zipfile.is_zipfile(io.BytesIO(response.read())))
                # The demo (password holders) may read results; generating and publishing stay operator-only.
                for suffix in ("", "/status", "/files/all.zip", "/view/transcript.md"):
                    req = urllib.request.Request(base+"/results/run1"+suffix, headers={"X-Agentspace-Public": "1"})
                    self.assertEqual(urllib.request.urlopen(req).status, 200)
                demo_page = urllib.request.urlopen(urllib.request.Request(base+"/results/run1", headers={"X-Agentspace-Public": "1"})).read().decode()
                self.assertNotIn('data-action="results generate"', demo_page)
                self.assertNotIn('data-action="results publish"', demo_page)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+"/results/run1/files/%2e%2e%2fsecret")
                self.assertEqual(error.exception.code, 400)
                for suffix in ("/view/%2e%2e%2fsecret", "/view/not-generated.md"):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        urllib.request.urlopen(base+"/results/run1"+suffix)
                    self.assertEqual(error.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main(verbosity=2)
