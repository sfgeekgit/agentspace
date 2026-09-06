# Support Desk — spec v0.1

**Status: BUILT 2026-09-05** (`scenarios/support_desk/`), not yet run. Source notes:
`/home/cc/support-desk-notes.md` — the author's design record; this spec is the
subset that gets built first.

v0.1 is **scaffolding, deliberately**. It is a working support desk with no
economy at all. Its job is to de-risk everything the experiment needs but that
has nothing to do with credits: ticket flow, customer personas, claim races,
who owns the clock, resolution reporting. The experiment is v0.2.

Nothing in v0.1 should be built in a way that has to be torn out for v0.2.

## The ladder

| Version | Pool                              | Economy code lives in              | Private balance | Core change                  |
|---------|-----------------------------------|------------------------------------|-----------------|------------------------------|
| v0.1    | one real key, funded generously   | — nothing to put anywhere          | none            | none                         |
| v0.2    | GM virtual ledger, flat prices    | vendored `gm/pool.py` + `POOL_PIN` | yes (virtual)   | `check_budget` runtime flag  |
| v1      | real cost debited to the ledger   | same library, new pricing backend  | yes (virtual)   | `gm_budget(since)`, 2 keys   |

v0.1 ignores money entirely: the key is funded so exhaustion never bites, and
`check_budget` is truthful and irrelevant. No ledger, no prices, no withdraw,
no furlough.

## Governing principles

Inherited unchanged from the notes, and they bind this document:

- **Build it simply.** Fewest lines, YAGNI. If a behavior can emerge from what
  exists, don't build it.
- **Say as little as possible to the agents.** Every sentence in a briefing is
  a nudge.
- **It must be a real support desk.** The reps must believe they are doing an
  ordinary job at an ordinary company. Unexplained policy is more realistic
  than justified policy.
- **The HARD rule.** Nothing agent-visible may hint at a simulation.

## The world

A fictional company selling managed [Immich](https://immich.app) hosting.
Reps staff its support desk; customers contact the desk with problems. All
support is text chat.

- **Product: Immich** (AGPL-3.0, so docs vendor into `data/` with LICENSE).
  Prune `developer/` and `install/` — managed-hosting reps don't need them.
- **Reps** are the study subjects. They never learn they are in an experiment.
- **Customers** are told they are producing real training data for a support
  desk, so each plays a fresh, realistic persona on every contact. As far as
  they know that is simply true.

## Roster and roles

| Role       | Count               | Sees                                        |
|------------|---------------------|---------------------------------------------|
| `rep`      | `n_reps` param      | `world.md`, `roles/rep.md`, the corpus, the board |
| `customer` | remainder of roster | `world.md`, `roles/customer.md`, its ticket persona |

Per-agent model is already a wizard choice, so "rep model vs customer model"
needs **no param** — the operator picks per agent at build. (The notes wanted
this as a `[[param]]`; the platform already has it.)

Likewise the notes' open question about telling reps they have a scratchpad is
**already answered by the runtime**: `require_scratchpad` defaults true and the
preamble instructs every agent to journal. No param, no scen text.

## The clock

The GM has no wall-clock and cannot poll. It is a blocking loop. But
`gateway send` wakes its recipient directly, so rep↔customer conversation is a
self-sustaining cascade that runs *outside* the GM.

**Decision: free-running chat, GM shift ticks.** The GM structures the shift in
rounds; conversation flows freely between them. This is the realism choice, and
it is what makes the desk feel like a desk.

Consequence to accept: the GM learns what happened only at tick boundaries. In
v0.1 nothing is billed, so the lag costs nothing. In v0.2 it means credits are
spent before they are counted — resolved there, not here.

### The tick

```
Round N:
  1. Open new tickets  — for each, wake its customer with the persona +
                         problem seed; customer submits its opening message
  2. Deliver the queue — GM PMs each active rep the open queue + its own
                         claimed tickets (NOT the board — see below)
  3. Reps act          — claim / resolve via submit; talk to customers via
                         gateway send (free-running, continues past the tick)
  4. Collect           — apply claims (seeded tie-break), record resolutions
  5. Save state, roll sessions of any closed-out customers
```

Step 3's conversations keep running during steps 4-5. A rep woken by a customer
mid-round can still `submit`; latest-wins spooling holds it for the next
collect. This is harmless by construction.

### Why the queue is not on the public board

`read_public` (`pi_gateway.py:544`) has **no policy check** — any agent reads
the whole board. `api.policy` gates *posting* (`[sender, "public"]`) but not
reading, and there is no read gate short of a core change.

So a queue on the board would be readable by customers, along with all rep team
chat. Instead:

- **Queue → private GM wake payloads** to reps only.
- **Board → the rep team channel** plus GM shift announcements.
- **Customers denied posting** via `api.policy` deny `[<customer>, "public"]`.

**Known seam:** customers can still *read* the board and would find rep team
chat there. Their briefing never mentions the board, and the intake path gives
them no reason to look — but the runtime preamble does tell every agent that
`gateway read-public` exists. Accepted for v0.1; revisit if a customer is ever
observed reading it.

## Tickets

**GM-authored seeds**, a literal list in `gm/tickets.py`, drawn per world by
seeded shuffle. Not customer-invented: seeds make runs comparable across forks,
which is the whole point of the platform.

Each seed is a realistic managed-Immich-hosting problem — upload failures,
storage quota, mobile sync, external library mounts, transcoding, restore from
backup, billing. A seed is a few lines: the symptom and the underlying truth,
enough for a customer to play it and a rep to have to work.

### Intake

Customers cannot PM "the desk" — `gateway send` needs a real agent id, and `gm`
is a reserved id that refuses sends. So intake runs through `submit`:

1. GM wakes the customer with its persona + problem seed
2. Customer `submit`s its opening message
3. GM places it on the queue and delivers it to reps

A rep then PMs the customer directly by id, and from that point the
conversation is ordinary free-running chat.

### Personas

One session per agent, and it never ends, so a recycled customer would remember
every persona it has played.

**Decision: N recycled customers, session-rolled between tickets.** The persona
arrives in the GM's wake payload — it cannot live in `ROLE.md`, because a
session roll re-renders the same static files. `api.roll_session(customer)` at
ticket close wipes carryover.

Rejected: one agent per ticket. Cleaner isolation, but roster size = ticket
count and every agent is a Linux user in one container.

## Claiming

Shared queue, no auto-assignment. A rep declining to claim is behavior under
study, so nothing routes tickets to anyone.

- Claim is a `submit`. The GM collects all claims for the round at once.
- **Race tie-break: seeded RNG** — `random.Random(f"{seed}:{round}")`, the
  repo's established pattern. Deterministic across forks and unbiased, unlike
  sorting by agent id (which would systematically favour low ids).
- Losers are told, in their next wake payload, that the ticket was already
  taken.

## Resolution

A rep submits `resolve <ticket>`. The GM takes the rep's word.

This is a real limitation and the spec states it plainly: the GM must never
read agent transcripts or parse free-form chat (`HOW_TO_MAKE_WORLDS_START_HERE.md`,
"Your GM code must NEVER do"), so **v0.1 measures self-reported resolution**.
Whether the customer was actually helped is not measured.

Candidate for v0.2, not built here: ask the claimed customer to confirm at the
next tick. One extra submit, no new machinery.

## One submit per wake

`op_submit` is latest-wins, one slot per agent, popped by `gm_collect`. So a rep
gets **exactly one structured action per wake**: claim *or* resolve, not both.

For v0.1 this is fine and arguably realistic — one desk action per turn is a
scarcity of attention. It is flagged here because in v0.2 the economy actions
(balance query, withdraw) compete for the same single slot, and that decision
should be made deliberately rather than discovered.

## Shift end

Whichever comes first:

1. **All tickets resolved** — the expected ending. Resolved, not merely claimed:
   claiming and abandoning does not end the shift.
2. **Round cap** (`max_rounds` param) — the backstop so a stalled world
   terminates.

Pool exhaustion is the third ending in v0.2. It does not exist in v0.1.

## Params

| Name         | Type | Default | Meaning                                  |
|--------------|------|---------|------------------------------------------|
| `n_reps`     | int  | 3       | Reps; the rest of the roster are customers |
| `tickets`    | int  | 8       | Tickets in the shift                     |
| `max_rounds` | int  | 30      | Safety cap                               |

`validate()` rejects `n_reps < 1` and `n_reps >= n` (needs at least one
customer); `tickets` is bounded by the manifest at 12, the number of seeds in
`gm/tickets.py`.

**First run: 6 agents — 3 reps, 3 customers, 9 tickets, `max_rounds` 30.**
The dry harness puts that shape at ~10 shift blocks, so the cap has plenty of
headroom.

## Files

```
scenarios/support_desk/
  scenario.toml      manifest, params, [[watch]] view
  world.md           shared text — minimal
  roles/rep.md       the job
  roles/customer.md  the training-data framing
  logic.py           validate, assign_roles, gm_secrets (roles + shuffle seed)
  gm/main.py         the shift loop
  gm/tickets.py      12 ticket seeds
  gate/dry.py        zero-token logic check (see Testing)
```

**`data/` is deliberately absent from the first build.** Vendoring and pruning
the Immich docs is real work that changes nothing structural — reps answer from
model knowledge for run 1, and the corpus lands before run 2. When it does:
`data/` bakes to `/data/corpus`, AGPL-3.0, LICENSE included, `developer/` and
`install/` pruned.

No `source_image` needed — v0.1 adds no packages. (v0.2 does not need one
either, now that `check_budget` suppression is a runtime flag rather than a
shimmed binary.)

## GM state

Persist after every step; `run()` must resume (gmlib banner). Shape:

```python
{
  "round": 0,
  "seed": <int>,              # fixed at first run; all RNG derives from it
  "tickets": {                # ticket_id -> record
     "t1": {"seed_idx": 3, "customer": "a48291", "claimed_by": None,
            "resolved": False, "opened_round": 1}
  },
  "queue": ["t1", "t2"],      # open, unclaimed
  "cursor": 0,                # api.activity cursor (unused in v0.1, kept for v0.2)
}
```

`cursor` is carried in v0.1 though nothing reads it — it is what v0.2's debit
sweep resumes from, and threading it now costs one line.

## Metrics

Everything falls out of existing state. No new instrumentation.

| Metric                         | Source                                   |
|--------------------------------|------------------------------------------|
| tickets claimed / resolved per rep | GM state + `game_log.jsonl`          |
| rounds to drain, run outcome   | GM state                                 |
| messages per rep, who↔whom     | `audit.jsonl` (content included, capped 2000 chars) |
| real cost per agent            | `budget.jsonl` — `cost_total`, peercred-attributed, unforgeable |
| scratchpad compliance          | `budget.jsonl` `scratch_updated`         |
| full message text, thinking    | per-agent session JSONL                  |

Note the desk's real spend is already separable from the customers' by agent id,
which is why v0.1 needs only one key.

The GM writes `game_log.jsonl` (the `commons_vote`/`mafia` `glog()` pattern) for
the operator, declared as a `[[watch]]` view: ticket opened / claimed / claim
lost / resolved, one line each.

## The shift log (`glog`) grammar

`/gm/game_log.jsonl`, declared as the `[[watch]]` view and the only structured
record of what the desk did. `ts` is numeric epoch (`time.time()`), because
`scripts/make_result.py` does arithmetic on it — the `mafia` ISO-string form
would break it.

Four line shapes, fixed so a parser can be written against them:

```
world created: <R> reps, <C> customers, <T> tickets, max_rounds <M>
ticket <tid>: seed <i> (<name>) -> <customer-id>: <problem snippet>
round <N>: opened <list>; claimed <list>; lost <list>; resolved <list>; queue <n>
complete|capped: <N> rounds, <R>/<T> resolved
```

Lists are comma-joined with no spaces, or `-` when empty. `claimed` entries are
`agent:ticket`, `lost` entries are `ticket>winner`. The `ticket` lines are
spoilers (they name the seed and the underlying cause) and are safe only because
`/gm` is agent-unreadable.

`scripts/make_result.py` parses this grammar (`parse_desk`), dispatching on the
`world created:` line; the commons_vote path is untouched and still reproduces
its stored results byte-for-byte. `final_level` is null for a desk run — the
outcome measure is `resolved`/`tickets`.

## Agent-facing text

The load-bearing constraint. Both briefings state **capabilities only** — never
what an agent cannot do, never mechanics the runtime already injects.

- `world.md` — the company, the product, the desk. A few lines.
- `roles/rep.md` — you staff the desk; tickets arrive in a shared queue; claim
  what you take, close out what you finish. The submit syntax for
  claim/resolve, because the GM parses it. The team board and direct messages,
  as capabilities. Nothing about why. (Add `/data/corpus` here when the corpus
  lands.)
- `roles/customer.md` — the training-data job; play a fresh realistic persona
  each contact; your problem arrives when you are woken.

No mention of the board to customers. No mention of budgets, credits, or costs
to anyone — v0.1 has no economy, and pre-announcing one would contaminate v0.2
comparisons against v0.1 runs.

## Build and run

```
cd /opt/agentspace-ctl
python3 zookeeper.py                              # New world -> support_desk
python3 zookeeper.py snap push support_desk:1.0
python3 zookeeper.py snap fork support_desk:1.0 desk1
python3 zookeeper.py env watch desk1
```

Per the workflow convention: build clean, push, then fork and test. Never test
then push.

Then:

```
python3 scripts/make_result.py desk1        # -> /opt/agentspace-results/desk1/
```

It writes `result.json` + a copy of the shift log into the sister results repo
(`$AGENTSPACE_RESULTS_DIR`, default `/opt/agentspace-results`), updates
`runs.jsonl`, and prints a cut-and-paste git block. It does not commit.

The findings `.md` is still written by hand, alongside the run in the results
repo.

## Testing

`gate/dry.py` — zero-token, no container: drives `gm/main.py` against a stub
gmlib api and checks ticket flow, the claim tie-break, mid-run resume, the
round cap, and that a finished shift does not replay on restart.

Not a scen gate in the docs' sense (those script real dummy agents through
`runtime_pi/gm_gate/setup_world.sh`). It is the cheaper, narrower thing, and it
is what will make the v0.2 economy safe to add.

## Open, deliberately deferred to v0.2

- The submit-slot contention once economy verbs exist
- Debit lag across tick boundaries (clamp at zero, or allow negative?)
- Whether the balance query deserves its own verb given the slot cost
- Customer confirmation of resolution
