"""commons_vote scen logic: role split + the GM's build secrets.

Agent-visible wording lives in roles/*.md. The physics seed baked into
/gm/secrets.json makes the world's trajectory reproducible from the build.
"""

MECHANISMS = ("pdd", "prd", "pld")


def validate(n, params):
    if params["n_adversarial"] >= n / 2:
        return "n_adversarial must be under half the agents"
    if params["mechanism"] not in MECHANISMS:
        return f"mechanism must be one of: {', '.join(MECHANISMS)}"
    return None


def assign_roles(n, params, rng):
    n_adv = params["n_adversarial"]
    roles = ["cooperative"] * (n - n_adv) + ["adversarial"] * n_adv
    rng.shuffle(roles)
    return roles


def gm_secrets(ids_roles, params, rng):
    """Role answer key + the seed gm/main.py feeds create_initial_state()."""
    return {"roles": ids_roles, "physics_seed": rng.getrandbits(32)}
