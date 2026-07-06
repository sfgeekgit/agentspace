"""multi_n_budget_test scen logic. All agents get the symmetric "player" role.

Keep agent-visible wording out of here — briefings live in roles/player.md.
"""


def validate(n, params):
    """At least three agents (the point is N>2; max enforced by the manifest)."""
    if n < 3:
        return "this scenario needs at least 3 agents"
    return None


def assign_roles(n, params, rng):
    """Symmetric: everyone is a player. (rng unused — assignment is fixed.)"""
    return ["player"] * n
