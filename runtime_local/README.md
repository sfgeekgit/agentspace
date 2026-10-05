# runtime_local — plain-mode scens without Docker

Runs a plain-mode dispatcher scen (`plain = true`, e.g. the recess family) in one
local process. The scen's `logic.py` and `dispatch/main.py` run unchanged against
`agentspace/dispatchlib.py`; only the transport differs. `LocalAdapter` implements
the adapter calls (`wake`, `collect`, `roll_session`, ...) over in-memory sessions,
and the run directory stands in for the dispatcher's home (`/dispatch`).

Every agent is an OpenRouter chat model. The system prompt is the same file sandwich
`runtime_pi/agentd.py` renders in plain mode (SOUL.md from the blank persona, ROLE.md
with `fill_briefing`, WORLD.md). Sampling is fixed in `CHAT_SAMPLING` and written to
`meta.json`.

```bash
export OPENROUTER_API_KEY=...
python3 runtime_local/localrun.py recess_mvp runs/t0 --param max_turns=10
```

Outputs per run: `meta.json`, `state.json`, `game_log.jsonl`, `transcript.md` and
`calls.jsonl` (every chat call: payload, reply, usage).

Not supported: messaging, tools, the public board, budgets, snapshots. It is a
research harness for single-player plain-mode worlds, not a replacement for PI.
