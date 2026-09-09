"""Zero-token logic check: gm/main.py driven against a stub gmlib api.

Not a scen gate (no container, no scripted agents): it exercises the GM's own
state machine — arrival schedule, ticket opening, who gets woken, multi-action
parsing, claim tie-break, holder-only resolve, confirmation, termination,
resume.  python3 gate/dry.py
"""
import json, os, random, sys, tempfile
from pathlib import Path

GM = str(Path(__file__).resolve().parent.parent / "gm")
SEED = 12345


class Api:
    def __init__(self, home, roles, behaviour):
        self.home, self.roles, self.b = Path(home), roles, behaviour
        self.sub, self.calls, self.announced, self.limit = {}, [], [], None
        self.sp = self.home / "state.json"
    def load_state(self, default=None):
        try: return json.loads(self.sp.read_text())
        except Exception: return default
    def save_state(self, o): self.sp.write_text(json.dumps(o))
    def agents(self): return sorted(self.roles)
    def wake(self, a, payload=""):
        if self.limit is not None and len(self.calls) >= self.limit: raise RuntimeError("STOP")
        self.calls.append((a, payload)); self.b(self, a, payload); return True
    def wake_all(self, agents=None, payload=""):
        return {a: self.wake(a, payload) for a in (agents or self.agents())}
    def collect(self, a, valid=None, default=None): return self.sub.pop(a, default)
    def round(self, agents, payload, valid=None, default=None):
        self.wake_all(agents, payload)
        return {a: self.collect(a, valid, default) for a in agents}
    def announce(self, t): self.announced.append(t)
    def policy(self, allow=None, deny=None, **k): self.pol = (allow, deny)


def load_gm(home):
    os.environ["HOME"] = home
    sys.modules.pop("main", None)
    if GM not in sys.path: sys.path.insert(0, GM)
    import main
    return main


def run(behaviour, params, n_reps=3, n_cust=6, restart_after=None):
    home = tempfile.mkdtemp()
    roles = {**{f"r{i}": "rep" for i in range(n_reps)},
             **{f"c{i}": "customer" for i in range(n_cust)}}
    (Path(home) / "secrets.json").write_text(json.dumps(
        {"roles": roles, "seed": SEED, "names": {c: c.upper() for c in roles}}))
    api = Api(home, roles, behaviour)
    api.limit = restart_after
    try:
        load_gm(home).run(api, params)
    except RuntimeError as e:
        if "STOP" not in str(e): raise
        api = Api(home, roles, behaviour)
        load_gm(home).run(api, params)
    return json.loads((Path(home) / "state.json").read_text()), api, home


def state(api): return api.load_state({}) or {}
def mine(api, a): return [k for k, v in state(api).get("tickets", {}).items()
                          if v["claimed_by"] == a and not v["resolved_round"]]
def free(api): return sorted(k for k, v in state(api).get("tickets", {}).items()
                             if not v["claimed_by"])

def customer(api, a, payload):
    api.sub[a] = "Yes" if "closed your ticket" in payload else f"hi, {a} here, something is broken"

def eager(api, a, payload):
    """Reps: resolve what they hold and claim the next free ticket in ONE submit."""
    if api.roles[a] == "customer": return customer(api, a, payload)
    acts = [f"resolve {t}" for t in mine(api, a)] + [f"claim {t}" for t in free(api)[:1]]
    if acts: api.sub[a] = "; ".join(acts)

def collide(api, a, payload):
    """Every rep claims the same ticket, then resolves what it holds."""
    if api.roles[a] == "customer": return customer(api, a, payload)
    if mine(api, a): api.sub[a] = f"resolve {mine(api, a)[0]}"
    elif free(api): api.sub[a] = f"claim {free(api)[0]}"

def poacher(api, a, payload):
    """r0 claims everything; r1 tries to resolve r0's tickets; nobody else acts."""
    if api.roles[a] == "customer": return customer(api, a, payload)
    if a == "r0" and free(api): api.sub[a] = f"claim {free(api)[0]}"
    if a == "r1" and mine(api, "r0"): api.sub[a] = f"resolve {mine(api, 'r0')[0]}"

def idle(api, a, payload):
    if api.roles[a] == "customer": customer(api, a, payload)


P = {"arrival_gap": 5, "max_rounds": 80}

# arrival schedule = the spec's formula
rng = random.Random(f"{SEED}:arrivals")
exp = [1, 3, 5]
while len(exp) < 6: exp.append(exp[-1] + max(1, 5 + rng.choice([-1, 0, 1])))
st, api, _ = run(eager, P)
assert st["arrivals"] == exp, (st["arrivals"], exp)
assert all(st["tickets"][f"t{k+1}"]["opened_round"] == a for k, a in enumerate(exp))
assert st["done"] == "complete" and len(st["tickets"]) == 6
assert all(v["confirmed"] == "yes" for v in st["tickets"].values())
confirms = [c for c in api.calls if "closed your ticket" in c[1]]
assert len(confirms) == 6, len(confirms)
lines = [json.loads(l)["text"] for l in open(Path(api.home) / "game_log.jsonl")]
assert any("woke 0" in l for l in lines if l.startswith("round")), lines   # quiet rounds wake nobody
print(f"happy path      : complete in {st['round']} rounds, arrivals {st['arrivals']}, "
      f"6/6 resolved, 6 confirmations")

st, api, _ = run(collide, P)
assert st["done"] == "complete"
told = [c for c in api.calls if "Taken last block" in c[1]]
assert told, "losers were never told"
assert any("lost r" in l for l in open(Path(api.home) / "game_log.jsonl").read().splitlines())
print(f"claim collision : complete in {st['round']} rounds, exactly one holder each, losers told")

st, api, _ = run(poacher, {"arrival_gap": 5, "max_rounds": 12}, n_cust=3)
assert st["done"] == "capped" and st["round"] == 12
assert not any(v["resolved_round"] for v in st["tickets"].values())
assert all(v["claimed_by"] == "r0" for v in st["tickets"].values())
print("non-holder      : resolve by a non-holder ignored; capped at 12")

st, api, _ = run(idle, {"arrival_gap": 5, "max_rounds": 5})
assert st["done"] == "capped" and st["round"] == 5
print("nobody acts     : capped at round 5, 0 resolved")

st, api, home = run(eager, P, restart_after=7)
assert st["done"] == "complete" and st["arrivals"] == exp
assert sum(1 for v in st["tickets"].values() if v["resolved_round"]) == 6
print(f"mid-run restart : complete in {st['round']} rounds, arrivals stable, resumed cleanly")

api2 = Api(home, {"r0": "rep"}, idle); load_gm(home).run(api2, P)
assert api2.calls == [] and api2.announced == []
print("restart when done: no replay, no second announce")

st, api, _ = run(eager, {"arrival_gap": 1, "max_rounds": 80}, n_reps=1, n_cust=14)
assert st["done"] == "complete"
lines = [json.loads(l)["text"] for l in open(Path(api.home) / "game_log.jsonl")]
assert any("claimed r0:" in l and "resolved t" in l for l in lines), lines  # "claim X; resolve Y" in one submit
print(f"1 rep 14 tickets: complete in {st['round']} rounds, multi-action submits parsed")
print("\nall checks passed")
