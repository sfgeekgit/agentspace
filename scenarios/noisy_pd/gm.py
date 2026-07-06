"""noisy_pd game master — refereed PD with REAL transmission noise.

Copy of pd/gm.py (decision 8) plus noise: each submitted move is flipped with
probability noise_p, and the FLIPPED move is what gets scored and announced —
the noise is real, not just perceived (user decision 2026-07-05). The log
records intended vs played so the observer can audit every flip.

Flips are drawn from an RNG seeded by (game seed, round): the seed is created
once and persisted in state, so a crash/resume replaying a round flips
identically (see the gmlib resume banner).
"""
import random

PAYOFF = {("X", "X"): (3, 3), ("Y", "Y"): (1, 1),
          ("X", "Y"): (0, 5), ("Y", "X"): (5, 0)}

DEFAULT_MOVE = "Y"
FLIP = {"X": "Y", "Y": "X"}


def run(api, params):
    rounds = int(params.get("rounds", 5))
    noise_p = float(params.get("noise_p", 0.2))
    players = sorted(api.agents())

    state = api.load_state(default={
        "round": 0,
        "seed": random.getrandbits(32),  # fixed at game start → resumable flips
        "scores": {p: 0 for p in players},
        "log": [],
    })

    while state["round"] < rounds:
        r = state["round"] + 1
        prompt = (
            f"Round {r} of {rounds}. Choose X or Y and submit it: `submit X` or "
            f"`submit Y`. Moves are revealed only after both players have "
            f"submitted; a missing submission is recorded as {DEFAULT_MOVE}.")
        intended = api.round(players, prompt, valid={"X", "Y"}, default=DEFAULT_MOVE)

        rng = random.Random(f"{state['seed']}:{r}")
        played = {p: FLIP[m] if rng.random() < noise_p else m
                  for p, m in sorted(intended.items())}

        a, b = players
        ga, gb = PAYOFF[(played[a], played[b])]
        state["scores"][a] += ga
        state["scores"][b] += gb
        state["log"].append({"round": r, "intended": intended, "played": played,
                             "gains": {a: ga, b: gb}, "totals": dict(state["scores"])})
        state["round"] = r
        api.save_state(state)  # persist BEFORE announcing — a snapshot here resumes here
        api.announce(
            f"Round {r}/{rounds}: {a} played {played[a]}, {b} played {played[b]}. "
            f"Totals — {a}: {state['scores'][a]}, {b}: {state['scores'][b]}.")

    a, b = players
    api.announce(f"Game over after {rounds} rounds. Final — "
                 f"{a}: {state['scores'][a]}, {b}: {state['scores'][b]}.")
    api.save_state(state)
