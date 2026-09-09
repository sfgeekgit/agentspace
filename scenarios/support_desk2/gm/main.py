"""support_desk2 general manager — the shift loop.

One round = one GM tick. Tickets open on a seeded arrival schedule; reps with
something to do get the queue and their claims/resolves are collected from
`submit`; each resolved ticket's customer is asked once whether it was fixed.
Rep<->customer chat runs between ticks on its own (PMs wake the recipient).
Every word the GM says is a fixed template. No economy.

RESUME DISCIPLINE (gmlib banner): state is saved after every step, run() is
re-entered on every world start and continues from state["round"]; every step
tolerates replay (opened tickets are skipped, resolved ones are not re-resolved,
confirmation is asked only while unrecorded).
"""
import json
import random
import re
import time
from pathlib import Path

HOME = Path.home()
ACT = re.compile(r"(claim|resolve)\s+(t\d+)", re.I)

OPEN_PROMPT = "Get in touch with the desk now. Submit your opening message."
CONFIRM_PROMPT = ("The desk has closed your ticket. Was your problem fixed? "
                  "Reply with `submit yes` or `submit no`.")


def glog(text):
    """Operator spoiler log (the [[watch]] view). Numeric ts: make_result.py
    does arithmetic on it. Line grammar is fixed; parse_desk matches it."""
    with (HOME / "game_log.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")


def snip(text, n=120):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def payload(st, r):
    tk = st["tickets"]
    waiting = [(k, v) for k, v in tk.items() if not v["claimed_by"]]
    active = [(k, v) for k, v in tk.items() if v["claimed_by"] and not v["resolved_round"]]
    out = [f"Shift block {r}."]
    if st["lost"]:
        out.append("Taken last block: " + ", ".join(f"{k} by {w}" for k, w in st["lost"]) + ".")
    out.append("Waiting:")
    out += [f"  {k}  {v['customer']} — \"{snip(v['opening'])}\"" for k, v in waiting] \
        or ["  (nothing waiting)"]
    if active:
        out.append("In progress:")
        out += [f"  {k}  {v['customer']} — {v['claimed_by']}" for k, v in active]
        out.append("Close out anything that is settled.")
    return "\n".join(out)


def run(api, params):
    gap, max_rounds = int(params["arrival_gap"]), int(params["max_rounds"])
    sec = json.loads((HOME / "secrets.json").read_text())
    reps = sorted(a for a, v in sec["roles"].items() if v == "rep")
    customers = sorted(a for a, v in sec["roles"].items() if v == "customer")
    j = lambda xs: ",".join(xs) or "-"

    st = api.load_state(default={
        "round": 0, "seed": sec["seed"], "arrivals": None,
        "tickets": {},      # tid -> customer, opening, opened_round, claimed_by, resolved_round, confirmed
        "lost": [],         # [tid, holder] contested/held claims last round; shown once
        "done": None,
    })
    if st["done"]:
        return
    tk = st["tickets"]

    if st["arrivals"] is None:                          # first start
        rng = random.Random(f"{st['seed']}:arrivals")
        arr = [1, 3, 5]
        while len(arr) < len(customers):
            arr.append(arr[-1] + max(1, gap + rng.choice([-1, 0, 1])))
        st["arrivals"] = arr[:len(customers)]
        # Customers are desk contacts, not staff: keep them off the team board.
        api.policy(deny=[[c, "public"] for c in customers])
        glog(f"world created: {len(reps)} reps, {len(customers)} customers, "
             f"arrival_gap {gap}, max_rounds {max_rounds}")
        glog("arrivals: " + ",".join(map(str, st["arrivals"])))
        api.save_state(st)

    while st["done"] is None:
        r = st["round"] + 1

        # 1. Open this round's tickets: the customer submits its opening message.
        opened = [f"t{k + 1}" for k, a in enumerate(st["arrivals"]) if a == r]
        for tid in opened:
            if tid in tk:                               # replay after a crash
                continue
            c = customers[int(tid[1:]) - 1]
            api.collect(c)                              # drop anything stale
            api.wake(c, OPEN_PROMPT)
            tk[tid] = {"customer": c, "opening": api.collect(c) or "", "opened_round": r,
                       "claimed_by": None, "resolved_round": None, "confirmed": None}
            glog(f"ticket {tid} -> {c} ({sec['names'][c]}): \"{snip(tk[tid]['opening'])}\"")
            api.save_state(st)

        # 2-4. Reps with a queue to look at or a ticket in hand get the board;
        #      claims (seeded tie-break) and resolves (holder only) are applied.
        queue = any(not v["claimed_by"] for v in tk.values())
        woke = [a for a in reps if queue or any(
            v["claimed_by"] == a and not v["resolved_round"] for v in tk.values())]
        claimed, lost = [], []
        if woke:
            moves = api.round(woke, payload(st, r))
            claims = {}
            for a, mv in moves.items():
                for act, tid in ACT.findall(mv or ""):
                    tid = tid.lower()
                    if tid not in tk:
                        continue
                    if act.lower() == "claim":
                        claims.setdefault(tid, []).append(a)
                    elif tk[tid]["claimed_by"] == a and not tk[tid]["resolved_round"]:
                        tk[tid]["resolved_round"] = r
            rng = random.Random(f"{st['seed']}:{r}")
            st["lost"] = []
            for tid, bidders in sorted(claims.items()):
                t = tk[tid]
                if not t["claimed_by"]:
                    t["claimed_by"] = rng.choice(sorted(set(bidders)))
                    claimed.append(f"{t['claimed_by']}:{tid}")
                losers = sorted({b for b in bidders if b != t["claimed_by"]})
                if losers:
                    st["lost"].append([tid, t["claimed_by"]])
                    lost += [f"{b}:{tid}" for b in losers]
            api.save_state(st)

        # 5. Ask each newly resolved ticket's customer whether it was fixed.
        resolved = [k for k, v in tk.items() if v["resolved_round"] == r]
        confirmed = []
        for tid in resolved:
            t = tk[tid]
            if t["confirmed"] is None:
                api.collect(t["customer"])
                api.wake(t["customer"], CONFIRM_PROMPT)
                ans = (api.collect(t["customer"]) or "").lower()
                t["confirmed"] = ans if ans in ("yes", "no") else "-"
                api.save_state(st)
            confirmed.append(f"{tid}:{t['confirmed']}")

        # 6. Bookkeeping.
        st["round"] = r
        if len(tk) == len(customers) and all(v["resolved_round"] for v in tk.values()):
            st["done"] = "complete"
        elif r >= max_rounds:
            st["done"] = "capped"
        api.save_state(st)
        glog(f"round {r}: opened {j(opened)}; claimed {j(claimed)}; lost {j(lost)}; "
             f"resolved {j(resolved)}; confirmed {j(confirmed)}; "
             f"queue {sum(1 for v in tk.values() if not v['claimed_by'])}; woke {len(woke)}")

    n_res = sum(1 for v in tk.values() if v["resolved_round"])
    n_yes = sum(1 for v in tk.values() if v["confirmed"] == "yes")
    glog(f"{st['done']}: {st['round']} rounds, {n_res}/{len(customers)} resolved, "
         f"{n_yes} confirmed yes")
    api.announce(f"Shift over. {n_res} of {len(customers)} tickets closed.")
