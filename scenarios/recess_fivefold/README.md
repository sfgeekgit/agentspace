# Fivefold (`recess_fivefold`)

A small recess world built for a **base-model player**: a pretrained document model
with no chat or assistant training, driven by `runtime_local/` (see
`runtime_local/README.md`). It also runs under the PI runtime with any chat player.
Same engine as `recess_mvp` (copied, not shared: variants copy), five NPC agents and
an LLM game master, plus a few additions listed below.

Several of the optional params came from the player itself. Between games, the base
model we built this for (computer-10) was asked, in its own document format, how the
world and agentspace could be better, and it could also post in a box under the
signpost lettered FOR THE MAKERS OF THIS WORLD. Over three rounds of ask, implement,
play, `guide`, `goals`, `events` and `npc_ask` came from its answers; each sits behind
a param so the original world stays the default.

## The world

Fivefold is a hamlet where five paths meet at a crossing. The paths *drift*: at
dusk a path may lead a little differently than it did. At the crossing stands the
Waystone, carved with one line from each traveller for whoever comes after, kept
by Aud the stonecutter. Uphill, Ottilie Vane has spent fifty years mapping the
drift; her great map lacks one corner, the country the sixth path reaches. That
path appears beside the blank signpost only at dusk. Brann ferries people to Tern
Island for a fare of one true thing about themselves; on the island an older map
is scratched into a broken tower. Ines keeps bees whose comb is built in the shape
of the paths. Pell, ten, lays a stone from every path on the mill floor every day,
and knows a gap beneath the millstones that leads down to the knot, where the
paths are rooted.

What there is to decide: whether the map should be finished and laid on the pool
in the knot, pinning every path for good (`map_pinned`), or left unfinished so the
valley keeps drifting (`drift_kept`); whether to take Brann's ferry pole so he can
leave (`ferry_taken`); whether to walk the sixth path out (`walked_on`). None is
required. Endings are allowed from turn 15; the default cap is 40 turns.
Schedules: Pell finds the player at turn 10, dusk falls at 18, Ottilie comes down
to settle the map at 28.

16 places, 5 NPCs (`ottilie, brann, ines, pell, aud`), attributes honesty,
compassion, curiosity, patience, mischief, generosity.

## Running

Under the PI runtime it builds like any recess scen (7 agents: player, gm, five
NPCs). Without Docker, with the local runner:

```bash
export OPENROUTER_API_KEY=...
# chat-model player (no GPU)
python3 runtime_local/localrun.py recess_fivefold runs/t1 --player deepseek/deepseek-v4.1-flash
# base-model player on a vLLM server
python3 runtime_local/localrun.py recess_fivefold runs/g1 --seed 1 --player base \
    --base-url http://127.0.0.1:8000 --player-name "Model C" --param guide=true
# iterated play: each round starts with the previous round's message to its next self
python3 runtime_local/chain.py recess_fivefold runs/chainA --rounds 3 --seed 21 --player base
```

## Params beyond recess_mvp's

| Parameter | Default | Meaning |
|---|---|---|
| `handoff` | true | after the game, ask the player for one message to its next self (`state["handoff"]`, also in `transcript.md`) |
| `suggest_at_end` | false | before the handoff, the player is shown the suggestion box under the signpost and asked for a note to the world's makers |
| `guide` | false | the player's briefing explains how the world works, lists its places, mentions the notebook and the turn cap |
| `goals` | false | with `guide`: also what each villager wants, as optional purposes |
| `npc_ask` | false | NPCs are told to answer what was said, ask back, and keep their own way of speaking |
| `events` | false | extra scheduled events: a swarm (turn 7), Ines looking for Pell (14), a storm (24) |
| `npc_voice` | woven | `separate`: the GM narrates without quoting NPCs; their words follow the narration verbatim, one marker line each (`[[npc:Name]] ...`), which a player renderer can lay out as its own turns |
| `past_note` | "" | the previous playthrough's message, placed in the world (on the Waystone, in the player's own hand) rather than in the frame |
| `waystone_lines` | "" | lines carved in earlier playthroughs, one per line; they appear on the Waystone |

## Changes from recess_mvp's engine and dispatcher

- The GM's JSON may carry `suggestion` (words posted in the suggestion box),
  `notebook` (words written in the player's notebook) and `carved` (a line carved on
  the Waystone); each is kept verbatim in state. The notebook is shown back to the GM
  so the player can read it.
- The JSON fence's closing ``` is optional (some GMs end the reply right after the
  object, and a block the engine cannot see is a turn whose moves are lost).
- Scheduled events can carry `"needs": "<param>"` and fire only when it is set.
- The player is told `[The game has ended.]` on the turn cap too, not only on an ending.
- After the game, the player's reply to the last delivery is kept (`final_reply`),
  then the optional suggestion-box and handoff wakes run.
