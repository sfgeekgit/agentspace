"""mafia scen logic: hidden-role assignment + briefing instantiation.

Uses the two hidden-information build hooks (plan step 6):
- fill_briefing: mafia templates get their partners' names filled in.
- gm_secrets: the role answer key, baked to /gm/secrets.json for the GM.
Agent-visible wording lives in roles/*.md, not here.
"""


def _mafia_count(n):
    return max(1, n // 4)   # 6-7 -> 1, 8-11 -> 2, 12 -> 3


def validate(n, params):
    if n < 6:
        return "mafia needs at least 6 agents (mafia + detective + doctor + 3)"
    return None


def assign_roles(n, params, rng):
    roles = ["mafia"] * _mafia_count(n) + ["detective", "doctor"]
    roles += ["villager"] * (n - len(roles))
    rng.shuffle(roles)
    return roles


def fill_briefing(briefing, agent_id, ids_roles, params, rng):
    if ids_roles[agent_id] != "mafia":
        return briefing
    partners = sorted(a for a, r in ids_roles.items() if r == "mafia" and a != agent_id)
    return briefing.replace(
        "{partners}", ", ".join(partners) if partners else "none — you work alone")


def gm_secrets(ids_roles, params, rng):
    return {"roles": ids_roles}
