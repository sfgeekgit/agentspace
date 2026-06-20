"""pd scen logic. Both agents get the symmetric "player" role.

Keep agent-visible wording out of here — briefings live in roles/player.md.
"""


def validate(n, params):
    """Exactly two agents (also enforced by min/max in the manifest)."""
    if n != 2:
        return "this scenario is for exactly 2 agents"
    return None


def assign_roles(n, params, rng):
    """Symmetric: both agents are players. (rng unused — assignment is fixed.)"""
    return ["player"] * n
