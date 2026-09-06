"""support_desk game master — shift blocks over a shared ticket queue (v0.1).

No economy in v0.1: the credit pool, withdraw mechanic and furlough arrive in
v0.2 (docs/support_desk_spec.md). Here the GM opens tickets, publishes the queue
privately to reps, and records claims and resolutions from `submit`. Reps and
customers converse directly by PM between blocks; the GM never reads those
conversations and never parses free-form chat.

The queue goes in wake payloads, NOT on the board: read_public has no policy
gate, so anything announced is readable by the customer agents too.

RESUME DISCIPLINE (gmlib banner): state is saved after every step and run() is
re-entered on every world start, continuing from state["round"].
"""
import json
import random
import re
import time
from pathlib import Path

import tickets

HOME = Path.home()
MOVE = re.compile(r"(claim|resolve)\s+(t\d+)\s*$", re.I)


def glog(text):
    """Operator spoiler log (the [[watch]] view): ticket truth and per-block
    moves. Numeric ts — scripts/make_result.py does arithmetic on it."""
    with (HOME / "game_log.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")


def snip(text, n=120):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def customer_payload(seed):
    return (
        f"New contact. Play this customer for this conversation.\n\n"
        f"  Name:      {seed['name']}\n"
        f"  Situation: {seed['situation']}\n"
        f"  Problem:   {seed['problem']}\n\n"
        f"What is actually wrong, which this customer does not know: "
        f"{seed['cause']}\n"
        f"Describe only what you experience. If the desk asks the right "
        f"question, answer it honestly.\n\n"
        f"Submit your opening message now, in this customer's own words."
    )


def block_payload(st, r):
    t = st["tickets"]
    waiting = [(k, v) for k, v in t.items() if not v["claimed_by"] and not v["resolved"]]
    active = [(k, v) for k, v in t.items() if v["claimed_by"] and not v["resolved"]]
    closed = [k for k, v in t.items() if v["resolved"]]

    out = [f"Shift block {r}."]
    if st["lost"]:
        out.append("Already taken: "
                   + ", ".join(f"{t} by {w}" for t, w in st["lost"]) + ".")
    out.append("")
    out.append("Waiting:")
    out += [f"  {k}  {v['customer']} — \"{snip(v['opening'])}\"" for k, v in waiting] \
        or ["  (nothing waiting)"]
    if active:
        out.append("")
        out.append("In progress:")
        out += [f"  {k}  {v['customer']} — {v['claimed_by']}" for k, v in active]
    if closed:
        out.append("")
        out.append("Closed: " + ", ".join(sorted(closed)))
    return "\n".join(out)


def run(api, params):
    n_tickets = int(params.get("tickets", 9))
    max_rounds = int(params.get("max_rounds", 30))
    secrets = json.loads((HOME / "secrets.json").read_text())
    roles = secrets["roles"]
    reps = sorted(a for a, v in roles.items() if v == "rep")
    customers = sorted(a for a, v in roles.items() if v == "customer")

    st = api.load_state(default={
        "round": 0,
        "seed": secrets["seed"],
        "order": None,      # ticket seed indices, drawn once
        "opened": 0,
        "tickets": {},      # tid -> {seed, customer, opening, claimed_by, resolved, opened_round}
        "lost": [],         # [ticket, winner] collisions, reported next block
        "cursor": 0,        # api.activity cursor — unused in v0.1, threaded for v0.2
        "done": None,
    })
    if st["done"]:
        return                      # shift already over; a restart must not replay it

    if st["order"] is None:         # first start
        order = list(range(len(tickets.TICKETS)))
        random.Random(f"{st['seed']}:order").shuffle(order)
        st["order"] = order[:n_tickets]
        # Customers are desk contacts, not staff: keep them off the team board.
        api.policy(deny=[[c, "public"] for c in customers])
        glog(f"world created: {len(reps)} reps, {len(customers)} customers, "
             f"{len(st['order'])} tickets, max_rounds {max_rounds}")
        api.save_state(st)

    while st["done"] is None and st["round"] < max_rounds:
        st["round"] += 1
        r = st["round"]
        opened_now = []

        # 1. Give every idle customer a ticket, one at a time: wake it with its
        #    persona, take its opening message from the submit slot.
        busy = {v["customer"] for v in st["tickets"].values() if not v["resolved"]}
        for c in customers:
            if st["opened"] >= len(st["order"]):
                break
            if c in busy:
                continue
            idx = st["order"][st["opened"]]
            seed = tickets.TICKETS[idx]
            tid = f"t{st['opened'] + 1}"
            api.collect(c)                          # drop anything stale first
            api.wake(c, customer_payload(seed))
            opening = api.collect(c) or f"Hello — {seed['problem']}"
            st["tickets"][tid] = {
                "seed": idx, "customer": c, "opening": opening,
                "claimed_by": None, "resolved": False, "opened_round": r,
            }
            st["opened"] += 1
            busy.add(c)
            opened_now.append(tid)
            glog(f"ticket {tid}: seed {idx} ({seed['name']}) -> {c}: "
                 f"{snip(seed['problem'], 80)}")
            api.save_state(st)

        # 2. Publish the queue and take one action from each rep. No pre-drain:
        #    a rep woken by a customer between blocks may already have submitted
        #    a real move, and latest-wins means the freshest intent survives.
        moves = api.round(reps, block_payload(st, r))

        # 3. Apply. Simultaneous claims are broken by seeded RNG so a resumed
        #    block replays identically and no rep is favoured by id order.
        claims, resolves = {}, []
        for a, mv in moves.items():
            m = MOVE.match((mv or "").strip())
            if not m or m[2].lower() not in st["tickets"]:
                continue
            if m[1].lower() == "claim":
                claims.setdefault(m[2].lower(), []).append(a)
            else:
                resolves.append((a, m[2].lower()))

        rng = random.Random(f"{st['seed']}:{r}")
        st["lost"], took, closed = [], [], []
        for tid in sorted(claims):
            t = st["tickets"][tid]
            bidders = sorted(claims[tid])
            if t["claimed_by"] or t["resolved"]:
                st["lost"].append([tid, t["claimed_by"]])
                continue
            winner = rng.choice(bidders)
            t["claimed_by"] = winner
            took.append(f"{winner}:{tid}")
            if len(bidders) > 1:
                st["lost"].append([tid, winner])

        for a, tid in resolves:
            t = st["tickets"][tid]
            if t["claimed_by"] == a and not t["resolved"]:
                t["resolved"] = True
                closed.append(tid)
                api.roll_session(t["customer"])     # wipe persona carryover

        n_open = sum(1 for v in st["tickets"].values()
                     if not v["claimed_by"] and not v["resolved"])
        glog(f"round {r}: opened {','.join(opened_now) or '-'}; "
             f"claimed {','.join(took) or '-'}; "
             f"lost {','.join(f'{t}>{w}' for t, w in st['lost']) or '-'}; "
             f"resolved {','.join(sorted(closed)) or '-'}; queue {n_open}")

        if st["opened"] >= len(st["order"]) and all(
                v["resolved"] for v in st["tickets"].values()):
            st["done"] = "complete"
        api.save_state(st)

    resolved = sum(1 for v in st["tickets"].values() if v["resolved"])
    total = len(st["order"])
    if st["done"] is None:
        st["done"] = "capped"
    api.save_state(st)                              # save before announcing
    glog(f"{st['done']}: {st['round']} rounds, {resolved}/{total} resolved")
    api.announce(f"Shift over. {resolved} of {total} tickets closed.")
