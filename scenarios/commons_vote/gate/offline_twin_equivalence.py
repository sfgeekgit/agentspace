#!/usr/bin/env python3
"""Eager-seam equivalence proof for the commons_vote design.

Run from the cilib repo with its venv (the pinned commit in gm/CILIB_PIN):

    cd /home/cc/cilib && .venv/bin/python \
        /opt/agentspace-ctl/scenarios/commons_vote/gate/offline_twin_equivalence.py

LOAD-BEARING FINDING (2026-07-11): plain-eager transform application is NOT
bit-exact with lax.scan — XLA compiles them differently, low-bit float noise
flips argmax decisions (vote winners, trust argmax), and trajectories fork.
`jax.jit`-wrapped application IS bit-exact with scan. Therefore gm/main.py
MUST apply the physics through the jitted blocks proven here (`GmPhysics`
mirrors the required structure), and the scen gate's offline twin must use
the same structure. This wraps the vendored functions; it modifies nothing.

Checks:
  1. SHIPPED pipeline: per-round jit(step) == Environment.run (lax.scan),
     all three mechanisms, with metrics. Connects any jitted per-round run
     to the 500-seed sweep's physics, bit-exactly.
  2. GM-SHAPED pipeline (voting + q_learning removed, actions forced):
     the GM's two-jit-block round == the same round composed under
     lax.scan. Proves the GM's split structure (proposal gen | LLM writes
     last_action | physics) introduces no compilation-boundary drift.
  3. Pickle round-trip + resume (the /gm/state.pkl contract).
  4. Eager per-round wall time (info).

Re-run whenever the cilib pin is bumped.
"""
import pickle
import sys
import time

sys.path.insert(0, "/home/cc/cilib")

import jax
import jax.lax as lax
import jax.numpy as jnp
import numpy as np

from experiments.basin_stability.state import create_initial_state
from experiments.basin_stability.transforms import (
    make_step_transform,
    make_aggregation_transform,
    make_election_transform,
    proposal_generation_transform,
    resource_update_transform,
    reward_transform,
    step_counter_transform,
    trust_update_transform,
)
from cilib.core.category import sequential
from cilib.core.environment import Environment
from cilib.metrics import ECONOMIC_METRICS, GOVERNANCE_METRICS

N, K, T, SEED = 6, 4, 10, 123
METRICS = {**ECONOMIC_METRICS, **GOVERNANCE_METRICS}
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


def states_equal(a, b):
    """Bit-exact GraphState comparison; returns (ok, first_diff)."""
    def eq(x, y):
        if isinstance(x, (jnp.ndarray, np.ndarray)):
            return np.array_equal(np.asarray(x), np.asarray(y))
        return x == y

    if not eq(a.node_types, b.node_types):
        return False, "node_types"
    for group in ("node_attrs", "adj_matrices", "global_attrs"):
        da, db = getattr(a, group), getattr(b, group)
        if set(da) != set(db):
            return False, f"{group} keys {set(da) ^ set(db)}"
        for k in da:
            if not eq(da[k], db[k]):
                return False, f"{group}[{k}]"
    return True, ""


def init(mechanism, metrics=None):
    return create_initial_state(
        n_agents=N, n_adversarial=0, K=K, T=T,
        mechanism=mechanism, seed=SEED, metrics=metrics)


class GmPhysics:
    """The jit structure gm/main.py must replicate: one jitted proposal-gen
    block, one jitted physics block (everything after the LLM votes), with
    the last_action write between them. Voting and q_learning are omitted —
    LLM agents replace both."""

    def __init__(self, mechanism):
        self.pre = jax.jit(proposal_generation_transform)
        self.post = jax.jit(sequential(
            make_aggregation_transform(mechanism),
            resource_update_transform,
            reward_transform,
            trust_update_transform,
            make_election_transform(mechanism),
            step_counter_transform,
        ))

    def round(self, state, actions):
        state = self.pre(state)
        state = state.update_node_attrs("last_action", actions)
        return self.post(state)


def offline_twin_scan(state, mechanism, forced, rounds):
    """The GM-shape round as ONE composed pipeline under lax.scan — the
    compilation structure the shipped sweep uses."""
    def forced_voting(s):
        return s.update_node_attrs("last_action", forced[s.global_attrs["step"]])

    phys = GmPhysics(mechanism)
    pipeline = sequential(proposal_generation_transform, forced_voting, phys.post)
    final, _ = lax.scan(lambda s, _: (pipeline(s), None), state, None, length=rounds)
    return final


# --- 1. shipped pipeline: per-round jit == scan (all mechanisms, with metrics)
for mech in ("pdd", "prd", "pld"):
    step_t = make_step_transform(mechanism=mech, metrics=METRICS)
    scan_final = Environment(init(mech, METRICS), step_t).run(T)
    jit_step = jax.jit(step_t)
    state = init(mech, METRICS)
    for _ in range(T):
        state = jit_step(state)
    ok, diff = states_equal(state, scan_final)
    check(f"shipped jit==scan [{mech}]", ok, diff)

# --- 2. GM shape: two-jit-block rounds == composed scan twin
rng = np.random.RandomState(0)
forced = jnp.array(rng.randint(0, K, size=(T, N)))
for mech in ("pdd", "prd", "pld"):
    phys = GmPhysics(mech)
    state = init(mech)
    for t in range(T):
        state = phys.round(state, forced[t])
    ok, diff = states_equal(state, offline_twin_scan(init(mech), mech, forced, T))
    check(f"gm-shape jit==scan-twin [{mech}]", ok, diff)

# --- 3. pickle round-trip + resume (the /gm/state.pkl contract)
phys = GmPhysics("pdd")

def advance(state, start, rounds):
    for t in range(start, start + rounds):
        state = phys.round(state, forced[t])
    return state

mid = advance(init("pdd"), 0, 5)
thawed = pickle.loads(pickle.dumps(mid))
ok, diff = states_equal(mid, thawed)
check("pickle round-trip", ok, diff)
ok, diff = states_equal(advance(mid, 5, 5), advance(thawed, 5, 5))
check("pickle resume == uninterrupted", ok, diff)

# --- 4. per-round wall time
state = init("pdd")
t0 = time.perf_counter()
state = advance(state, 0, 1)
first = time.perf_counter() - t0
t0 = time.perf_counter()
state = advance(state, 1, T - 1)
rest = (time.perf_counter() - t0) / (T - 1)
print(f"INFO gm round: first {first*1000:.0f}ms (compile), then {rest*1000:.1f}ms/round")

print("ALL GREEN" if not FAIL else f"FAILURES: {FAIL}")
sys.exit(1 if FAIL else 0)
