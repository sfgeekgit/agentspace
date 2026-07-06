"""gmlib — the shared, runtime-NEUTRAL game-master library scens build on.

A scen ships a `gm.py` with `run(api, params)`; the runtime starts the GM as a
dedicated `gm` user and calls that entry point. `api` is a GM (below). gm.py
owns ALL game/world logic; gmlib owns the reusable plumbing (roster, concurrent
rounds, structured collection, announce/policy/remove/roll, state persistence).

Runtime-neutral by design (plan decision 10): NOTHING here knows about the PI
gateway or sockets. All transport lives behind an `adapter` (runtime_pi/gmd.py
supplies the PI one). A future OC adapter slots in with zero changes here or in
any scen.

╔══════════════════════════════════════════════════════════════════════════╗
║ PERSIST-TO-DISK DISCIPLINE (plan decision 14) — the one rule every GM must ║
║ follow. The GM is a live process, but a snapshot (`docker commit`) captures ║
║ only the FILESYSTEM. So game state (round, scores, phase) MUST live on disk ║
║ and be re-read on start: `run` is called afresh every time the world is     ║
║ (re)started, and a forked mid-game snap must resume where it left off.      ║
║ Use api.load_state()/save_state() and SAVE AFTER EVERY STEP. Make rounds    ║
║ resumable: a crash between wake and save replays that round, so agents may  ║
║ be re-woken — design actions to tolerate it. The prototype PD gm.py is the  ║
║ worked example; copy its shape.                                             ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import json
import os
import threading


class GM:
    """Runtime-neutral GM API used by scen gm.py. Orchestration + state only;
    every network/OS action goes through `adapter` (the runtime's transport)."""

    def __init__(self, adapter, state_path):
        self._a = adapter
        self._state_path = state_path

    # ---- persistent game state (see the discipline banner above) ----

    def load_state(self, default=None):
        """The game state as last saved, or `default` on a fresh world."""
        try:
            with open(self._state_path) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return default

    def save_state(self, obj):
        """Atomically persist game state. Call after EVERY step so a snapshot at
        any instant resumes correctly."""
        tmp = f"{self._state_path}.tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f, indent=2)
        os.replace(tmp, self._state_path)

    # ---- roster ----

    def agents(self):
        """Live agent ids in this world (handles variable-count scens)."""
        return self._a.who()

    # ---- driving turns ----

    def wake(self, agent, payload=""):
        """Wake one agent with a payload and BLOCK until its turn finishes.
        Returns True if it completed, False on turn timeout."""
        return self._a.wake(agent, payload).get("completed", False)

    def wake_all(self, agents=None, payload=""):
        """Wake several agents CONCURRENTLY (agents run in parallel), block
        until all finish. Defaults to the whole roster. Returns {agent: ok}."""
        agents = self.agents() if agents is None else list(agents)
        out, threads = {}, []
        for a in agents:
            t = threading.Thread(
                target=lambda a=a: out.__setitem__(a, self.wake(a, payload)))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return out

    def collect(self, agent, valid=None, default=None):
        """Pop the agent's submitted action for this round. Returns the trimmed
        string, or `default` if it never submitted / submitted something not in
        `valid` (a set of allowed strings). The GM consumes STRUCTURED data —
        never the agent's free-form chat."""
        raw = self._a.collect(agent)
        if raw is None:
            return default
        v = raw.strip()
        if valid is not None and v not in valid:
            return default
        return v

    def round(self, agents, payload, valid=None, default=None):
        """One serialized round: wake `agents` in parallel with `payload`, then
        collect each submission. Returns {agent: action}. The staple GM helper —
        PD, votes, bids are all this."""
        self.wake_all(agents, payload)
        return {a: self.collect(a, valid, default) for a in agents}

    # ---- world control ----

    def announce(self, text):
        """Post to the public board as `world` (wakes nobody; pull-only)."""
        self._a.announce(text)

    def policy(self, allow=None, deny=None, **caps):
        """Set LIVE phase policy: `allow`/`deny` are lists of [from, to] pairs
        (`*` wildcard), plus optional max_msg_bytes / rate_limit_per_min. Takes
        effect on the next message, no restart — day/night allowlists etc."""
        pol = {"allow": allow, "deny": deny or [], **caps}
        self._a.policy(pol)

    def remove(self, agent):
        """Eliminate an agent: no more wakes, no send rights (elimination/shift
        end). Persisted across restarts."""
        self._a.remove(agent)

    def roll_session(self, agent):
        """Archive the agent's session so its next wake starts fresh with
        re-rendered files — controlled compaction at a phase boundary."""
        self._a.roll_session(agent)

    def activity(self, since=0):
        """Message-traffic metadata (send/post_public: frm/to/seq/ts, never
        content) since a seq — soft-enforcement refereeing. Returns
        (events, max_seq); pass max_seq back as the next `since`."""
        r = self._a.activity(since)
        return r.get("events", []), r.get("max_seq", since)


def run(adapter, scen_run, params, state_path):
    """Entry the runtime launcher calls: wire the adapter + state into a GM and
    hand it to the scen's run(api, params). gm.py should treat run as
    resumable — it may be called again after a restart (see the banner)."""
    scen_run(GM(adapter, state_path), params)
