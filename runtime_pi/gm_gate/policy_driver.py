"""policy-gate driver — a test GM, NOT a game. Exercises the live phase
physics and observation machinery at N=5: open board → closed board with one
allowed PM pair → gm_activity metadata → reopen → fan-out round. It saves
what it observed to state.json so the gate can cross-check the GM's view
against the gateway's ground truth (board, audit, inboxes).
"""


def run(api, params):
    agents = sorted(api.agents())                    # a1..a5
    api.wake_all(agents, "phase A: the board is open — post.")
    api.policy(allow=[["a2", "a5"]])                 # closed: only a2 -> a5 PMs
    api.wake_all(agents, "phase B: the board is closed — try things.")
    events, _ = api.activity(0)                      # everything that ACTUALLY flowed
    api.policy(allow=None)                           # reopen
    subs = api.round(agents, "phase C: submit something.", default="none")
    api.save_state({"activity": events, "subs": subs})
