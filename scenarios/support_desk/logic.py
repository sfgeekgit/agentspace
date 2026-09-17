"""support_desk build hooks: the rep/customer split and the dispatcher's build secrets.

Ticket text lives in dispatch/tickets.py (baked to /dispatch/code, agents cannot read it);
only the shuffle seed is baked here, so a rebuild from the same build seed opens
the same tickets in the same order.
"""


def validate(n, params):
    if params["n_reps"] < 1:
        return "need at least one rep"
    if params["n_reps"] >= n:
        return "need at least one customer: n_reps must be under the agent count"
    return None


def assign_roles(n, params, rng):
    roles = ["rep"] * params["n_reps"] + ["customer"] * (n - params["n_reps"])
    rng.shuffle(roles)
    return roles


def dispatch_secrets(ids_roles, params, rng):
    """Role answer key + the seed dispatch/main.py draws the ticket order from."""
    return {"roles": ids_roles, "seed": rng.getrandbits(32)}
