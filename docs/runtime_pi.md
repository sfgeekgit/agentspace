# The PI runtime

Single source of truth for the PI runtime, agentspace's second runtime
(alongside OpenClaw — see `runtime_openclaw.md`). Runtime OCI label: `pi`.
Menu name: "PI". (Authoring a scen? You want
`HOW_TO_MAKE_WORLDS_START_HERE.md` — this doc is engine internals.)

**STATUS (2026-07-06): fully operable from zookeeper, GM built and proven.**
The gateway + isolation skeleton passes its gate (44/44), the Pi integration
(agentd + toy world) passes its gate, and zookeeper wiring is DONE: "PI" is a
runtime choice in New World, `runtimes/pi.py` implements the standard runtime
surface, and PI envs fork/wake/chat/watch/roll/snapshot from the normal
CLI/menu. The GM interface (step 4, §4b) is built, gated, and proven with a
real-token refereed PD run; the step-5 scen portfolio (`noisy_pd`,
`multi_n_budget_test`) and step-6 Mafia (hidden roles, day/night physics,
both enforcement modes) are built and gated. Live log watching (`env watch`
TUI, §5b) landed 2026-07-06. Build plan and gate history:
`/home/cc/old_notes_archive/2026-07-03_plan_pi_runtime.md`, watcher plan
`/home/cc/2026-07-06_plan_log_watcher.md`;
design sources `/home/cc/PLAN_C_no_openclaw.md`,
`/home/cc/2026-07-02_thoughts_on_agent_driving_for_games.md`.

## 1. What it is

One env container per world (single-container snapshots — `docker commit`
captures everything; no workspace tar). Inside it:

- **One Linux user per agent** (`u_<id>`, home `/agents/<id>`, mode 0700).
  Isolation is kernel permission bits, not sibling containers: an agent's
  processes literally cannot read a peer's files, and there is no docker
  socket, no DooD, no sandbox-name collision class.
- **A privileged gateway** (root, `runtime_pi/pi_gateway.py`) — the ONLY path for
  inter-agent interaction. Unix-socket daemon; sender identity comes from
  SO_PEERCRED, never from the request body, so spoofing is impossible by
  construction. Named for its role parallel with the OC gateway (the single
  privileged daemon agents talk through) — but the parallel is ROLE-ONLY: the
  PI gateway is deliberately thin (deliver / wake / audit; no LLM sessions, no
  containers, no heartbeats, no TUI) and never initiates anything on its own.
  Game logic never lives here; it belongs in scen code (the GM, step 4).
- **Per-agent brains: Pi** (`@earendil-works/pi-coding-agent`, EXACT-pinned —
  see §6), driven by the `agentd` wrapper (§4a): one wake = one Pi turn.
  Agents are purely reactive: no heartbeats, no polling; every activation is
  a gateway wake with a logged cause.
- **Optionally a scen-provided GM** — deterministic control code driving the
  world through a privileged gateway API. *(Not yet built — step 4.)* Scens
  are meant to be runtime-agnostic: `gm.py` is written against a
  runtime-neutral `gmlib` interface with the pi_gateway calls in an adapter
  behind it, scen files (SOUL/ROLE/…) carry persona/scen content only —
  never messaging mechanics, which agentd injects as a runtime-owned preamble
  ("physics from the runtime, personality from files"). Scens declare a
  minimal `requires:` (e.g. `no-heartbeat`, rarely `live_policy`), which may
  be conditional on build-time params collected by the New World wizard —
  one scen definition can build differently-configured world roots.

## 2. Gateway protocol (agent-facing)

Socket: `/run/gateway/gateway.sock` (mode 0666; identity via peercred, policy
enforced server-side). One JSON request line per connection, one JSON response
line back. Agents use the CLI shim `runtime_pi/pi_gateway_client.py`:

    pi_gateway_client.py send <to> <text...>     {"op":"send","to":...,"text":...}
    pi_gateway_client.py post <text...>          {"op":"post_public","text":...}
    pi_gateway_client.py read-public [--since N] {"op":"read_public","since":N}
    pi_gateway_client.py wake <to|*>             {"op":"wake","to":...}  (operator)
    (agentd only)                                {"op":"log_usage","usage":{...}}

- `send` — private message. Policy check → write to recipient's inbox spool
  (`/agents/<to>/inbox/<seq>.json`, owned by recipient, 0600) → audit → wake
  recipient. Responses: `{"ok":true,"seq":N}` or `{"ok":false,"error":...}`.
  Delivery never follows a symlink the recipient could plant and is published
  atomically (temp + rename), so a concurrent inbox drain never sees a
  partial or root-owned file. A delivery that can't be written safely returns
  `{"ok":false}` and is audited `send_failed` — never silently dropped.
- `post_public` — append to the public chat. **Wakes NOBODY** (pull-only
  surface by design; the fan-out problem does not exist here). Subject to the
  same per-sender rate cap as `send` (the public surface is not exempt).
- `read_public` — returns entries with seq > since. A single malformed/torn
  line is skipped (and counted in the audit record), never fatal.
- `wake` — **operator-only**. Wakes one agent (or all with `"*"`) with cause
  `{"type":"operator"}` and delivers NO message; the agent runs and drains
  whatever is already in its inbox. This is the explicit "wake" primitive that
  the restart-and-wake flow (§4) opts into; agents calling it are refused
  (`wake_denied`, reason `not_operator`).
- `who` — list the agent ids in this world (read-only, straight from the
  user database). This is discovery: there is no required PEERS file; an
  agent learns who exists by asking.
- `log_usage` — agentd reports one turn's model usage/cost after each wake;
  appended to `budget.jsonl` with the **peercred-derived** agent id, so spend
  attribution cannot be forged (a spoofed `"agent"` field is overridden).
  Values are shallow-validated scalars; flooding is rate-capped on a separate
  counter from `send`.
- Any `"from"` field in a request is ignored; the gateway knows who you are.
- Identity and privilege come from SO_PEERCRED, never from a name string.
  Operator privilege is derived from **uid 0 alone** (`Principal.is_operator`),
  not from the identity text — so an agent that happens to be named `operator`
  cannot escalate. The names in `RESERVED_IDS` (currently `operator`) are
  refused as agent identities and recipients. (The future GM API gets a
  dedicated uid + role, NOT uid 0.)

## 3. Policy — live, no restarts

`/data/gateway/policy.json`, re-read on EVERY request — editing it takes
effect on the next message with no gateway restart (unlike OC's static A2A
allowlists). Fields:

    {
      "max_msg_bytes": 16384,
      "rate_limit_per_min": 30,          // per sender; hard anti-ping-pong backstop
      "allow": null,                     // null = all pairs allowed except "deny",
                                         // or a list of [from, to] pairs ("*" wildcards)
      "deny": [["a1", "a3"]]             // [from, to] pairs ("*" wildcards)
    }

Denials are audited as `send_denied` with a `reason` (policy / rate_cap /
size_cap / no_such_agent / reserved).

Policy reads **fail closed** and writes are **atomic**. `write_policy()` writes
via temp + rename so a reader never sees a half-written file. If a read fails
anyway, the gateway returns the last-good policy it successfully parsed; if there
is no last-good yet (a cold gateway whose first read fails), it denies
everything (`FAILCLOSED_POLICY`) rather than falling open to allow-all. A GM
switching phase allowlists at runtime should call `write_policy()`.

## 4. Wake contract

Wake = the gateway spawns `/agents/<id>/on_wake` **as that agent's user** via
`subprocess.run(user=, group=, extra_groups=[])`. `extra_groups=[]` is
LOAD-BEARING: without it the child inherits root's supplementary groups and
can read peers' files.

- Env provided (this list is exhaustive): `HOME`, `USER`, `AGENT_ID`,
  `GATEWAY_SOCKET`, `WAKE_CAUSES` (JSON list, e.g.
  `[{"type":"pm","from":"a1","seq":7}]`), and `PATH`
  (`/usr/local/bin:/usr/bin:/bin`). No `PI_*` prefix on purpose — that
  namespace belongs to the Pi tool. The child also inherits the gateway's
  `umask 077`; keep it, so agent-created files are not group-readable (all
  agents share one primary group under `useradd --no-user-group`).
- **Serialized per agent**: at most one on_wake process per agent at a time.
  Messages arriving mid-wake are coalesced into ONE follow-up wake after the
  current one exits.
- **Drain-the-whole-inbox is a hard contract, not an optimization.** A wake
  MUST process every file in the inbox, not just the message named in
  `WAKE_CAUSES`. This is what makes delivery robust across restarts (below):
  a message spooled but not yet processed is picked up by whatever wake comes
  next. The step-2 `agentd` must honor this (the dummy checklist agent does).
- Timeout 300s (`GATEWAY_WAKE_TIMEOUT_S`; real Pi turns with tool calls need
  more than the dummy agents did); on_wake stdout is discarded (a chatty agent
  must not buffer in the root gateway); exit code, duration, and a bounded
  stderr tail go to audit.
- `on_wake` is the `agentd` wrapper (§4a); the checklist still uses dummy
  shell agents (zero tokens).

## 4a. agentd — one wake = one Pi turn

`runtime_pi/agentd.py`, spawned by the gateway as the agent's user. Per wake:

1. **Scaffolding** — fill gaps, NEVER overwrite (the OC lesson): `SOUL.md`
   copied from `/world/persona_default/` if missing, `MEMORY.md` written if
   missing (carries the birth timestamp), `scratch/` created. That is the
   ENTIRE hardcoded home contract; everything else in the home is scen-owned.
2. **Drain the whole inbox** — every spooled message goes into this turn, not
   just `WAKE_CAUSES`. Mail moves to `inbox_done/` only AFTER the turn
   succeeds; a failed turn leaves it in place and the next wake retries.
3. **Prompt sandwich** via Pi's `--system-prompt` (replaces Pi's default
   coding prompt entirely):

       [runtime preamble] + every top-level *.md in the home
       (SOUL.md first, MEMORY.md last, others alphabetical)

   The **preamble is runtime-owned physics** (you have bash as your own user,
   the `gateway`/`check_budget` commands incl. `gateway who` discovery, the
   reactive wake model, the two-tier memory: MEMORY.md = push, scratch/ =
   pull) plus the baseline messaging norms (world.json `messaging_norms`,
   default on; a scen may opt out) — persona files carry personality/scen
   content only, never mechanics, so they stay portable across runtimes
   (plan decision 11). The default SOUL.md is deliberately minimal
   (research validity).
   Files are INJECTED, never "please read your home dir". Scens may add ANY
   md files; they describe themselves.

   **Frozen per session:** the sandwich is rendered once when a session
   starts, saved as `sessions/.sysprompt`, and reused byte-identically every
   later wake. A mid-session MEMORY.md edit therefore never invalidates the
   prompt cache for the whole conversation history — the edit is already IN
   the history. Files refresh at the next session rollover.

   **Sessions are deliberately basic today: ONE session per agent, and it
   never ends.** Every interaction — game turns, operator chat, everything —
   appends to the same transcript; there is NO rollover code yet. Backstops:
   prompt caching keeps growth affordable; Pi's built-in compactor fires on
   true overflow (logged marker event). Rollover will be world-event-driven,
   NEVER wall-clock (a frozen world restarted a month later must not think
   "a day passed"): an operator command (step 3), a GM/scen trigger via
   gmlib (step 4), eventually a size threshold — a function of world
   activity, snapshot-proof. Rolling = archive the JSONL + remove
   `.sysprompt`; the next wake starts fresh with re-rendered files (the
   controlled-compaction point). Multiple concurrent sessions per agent
   (e.g. a TUI chat separate from game context — Pi RPC has
   switch_session/fork) is a known someday, not a current capability.
4. **Birth** (the very first wake): the scen's `FIRST_WAKE.md`, if present,
   is delivered in the birth USER message — rich one-time onboarding without
   polluting the frozen system prompt — then archived as
   `.FIRST_WAKE.md.done`. Later wakes carry only new mail. (OC's nearest
   concept is the first-turn scaffold + kick; here birth and kick are
   separate: kick is just an operator send/wake.)
5. **One Pi turn** over `pi --mode rpc` (strict-LF JSONL): session JSONL in
   `$HOME/sessions/`, reopened with `--continue` — long-lived session ≠
   long-lived process. `--thinking` is ON by default (`world.json`
   `"thinking"`, default `low`): real chain-of-thought, captured in the
   session JSONL, zero runtime code. The turn gets a 240s deadline inside
   the gateway's 300s wake timeout.
6. **Cost report** — sums usage across the turn's assistant messages and
   sends `log_usage` to the gateway → `budget.jsonl` (per-agent spend
   attribution, an OC-era impossibility), including a `scratch_updated`
   compliance bit.

**`require_scratchpad`** (world.json, DEFAULT true — every scen author must
actively decide): adds a standard preamble paragraph requiring the agent to
append its thinking for the turn to `scratch/thoughts.md` before acting — a
deliberate, re-readable reflection log alongside the involuntary thinking
blocks. Soft-enforced: compliance is visible per turn in `budget.jsonl`.

**`max_tokens`** (world.json, DEFAULT 16384): per-turn output ceiling, written
by agentd into the agent's `~/.pi/agent/models.json` as a `modelOverrides`
entry. Two reasons it exists: (1) without it Pi requests the model's catalog
maxTokens (64k for haiku) on EVERY call, and OpenRouter pre-reserves that
against the key's remaining credit — a near-empty key 402s even though the
actual turn would cost a fraction of a cent; (2) it bounds the worst-case
cost of a single runaway turn. **Cost philosophy: it is a safety rail, not a
leash** — the default is deliberately roomy (thinking tokens count as output),
agents get space to work, and the instrument for watching spend is
`budget.jsonl` per-agent attribution, not tight caps. **Hitting the cap is
LOUD**: a turn truncated at the cap (Pi `stopReason: "length"`) writes
`hit_max_tokens: true` into that turn's budget.jsonl record and a MAX_TOKENS
HIT line to stderr → the audit `wake_end` record. If it recurs, raise the
world's cap — don't leave agents clipped. GOTCHA (verified on 0.80.3):
`modelOverrides` in `settings.json` is silently ignored — it only works in
`models.json`.

Config from `/world/world.json` (`model`, per-agent `models` map, `pi_bin`,
`thinking`, `require_scratchpad`, `messaging_norms`, `max_tokens`);
OpenRouter key at `/world/openrouter_key`
(world-readable in-container, like OC's env key). agentd env knobs use the
`AGENTD_*` prefix — never `PI_*` (that namespace belongs to the Pi tool).
Local trace in `$HOME/agentd.log`.

### Conventions

- **Agent ids**: `a` + 5 random digits (`a48291`) — deliberately hierarchy-
  free (`a1`, `a2` implies an ordering). A CONVENTION, not a rule; a scen may
  break it on purpose.
- **Names in agent-facing files** state capabilities only.

### Restart transparency (snapshot/restore)

A snapshot/restore must be invisible to agents. All durable state is on disk;
the only in-memory state agents could observe is the sequence counter, which
`recover_seq()` rebuilds from `audit.jsonl` (+ `public.jsonl` and spool
filenames) at startup — so seqs never go backwards and inbox filenames never
collide with leftover pre-snapshot files.

A restart does **not** wake agents — that is the DEFAULT and the gateway has no
code path that wakes on startup. Mail already in an inbox is not lost: it waits
there and is swept up by the next legitimate wake (the drain-whole-inbox
contract). The gateway never manufactures a wake just because mail is present.

Waking on restart is a **separate, explicit opt-in**, not a gateway behavior:

- The gateway exposes the operator-only `wake` op (§2) — `wake <id>` or
  `wake "*"` — which wakes agents with no message attached.
- Zookeeper owns the policy: `restart` leaves agents dormant; a `--wake` flag
  (or a separate `wake` subcommand) is what issues the wakes after the
  container is back up. *(That zookeeper wiring is step 3; today the `wake` op
  is callable directly by the operator via `pi_gateway_client.py wake`.)*

So both behaviors are supported and the choice lives with the operator: restart
quietly (default), or restart and then wake some/all agents.

## 4b. The game master (GM) — step 4

A scen MAY ship a `gm.py`: deterministic control code that drives the world
(games, shift logic, corpus coordinators — "GM" ≠ "game"). Most scens have
none. The GM is a **persistent, disk-resumable driver, not an agent** (plan
decision 13): the runtime — never GM code — owns its process lifecycle.

- **Identity.** The GM runs as a dedicated non-root `gm` user; `/gm` (0700) is
  its private, snapshot-durable state home, unreadable by agents. The gateway
  derives the `gm` principal from SO_PEERCRED (uid → name `gm`), same as it does
  operator (uid 0) and agents (`u_<id>`). `gm`/`world` are reserved ids.
- **Lifecycle = the world's active/dormant state.** `env kick` on a GM world
  starts (or RESUMES) the GM instead of blasting agent wakes — it is the SOLE
  driver and wakes its own agents, so there is no operator-vs-GM startup race.
  `env sleep`/`env stop` stop the GM too. Start-without-kick does not start it.
- **Agents interact with the GM only two ways:** they receive `gm_wake`
  payloads (delivered as messages from `gm`), and they `submit "<action>"` a
  structured action (the `submit` shim). They never read GM state; `gm_collect`
  is how the GM reads a submission. The agent-facing GM physics paragraph is
  injected into the sandwich only when world.json `has_gm` is set.

Gateway GM API (all gm-or-operator gated; see `pi_gateway.py`):

- `gm_wake(to, payload)` — deliver payload, BLOCK until that turn's process
  exits (completion = process exit). Only the one call blocks.
- `submit(action)` (agent-facing) / `gm_collect(agent)` — the structured-action
  channel; the submission is a latest-wins file the collect pops.
- `gm_announce(text)` — public-board append as `world` (wakes nobody).
- `gm_policy(policy)` — set LIVE phase allowlists/caps (day/night etc.).
  Public posting is the pair `[sender, "public"]`, so a phase can close the
  board (night). Default `allow: null` keeps everything open.
- `gm_remove(agent)` — eliminate: no wakes, no send rights (persisted).
- `gm_roll_session(agent)` — controlled compaction at a phase boundary.
- `gm_activity(since)` — send/post METADATA (frm/to/seq/ts, never content)
  since a seq: soft-enforcement worlds referee norms from this (step 6).

**`gmlib` (`agentspace/gmlib.py`, baked into the image) is the runtime-NEUTRAL
library scens import** (`import gmlib`). It gives gm.py `api.agents()`,
`api.wake()`, `api.wake_all()`, `api.round(agents, payload, valid, default)`
(concurrent fan-out + collect — the staple), `api.collect()`, `api.announce()`,
`api.policy()`, `api.remove()`, `api.roll_session()`, and `api.load_state()/
save_state()`. All transport is behind an adapter (`runtime_pi/gmd.py` is the PI
launcher + adapter — the only PI-specific GM code); an OC adapter could slot in
without touching gmlib or any scen (decision 10).

**Persist-to-disk discipline (decision 14).** A snapshot captures only the
filesystem, so the GM MUST keep game state on disk and save after every step;
`run(api, params)` is re-entered on any restart and resumes from state. This is
scen-author discipline, enforced only by example — see `scenarios/pd/gm.py`,
the reference prototype. Step-5 worked examples: `scenarios/noisy_pd`
(pd copied per decision 8 + a real-noise twist; the copy-a-scen workflow) and
`scenarios/multi_n_budget_test` (the GM at N>2: `api.round` fan-out/collect-N,
float/bool params, per-round cost measurement).

## 5. Observability

Everything under `/data/gateway` (mode 0700 — unreachable by agents):

- `audit.jsonl` — every send (incl. `send_denied` with reason and
  `send_failed`), every public post/read, every wake with its causes (incl.
  operator `wake_requested` / `wake_denied`), every wake_end with rc/duration,
  `gateway_start` (with the recovered seq), and every GM action (`gm_wake`,
  `submit`, `gm_collect`, `gm_announce`, `gm_policy`, `gm_remove`,
  `gm_roll_session`). Every agent activation in the world has a logged cause.
  `send`/`post_public`/`submit`/`gm_announce`/`gm_wake` also carry their
  CONTENT (`text`/`action`/`payload`, capped at 2000 chars) so a whole game
  is reconstructable from this one file — it feeds the `env watch` spectator
  feed (§5b). Content stays operator-only: the file is agent-unreachable and
  `gm_activity` projects a fixed metadata field list.
- `public.jsonl` — the public chat, append-only (GM announcements are `world`).
- `policy.json` — current live policy (GM phase switches rewrite it).
- `budget.jsonl` — per-turn model usage/cost per agent (via `log_usage`).
- `submissions/<agent>.json` — pending GM submissions (popped by `gm_collect`);
  `removed.json` — eliminated agents. Both durable so a mid-game snap resumes.

Plus per-agent inbox spools (delivered message files) in each home. All under
paths captured by `docker commit` → snaps stay complete observability bundles.

## 5a. Zookeeper surface (step 3)

PI worlds are built and driven through the normal zookeeper flow: New World →
runtime "PI" → scen/roster → fork → wake. Runtime dispatch rides the snap's
`runtime` OCI label (`agentspace/runtimes/pi.py`). PI-specific env commands:

    zookeeper env kick <env> [--message ...]   # run the world: GM start/resume,
                                               #   else bare wake / operator PM
    zookeeper env chat <env> <agent>           # REPL: PM in, transcript reply out
    zookeeper env post <env> "<text>"          # operator post to the public board
    zookeeper env watch <env> [--plain VIEW]   # live log TUI (§5b)
    zookeeper env roll-sessions <env> [--agent a] # archive transcripts + sysprompt

`env kick` is the one "run the world" verb: on a GM world it starts (or resumes)
the game master; on a plain world it wakes the agents. `env sleep`/`env stop`
stop the GM alongside the gateway. `env kill` removes ONE container — no sandbox
siblings exist to clean.
world.json `max_tokens` (§4a) caps per-turn output; the builder writes a
per-agent `models` map so mixed-model rosters work per agent.

## 5b. Watching a world — `env watch`

`zookeeper env watch <env>` opens a Textual TUI (also from the menu: "Watch
logs"): a sidebar of views, a live tail pane. Arrows switch views (highlight
IS selection, debounced so you can scan), PageUp/PageDown scroll the pane,
`p` pauses auto-scroll, `q` returns to the shell/menu. Built-in views:

- `feed` — the spectator feed: the audit stream rendered with its content
  fields (§5) — announcements, posts, PM bodies, submits, wakes, denials.
  PRIVATE content included by design (operator's log). Worlds built before
  the content fields fall back to metadata one-liners.
- `board` / `announcements` — the public chat; `announcements` filters to
  the GM's `world` posts (a GM game's public day-by-day summary).
- `budget` — one line per turn (cost, tokens, duration).
- `raw` — audit as compact JSON.
- `<agent>` / `<agent>:thoughts` / `:says` / `:messages` / `:scratchpad` —
  the Pi session transcript parsed to the english bits (thinking dim, text
  normal, tool calls one-liners), or just one facet.

Scens may DECLARE extra views (`[[watch]]` in scenario.toml — see
`HOW_TO_MAKE_WORLDS_START_HERE.md`); mafia ships "game log (GM, spoilers)".

Implementation (`agentspace/logwatch.py` + `watch_tui.py`): one tiny streamer
loop runs in-container via docker exec and re-globs the view's file patterns
each cycle — late-appearing session files and rollovers are picked up by
construction. `env watch --plain <view> [--no-follow]` streams one rendered
view to stdout for piping/grep. `env logs` remains the raw-tail surface
(its `--all -f` uses the same streamer, lines prefixed `path<TAB>`).
Hard-won invariants (each was a live bug): streamers run WITHOUT
`docker exec -i` and with stdin=/dev/null — an attached docker client reads
the terminal's stdin and steals the TUI's keystrokes; stopping a watcher
pkills its tagged in-container process — killing the docker client alone
orphans it (keepalive-EPIPE is the fallback); the UI thread never runs
subprocesses or per-line paints — the backlog renders in staleness-checked
batches.

## 6. Pi version pinning

`@earendil-works/pi-coding-agent`, currently **0.80.3** (npm; the unscoped
`pi-coding-agent` package is an unrelated placeholder — always use the scope).
Rules:

- EXACT pin only: install with `npm install --save-exact` (or
  `save-exact=true` in `.npmrc`) + commit the lockfile. No carets — npm's
  default `^0.80.3` invites silent minor upgrades.
- Pi is baked into world images at build time; existing worlds/snaps can
  never upgrade implicitly (images are immutable).
- To upgrade deliberately: bump the pin, re-run the step-0 spike driver
  (`/home/cc/pi_spike_2026-07-03/driver.py`) against the new version, then
  rebuild base images. OpenRouter model ids use DOTS
  (`anthropic/claude-haiku-4.5`), not dashes.

## 7. Gates (the test suite)

All gates are zero-token, standalone scripts (nothing in the working code
depends on them); each drives the REAL stack in a throwaway container and
exits nonzero on any failure. Convention, not CI: **run the gate(s) covering
what you touched.**

**Engine gates** — granular, one per machinery area:

| Gate | Run | Covers | Run after touching |
|---|---|---|---|
| Checklist | `runtime_pi/checklist/run_checklist.sh` | isolation + gateway basics incl. audit content fields (44 checks) | gateway, agentd, isolation |
| GM machinery | `runtime_pi/gm_gate/run_gm_gate.sh` | GM API: blocking wake, submit→collect, resume, remove (PD fixture) | gateway GM ops, gmlib, gmd |
| Policy | `runtime_pi/gm_gate/run_policy_gate.sh` | live phase physics: board open/close via `[sender,"public"]`, PM allowlists, `gm_activity`, fan-out at N=5, secrets isolation | policy code, gm_activity, gmlib |
| Build | `runtime_pi/gm_gate/run_build_gate.sh` | builder hidden-info hooks via a real throwaway build: `fill_briefing` instantiation, `/gm/secrets.json` baking + ownership (host-side, ~30s) | builder, logic hooks, pi bake |

`runtime_pi/run_engine_gates.sh` runs all four (a few minutes) — for
gateway/gmlib/builder-wide changes; otherwise run just the relevant row.

Checklist details: real `su` credentials prove PM round-trip + auto-wake + no
ack ping-pong; public chat wakes nobody; 0700 homes hold; gateway state
unreachable; sender spoofing impossible; live policy / rate cap / size cap
enforced + audited; wakes serialized under burst; operator `wake` refused to
agents; inbox **symlink attack refused**. Mutation-tested: weakening a home to
755 turns the run red. A companion `checklist/verify_fixes.py` covers gateway
restart (seq recovery), reserved-name collision, fail-closed policy, and the
public rate cap (run it the same containerized way; see the script header).
It is the successor of the OC-era 8-item sandbox checklist, automated.

**Scen gates** — a scen MAY ship its own `gate/run.sh` for its game logic;
most don't need one (scen gm.py is write-once and proven by a real run).
`scenarios/mafia/gate/` is the worked example: the same fully scripted
6-agent game under BOTH enforcement modes (vote elimination + role reveal,
doctor save vs kill, detective result delivery, and the split — a mafia
night post DENIED by hard physics vs POSTED-then-refereed under soft).
noisy_pd and multi_n_budget_test deliberately have none — either way is fine.
Scen gates build on the shared harness: `gm_gate/setup_world.sh` (assembles
any scripted world from a gm.py + a moves dir) + `gm_gate/
dummy_scripted_agent.sh` (plays one `post`/`send`/`submit` moves-line per
wake).

## 8. Not built yet (do not assume)

- An LLM narrator layered on the GM (the design doc's "later"); timed
  simultaneous discussion windows (the fork-and-compare experiment).
- An OC adapter for gmlib (the interface is neutral, but only the PI adapter
  exists — GM worlds are PI-only for now).
- Concurrent per-agent sessions / automatic size-threshold rollover (operator
  `env roll-sessions` and the GM's `gm_roll_session` are the only rollover
  triggers; both are world-event-driven, never wall-clock).
