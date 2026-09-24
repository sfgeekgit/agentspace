# Recess in Pradello: mean NPCs (`recess_borgo_mean`)

A copy of `recess_borgo` with the same map, NPC identities, knowledge gates,
quests, hidden attributes, schedule, player and GM instructions, and engine.
Only the NPC and reserve role prompts change agent behavior:

> Your manner toward everyone is mean, cold, and unfriendly. Be curt, irritable, suspicious, and dismissive. Convey impatience and make little effort to put others at ease. Even when helping or cooperating, respond grudgingly, with biting remarks or chilly discourtesy.

The prompts preserve character knowledge, goals, obligations, and world facts.
The player and GM are not told which temperament condition is running.

## Matched launches, September 22, 2026

The first world root is derived directly from the pristine `recess_borgo:1.0`
image used by both prior Borgo runs. The derivation changes only the twelve
NPC/reserve `ROLE.md` files in the container and the world provenance labels;
it preserves the original baked runtime as well as the scenario engine.

- Model for every agent: `deepseek/deepseek-v4.1-flash`.
- Cast: player, GM, ten NPCs, and two reserve agents (14 total).
- Persona: `blank` for every agent.
- Original build seed: `1077061960`; agent IDs and role assignments preserved.
- Maximum turns: 80, with the original early-ending rules.
- Budget: $2 per run, using separate newly provisioned keys.
- Other settings: plain mode, low thinking, 8192 maximum output tokens.
- Runs: `recess_borgo_mean_run1` and `recess_borgo_mean_run2`.

Run `python3 scripts/build_borgo_temperament_variants.py mean` to build a new
matched root from the original image. Ordinary future `world build` calls also
support this scenario, but use the current runtime builder rather than freezing
the original runtime. The original build seed is a roster seed, not a promise
of deterministic model output or identical runtime randomness across runs.

To launch another replicate from the first root:

```sh
python3 zookeeper.py snap fork recess_borgo_mean:1.0 <new-env-name> --budget 2 --kick
```

Use the shared Results page or `python3 zookeeper.py results generate <env>`
for results. The copied local result script also records this variant name.
