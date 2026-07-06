# mafia — the step-6 acceptance test

Human-facing notes (this file is NOT baked into the world — only `world.md`,
`roles/*.md`, `kick.txt`, and `data/` reach the agents).

Classic Mafia at 6–12 agents: mafia = N//4 (min 1), 1 detective, 1 doctor,
rest villagers. Deterministic GM (design:
`/home/cc/2026-07-02_thoughts_on_agent_driving_for_games.md`): morning report
→ serialized seeded-order discussion (`discussion_passes` per day) →
structured vote (plurality, role revealed on elimination) → night (mafia
kill, detective investigate — result arrives in their next GM message —
doctor save) → repeat until mafia are gone (town wins) or mafia ≥ town
(mafia wins).

Uses the whole step-4/6 platform surface:
- **Hidden info at build**: `logic.fill_briefing` names each mafia's partners
  in their briefing; `logic.gm_secrets` bakes the answer key to
  `/gm/secrets.json` (agents can't read it; also in the build audit record).
- **`hard_enforcement` build param (decision 12)** — one scen, two world
  physics. Hard: live `gm_policy` allowlists (day = public posts only /
  night = mafia↔mafia PMs only; public posting is a policy pair
  `[sender, "public"]`). Soft: identical rules as briefing NORMS only; the
  GM referees overnight violations from `api.activity()` metadata and
  announces them. Playtesting both is the experiment.
- Elimination via `gm_remove` (no wakes, no sends), pull-only public board,
  every activation caused by a logged GM wake or PM delivery.

Scen gate: `gate/run.sh` (the worked example of a scen shipping its own gate)
— the same scripted 6-agent game in both modes; a mafia night post is DENIED
under hard physics and POSTED-then-refereed under soft. Runs on the engine's
shared harness (`runtime_pi/gm_gate/setup_world.sh` + scripted dummy).

Watch a run: `env logs <name> -f` (gateway audit), the public board for the
game as spectators see it, `/gm/state.json` for the true state.
