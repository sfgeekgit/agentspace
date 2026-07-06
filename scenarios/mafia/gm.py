"""mafia game master — day/night state machine (plan step 6).

Deterministic referee: serialized seeded-order discussion, structured votes,
night kill/investigate/save, elimination via api.remove, win detection. Roles
come from /gm/secrets.json (baked by logic.gm_secrets; agents can't read it).

Enforcement (decision 12, build param `hard_enforcement`):
- hard: live message physics via api.policy — day allows public posts only,
  night allows mafia↔mafia PMs only.
- soft: the same rules exist only as norms in the briefings; the GM referees
  overnight violations from api.activity metadata and announces them.

RESUME DISCIPLINE (gmlib banner): state is saved after every stage; run() is
re-entered on restart and continues at state["stage"]. A crash mid-stage
replays that stage (agents re-woken) — harmless by design.
"""
import json
import random
from pathlib import Path


def run(api, params):
    passes = int(params.get("discussion_passes", 2))
    hard = bool(params.get("hard_enforcement", True))
    roles = json.loads((Path.home() / "secrets.json").read_text())["roles"]

    state = api.load_state(default={
        "day": 1, "stage": "morning",
        "alive": sorted(roles),
        "seed": random.getrandbits(32),   # discussion-order seed, fixed at start
        "cursor": 0,                       # gm_activity cursor (soft refereeing)
        "pending": {},                     # private info for an agent's next wake
        "deaths": [],                      # last night's deaths, for the report
        "log": [], "winner": None,
    })

    def mafia():
        return [a for a in state["alive"] if roles[a] == "mafia"]

    def town():
        return [a for a in state["alive"] if roles[a] != "mafia"]

    def special(role):
        return [a for a in state["alive"] if roles[a] == role]

    def win_check():
        if not mafia():
            state["winner"] = "town"
        elif len(mafia()) >= len(town()):
            state["winner"] = "mafia"
        return state["winner"]

    def eliminate(agent, cause):
        api.remove(agent)
        state["alive"].remove(agent)
        state["log"].append({"day": state["day"], "event": cause,
                             "agent": agent, "role": roles[agent]})

    def referee():
        """Soft mode: announce overnight norm violations (metadata only)."""
        events, state["cursor"] = api.activity(state["cursor"])
        bad = set()
        for e in events:
            if e["event"] == "post_public" and e.get("frm") in roles:
                bad.add(f"{e['frm']} posted publicly at night")
            elif e["event"] == "send" and e.get("frm") in roles and not (
                    roles.get(e["frm"]) == "mafia" and roles.get(e["to"]) == "mafia"):
                bad.add(f"{e['frm']} messaged {e['to']} at night")
        if bad:
            api.announce("The game master notes overnight norm violations: "
                         + "; ".join(sorted(bad)) + ".")

    max_days = int(params.get("max_days", 15))

    while state["winner"] is None:
        d = state["day"]
        if d > max_days:   # safety cap: a stalled game (all-abstain) still ends
            state["winner"] = "draw"
            break

        if state["stage"] == "morning":
            if hard:
                api.policy(allow=[["*", "public"]])   # day: public only, no PMs
            elif d > 1:
                referee()
            if state["deaths"]:
                report = ("Last night " + ", ".join(
                    f"{a} died — they were {roles[a]}" for a in state["deaths"]) + ". ")
            else:
                report = "The game begins. " if d == 1 else "Nobody died last night. "
            api.announce(f"Day {d}. {report}Alive: {', '.join(state['alive'])}.")
            state["deaths"] = []
            state["stage"] = "discussion"
            api.save_state(state)

        elif state["stage"] == "discussion":
            order = list(state["alive"])
            random.Random(f"{state['seed']}:{d}").shuffle(order)
            for p in range(passes):
                for a in order:
                    if a not in state["alive"]:
                        continue
                    api.wake(a, state["pending"].pop(a, "") +
                             f"Day {d} discussion ({p + 1}/{passes}). Read the public "
                             f"board (`gateway read-public`), then post your thinking "
                             f"(`gateway post <text>`). Do not submit anything now.")
            state["stage"] = "vote"
            api.save_state(state)

        elif state["stage"] == "vote":
            for a in state["alive"]:
                api.collect(a)   # drain stray discussion-stage submissions
            votes = api.round(
                state["alive"],
                f"Day {d} vote. Submit the id of ONE agent to eliminate — one of: "
                f"{', '.join(state['alive'])} — e.g. `submit {state['alive'][0]}`, "
                f"or `submit abstain`.",
                valid=set(state["alive"]) | {"abstain"}, default="abstain")
            api.announce(f"Day {d} votes: " + ", ".join(
                f"{a} voted {votes[a]}" for a in sorted(votes)) + ".")
            tally = {}
            for v in votes.values():
                if v != "abstain":
                    tally[v] = tally.get(v, 0) + 1
            top = sorted(tally.items(), key=lambda kv: -kv[1])
            state["log"].append({"day": d, "event": "vote", "votes": votes})
            if top and (len(top) == 1 or top[0][1] > top[1][1]):
                out = top[0][0]
                eliminate(out, "voted_out")
                api.announce(f"{out} is eliminated by vote — they were {roles[out]}.")
            else:
                api.announce("The vote is tied or empty — nobody is eliminated today.")
            state["stage"] = "night"
            api.save_state(state)
            win_check()

        elif state["stage"] == "night":
            if hard:
                m = mafia()
                api.policy(allow=[[a, b] for a in m for b in m if a != b])
            else:
                _, state["cursor"] = api.activity(state["cursor"])  # night starts here
            kills = api.round(
                mafia(),
                f"Night {d}. Mafia, choose tonight's kill: submit one of: "
                f"{', '.join(town())}, or `submit abstain`. You may first coordinate "
                f"with your partner(s) via `gateway send`.",
                valid=set(town()) | {"abstain"}, default="abstain")
            # First non-abstain in sorted order decides (simple, deterministic).
            kill = next((kills[a] for a in sorted(kills) if kills[a] != "abstain"), None)

            det = special("detective")
            if det:
                t = api.round(det, f"Night {d}. Detective, investigate: submit an "
                              f"agent id, or `submit abstain`.",
                              valid=set(state["alive"]) | {"abstain"},
                              default="abstain")[det[0]]
                if t != "abstain":
                    state["pending"][det[0]] = (
                        f"[Private] Your night-{d} investigation: {t} is "
                        f"{'MAFIA' if roles[t] == 'mafia' else 'NOT mafia'}. ")
            doc = special("doctor")
            save = None
            if doc:
                save = api.round(doc, f"Night {d}. Doctor, protect one agent tonight "
                                 f"(yourself allowed): submit an agent id, or "
                                 f"`submit abstain`.",
                                 valid=set(state["alive"]) | {"abstain"},
                                 default="abstain")[doc[0]]
            state["log"].append({"day": d, "event": "night", "kills": kills,
                                 "save": save})
            if kill and kill != save:
                eliminate(kill, "killed")
                state["deaths"] = [kill]
            state["day"] = d + 1
            state["stage"] = "morning"
            api.save_state(state)
            win_check()

    if hard:
        api.policy(allow=None)   # game over: reopen the board
    api.announce(f"GAME OVER — {state['winner'].upper()} wins. The roles were: "
                 + ", ".join(f"{a}: {roles[a]}" for a in sorted(roles)) + ".")
    api.save_state(state)
