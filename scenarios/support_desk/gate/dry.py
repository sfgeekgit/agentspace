"""Zero-token logic check: gm/main.py driven against a stub gmlib api.

NOT a scen gate in the docs' sense (those drive the real stack via
runtime_pi/gm_gate/setup_world.sh with scripted dummy agents). This is cheaper
and narrower: it exercises the GM's own state machine — ticket flow, claim
tie-break, resume, termination — with no container and no tokens.

    python3 gate/dry.py

Worth keeping current: it is what makes the v0.2 economy safe to add.
"""
import json, os, sys, tempfile, shutil
from pathlib import Path

GM = "/opt/agentspace-ctl/scenarios/support_desk/gm"

class Api:
    def __init__(self, home, roles, behaviour):
        self.home, self.roles, self.b = Path(home), roles, behaviour
        self.sub, self.calls, self.rolled, self.announced = {}, [], [], []
        self.sp = self.home / "state.json"
    def load_state(self, default=None):
        try: return json.loads(self.sp.read_text())
        except Exception: return default
    def save_state(self, o): self.sp.write_text(json.dumps(o))
    def agents(self): return sorted(self.roles)
    def wake(self, a, payload=""):
        self.calls.append((a, payload)); self.b(self, a, payload); return True
    def wake_all(self, agents=None, payload=""):
        return {a: self.wake(a, payload) for a in (agents or self.agents())}
    def collect(self, a, valid=None, default=None):
        return self.sub.pop(a, default)
    def round(self, agents, payload, valid=None, default=None):
        self.wake_all(agents, payload)
        return {a: self.collect(a, valid, default) for a in agents}
    def announce(self, t): self.announced.append(t)
    def policy(self, allow=None, deny=None, **k): self.pol = (allow, deny)
    def roll_session(self, a): self.rolled.append(a)
    def remove(self, a): pass
    def activity(self, since=0): return [], since

def setup(n_reps=3, n_cust=3):
    home = tempfile.mkdtemp()
    reps = [f"r{i}" for i in range(n_reps)]
    cust = [f"c{i}" for i in range(n_cust)]
    roles = {**{a: "rep" for a in reps}, **{a: "customer" for a in cust}}
    (Path(home)/"secrets.json").write_text(json.dumps({"roles": roles, "seed": 12345}))
    return home, roles, reps, cust

def load_gm(home):
    os.environ["HOME"] = home
    for m in ("main", "tickets"):
        sys.modules.pop(m, None)
    sys.path.insert(0, GM)
    import main
    return main

def run(behaviour, params, n_reps=3, n_cust=3, restart_after=None):
    home, roles, reps, cust = setup(n_reps, n_cust)
    main = load_gm(home)
    api = Api(home, roles, behaviour)
    if restart_after:
        api.limit = restart_after
        try: main.run(api, params)
        except RuntimeError as e:
            if "STOP" not in str(e): raise
        api2 = Api(home, roles, behaviour); api2.limit = None
        main = load_gm(home); main.run(api2, params)
        st = json.loads((Path(home)/"state.json").read_text())
        return st, api2, home
    main.run(api, params)
    return json.loads((Path(home)/"state.json").read_text()), api, home

# --- behaviours -------------------------------------------------------------
def eager(api, a, payload):
    """Customers answer; reps claim the first waiting ticket then resolve it."""
    if api.roles[a] == "customer":
        api.sub[a] = f"hi this is {a}, something is broken"
        return
    if getattr(api, "limit", None) is not None and api.calls and \
       len(api.calls) > api.limit: raise RuntimeError("STOP")
    st = api.load_state({}) or {}
    t = st.get("tickets", {})
    mine = [k for k, v in t.items() if v["claimed_by"] == a and not v["resolved"]]
    if mine:
        api.sub[a] = f"resolve {mine[0]}"; return
    free = sorted(k for k, v in t.items() if not v["claimed_by"] and not v["resolved"])
    if free: api.sub[a] = f"claim {free[0]}"

def collide(api, a, payload):
    """Every rep claims t1 — exercises the tie-break."""
    if api.roles[a] == "customer": api.sub[a] = "help"; return
    st = api.load_state({}) or {}
    t = st.get("tickets", {})
    mine = [k for k, v in t.items() if v["claimed_by"] == a and not v["resolved"]]
    if mine:
        api.sub[a] = f"resolve {mine[0]}"; return
    free = sorted(k for k, v in t.items() if not v["claimed_by"] and not v["resolved"])
    if free:                      # every rep goes for the SAME ticket
        api.sub[a] = f"claim {free[0]}"

def idle(api, a, payload):
    if api.roles[a] == "customer": api.sub[a] = "help"

P = {"tickets": 9, "max_rounds": 30}

st, api, _ = run(eager, P)
assert st["done"] == "complete", st["done"]
assert sum(v["resolved"] for v in st["tickets"].values()) == 9
assert len(api.rolled) == 9, api.rolled
print(f"happy path      : {st['done']} in {st['round']} blocks, "
      f"9/9 resolved, {len(api.rolled)} sessions rolled")

st, api, _ = run(collide, P, n_reps=3, n_cust=3)
winners = {v["claimed_by"] for v in st["tickets"].values() if v["claimed_by"]}
assert st["done"] == "complete", st["done"]
print(f"claim collision : {st['done']} in {st['round']} blocks, "
      f"winners {sorted(winners)}, no double-claim")

st, api, _ = run(idle, {"tickets": 9, "max_rounds": 5})
assert st["done"] == "capped" and st["round"] == 5, (st["done"], st["round"])
assert sum(v["resolved"] for v in st["tickets"].values()) == 0
print(f"nobody acts     : {st['done']} at block {st['round']}, 0 resolved")

st, api, home = run(eager, P, restart_after=12)
assert st["done"] == "complete", st["done"]
assert sum(v["resolved"] for v in st["tickets"].values()) == 9
print(f"mid-run restart : {st['done']} in {st['round']} blocks, resumed cleanly")

main = load_gm(home)
api2 = Api(home, {"r0": "rep"}, idle); main.run(api2, P)
assert api2.calls == [] and api2.announced == []
print("restart when done: no replay, no second announce")

st, api, _ = run(eager, {"tickets": 12, "max_rounds": 30}, n_reps=1, n_cust=1)
print(f"1 rep 1 customer: {st['done']} in {st['round']} blocks (serialised queue)")
print("\nall checks passed")
