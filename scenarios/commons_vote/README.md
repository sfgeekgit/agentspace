# commons_vote — shared-reservoir commons game on CI Lib physics

LLM agents replace the scripted Q-learning voters of CI Lib's
basin_stability experiment ("Basin Stability of Democratic Mechanisms Under
Adversarial Pressure"). Each round the dispatcher generates K proposals with
per-agent noisy signals (vendored physics), wakes every agent PRIVATELY
with its own signals, collects `submit <n>` votes, then applies the
unmodified vendored pipeline: aggregation → resource update → reward →
trust EMA → election (PRD) → step counter. Collapse ends the world early.

**The point is the comparison**: offline 500-seed robot sweeps (run in the
untouched cilib repo at `/home/cc/cilib`) vs live LLM worlds, on
bit-identical rules. What makes that claim hold:

- `dispatch/cilib/` + `dispatch/experiments/` are **byte-for-byte copies** of the repo
  at the commit pinned in `dispatch/CILIB_PIN`. `gate/vendor.sh` re-vendors and
  verifies with diff; never edit these files. To bump the pin: update
  CILIB_PIN, re-run vendor.sh, re-run the equivalence gate.
- dispatcher physics runs ONLY through two `jax.jit` blocks (`make_physics` in
  `dispatch/main.py`) — plain-eager application is NOT bit-exact with the
  offline `lax.scan` sweeps (float noise flips argmax decisions and forks
  trajectories). `gate/offline_twin_equivalence.py` proves jit==scan for
  all three mechanisms; it mirrors the dispatcher's exact jit structure and must
  stay structurally identical to it.
- Environment (`source_image` in scenario.toml, ghcr `env-commons_vote-1`):
  pi-world:base + jax/jaxlib 0.10.2 + numpy 2.5.1 — exact match of the
  host cilib venv, incl. Python 3.13.5. cilib is deliberately NOT installed
  in the image (agents can read installed source; its names are
  prejudicial) — the vendored copy ships privately in `/dispatch/code`.

State: GraphState pickles to `/dispatch/physics.pkl` after every round, before
announcing (stable across snap/fork because the env is digest-pinned;
`logic.dispatch_secrets` bakes a `physics_seed` so the trajectory is reproducible
from the build). Missing/invalid votes record as option 1 (stated in every
round prompt). Watch view: `/dispatch/game_log.jsonl` (spoilers: true option
utilities + per-agent votes).

Mechanisms: pdd/prd fully supported. pld runs but agents can only vote, not
delegate — the delegate move syntax is not built yet. Adversarial roles
(`n_adversarial` > 0) are briefed but not yet exercised in a real run.

**The scen gate** (`gate/run.sh`, zero tokens, ~30s): scripted dummies play
a fixed 3-agent, 6-round PDD game through the REAL stack (gateway + dispatchd +
this dispatcher + vendored physics) in a throwaway container on the pinned
environment — including an invalid and a missing vote (both must default)
and a clean post-game re-entry — then `gate/twin_check.py` replays the same
game in the untouched cilib repo on the host and asserts the final
GraphState matches bit-exactly. Run it after any change to the dispatcher, the
vendored code, the pin, or the environment. (Also in gate/:
`offline_twin_equivalence.py`, the standalone host-side proof that jitted
physics == the lax.scan sweeps; and `vendor.sh`.)
