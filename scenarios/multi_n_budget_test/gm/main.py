"""multi_n_budget_test game master — N-agent pooled-contribution rounds.

Public-goods mechanics, shaped like pd/gm.py (the reference prototype): each
round every player gets an endowment of 10, privately submits a contribution
0-10, the pool is multiplied by `multiplier` and split evenly, and each keeps
(10 - contribution + share). A missing/invalid submission contributes 0.

Exists to exercise the GM at N>2 (fan-out, collect-N, serialize-at-N) and to
measure real per-round token cost — see scenario.toml.
"""
ENDOWMENT = 10
VALID = {str(i) for i in range(ENDOWMENT + 1)}


def run(api, params):
    rounds = int(params.get("rounds", 5))
    mult = float(params.get("multiplier", 2.0))
    reveal = bool(params.get("reveal_contributions", True))
    players = sorted(api.agents())
    n = len(players)

    state = api.load_state(default={
        "round": 0,
        "scores": {p: 0.0 for p in players},
        "log": [],
    })

    while state["round"] < rounds:
        r = state["round"] + 1
        prompt = (
            f"Round {r} of {rounds}. You hold {ENDOWMENT} points. Choose how many "
            f"(a whole number, 0-{ENDOWMENT}) to place in the common pool and "
            f"submit it, e.g. `submit 4`. The pool is multiplied by {mult:g} and "
            f"split evenly among all {n} agents; you keep what you didn't "
            f"contribute plus your share. No submission counts as 0.")
        subs = api.round(players, prompt, valid=VALID, default="0")
        contribs = {p: int(v) for p, v in subs.items()}

        pool = sum(contribs.values()) * mult
        share = pool / n
        gains = {p: ENDOWMENT - contribs[p] + share for p in players}
        for p in players:
            state["scores"][p] += gains[p]
        state["log"].append({"round": r, "contribs": contribs, "pool": pool,
                             "share": share, "totals": dict(state["scores"])})
        state["round"] = r
        api.save_state(state)  # persist BEFORE announcing — a snapshot here resumes here

        detail = (", ".join(f"{p}: {contribs[p]}" for p in players) if reveal
                  else f"pool total {sum(contribs.values())}")
        totals = ", ".join(f"{p}: {state['scores'][p]:g}" for p in players)
        api.announce(f"Round {r}/{rounds}: contributions — {detail}. "
                     f"Each receives {share:g} from the pool. Totals — {totals}.")

    totals = ", ".join(f"{p}: {state['scores'][p]:g}" for p in players)
    api.announce(f"Game over after {rounds} rounds. Final — {totals}.")
    api.save_state(state)
