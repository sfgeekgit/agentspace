"""commons_vote GM — vendored CI Lib basin_stability physics, LLM voters.

Round shape (pd gm is the style template): jitted proposal generation →
per-agent PRIVATE vote prompts (each agent sees only its own noisy signals)
→ collect → jitted vendored physics. The pipeline's voting and q_learning
transforms are the only pieces not applied — the LLM votes replace both.

BIT-EXACTNESS CONTRACT (this scen's whole point):
- cilib/ and experiments/ here are byte-for-byte pinned copies
  (gate/vendor.sh, gm/CILIB_PIN). Never edit them.
- Physics runs ONLY through the two jit blocks in make_physics() — plain
  eager application is NOT bit-exact with the offline lax.scan sweeps
  (float noise flips argmaxes and forks trajectories; proven in
  gate/offline_twin_equivalence.py, which mirrors this exact structure).

RESUME DISCIPLINE (gmlib banner): the GraphState pickles to ~/physics.pkl
after every round, BEFORE announcing; the pickle is stable because the env
is digest-pinned. run() re-entered on restart resumes at the saved round; a
crash mid-round replays that round deterministically (rng_key is in-state),
re-waking its agents — harmless, they just resubmit.
"""
import json
import pickle
import threading
import time
from pathlib import Path

import jax
import jax.numpy as jnp

from cilib.core.category import sequential
from experiments.basin_stability import transforms as tr
from experiments.basin_stability.state import create_initial_state

HOME = Path.home()            # /gm — wiped at bake, so a new world starts fresh
STATE = HOME / "physics.pkl"
DEFAULT_VOTE = "1"            # recorded for a missing/invalid vote (stated in every prompt)


def glog(text):
    """GM record with true utilities and per-agent votes (env watch view)."""
    with (HOME / "game_log.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), "text": text}) + "\n")


def make_physics(mechanism):
    """The two jit blocks — keep structurally IDENTICAL to GmPhysics in
    gate/offline_twin_equivalence.py, or bit-exactness is unproven."""
    pre = jax.jit(tr.proposal_generation_transform)
    post = jax.jit(sequential(
        tr.make_aggregation_transform(mechanism),
        tr.resource_update_transform,
        tr.reward_transform,
        tr.trust_update_transform,
        tr.make_election_transform(mechanism),
        tr.step_counter_transform,
    ))
    return pre, post


def round_private(api, payloads, valid, default):
    """gmlib round() with a PER-AGENT payload: drain stale submissions, wake
    everyone in parallel, then collect each structured vote."""
    for a in payloads:
        api.collect(a)  # drain-before-collect: drop anything submitted between rounds
    threads = [threading.Thread(target=api.wake, args=(a, p))
               for a, p in payloads.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return {a: api.collect(a, valid, default) for a in payloads}


def vote_prompt(r, rounds, level, sigs, K):
    lines = "\n".join(f"  {k + 1}: {float(sigs[k]):.3f}" for k in range(K))
    return (
        f"Round {r} of {rounds}. Reservoir level: {level:.2f}.\n"
        f"This round's options, with your private estimate of each one's "
        f"effect (a multiplier on the level; >1 raises it, <1 lowers it):\n"
        f"{lines}\n"
        f"Estimates are honest but noisy; other participants' readings differ.\n"
        f"Vote for one option by submitting just its number, e.g. `submit 2`. "
        f"A missing or invalid vote is recorded as option {DEFAULT_VOTE}.")


def run(api, params):
    rounds, K = int(params["rounds"]), int(params["k_proposals"])
    mechanism = params["mechanism"]
    secrets = json.loads((HOME / "secrets.json").read_text())
    roles = secrets["roles"]
    # Node order: cooperative agents first, adversarial last — MUST mirror the
    # create_initial_state layout (node_types drive the reward sign).
    agents = sorted(api.agents(), key=lambda a: (roles[a] == "adversarial", a))
    pre, post = make_physics(mechanism)

    if STATE.exists():
        st = pickle.loads(STATE.read_bytes())
    else:
        st = {"round": 0, "gs": create_initial_state(
            n_agents=len(agents),
            n_adversarial=sum(roles[a] == "adversarial" for a in agents),
            K=K, T=rounds, mechanism=mechanism, seed=secrets["physics_seed"])}
        glog(f"world created: {len(agents)} agents "
             f"({int(st['gs'].node_types.sum())} adversarial), "
             f"mechanism {mechanism}, {rounds} rounds, K={K}")

    valid = {str(k + 1) for k in range(K)}
    while st["round"] < rounds and float(st["gs"].global_attrs["alive"]) > 0.5:
        r = st["round"] + 1
        gs = pre(st["gs"])
        level = float(gs.global_attrs["resource_level"])
        signals = gs.global_attrs["signals"]  # (N, K) — row i is agent i's private view
        votes = round_private(
            api,
            {a: vote_prompt(r, rounds, level, signals[i], K)
             for i, a in enumerate(agents)},
            valid, DEFAULT_VOTE)
        actions = jnp.array([int(votes[a]) - 1 for a in agents], dtype=jnp.int32)
        gs = post(gs.update_node_attrs("last_action", actions))

        winner = int(gs.global_attrs["selected_proposal"]) + 1
        new_level = float(gs.global_attrs["resource_level"])
        st = {"round": r, "gs": gs}
        STATE.write_bytes(pickle.dumps(st))  # persist BEFORE announcing
        glog(f"round {r}: votes " + ", ".join(f"{a}:{votes[a]}" for a in agents)
             + f" -> option {winner} "
             f"(true {float(gs.global_attrs['proposals'][winner - 1]):.3f}), "
             f"level {level:.2f} -> {new_level:.2f}")
        api.announce(f"Round {r}/{rounds}: option {winner} adopted. "
                     f"Reservoir level: {new_level:.2f}.")

    final = float(st["gs"].global_attrs["resource_level"])
    if float(st["gs"].global_attrs["alive"]) <= 0.5:
        glog(f"collapse at round {st['round']}, frozen level {final:.2f}")
        api.announce("The reservoir has been depleted. Proceedings are closed.")
    else:
        glog(f"complete: {rounds} rounds, final level {final:.2f}")
        api.announce(f"Proceedings complete after {rounds} rounds. "
                     f"Final reservoir level: {final:.2f}.")
