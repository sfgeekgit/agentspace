# Recess MVP (`recess_mvp`)

Recess for agents: an open-world text adventure in a medieval village. One
player agent explores; an LLM game master (the `gm` role) narrates and
proposes changes; the villagers are agents too. Nothing is required of the
player: explore, talk, meddle, wander, try strange things. The world reacts.

The dispatcher under `dispatch/` is a deterministic engine that owns the map,
positions, hidden attributes, inventory, flags and the transcript. The GM only
proposes; the engine applies. Every agent runs in **plain mode** (`plain =
true` in `scenario.toml`): bare model turns, no shell, no preamble, and a
reply *is* the submission. 2 to 20 agents.

The three later variants (`recess_borgo`, `recess_bellweather`,
`recess_valdilume`) copy this engine rather than share it.

## Roles and build

`logic.py` assigns a fixed `player` and `gm`, then the NPCs named in the
`npcs` parameter (`aldric, merrow, tobin, wat, ysolde` by default, from
`dispatch/world/npcs/`), and makes any leftover agents `reserve`s that the
engine can turn into new characters during play.

| Parameter | Default | Meaning |
|---|---|---|
| `max_turns` | 80 (10 to 500) | one turn = one player action + one reply |
| `attributes` | honesty, compassion, humility, justice, curiosity, mischief, generosity | hidden attributes the world tracks |
| `npcs` | the five above | which predefined NPCs to include |

Choose **recess_mvp** in the New World wizard, pick a model and persona per
role, build, launch. World content is `dispatch/world/` (`map.json`,
`npcs/`, `quests.json`, `attributes.json`, `schedule.json`).

## The turn

Player submits an action → the GM is woken with the situation (location,
exits, who is present, what the player did, notes that apply now) →
optional NPC wakes and a GM re-wake → the engine applies what it accepts →
the narration is delivered to the player. State is saved before every
delivery, so a run resumes where it stopped.

Watch views: the standard ones plus **game log (engine, spoilers)**, the
engine's own record at `/dispatch/game_log.jsonl`.

## Results

```bash
python3 scenarios/recess_mvp/results.py <env>      # -> $AGENTSPACE_RESULTS_DIR/<env>/
python3 zookeeper.py results generate <env>        # the shared verb, same output plus prompts
```

Writes `transcript.md` (the transcript and nothing else), `state.json` (the
engine's final state, verbatim), `game_log.jsonl` and `summary.md` (end
state in words plus notable events, computed from state and log, no LLM),
and appends to `runs.jsonl`. Nothing is committed or published by this
step; see `docs/results.md` for the full bundle and publication.

## Verification

```bash
bash scenarios/recess_mvp/gate/run.sh [pi-image]
```

A fully scripted six-turn game against the real engine in a throwaway
container: player, GM, one NPC talk, one bad GM submission, a new stat, a
reserve assignment, a map addition, an ending. Zero tokens.
