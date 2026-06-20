"""roles_demo scen logic. Both functions are optional hooks the builder calls.

Keep agent-visible text out of here — this only decides role *assignment*. The
role briefings live in roles/<role>.md.
"""


def validate(n, params):
    """Return an error string to block the build, or None to allow it. (min/max
    is already enforced by the builder from the manifest; this is just a demo of
    a scen-owned check.)"""
    if n < 2:
        return "need at least 2 agents (one coordinator + one member)"
    return None


def assign_roles(n, params, rng):
    """One coordinator at a random position, the rest members. `rng` is seeded by
    the build seed, so assignment is reproducible and recorded in audit.log."""
    roles = ["member"] * n
    roles[rng.randrange(n)] = "coordinator"
    return roles
