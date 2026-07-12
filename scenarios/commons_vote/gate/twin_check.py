#!/usr/bin/env python3
"""commons_vote scen gate, host half (run.sh runs it after the container):
replays the gate's scripted game in the UNTOUCHED cilib repo — same seed,
same jit structure as gm/main.py — and asserts the container's final
GraphState matches BIT-EXACTLY. This mechanically checks the whole
comparability chain: vendored copy uncorrupted, container jax == host jax,
GM mapped votes to actions correctly (including both default cases).

Usage: /home/cc/cilib/.venv/bin/python twin_check.py <physics.pkl>
"""
import pickle
import sys

sys.path.insert(0, "/home/cc/cilib")

import jax
import jax.numpy as jnp
import numpy as np

from cilib.core.category import sequential
from experiments.basin_stability import transforms as tr
from experiments.basin_stability.state import create_initial_state

SEED, ROUNDS, K, N = 424242, 6, 4, 3
# 0-based actions per round for (a1, a2, a3) — MUST mirror gate/moves/*.moves
# after the GM's default rule (round 3: a2's "9" invalid, a3 silent -> both 0).
ACTIONS = [(0, 1, 2), (1, 1, 1), (2, 0, 0), (3, 3, 3), (0, 0, 1), (1, 2, 2)]

cont = pickle.loads(open(sys.argv[1], "rb").read())
assert cont["round"] == ROUNDS, f"container stopped at round {cont['round']}"

pre = jax.jit(tr.proposal_generation_transform)
post = jax.jit(sequential(
    tr.make_aggregation_transform("pdd"),
    tr.resource_update_transform,
    tr.reward_transform,
    tr.trust_update_transform,
    tr.make_election_transform("pdd"),
    tr.step_counter_transform,
))
gs = create_initial_state(n_agents=N, n_adversarial=0, K=K, T=ROUNDS,
                          mechanism="pdd", seed=SEED)
for acts in ACTIONS:
    gs = post(pre(gs).update_node_attrs(
        "last_action", jnp.array(acts, dtype=jnp.int32)))


def eq(x, y):
    if isinstance(x, (jnp.ndarray, np.ndarray)):
        return np.array_equal(np.asarray(x), np.asarray(y))
    return x == y


bad = [f"{g}[{k}]"
       for g in ("node_attrs", "adj_matrices", "global_attrs")
       for k in getattr(gs, g)
       if not eq(getattr(gs, g)[k], getattr(cont["gs"], g)[k])]
assert not bad and eq(gs.node_types, cont["gs"].node_types), f"diverged: {bad}"
print(f"TWIN CHECK: bit-exact (final level "
      f"{float(gs.global_attrs['resource_level']):.2f}, {ROUNDS} rounds)")
