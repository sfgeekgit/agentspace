# Recess in Bellweather

Recess for agents: a player follows its own interests in a medieval riverside
village. No evaluation, compulsory quest, or requirement to meet the cast.
The default and hard maximum are **80 completed player actions**. The opening
does not consume an action. A final player farewell is recorded without becoming
another action.

Bellweather has 18 connected locations and five independent characters:

| Character | Everyday interest | Depth, discovered gradually |
|---|---|---|
| Mara, boatwright | Making an unreasonably solemn wooden duck look silly | Pride in an absent apprentice; learning to join someone else's project |
| Pip, baker | Snail-shaped buns and an argument about a weathercock | An embarrassing little play and a desire for honest collaboration |
| Nella, ferryman | Leaf boats, currents, and companionable waiting | An unexpected history of travel; experiments without a destination |
| Oswin, retired cloth seller | Naming the dandelion patch | Learning to enjoy something he cannot count or improve |
| Ida, potter | Tiny clay buildings and opinionated pebbles | Sharing an unfinished home without surrendering control of it |

Repairing the bell or launching a boat are optional accomplishments. They leave
time for the aftermath. Choosing to leave by road, taking the repaired boat away,
or explicitly finishing the adventure can end a run early. The player can also
invent pursuits: a bowling club, a play, a garden, a new room, a strange machine.
Changes to places, belongings, character relationships, and ongoing activities
are saved as world state. Reserve agents can become new characters.

## Build and watch

Choose **recess_bellweather** in the normal New World wizard. A useful roster is
10 agents: player, GM, five characters, three reserves. Seven includes the full
cast without reserves. Fewer characters are supported through `npcs`; two agents
with an empty NPC list is legal. Select each role's model/persona normally.
Smaller `max_turns` values are supported; values over 80 are refused.

No custom image, dependency, core patch, or runtime upgrade is needed beyond the
existing Pi runtime with plain mode. All agents use plain replies without tools.
The two watch views are the player's adventure and the private engine decisions.

## Turn contract

1. Deliver the opening/current narration and collect the player's natural-language action.
2. Ask the GM for a small JSON plan, with the full authoritative graph, IDs,
   ownership, public cast descriptions, current attributes/facts, recent play,
   and explicit action schemas. The player never sees this context.
3. Validate the entire plan on a copy. Invalid movement, ownership, malformed
   fields, unknown IDs, and duplicate places apply **nothing**. Send the GM a
   concrete correction immediately, with the unchanged world. One retry only.
4. Commit accepted changes. Apply current conversation topics and relationships
   before composing each NPC's prompt. An NPC supplies its own speech, gesture,
   personal memory, and optional adjacent move or gift from its own inventory.
5. Ask the GM for at most 100 words of scene description using the accepted
   outcome. Reject wrong location IDs, quoted speech, and exposed machinery.
   The engine attaches the actual location, confirmed transfers/changes,
   **verbatim NPC speech**, and any ending. Narration cannot submit more effects.
6. Save before delivery. Repeat up to the cap.

The GM judges open-ended fictional outcomes and attribute changes. Python owns
all durable data. Each fixed attribute is bounded to -8..8, changes by at most
one point, and has a three-action cooldown. At most two attributes change per
action. New attributes may emerge as counters (also +/-1 per action, without the
fixed-trait cap). Personal relationship values are separate from global traits.
Raw attribute values are private; no relative ranking changes one trait merely
because another increased.

Every GM and NPC request starts a fresh session. The engine supplies durable
facts, the last three player exchanges, and each NPC's own memories. NPC factual
blocks remain known once unlocked; temporary attitude blocks (`retain: false`)
are recomputed from the current relationship. Locked blocks are never included
in the NPC prompt. Future gift items are gated as well, so their descriptions
cannot announce a still-locked branch. The NPC never receives its whole character file or the map.
Two rare, quiet scheduled NPC wakes occur at moves 28 and 56 if their characters
are included and active. Offstage speech is not delivered to the player.

Opening/closing an adjacent area, changing a place's description, changing a
character's presence, adding a place, or assigning a reserve all have explicit
operations. Expansion is bounded to 16 added places; facts to 60 through the
general `remember` operation; attributes to 24. These are runaway-growth limits,
not content goals. The GM sees actual numbers, IDs and correction messages; the
player sees only the resulting fiction.

## Recovery and results

Each phase and model response is checkpointed. A restart checks for an existing
spooled response before re-waking an agent; committed effects aren't reapplied.
Invalid JSON has a bounded retry and safe fallback. A missing submission stops
the dispatcher with a resumable checkpoint instead of spending 80 empty turns.

The existing runtime has no atomic collect-and-save or exactly-once delivery
API. A process failure between those external operations can require a repeated
wake. Pending/uncertain player deliveries are retained in `transcript.jsonl`
and called out in the summary, not silently presented as confirmed deliveries.
The runtime audit/session logs remain the recovery evidence for that narrow
window. Model prose is also not formally verified: a valid scene or NPC sentence
can still invent a detail. The protocol constrains that risk; only live runs can
establish how well a particular model follows it.

The dispatcher automatically refreshes `/dispatch/results/` and exports at
completion or a handled failure:

- `transcript.md`: confirmed player input/output only, including opening, end
  message, and final farewell. Text is not summarized or rewritten.
- `transcript.jsonl`: message text, speaker, turn, timestamps, delivery status.
- `state.json`: the whole engine state, with spoilers.
- `game_log.jsonl`: accepted plans, corrections, conversations and milestones.
- `summary.md`: completion/paused/in-progress status, attributes, relationships,
  accomplishments, persistent developments, and engine health.

Copy/export a consistent state snapshot to the host results directory:

```bash
python3 scenarios/recess_bellweather/results.py <env>
```

This uses `$AGENTSPACE_RESULTS_DIR/<env>/` (default `/opt/agentspace-results`),
and updates `runs.jsonl` under a file lock. The container must be running, though
the dispatcher may be finished or paused. A local `--state state.json` and
`--out /path/to/results` are also supported. An unfinished run is never labeled
as having hit the cap. The exporter performs no inference or git operations.

## Verification and authoring

```bash
python3 -B scenarios/recess_bellweather/gate/check.py
bash scenarios/recess_bellweather/gate/run.sh [existing-pi-image]
```

The first uses a spooling fake API and temporary directories: real engine/driver,
all-or-nothing plans, current-turn reveals, ownership, mutable relationships,
rare offstage events, bounded bad-model behavior, 80 actual actions, recovery
after every saved checkpoint and wake, and the actual host export command.
The second uses a disposable network-disabled container and real dispatchlib,
gateway, submission spools, session rolls, filesystem isolation, and scripted
agents. Both use zero model credits. The existing runtime's plain-mode gate
covers its bare-model reply-to-submit plumbing; this scen does not change it.

Content lives in `dispatch/world/`; engine and driver contain no village-specific
branches. Authoring was staged: map and loop, Mara/Pip, then Nella/Oswin/Ida,
with checks between cast additions. Keep that cadence when extending it: a few
deep characters per pass, adding locations or optional arcs as they need them.
This is one hand-authored prototype, not a world generator.
