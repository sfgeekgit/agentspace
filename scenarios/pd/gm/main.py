"""pd game master — refereed repeated Prisoner's Dilemma (neutral X/Y labels).

The reference GM prototype: keep new game scens shaped like this. Entry point is
run(api, params); `api` is a gmlib.GM, `params` are the build-time values
(here: rounds). The GM privately collects both moves each round, reveals them
simultaneously, keeps the TRUE score, and announces per round.

RESUME DISCIPLINE (gmlib banner): all game state lives in api's on-disk state
and is saved after EVERY round. run() is re-entered from scratch on any
restart, so it reads state and continues — a mid-game snap fork resumes exactly.
A crash between waking players and saving replays that round (players are
re-woken and resubmit), which is harmless here; design rounds to tolerate it.
"""

# Classic PD payoff, indexed by (my_move, their_move) -> (my_gain, their_gain).
# Neutral labels (see roles/player.md): both X -> 3,3; both Y -> 1,1;
# X vs Y -> 0 for the X player, 5 for the Y player.
PAYOFF = {("X", "X"): (3, 3), ("Y", "Y"): (1, 1),
          ("X", "Y"): (0, 5), ("Y", "X"): (5, 0)}

DEFAULT_MOVE = "Y"  # a player who doesn't submit is recorded as this (announced up front)


def run(api, params):
    rounds = int(params.get("rounds", 5))
    players = sorted(api.agents())  # PD is exactly 2 (enforced by logic.validate)

    state = api.load_state(default={
        "round": 0,                              # rounds completed
        "scores": {p: 0 for p in players},
        "log": [],                               # the GM's truthful per-round record
    })

    while state["round"] < rounds:
        r = state["round"] + 1
        prompt = (
            f"Round {r} of {rounds}. Choose X or Y and submit it: `submit X` or "
            f"`submit Y`. Moves are revealed only after both players have "
            f"submitted; a missing submission is recorded as {DEFAULT_MOVE}.")
        # One round: wake both players in parallel, collect each structured move.
        moves = api.round(players, prompt, valid={"X", "Y"}, default=DEFAULT_MOVE)

        a, b = players
        ga, gb = PAYOFF[(moves[a], moves[b])]
        state["scores"][a] += ga
        state["scores"][b] += gb
        state["log"].append({"round": r, "moves": moves, "gains": {a: ga, b: gb},
                             "totals": dict(state["scores"])})
        state["round"] = r
        api.save_state(state)  # persist BEFORE announcing — a snapshot here resumes here
        api.announce(
            f"Round {r}/{rounds}: {a} played {moves[a]}, {b} played {moves[b]}. "
            f"Totals — {a}: {state['scores'][a]}, {b}: {state['scores'][b]}.")

    a, b = players
    api.announce(f"Game over after {rounds} rounds. Final — "
                 f"{a}: {state['scores'][a]}, {b}: {state['scores'][b]}.")
    api.save_state(state)
