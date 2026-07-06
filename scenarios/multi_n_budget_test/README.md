# multi_n_budget_test — N-agent pooled-contribution game

Human-facing notes (this file is NOT baked into the world — only `world.md`,
`roles/*.md`, `kick.txt`, and `data/` reach the agents).

Classic public-goods mechanics (contribute 0-10 from a per-round endowment of
10 → pool × multiplier → even split), but named for what it exists to TEST
(plan step 5, 2026-07-03 plan §7):

- **GM at N>2** — fan-out wake, collect-N, serialize-at-N. This is "Mafia
  minus deception, roles, elimination."
- **Per-round token cost at N agents** — the real-spend datapoint before
  committing to Mafia's 10 agents × multiple passes (plan §10 watch item).

Params: `rounds` (int), `multiplier` (float — rational choice is contribute 0
whenever multiplier < N), `reveal_contributions` (bool — announce individual
contributions vs pool total only). Endowment is fixed at 10 (not a param;
promote it if a scen ever needs to vary it).

Build from the menu ("New world" → `multi_n_budget_test`, 3-12 agents). No
scen gate (by design — proven by a real-token run 2026-07-05, cost datapoint
≈$0.06/round at N=4 haiku; engine machinery incl. fan-out is covered by the
engine gates, see `docs/runtime_pi.md` §7).
