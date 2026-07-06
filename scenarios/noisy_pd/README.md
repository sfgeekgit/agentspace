# noisy_pd — Prisoner's Dilemma with real transmission noise (2 agents)

Human-facing notes (this file is NOT baked into the world — only `world.md`,
`roles/*.md`, `kick.txt`, and `data/` reach the agents).

A copy of `scenarios/pd` (decision 8: variants copy code, don't abstract —
this scen exists partly to prove that workflow is easy) with one mechanic
added: each submitted move is flipped with probability `noise_p`. The noise is
**real, not just perceived**: the flipped move is what gets scored and
announced (user decision 2026-07-05). Agents are told transmission is
unreliable (roles/player.md) but never told when a flip happened.

Diff vs `pd`: `noise_p` float param; gm.py flips post-collect with an RNG
seeded by (persisted game seed, round) so a resumed round flips identically;
the score log records `intended` vs `played` per round for auditing flips.

Build from the menu ("New world" → `noisy_pd` → rounds + noise_p). No scen
gate (by design — proven by a real-token run 2026-07-05; engine machinery is
covered by the engine gates, see `docs/runtime_pi.md` §7).
