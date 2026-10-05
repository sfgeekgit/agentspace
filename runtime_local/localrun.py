#!/usr/bin/env python3
"""Run a plain-mode dispatcher scen on this machine, without Docker or Pi.

The scen's own logic.py (roles, briefings) and dispatch/main.py (the turn loop) run
unchanged against dispatchlib; only the transport is local. Each agent is an
in-memory session: the GM and NPCs are OpenRouter chat models whose system prompt is
the same SOUL/ROLE/WORLD sandwich the PI runtime renders, and so is the player.

    python3 runtime_local/localrun.py recess_mvp runs/t1 --seed 1 --param max_turns=10 \\
        [--player deepseek/deepseek-v4.1-flash] [--gm-model deepseek/deepseek-v4.1-flash]

The run dir doubles as the dispatcher's home (what /dispatch is in a container):
meta.json, state.json, secrets.json, game_log.jsonl, transcript.md, calls.jsonl (every
chat call: payload, reply, usage)."""
import argparse
import importlib.util
import json
import os
import random
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agentspace import dispatchlib  # noqa: E402

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
# Fixed for the environment (GM and NPCs), logged in meta.json.
CHAT_SAMPLING = {"temperature": 0.7, "top_p": 0.95, "max_tokens": 4096, "reasoning": {"enabled": False}}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Log:
    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()

    def __call__(self, rec):
        with self.lock, self.path.open("a") as f:
            f.write(json.dumps({"ts": now(), **rec}, ensure_ascii=False) + "\n")


def sandwich(files):
    """runtime_pi/agentd.py render_sandwich in plain mode: SOUL.md first, MEMORY.md
    last, the rest alphabetical, each as '# NAME' + body, joined by '---'."""
    names = sorted(files, key=lambda n: (n != "SOUL.md", n == "MEMORY.md", n))
    return "\n\n---\n\n".join(f"# {n}\n\n{files[n].strip()}".strip() for n in names)


class ChatAgent:
    """An OpenRouter chat model as a plain-mode agent: system = the file sandwich,
    the session is the list of user payloads and replies since the last roll."""

    def __init__(self, agent_id, role, system, model, key, log, seed=None):
        self.id, self.role, self.system, self.model, self.key, self.log = agent_id, role, system, model, key, log
        self.seed, self.history, self.session, self.n = seed, [], 0, 0

    def roll(self):
        self.history, self.session = [], self.session + 1

    def turn(self, payload):
        msgs = [{"role": "system", "content": self.system}] + self.history + [{"role": "user", "content": payload}]
        body = {"model": self.model, "messages": msgs, **CHAT_SAMPLING, "usage": {"include": True}}
        if self.seed is not None:
            body["seed"] = self.seed * 1000 + self.n
        out, err = None, None
        for attempt in range(6):
            req = urllib.request.Request(OPENROUTER, data=json.dumps(body).encode(), headers={
                "content-type": "application/json", "authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    out = json.loads(r.read())
                if out.get("choices") and (out["choices"][0]["message"].get("content") or "").strip():
                    break
                err = f"empty reply: {json.dumps(out)[:300]}"
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                err = repr(e)
            time.sleep(min(60, 3 * 2 ** attempt))
        reply = (out or {}).get("choices", [{}])[0].get("message", {}).get("content") or ""
        self.log({"agent": self.id, "role": self.role, "kind": "chat", "model": self.model, "session": self.session,
                  "n": self.n, "history_len": len(self.history), "payload": payload, "reply": reply,
                  "usage": (out or {}).get("usage"), "error": None if reply.strip() else err})
        self.history += [{"role": "user", "content": payload}, {"role": "assistant", "content": reply}]
        self.n += 1
        return reply


class LocalAdapter:
    """The dispatchlib transport (runtime_pi/dispatchd.py's role) over in-memory agents.
    wake() runs the agent's turn synchronously and spools its reply; collect() pops it."""

    def __init__(self, agents):
        self.agents, self.spool = agents, {}

    def who(self):
        return list(self.agents)

    def wake(self, agent, payload):
        self.spool[agent] = self.agents[agent].turn(payload)
        return {"completed": True}

    def collect(self, agent):
        return self.spool.pop(agent, None)

    def roll_session(self, agent):
        self.agents[agent].roll()

    def announce(self, text):
        pass

    def policy(self, pol):
        pass

    def remove(self, agent):
        self.agents.pop(agent, None)

    def activity(self, since):
        return {"events": [], "max_seq": since}


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scen_params(scen_dir, overrides):
    toml = tomllib.loads((scen_dir / "scenario.toml").read_text())
    params, types = {}, {}
    for p in toml.get("params", []):
        params[p["name"]], types[p["name"]] = p["default"], p["type"]
    for kv in overrides:
        k, v = kv.split("=", 1)
        t = types.get(k, "str")
        params[k] = int(v) if t == "int" else float(v) if t == "float" else \
            v.lower() in ("1", "true", "yes", "on") if t == "bool" else v
    return toml, params


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scen")
    ap.add_argument("run_dir")
    ap.add_argument("--param", action="append", default=[], help="k=v scen parameter")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--reserves", type=int, default=0)
    ap.add_argument("--agents", type=int, default=None,
                    help="agent count (default: the recess shape, player + gm + the npcs param + --reserves)")
    ap.add_argument("--player", default="deepseek/deepseek-v4.1-flash", help="the player's OpenRouter model id")
    ap.add_argument("--gm-model", default="deepseek/deepseek-v4.1-flash")
    ap.add_argument("--npc-model", default="deepseek/deepseek-v4.1-flash")
    a = ap.parse_args()

    scen_dir = REPO / "scenarios" / a.scen
    run_dir = Path(a.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    toml, params = scen_params(scen_dir, a.param)
    if not toml.get("plain"):
        sys.exit(f"{a.scen} is not a plain-mode scen; runtime_local only drives plain mode")
    logic = load_module(f"{a.scen}_logic", scen_dir / "logic.py")
    rng = random.Random(a.seed)
    n = a.agents or 2 + len([x for x in str(params.get("npcs", "")).split(",") if x.strip()]) + a.reserves
    roles = logic.assign_roles(n, params, rng)
    ids, seen = [], {}
    for r in roles:   # readable agent ids: the role, numbered when it repeats
        seen[r] = seen.get(r, 0) + 1
        ids.append(r if roles.count(r) == 1 else f"{r}{seen[r]}")
    ids_roles = dict(zip(ids, roles))

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        sys.exit("OPENROUTER_API_KEY is not set")
    world_md = (scen_dir / "world.md").read_text()
    soul = (REPO / "personas" / "blank.md").read_text()   # zookeeper's DEFAULT_PERSONA
    calls = Log(run_dir / "calls.jsonl")
    agents, files = {}, {}
    for aid, role in ids_roles.items():
        brief = (scen_dir / "roles" / f"{role}.md").read_text()
        if hasattr(logic, "fill_briefing"):
            brief = logic.fill_briefing(brief, aid, ids_roles, params, rng)
        files[aid] = {"SOUL.md": soul, "ROLE.md": brief, "WORLD.md": world_md}
        model = a.player if role == "player" else a.gm_model if role == "gm" else a.npc_model
        agents[aid] = ChatAgent(aid, role, sandwich(files[aid]), model, key, calls, seed=a.seed)

    meta = {"started": now(), "scen": a.scen, "seed": a.seed, "params": params, "roles": ids_roles,
            "player": a.player, "gm_model": a.gm_model, "npc_model": a.npc_model, "chat_sampling": CHAT_SAMPLING,
            "systems": {aid: ag.system for aid, ag in agents.items()}}
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    (run_dir / "secrets.json").write_text(json.dumps({"roles": ids_roles}))
    code = run_dir / "code"
    if not code.exists():
        code.symlink_to(scen_dir / "dispatch")
    os.environ["HOME"] = str(run_dir)   # dispatch/main.py keeps its files under Path.home()
    sys.path.insert(0, str(scen_dir / "dispatch"))
    scen_main = load_module(f"{a.scen}_main", scen_dir / "dispatch" / "main.py")
    random.seed(a.seed)   # engine.new_state draws the state seed from the global RNG
    dispatchlib.run(LocalAdapter(agents), scen_main.run, params, str(run_dir / "state.json"))
    meta["finished"] = now()
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
