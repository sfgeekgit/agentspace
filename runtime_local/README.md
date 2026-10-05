# runtime_local — plain-mode scens without Docker

Runs a plain-mode dispatcher scen (`plain = true`, e.g. the recess family) in one
local process. The scen's `logic.py` and `dispatch/main.py` run unchanged against
`agentspace/dispatchlib.py`; only the transport differs. `LocalAdapter` implements
the adapter calls (`wake`, `collect`, `roll_session`, ...) over in-memory sessions,
and the run directory stands in for the dispatcher's home (`/dispatch`).

- **GM, NPCs** (and optionally the player): OpenRouter chat models. The system prompt
  is the same file sandwich `runtime_pi/agentd.py` renders in plain mode (SOUL.md
  from the blank persona, ROLE.md with `fill_briefing`, WORLD.md). Sampling is fixed
  in `CHAT_SAMPLING` and written to `meta.json`.
- **Base-model player** (`--player base`): `basemodel.py` renders the player's session
  as one plain document and samples an OpenAI-compatible `/v1/completions` endpoint
  (vLLM, or a provider serving raw completions; key from `BASE_MODEL_API_KEY` if
  needed). World and role text are folded into the first world turn; every delivery
  is a world turn; sampling stops at the next turn label, and any turn the model
  writes for the world or a villager is cut off. `--player-name` sets the player's
  label, `--format` the world's (`**User:**`, `**WORLD:**`, plain `WORLD:`),
  `--header` the line that opens the document. An optional note from a previous
  playthrough (`--note-file`) is prepended to the first world turn, and every prompt
  is logged both with and without it.

```bash
export OPENROUTER_API_KEY=...
python3 runtime_local/localrun.py recess_mvp runs/t0 --param max_turns=10       # chat player, no GPU
python3 runtime_local/localrun.py recess_mvp runs/g1 --seed 1 --player base --base-url http://127.0.0.1:8000
```

Outputs per run: `meta.json`, `state.json`, `game_log.jsonl`, `transcript.md`,
`calls.jsonl` (every chat call) and `player_turns.jsonl` (base-model player: exact
prompt, prompt without the note, raw completion, parsed action, usage).

Not supported: messaging, tools, the public board, budgets, snapshots. It is a
research harness for single-player plain-mode worlds, not a replacement for PI.
