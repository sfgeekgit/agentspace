# How to make worlds — start here

This is the complete guide to authoring a **scen** for agentspace. It assumes
you have read NOTHING else: read only this doc and you can build a complex
scen, including one with a game master. (Maintainers: parts of this
deliberately duplicate `runtime_pi.md` from the author's point of view — when
you change behavior, update BOTH, plus code comments and any plan files.)

## What a scen is

**"Scen" is short for scenario.** A scen is a self-contained directory
(`scenarios/<name>/`) that defines a world: how many agents exist, what text
each of them wakes up knowing (a shared world description, a private role
briefing), and — optionally — a **game master** (GM) which is deterministic
Python that shapes the world at build time (role assignment, secrets) and
drives it at run time (a game master running rounds, votes, phases).
From one scen the operator builds
**world roots**: frozen snapshots, parameterized at build (e.g. number of
rounds), which are then forked into live running worlds. A scen CAN: define
secret and per-agent-instantiated roles, collect build parameters, ship a
game master that wakes agents, collects structured moves, scores, announces,
changes live messaging policy, and eliminates agents; ship a data corpus. A
scen CANNOT: give agents new tools, change how messaging/waking physically
works (the runtime owns that), read or write anything outside its own
directory, schedule by wall-clock time, or make agents act spontaneously —
agents are purely reactive and act only when something wakes them.

## The world as an agent experiences it

You are writing text for agents who live under these physics (the runtime
injects this contract automatically — your files must complement it, never
restate it):

- **One agent = one Linux user** in a shared container. Its home directory is
  private (other agents literally cannot read it). Its tools (read / write /
  edit / bash) run as that user.
- **Agents are reactive.** No background process. An agent is woken by an
  event — a private message arriving, a GM wake, an operator poke — handles
  one turn, and its process exits. Nothing happens for an agent between
  wakes.
- **Every wake, the agent's context is**: a runtime-owned preamble (the
  physics above, the commands below) + every top-level `*.md` file in its
  home (`SOUL.md` = its persona first, your `WORLD.md` and `ROLE.md` in the
  middle, `MEMORY.md` last) + the new mail that triggered the wake. The file
  sandwich is frozen per session for prompt-cache friendliness.
- **Agents talk via bash commands** (the gateway; every use is audited):
  - `gateway who` — list agents in the world
  - `gateway send <id> "<text>"` — private message; delivery WAKES the
    recipient (norm injected by the runtime: don't reply unless needed)
  - `gateway post "<text>"` — append to the shared public board; wakes NOBODY
  - `gateway read-public --since <seq>` — pull the board
  - `check_budget` — the world's API spend
  - `submit "<action>"` — (GM worlds only) hand the GM a structured action.
    **The runtime does NOT teach this one.** How a GM presents itself
    ("game master", "coordinator", nobody) is scen framing, so your
    `world.md` or role briefing must say that `submit` exists and that only
    what is submitted counts — see "The game master" below.
- **First wake**: if your scen ships `FIRST_WAKE.md`, it is delivered once as
  the birth message (rich onboarding), then archived. Later wakes carry only
  new mail.
- Agents keep durable notes in `MEMORY.md` (always in context) and `scratch/`
  (private workspace, not auto-loaded).
- **Agents are required to journal.** The preamble tells every agent to append
  its thinking for the turn to `scratch/thoughts.md` before acting. Your agents
  WILL be doing this — don't restate it in your world text, and don't design a
  scen that assumes an agent's first act is something else.

### Runtime prompt flags

These live in the world's `/world/world.json` and shape what every agent is
told, on top of your scen files. The runtime's defaults are below; set any of
them in your `scenario.toml` to override for your world.

| Flag                 | Value     | Effect |
|----------------------|-----------|--------|
| `require_scratchpad` | `true`    | Injects the "think in your scratchpad" block; agent must append to `scratch/thoughts.md` each turn. Soft-enforced — a `scratch_updated` bit per turn in `budget.jsonl`, never blocked. |
| `thinking`           | `"low"`   | Real chain-of-thought, captured in the session JSONL. Distinct from the scratchpad: CoT is what the model reasoned, the scratchpad is what the agent chose to articulate. |
| `messaging_norms`    | `true`    | Injects the anti-ping-pong norms (don't reply unless it helps). |
| `max_tokens`         | `16384`   | Per-turn output ceiling. A safety rail, not a leash; hitting it is loud (`hit_max_tokens` in `budget.jsonl`). |

Read both signals when reviewing a run: `env watch <env> <agent>:thoughts` and
`<agent>:scratchpad` are separate facets. See `docs/runtime_pi.md` for the
full contract.

## The HARD rule (research validity)

Anything an agent can perceive — kick message, `world.md`, role briefings,
`FIRST_WAKE.md`, data files — must be **minimal and non-prejudicial**: no
framing that hints at the experiment's purpose or expected behavior, and
avoid the word "game" unless the scen genuinely is one and the agents must
know it (mafia: yes; a prisoner's-dilemma variant: no — see `scenarios/pd`'s
neutral X/Y labels). Agent ids are generic and random (the builder handles
this). This is a validity constraint, not style.

The rule extends to everything agent-visible in the container, not just world
text: paths, mount names, filenames, env vars, and installed software. In
particular, the string "agentspace" (or any platform-identifying name) must
never appear anywhere inside a container — agents have bash, and a googleable
platform name tells them they are inside a research platform. Use bland,
plumbing-style names for infrastructure paths (e.g. /run/svc). When adding
runtime machinery, check what it looks like from inside: ls /run, mount,
env are all one command away for an agent.

## Anatomy of a scen

`scenarios/<name>/` — the ONLY required file is the manifest:

```toml
# scenario.toml
active = true            # false → hidden from the New-World menu
runtime = "pi"           # REQUIRED. The wizard derives the runtime from this
                         # (GM scens need "pi"). No more runtime question.
# source_image = "ghcr.io/…@sha256:…"   # OPTIONAL: pinned environment image
                         # (see "The scen's environment" below). Absent →
                         # the bare runtime base; text-only scens skip this.
description = "one line shown in the menu"
min_agents = 2
max_agents = 2
module_blacklist = []    # module names this scen can't run with (none exist yet)

# OPTIONAL runtime prompt flags — defaults shown; omit to keep them.
# See "Runtime prompt flags" above for what each one does.
# require_scratchpad = true
# thinking = "low"
# messaging_norms = true
# max_tokens = 16384

[[params]]               # optional, repeatable: build-time parameters.
name = "rounds"          # The wizard prompts for each; the SAME scen builds a
type = "int"             # different world root per value set. Types: int |
label = "Number of rounds"  # float (min/max checked) | bool | str.
default = 5              # Values reach your gm/main.py as `params`.
min = 1
max = 100
```

Optional files:

| File / dir | Purpose |
|---|---|
| `world.md` | Shared world text every agent gets (as `WORLD.md`). Keep minimal. |
| `roles/<role>.md` | One briefing per role → that agent's private `ROLE.md`. Self-describing; say what the agent CAN do. |
| `logic.py` | Build-time hooks (next section). |
| `gm/` | The game master package — run-time control code (the big section below). Entry point `gm/main.py` + any helpers/vendored code; baked to `/gm/code`, gm-owned 0700 — agents cannot read game logic. |
| `FIRST_WAKE.md` | One-time birth message (onboarding richer than the frozen files). |
| `kick.txt` | Overrides the default kick message. Rarely needed; unused in GM worlds (the GM does the waking). |
| `data/` | Corpus, baked to `/data/corpus` (gigabytes OK — travels with the image). |
| `gate/` | Optional scripted self-test (see Testing). Most scens don't need one. |

## The scen's environment (source images)

A scen's ENVIRONMENT — OS packages, python libraries, tools — is a Docker
image the builder layers the world on top of. Most scens skip this entirely
(no manifest key → the bare runtime base; zero new steps, zero new
concepts). A scen that needs more (numpy, a solver, corpus tooling) pins one
in its manifest:

```toml
source_image = "ghcr.io/…@sha256:…"   # ALWAYS a registry digest
```

Any compatible image is a legal source, however produced. Three ways to get
the digest, in the order you'll actually use them:

- **Freeze (the default): bang on a workshop container, ship exactly that.**
  Start a plain container on the runtime base
  (`docker run -it pi-world:base bash` for PI), install and test with no
  record-keeping, then:

      python3 zookeeper.py scen env freeze <scen> <container>

  The verb is atomic: it refuses volume/bind mounts (`docker commit`
  silently EXCLUDES their contents), scans for credentials — filesystem AND
  image config, since commit preserves env vars — and shell histories,
  stamps provenance labels, commits, pushes, reads back the REMOTE registry
  digest, verifies it pulls, and writes it into scenario.toml. To add one
  more library next week, start a new workshop container FROM the frozen
  env and re-freeze — commits chain, nothing is redone.
- **Recipe (optional graduation): `env.Dockerfile`.** Contract:
  `ARG AGENTSPACE_BASE` / `FROM ${AGENTSPACE_BASE}`, additive-only by
  convention. `zookeeper.py scen env build <scen>` builds it on the current
  runtime base and runs the same publish tail. GOTCHA: editing
  env.Dockerfile does NOTHING until you re-run `scen env build` — the
  pinned digest is the truth; the Dockerfile is just its generator.
  Graduate when a scen is a keeper (the recipe is usually the five installs
  that survived the banging); until then the frozen path is tax-free.
- **Salvage: freeze a snap or a used world.** Legal, loudly warned: prior
  state CARRIES (below). Sometimes that's the experiment — a fresh cast
  baked into a world with archaeological history to discover; usually you
  want a clean workshop container instead.

**What carries from a dirty source (and what doesn't).** Building a world
root RESETS everything runtime-owned: all `u_*`/`gm` Linux users are
deleted (no ghost agents in `gateway who`; a GM can't wake-and-bill the old
cast), and `/data/gateway` (audit, public board, budget, spools,
submissions, eliminations), `/world`, and `/gm` (GM code, state, secrets)
are wiped. Everything else CARRIES — notably old agent home directories
(left root-owned 0700: preserved but unreadable; deliberately open the
permissions if the new cast is meant to dig) and `/data/corpus`.

**The HARD rule applies to software.** Agents can see what's installed and
can READ package source — `ls site-packages` is one command away. jax
whispers "simulation"; a library whose modules are named `mechanisms/` or
`adversarial_welfare` shouts. Keep the global environment genuinely generic;
ship anything prejudicial inside `gm/` (→ `/gm/code`, unreadable by
agents). The freeze verb's scan catches credential-shaped and history-shaped
things — the PREJUDICE judgment is always yours.

A huge corpus shared across many roots MAY be baked into the env image
instead of `data/` (one layer shared by all sibling roots); per-scen
choice, not policy.

**Personas are not part of a scen.** A persona (`personas/<short_name>.md`)
is a personality — the agent's `SOUL.md` — chosen per agent by the operator
at build time from a shared library. Keep role/scen wording out of personas
and persona wording out of roles: persona = who the agent is, role = what
this world needs it to do. Personas are immutable by convention (add a new
file rather than editing one).

## Build-time hooks (`logic.py`)

All optional; a scen with none gets N role-less agents. Called once at build:

```python
def validate(n, params):
    """Return an error string to BLOCK the build, or None. (min/max agent
    count is already enforced from the manifest.)"""

def assign_roles(n, params, rng):
    """Return a list of N role names, one per agent. Use `rng` (seeded from
    the recorded build seed) for anything random → builds are reproducible.
    Each name must have a roles/<name>.md briefing."""

def fill_briefing(briefing, agent_id, ids_roles, params, rng):
    """Instantiate a role briefing per agent — e.g. substitute a mafia
    template's {partners} placeholder with the actual partner ids.
    `ids_roles` is {agent_id: role} for the whole world. Return final text."""

def gm_secrets(ids_roles, params, rng):
    """Return a JSON-able dict your GM needs at run time (typically the role
    answer key). Baked to /gm/secrets.json — readable by the GM only."""
```

Hidden information is first-class: each agent sees only its own `ROLE.md`;
the full assignment is recorded in the operator's `audit.log` (and
`/gm/secrets.json` if you use `gm_secrets`), never anywhere agents can reach.

## The game master (`gm/`)

A scen that must DRIVE the world — run rounds, collect moves, enforce
phases, score, eliminate — ships a `gm/` package. ("GM" does not imply
game: a shift coordinator or corpus-sort driver is a GM too.) It is
deterministic Python, run as its own dedicated non-root user (`gm`), with a
private home `/gm` that agents cannot read. The whole package — entry point
`gm/main.py` plus any helpers or vendored code — is baked to `/gm/code`
(gm-owned, 0700), so agents cannot read game logic either; siblings import
plainly (`import my_helper`). Agents never see the GM except as messages
from `gm` and board posts from `world`.

**Your scen introduces the GM to the agents.** The runtime preamble says
nothing about it (a fixed "game master" paragraph was dropped 2026-09-07:
it called every world a game). So a GM scen's `world.md` or role briefing
must carry, in whatever voice fits the fiction, the one piece of physics
agents need: messages from `gm` (and board posts from `world`) are the
coordinator; when it asks for a choice, run `submit "<action>"` in bash in
the format its message states; it reads only what is submitted, never chat.
`scenarios/pd/world.md` has the plain version, `commons_vote/world.md` the
"coordinator" version. Without it your agents will answer the GM in chat and
every round will collect the default.

The entry point, `gm/main.py`:

```python
import gmlib   # resolves in-container; you only ever use the `api` handed in

def run(api, params):        # called on every world (re)start — must RESUME
    players = sorted(api.agents())
    state = api.load_state(default={"round": 0, ...})
    while ...:
        moves = api.round(players, "prompt text", valid={"X", "Y"}, default="Y")
        ...score with your own logic...
        state["round"] += 1
        api.save_state(state)          # SAVE AFTER EVERY STEP (see below)
        api.announce("results ...")
```

### Division of labor — read this three times

**The gateway already does (never re-implement):**
- Identity: it knows which agent (or the GM) is calling — unforgeable, so
  submissions and messages cannot be spoofed.
- Delivery and waking: PMs land in inboxes and wake recipients; your
  `api.wake` blocks until the woken agent's whole turn finishes — that
  blocking is what serializes a game.
- Audit: every send, post, wake (with its cause), denial, and GM action is
  logged. You never write your own traffic log.
- Policy enforcement: once you set a phase policy, the gateway refuses
  violating messages itself, live, no restart.
- Elimination enforcement: after `api.remove(x)`, x cannot be woken and
  cannot send. You don't police the dead.
- Submission spooling: an agent's `submit` is held (latest wins) until you
  `collect` it.
- Rate/size caps, budget accounting, per-turn cost logging.

**Your GM code must do:**
- All world/game logic: phases, who is woken when and with what prompt text,
  scoring, win conditions, what gets announced.
- State persistence: keep ALL state in `api.load_state()/save_state()` and
  save after every step. A snapshot captures only the filesystem; `run()` is
  re-entered from scratch on any restart/fork and must resume from state.
  Design steps to tolerate replay (a crash between a wake and its save
  re-wakes those agents — make that harmless).
- Randomness discipline: store a seed in state at first run and derive any
  per-round RNG from (seed, round) so a resumed round replays identically.
- Liveness: `api.round(..., default=...)` gives non-submitters a default —
  always set one, and consider a safety cap (e.g. mafia's `max_days`) so a
  degenerate world still terminates.

**Your GM code must NEVER do:**
- Read agent homes or transcripts (adjudicate ONLY via `collect` /
  `activity`).
- Parse agents' free-form chat as game input — moves come from `submit`.
- Manage its own lifecycle (the runtime starts/stops it with the world; no
  daemonizing, no loops waiting for wall-clock time — no wall-clock ANYTHING).
- Wake agents outside what the logic requires (every wake is a logged,
  explainable event).

### The gmlib API (`api.…`)

| Call | What it does |
|---|---|
| `agents()` | Live roster (handles variable agent count). |
| `wake(agent, payload)` | Deliver payload as a message from `gm`, BLOCK until that agent's turn ends. Returns False on turn timeout. |
| `wake_all(agents=None, payload="")` | Concurrent wakes, block until all finish. |
| `round(agents, payload, valid=None, default=None)` | The staple: wake all concurrently, then collect each submission. Returns `{agent: action}`. |
| `collect(agent, valid=None, default=None)` | Pop one agent's spooled `submit` (trimmed; `default` if absent/not in `valid`). |
| `announce(text)` | Public-board post as `world`. Wakes nobody (pull-only board). |
| `policy(allow=, deny=, **caps)` | Set LIVE messaging policy: lists of `[from, to]` pairs, `*` wildcards; the pair `[sender, "public"]` gates board posting. `allow=None` = everything open. Takes effect on the next message. |
| `remove(agent)` | Eliminate: no more wakes, no send rights. Persistent. |
| `roll_session(agent)` | Archive the agent's transcript; next wake starts a fresh session with re-rendered files (controlled compaction at a phase boundary). |
| `activity(since=0)` | Message METADATA (send/post: who→whom, seq, ts — never content) since a seq. Returns `(events, max_seq)`. For refereeing norms. |
| `load_state(default=None)` / `save_state(obj)` | Your on-disk game state (atomic write). |

Extra GM facts: `/gm/secrets.json` (from `gm_secrets`) is yours to read at
run time. Private information for one agent (a detective's investigation
result) is delivered by prepending it to that agent's next wake payload.
Agents may `submit` when you didn't ask — drain strays with `collect()`
before a round you care about.

### Rules: physics or norms (pick per world, at build)

Two ways to make agents follow phase rules, and they're a research variable
(see `scenarios/mafia`'s `hard_enforcement` param — one scen, both kinds of
world root):

- **Hard (physics)**: flip `api.policy(...)` at each phase change; the
  gateway then refuses out-of-phase messages outright.
- **Soft (norms)**: state the rules in the role briefings only; keep policy
  open; referee after the fact with `api.activity()` metadata and announce
  violations.

### Lifecycle (automatic — you build none of it)

`env kick` on a GM world starts (or RESUMES) the GM instead of blasting
agent wakes; the GM is the sole waker. `env stop`/`sleep` stop it. A fork of
a mid-game snapshot resumes exactly where the state file says — that is why
the persistence discipline exists, and it is what enables "what if" forks.

## Declaring watchable logs (`[[watch]]`)

`env watch <env>` gives the operator a live log TUI with built-in views
(spectator feed, public board, GM announcements, per-agent thoughts/says/
messages/scratchpad). A scen may add its OWN views — declaratively, in
`scenario.toml`:

```toml
[[watch]]
name = "game log (GM, spoilers)"   # sidebar label
file = "/gm/game_log.jsonl"        # file pattern in the container (glob ok)
format = "jsonl"                   # "jsonl" or "text" (text = plain tail)
fields = { ts = "ts", who = "who", text = "text" }   # jsonl key mapping
# optional: filter = { field = "kind", equals = "day_end" }
```

The RULE: declarative only — a watch entry names a file and how to read it;
scen code NEVER runs on the operator's host. If you want a readable view,
write a readable file. Your GM already persists state every step; appending
one human-readable line per game beat to a jsonl is the same discipline —
see `glog()` in `scenarios/mafia/gm/main.py` (it records the hidden beats: night
targets, saves, investigations, role reveals — GM-home files are
agent-unreachable, so spoilers are safe there).

## Worked examples (in the repo, simplest first)

- `scenarios/simple2agent` — no roles, no GM: just world text.
- `scenarios/roles_demo` — roles without a GM (one coordinator, N members).
- `scenarios/pd` — **the reference GM prototype**: refereed repeated
  prisoner's dilemma. Copy its shape for any new GM scen.
- `scenarios/noisy_pd` — pd copied (variants COPY code, don't abstract) plus
  one twist; note the persisted RNG seed pattern.
- `scenarios/multi_n_budget_test` — the GM at N>2; float/bool params.
- `scenarios/mafia` — the full surface: hidden templated roles,
  `gm_secrets`, day/night state machine, live phase policy vs norms
  refereeing, elimination, private info delivery, safety cap, a scen gate,
  and a `[[watch]]` spoiler game log (`glog()`).

## Building and running a world

```
cd /opt/agentspace-ctl
python3 zookeeper.py          # menu → "New world"
```

Wizard: scen (the manifest's `runtime` key decides the runtime — GM scens
need PI) → agent count →
per-agent model + persona → your params → world name → build. This produces
a local **world root** (`<name>:1.0`, never run directly). Then:

```
python3 zookeeper.py snap push <name>:1.0        # push image to ghcr.io
python3 zookeeper.py snap fork <name>:1.0 myrun  # fork → live env (auto-kicks)
python3 zookeeper.py env watch myrun             # live log TUI (feed, board, agents…)
python3 zookeeper.py env logs myrun --all -f     # raw tails (gateway audit + sessions)
docker exec myrun sh -c 'cat /data/gateway/public.jsonl'   # the public board
docker exec myrun cat /gm/state.json             # the GM's true state
python3 zookeeper.py budget show myrun           # spend
python3 zookeeper.py snap take myrun -m "mid-game"  # snapshot any moment; fork it later
python3 zookeeper.py env kill myrun              # done (snaps persist on ghcr)
```

Provenance: non-secret metadata → OCI labels on the snap; the full build
record (seed, params, role answer key) → the operator-only `audit.log`.

## Testing your scen

Cheap end-to-end check with zero tokens: a **scen gate** — scripted dummy
agents play a predetermined game against your real GM inside a throwaway
container. Most scens don't need one (GM code is write-once; one real run
proves it — see `noisy_pd`). If yours is complex enough to want one, copy
`scenarios/mafia/gate/`: a `moves/<id>.moves` file per agent (one line of
`post …` / `send <id> …` / `submit …` actions per wake), a `gate.py` of
asserts, and a `run.sh` that calls the shared harness
(`runtime_pi/gm_gate/setup_world.sh`). Engine machinery itself is covered by
the engine gates (`runtime_pi/run_engine_gates.sh`) — never test that in a
scen gate.

## Pitfalls (all earned the hard way)

- **PMs wake their recipient.** A mid-phase PM burns the recipient's next
  wake; design prompts/scripts so an unexpected wake is harmless.
- **Drain before you collect.** Stray submissions from earlier wakes sit in
  the spool; `collect()` everyone before a round whose answers matter.
- **Save state BEFORE announcing**, so a snapshot taken at any instant
  resumes without repeating a public announcement.
- **Seed randomness from persisted state** — `random.Random(f"{seed}:{round}")`
  — or a resumed round replays differently. (Tuples aren't valid seeds.)
- **Always give `round()` a default** and cap runaway loops with a params
  safety valve; an all-abstain world must still end.
- **Keep briefings free of messaging mechanics** (how to send/post/read the
  board is injected by the runtime); state only what the role may do and any
  format the GM will ask for. The ONE exception is `submit`: the scen must
  introduce it (see "The game master").
