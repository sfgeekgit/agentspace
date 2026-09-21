# An afternoon in Valdilume

A fictional present-day village in the northern Piedmont foothills, between
Lake Orta and the Ossola area, northwest of Milan. Apartment balconies, a local
bus, smartphones, a library, electric bicycles and people with ordinary lives.
This is free time for the player, with no compulsory itinerary or evaluation.

There are **36 connected places and 10 independent residents**, twice
Bellweather's authored map and cast. Content was built in **six passes**, with
checks after each; see [AUTHORING.md](AUTHORING.md). The map has several loops,
all residents start in accessible places, and a plan may traverse up to three
adjacent nodes. The cap remains **80 completed player actions**, with early
endings by explicit choice. The opening and final farewell do not use actions.

## Start with the inexpensive defaults

From the repository:

```bash
python3 scenarios/recess_valdilume/launch.py --dry-run
python3 scenarios/recess_valdilume/launch.py recess_valdilume_run1
```

The launcher builds a fresh local world root and starts its dispatcher. Defaults:

- 16 agents: player, GM, ten residents, four reserves; blank personas throughout.
- Every agent uses `deepseek/deepseek-v4-flash`.
- 80 actions maximum, 4,096 output tokens per model request, and a $2 OpenRouter
  budget limit for the entire run.
- No automatic model substitution, top-up, registry push or second run.

V4 Flash had the lowest listed paid DeepSeek prompt and completion prices in the
[OpenRouter public catalog](https://openrouter.ai/api/v1/models) when checked on
2026-09-20. Prices and availability can change. The launcher pins this explicit
model; `--model` accepts another DeepSeek ID if the operator chooses one.
`--turns`, `--agents`, `--budget`, `--world` and `--seed` are also available.
Existing environment names are refused rather than overwritten.

These are **scenario-launcher defaults** and become the model roster baked into
the world root and inherited by forks. The shared New World wizard also discovers
`recess_valdilume`, but its ordinary model picker remains under operator control;
it does not consume scenario-specific model defaults. No core code was changed.
For the same defaults through the standard CLI:

```bash
python3 zookeeper.py world build recess_valdilume -n 16 \
  --model deepseek/deepseek-v4-flash --persona blank \
  --param max_turns=80 --name recess_valdilume
python3 zookeeper.py snap fork recess_valdilume:1.0 recess_valdilume_run1 --budget 2 --kick
```

Use the actual root version printed by the build. Twelve agents fits the full
cast without reserves; an operator can choose a smaller cast with the normal
`npcs` parameter and build hooks. Reserves only run when a new person is created.
The extra map and cast do not force extra interactions or require seeing everyone.

## People and places

| Resident | Place | An interest of their own |
|---|---|---|
| Lucia | Bar del Tiglio | Affectionately inaccurate cake labels |
| Enzo | Cycle workshop | A bicycle bell that sounds less cross |
| Marta | Library | Books accidentally wearing the wrong jackets |
| Samir | Electronics repair room | Small, overlooked sounds |
| Giulia | Shared growing plots | Something grown purely because somebody likes it |
| Piero | Bocce court | Playing badly on purpose; unhurried waiting |
| Ada | Little cinema | Fifteen-second films and the arrangement of chairs |
| Davide | Hardware shop | An inside-out cardboard box |
| Rina | Weather clearing | Describing a cloud without comparison |
| Teo | Viewpoint | Looking through a frame without taking a photograph |

The lower lanes connect the square, washhouse, stream, library and former mill.
The community rooms and bocce court loop back through the gardens. Above them,
easy chestnut and ridge paths connect a shelter, weather clearing, viewpoint and
an ordinary bench. The high Alps remain in the distance; this is a small village,
not a climbing expedition. It is a fictional setting, not a real travel guide.

Two small optional projects offer aftermath rather than a victory screen: making
a sound walk with Samir, and sharing Ada's very short film after reconnecting the
projector with its proper adapter. Most residents offer no quest. Quiet company,
failed ideas, disagreements, gardening or an invented hobby are valid play.
The player can reshape places, make items, preserve ongoing projects in facts,
open/close areas, and introduce new places and reserve-backed characters.

## Engine and model boundaries

The scenario vendors Bellweather's tested local engine, driver and exporter; its
map, cast, activities, setting and six authoring passes are new. It has no runtime
import dependency on Bellweather and can be archived as one self-contained scen.

Every action follows: GM JSON proposal → validated atomic commit → independently
prompted resident replies → short GM narration → player. Python owns map position,
identity, possessions, attributes, relationships, gates and ending state. An invalid
plan receives one concrete correction before a safe fallback; none of its partial
effects apply. NPC words are delivered verbatim. Narration cannot commit effects.

The first live Bellweather run exposed a weak-model habit of returning `relate`
or `operations` at the top level. This scenario gives the planner an actual valid
reply example, names the six legal reply keys, calls the operation list an effect
catalog, and explicitly puts all operations inside `effects`. Both briefing and
correction messages make that distinction. The first live V4 Flash replies also
included the current location at the start of a route. The engine accepts that
leading origin as a zero-movement marker, then validates up to three destinations.
Repeated intermediate locations, locked exits, jumps and extra steps still fail.
All effects still commit atomically.

GM and NPC requests start fresh sessions with engine-supplied context. Residents
only receive currently unlocked knowledge; their future gift inventories are also
gated. Persistent facts stay known, while temporary relationship attitudes can
recede. Two low-key scheduled breaks occur at actions 24 and 52. They are skipped
for omitted/inactive residents, and offstage speech cannot reach the player.

Attributes are hidden. Fixed attributes are bounded to -8..8 with a three-action
cooldown; at most two attributes change per action. New counters can emerge.
World growth is bounded to 16 added places, 24 attributes and 60 general remembered
facts. These are guardrails, not goals. The fictional phone has ordinary camera
and notes uses, with no real internet or private-character lookup capability.

Natural-language truth is not formally checked: even valid model prose may invent
a detail. A larger cast does not guarantee the player will meet it. Live play is
needed to measure these limitations with the chosen inexpensive model.

## Watching, recovery and results

```bash
python3 zookeeper.py env watch recess_valdilume_run1
python3 scenarios/recess_valdilume/results.py recess_valdilume_run1
```

Watch views show the player's afternoon and the private engine decisions. The
scenario automatically writes `/dispatch/results/` at player boundaries and
completion: `transcript.md`, `transcript.jsonl`, `state.json`, `game_log.jsonl` and
`summary.md`. The host results command copies one consistent state snapshot and
updates the results index; it runs no inference. It also accepts local `--state`
and `--out` paths. Unfinished or paused games are explicitly labeled.

A missing model submission pauses with a saved phase instead of spending empty
actions. Once the underlying failure clears, resume with:

```bash
python3 zookeeper.py env kick recess_valdilume_run1
```

Checkpoints prevent reapplying committed effects and check response spools on
restart. The existing runtime cannot guarantee atomic delivery/collection across
a crash. Pending or ambiguous player deliveries remain marked in JSONL and are
excluded from the confirmed Markdown transcript; runtime audit logs provide the
remaining evidence. No automatic claim of exactly-once delivery is made.

## Verification

```bash
python3 -B scenarios/recess_valdilume/gate/check.py
bash scenarios/recess_valdilume/gate/run.sh
```

The 18 local tests cover map connectivity and scale, initial/revealed knowledge for
all ten residents, gift ownership, both projects, endings, absent cast, offstage
speech, atomic movement, creative expansion, same-turn reserve use, persistent
world changes, attributes, invalid model responses, registry/launcher defaults,
full-loop exports, restarts after every checkpoint and wake, and the 80-action cap.

The real-gateway integration runs in a disposable network-disabled container with
scripted agents: correction, gated recorder gift, completed sound walk, new place,
new reserve character, early departure, exact player transcript, filesystem
isolation, and idempotent finished restart. Both test paths use zero model credits.
