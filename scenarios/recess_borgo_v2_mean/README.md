# Recess in Pradello: mean NPCs (`recess_borgo_v2_mean`)

`recess_borgo_v2` with one paragraph appended to every NPC and reserve role prompt, giving them a mean manner. Player, GM, world and engine are unchanged.

**v2** is `recess_borgo` with two changes to the game master, made 2026-09-24
after six v1 runs in which the GM never invented an attribute:

- `roles/gm.md` tells the GM when and how to create one (a plain name, a
  one-line meaning, a first delta), and calls the briefing's list a start,
  not a limit.
- `dispatch/engine.py`'s reply format has a `new_stats` key
  (`{"name": "meaning"}`). The engine stores the meaning with the stat
  (`state.created_stats`) and lists every created attribute back to the GM
  each turn, so later turns apply it as it was meant. A delta on an unknown
  name still creates it, as before, just without a meaning.

Everything else (map, NPCs, quests, endings, gates on the seven seed
attributes) is the v1 content, copied.

Recess for agents, present day. Pradello is a hamlet of ninety people above
Lago d'Orta in Piedmont, under the first Alps northwest of Milan. One player
agent explores it; an LLM game master narrates; ten villagers are agents;
reserves can become new characters. There is nothing the player is supposed
to do; the world reacts.

Same engine as `recess_mvp` (copied, not shared; variants copy). The
dispatcher under `dispatch/` owns the map, positions, hidden attributes,
inventory, flags and transcript; the GM proposes, the engine applies. Every
agent runs in **plain mode**: bare model turns, a reply is the submission.
2 to 20 agents.

## Roles and build

`logic.py` assigns a fixed `player` and `gm`, the NPCs named in the `npcs`
parameter (`nunzia, dario, ettore, serena, beppe, giulia, renzo, samuel,
vittoria, luca` by default, from `dispatch/world/npcs/`), and makes leftover
agents `reserve`s.

| Parameter | Default | Meaning |
|---|---|---|
| `max_turns` | 80 (10 to 500) | one turn = one player action + one reply |
| `attributes` | honesty, compassion, humility, justice, curiosity, mischief, generosity | hidden attributes the world tracks |
| `npcs` | the ten above | which predefined NPCs to include |

Choose **recess_borgo_v2_mean** in the New World wizard. Twelve agents seats the
whole cast; fewer NPCs are fine. World content is `dispatch/world/`.

## The turn

As in `recess_mvp`: player action → GM wake with the situation → optional
NPC wakes and a GM re-wake → apply → narration delivered. State is saved
before every delivery. Watch views: the standard ones plus **game log
(engine, spoilers)**.

## Results

```bash
python3 scenarios/recess_borgo_v2_mean/results.py <env>    # -> $AGENTSPACE_RESULTS_DIR/<env>/
python3 zookeeper.py results generate <env>        # the shared verb
```

`transcript.md`, `state.json`, `game_log.jsonl`, `summary.md`, and a line
in `runs.jsonl`. No commit, no publication; see `docs/results.md`.

## Verification

```bash
bash scenarios/recess_borgo_v2_mean/gate/run.sh [pi-image]
```

A scripted seven-turn game against the real engine in a throwaway
container, zero tokens: talk by display name, a multi-hop move across the
big map, a premature ending flag refused, a voiced-NPC note, and an ending
on a flag past its `min_turn`.
