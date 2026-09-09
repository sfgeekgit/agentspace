"""support_desk2 build hooks: rep/customer split, ticket-instantiated customer
briefings, and the GM's build secrets. Ticket seeds live in tickets.py (scen
root, never baked into the container); ticket t{k+1} goes to the k-th customer
id in sorted order, so every root opens the same tickets in the same order."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import tickets


def validate(n, params):
    c = n - params["n_reps"]
    if c < 1:
        return "need at least one customer: n_reps must be under the agent count"
    if c > len(tickets.TICKETS):
        return f"at most {len(tickets.TICKETS)} customers (one ticket seed each)"
    return None


def assign_roles(n, params, rng):
    roles = ["rep"] * params["n_reps"] + ["customer"] * (n - params["n_reps"])
    rng.shuffle(roles)
    return roles


def _customers(ids_roles):
    return sorted(a for a, r in ids_roles.items() if r == "customer")


def fill_briefing(briefing, agent_id, ids_roles, params, rng):
    if ids_roles[agent_id] != "customer":
        return briefing
    return briefing.format(**tickets.TICKETS[_customers(ids_roles).index(agent_id)])


def gm_secrets(ids_roles, params, rng):
    return {"roles": ids_roles, "seed": rng.getrandbits(32),
            "names": {c: tickets.TICKETS[i]["name"]
                      for i, c in enumerate(_customers(ids_roles))}}
