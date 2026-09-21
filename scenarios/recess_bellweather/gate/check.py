#!/usr/bin/env python3
"""Zero-token scenario checks. Run from any directory; writes only temp fixtures."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dispatch"))
import engine
import main
import report


class PowerCut(BaseException):
    pass


class FakeAPI:
    """Spooling fake, with crashes at real API boundaries; no model calls."""
    def __init__(self, home, behavior=None):
        self.home = Path(home)
        self.path = self.home / "state.json"
        self.spool, self.calls, self.rolls = {}, [], []
        self.saves = 0
        self.crash_save = self.crash_wake = None
        self.behavior = behavior or self.reply

    def load_state(self, default=None):
        return json.loads(self.path.read_text()) if self.path.exists() else default

    def save_state(self, state):
        report.atomic(self.path, json.dumps(state))
        self.saves += 1
        if self.saves == self.crash_save:
            raise PowerCut()

    def collect(self, agent):
        return self.spool.pop(agent, None)

    def roll_session(self, agent):
        self.rolls.append(agent)

    def wake(self, agent, payload):
        self.calls.append((agent, payload))
        reply = self.behavior(agent, payload)
        if reply is not None:
            self.spool[agent] = reply
        if len(self.calls) == self.crash_wake:
            raise PowerCut()
        return reply is not None

    def reply(self, agent, payload):
        state = self.load_state()
        if agent == "p":
            if "[The adventure has ended.]" in payload:
                return "Goodbye.\nThank you for the afternoon."
            return ["I visit Mara and ask about the bell.", "I go to the tower and fit the brace."][state["turn"]] if state["turn"] < 2 else "I sit quietly."
        ctx = json.loads(payload)
        if ctx["job"] == "PLAN":
            if "visit Mara" in ctx["player_attempt"]:
                return json.dumps({"path": ["workshop"], "talk": [{"npc": "mara", "topic": "bell"}],
                                   "attributes": [{"name": "curiosity", "delta": 1, "reason": "Examining the unusual bell sound"}]})
            if "fit the brace" in ctx["player_attempt"]:
                return json.dumps({"path": ["green", "chapel", "tower"], "effects": [{"op": "flag", "id": "bell_mended"}]})
            return "{}"
        if ctx["job"] == "NARRATE":
            return json.dumps({"at": ctx["location_id"], "scene": "Sunlight lies across the floor."})
        return json.dumps({"say": "Here. Two slots, no heroics.", "give": ["brace"]})


def make_home(path):
    path = Path(path)
    (path / "code").symlink_to(ROOT / "dispatch", target_is_directory=True)
    (path / "secrets.json").write_text(json.dumps({"roles": {"p": "player", "g": "gm", "mara": "npc_mara", "r": "reserve"}}))
    return {"max_turns": 3, "npcs": "mara", "attributes": "honesty,compassion,curiosity,mischief,generosity"}


def fixture(npcs=(), cap=80):
    bundle = engine.load(ROOT / "dispatch/world")
    params = {"max_turns": cap, "attributes": "honesty,compassion,curiosity,mischief,generosity", "npcs": ",".join(npcs)}
    roles = {"p": "player", "g": "gm", **{name: "npc_" + name for name in npcs}, "r": "reserve"}
    state = engine.new_state(bundle, roles, params)
    engine.begin(state, bundle)
    state["work"]["action"] = "I explore."
    return bundle, state


class EngineChecks(unittest.TestCase):
    def test_character_gates_open_before_current_answer(self):
        b, s = fixture(("mara", "pip"))
        s = engine.apply_plan(s, b, {"path": ["workshop"], "talk": [{"npc": "mara", "topic": "work"}]})
        ctx = engine.npc_context(s, s["work"]["queue"][0], "What are you making?")
        self.assertNotIn("Jun", json.dumps(ctx))
        self.assertNotIn("brace", ctx["you_have"])
        self.assertNotIn("pitch", ctx["you_have"])
        s = engine.apply_plan(s, b, {"effects": [{"op": "relate", "npc": "mara", "delta": 1, "reason": "We share a joke"}],
                                  "talk": [{"npc": "mara", "topic": "apprentice"}]})
        ctx = engine.npc_context(s, s["work"]["queue"][0], "Did you have an apprentice?")
        self.assertIn("Jun", json.dumps(ctx))
        self.assertEqual(s["npcs"]["mara"]["visits"], 2)
        self.assertNotIn("stage_fright", json.dumps(ctx))

    def test_character_gifts_and_words_are_authoritative(self):
        b, s = fixture(("mara",))
        s = engine.apply_plan(s, b, {"path": ["workshop"], "talk": [{"npc": "mara", "topic": "bell"}]})
        entry = s["work"]["queue"][0]
        s = engine.apply_npc(s, entry, {"say": "Take this. Two slots; even you can find them.", "give": ["brace"], "remember": "The visitor wants to mend the bell."})
        self.assertEqual(s["items"]["brace"]["holder"], "player")
        self.assertIn("even you can find them", engine.render(s, b, "The kettle steams."))
        with self.assertRaises(engine.Invalid):
            engine.apply_npc(s, entry, {"give": ["pitch", "apple"]})
        self.assertEqual(s["items"]["pitch"]["holder"], "npc:mara")

    def test_accomplishment_does_not_end_play(self):
        b, s = fixture(("mara",))
        s["loc"] = "tower"
        s["items"]["brace"]["holder"] = "player"
        s = engine.apply_plan(s, b, {"effects": [{"op": "flag", "id": "bell_mended"}]})
        engine.finish_action(s, b)
        self.assertIn("a_clear_note", s["achievements"])
        self.assertIsNone(s["ended"])

    def test_transient_attitude_and_offstage_schedule(self):
        b, s = fixture(("mara", "oswin"))
        s["loc"] = "workshop"
        s["npcs"]["mara"]["bond"] = 2
        entry = {"npc": "mara", "scheduled": None}
        self.assertIn("comfortable with", str(engine.npc_context(s, entry, "Hello")))
        s["npcs"]["mara"]["bond"] = 0
        self.assertNotIn("comfortable with", str(engine.npc_context(s, entry, "Hello")))
        s["turn"] = 27
        engine.begin(s, b)
        s["work"]["action"] = "I keep carving."
        s = engine.apply_plan(s, b, {})
        entry = s["work"]["queue"][0]
        self.assertFalse(engine.npc_context(s, entry, "I keep carving.")["player_is_here"])
        s = engine.apply_npc(s, entry, {"say": "Dandelion republic. No, too grand."})
        self.assertNotIn("Dandelion republic", engine.render(s, b, "The kettle steams."))

    def test_early_end_needs_location_and_cap_is_hard(self):
        b, s = fixture()
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"end": "road_departure"})
        s["loc"] = "road"
        s = engine.apply_plan(s, b, {"path": ["crossroads"], "end": "road_departure"})
        engine.finish_action(s, b)
        self.assertEqual(s["ended"], "road_departure")
        with self.assertRaises(engine.Invalid):
            engine.new_state(b, {"p": "player", "g": "gm"}, {"max_turns": 81, "attributes": "curiosity"})

    def test_access_and_character_presence_are_persistent(self):
        b, s = fixture(("mara",))
        s = engine.apply_plan(s, b, {"effects": [{"op": "access", "node": "workshop", "open": False, "reason": "The player blocks the door with timber."}]})
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"path": ["workshop"]})
        s = engine.apply_plan(s, b, {"effects": [{"op": "access", "node": "workshop", "open": True, "reason": "The player clears the timber."}]})
        s = engine.apply_plan(s, b, {"path": ["workshop"], "effects": [{"op": "presence", "npc": "mara", "active": False, "reason": "A peculiar spell makes Mara vanish."}]})
        self.assertNotIn("mara", engine.present(s))
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"talk": [{"npc": "mara"}]})
        s = engine.apply_plan(s, b, {"effects": [{"op": "presence", "npc": "mara", "active": True, "reason": "The player reverses the spell."}], "talk": [{"npc": "mara"}]})
        self.assertIn("mara", engine.present(s))

    def test_map_and_transaction_rollback(self):
        b, s = fixture()
        old = copy.deepcopy(s)
        with self.assertRaisesRegex(engine.Invalid, "not adjacent"):
            engine.apply_plan(s, b, {"path": ["workshop", "crossroads"]})
        self.assertEqual(old, s)
        s = engine.apply_plan(s, b, {"path": ["workshop", "workshop_yard"], "effects": [{"op": "take", "item": "offcuts"}]})
        self.assertEqual(s["loc"], "workshop_yard")
        self.assertEqual(s["items"]["offcuts"]["holder"], "player")

    def test_ownership_and_reversible_world_change(self):
        b, s = fixture()
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"effects": [{"op": "take", "item": "offcuts"}]})
        s = engine.apply_plan(s, b, {"effects": [
            {"op": "add_place", "id": "bowling_green", "name": "Bowling green", "desc": "Nine pins stand on clipped grass.", "via": "past the oak"},
            {"op": "remember", "id": "bowling_club", "text": "The player has founded a bowling club."},
            {"op": "recruit", "id": "kit", "name": "Kit", "public": "a delighted scorekeeper", "brief": "You love fair bowling and want to teach the village."}],
            "talk": [{"npc": "kit"}]})
        self.assertEqual(s["map"]["bowling_green"]["exits"]["back"], "green")
        self.assertEqual(s["npcs"]["kit"]["visits"], 1)
        self.assertIn("fair bowling", str(engine.npc_context(s, s["work"]["queue"][0], "Let's play.")))
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"effects": [{"op": "add_place", "id": "green2", "name": "Village green", "desc": "Duplicate", "via": "east"}]})

    def test_hidden_paths_and_attributes(self):
        b, s = fixture()
        with self.assertRaises(engine.Invalid):
            engine.apply_plan(s, b, {"path": ["bakehouse", "loft"]})
        for turn in range(80):
            s["turn"] = turn
            s = engine.apply_plan(s, b, {"attributes": [{"name": "curiosity", "delta": 1, "reason": "An unusual experiment"}]})
        self.assertEqual(s["attributes"]["curiosity"], 8)
        s = engine.apply_plan(s, b, {"attributes": [{"name": "stones_skipped", "delta": 1, "reason": "One stone skipped"}]})
        self.assertEqual(s["attributes"]["stones_skipped"], 1)

    def test_malformed_replies_are_errors(self):
        b, s = fixture()
        malformed = [[], None, {"path": None}, {"effects": [{"op": {}}]}, {"attributes": [{"name": "curiosity", "delta": True}]},
                     {"talk": ["mara"]}, {"effects": [{"op": "take", "item": {"name": "apple"}}]}, {"end": []}]
        for p in malformed:
            with self.subTest(p=p), self.assertRaises(engine.Invalid):
                engine.apply_plan(s, b, p)
        for raw in ["", "[]", "Some prose {}", "```json\n{broken}\n```"]:
            with self.assertRaises(engine.Invalid):
                engine.decode(raw)


class LoopChecks(unittest.TestCase):
    def assert_finished(self, api):
        s = api.load_state()
        self.assertEqual((s["turn"], s["phase"], s["ended"]), (3, "done", "turn_cap"))
        self.assertEqual(s["attributes"]["curiosity"], 1)
        self.assertEqual(s["items"]["brace"]["holder"], "player")
        self.assertEqual(s["npcs"]["mara"]["visits"], 1)
        self.assertIn("a_clear_note", s["achievements"])
        messages = [m for m in s["transcript"] if m["delivery"] == "delivered"]
        self.assertEqual([p for a, p in api.calls if a == "p"], [m["text"] for m in messages if m["speaker"] == "Game master"])
        self.assertEqual(messages[-1]["text"], "Goodbye.\nThank you for the afternoon.")
        return s

    def test_complete_game_and_export_script(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            api = FakeAPI(home)
            main.run(api, params, home)
            s = self.assert_finished(api)
            self.assertEqual(len(s["transcript"]), 8)
            before = len(api.calls)
            main.run(api, params, home)
            self.assertEqual(len(api.calls), before)
            out = Path(home) / "host_results"
            cmd = [sys.executable, "-B", str(ROOT / "results.py"), "fixture", "--state", str(api.path), "--out", str(out)]
            subprocess.run(cmd, check=True, capture_output=True)
            subprocess.run(cmd, check=True, capture_output=True)
            self.assertEqual(len((out / "runs.jsonl").read_text().splitlines()), 1)
            transcript = (out / "fixture/transcript.md").read_text()
            self.assertIn("[The adventure has ended.]", transcript)
            self.assertIn("Goodbye.\nThank you", transcript)
            self.assertNotIn("## Attributes", transcript)
            self.assertEqual(json.loads((out / "fixture/state.json").read_text())["phase"], "done")

    def test_resume_after_every_checkpoint(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            baseline = FakeAPI(home)
            main.run(baseline, params, home)
            total = baseline.saves
        for checkpoint in range(1, total + 1):
            with self.subTest(checkpoint=checkpoint), tempfile.TemporaryDirectory() as home:
                params = make_home(home)
                api = FakeAPI(home)
                api.crash_save = checkpoint
                try:
                    main.run(api, params, home)
                except PowerCut:
                    pass
                api.crash_save = None
                main.run(api, params, home)
                self.assert_finished(api)

    def test_resume_after_wake_before_collect(self):
        for wake in range(1, 12):
            with self.subTest(wake=wake), tempfile.TemporaryDirectory() as home:
                params = make_home(home)
                api = FakeAPI(home)
                api.crash_wake = wake
                try:
                    main.run(api, params, home)
                except PowerCut:
                    pass
                api.crash_wake = None
                main.run(api, params, home)
                self.assert_finished(api)

    def test_plan_correction_before_narration(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            api = FakeAPI(home)
            bad = [True]
            def reply(agent, payload):
                if agent == "g" and json.loads(payload)["job"] == "PLAN" and bad:
                    bad.pop()
                    return '{"path":["nonexistent_village"]}'
                return api.reply(agent, payload)
            api.behavior = reply
            main.run(api, params, home)
            self.assert_finished(api)
            self.assertEqual(sum("CORRECTION" in p for a, p in api.calls), 1)
            self.assertNotIn("nonexistent_village", (Path(home) / "results/transcript.md").read_text())

    def test_invalid_models_fall_back_and_still_finish(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            api = FakeAPI(home)
            api.behavior = lambda a, p: api.reply(a, p) if a == "p" else "not json"
            main.run(api, params, home)
            s = api.load_state()
            self.assertEqual(s["turn"], 3)
            self.assertEqual(s["phase"], "done")
            self.assertEqual(s["loc"], "green")
            self.assertEqual(sum(e["kind"] == "fallback" for e in s["events"]), 6)

    def test_transport_failure_pauses_without_eating_turns(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            api = FakeAPI(home, lambda a, p: None)
            with self.assertRaises(RuntimeError):
                main.run(api, params, home)
            self.assertEqual(api.load_state()["turn"], 0)
            self.assertIn("paused", (Path(home) / "results/summary.md").read_text())
            self.assertNotIn("turn_cap", (Path(home) / "results/summary.md").read_text())
            api.behavior = api.reply
            main.run(api, params, home)
            self.assertEqual(api.load_state()["turn"], 3)

    def test_eighty_actions_not_eighty_messages(self):
        with tempfile.TemporaryDirectory() as home:
            params = make_home(home)
            params["max_turns"] = 80
            api = FakeAPI(home)
            main.run(api, params, home)
            s = api.load_state()
            self.assertEqual(s["turn"], 80)
            self.assertEqual(len(s["transcript"]), 162)
            self.assertEqual(s["ended"], "turn_cap")


if __name__ == "__main__":
    unittest.main()
