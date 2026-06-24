# Agentspace — MVP Architecture

Infrastructure for running reproducible, forkable multi-agent experiments. This doc covers infrastructure only; agent-level details (scenarios, inter-agent protocols, experimental design) are out of scope here. Cooperation under scarcity was the first prototype scenario — one example, not the platform's purpose.

## Hierarchy

```
[1+ agents] → [1 env = 1 Docker container] → [1+ envs per host droplet] → [1+ host droplets]
```

- **Agent**: an OpenClaw agent (one entry in `agents.list`), with its own workspace, memory, and session store inside the container. Multiple agents run inside one Gateway process.
- **Env**: a Docker container. The container's internal filesystem is the complete env state — corpus, agent memories, session logs, inter-agent messages, everything. Multiple agents share this filesystem.
- **Host droplet**: a DO droplet running Docker, hosting one or more env containers. Envs on the same host are isolated by Docker.
- **Control droplet**: a separate small DO droplet running the orchestration CLI.

## Isolation Model

An env guarantees that agents interact with each other **only via the messaging
channel**. Everything else an agent owns — workspace files, memory, session
transcripts — is private to it.

Isolation has two layers, and both must be closed for the guarantee to hold:

1. **Communication-tools layer**: agents must be able to send messages to each
   other, but not read each other's message history or session state through
   runtime tools.
2. **Filesystem layer**: an agent with file/exec tools must not be able to
   reach a peer's workspace, memory, or transcripts through the filesystem.

Closing only the first layer is not isolation — any agent with exec tools can
read peers' files straight off a shared filesystem. Current implementation:
per-agent sandbox containers; each agent's file/exec tools run in a dedicated
container that mounts only that agent's own workspace.

## Agent Memory Durability

Conversational state is runtime-ephemeral: sessions may reset across idle
periods or day boundaries, and a fresh fork starts from whatever was frozen in
the snap. **Durable agent memory must live in workspace files.** Souls should
instruct agents to journal to their workspace when continuity matters —
anything not written to a file should be assumed forgettable.

## Stack

- **Envs = Docker containers**. The container's internal filesystem IS the env — corpus, OpenClaw config, all agent state. No Docker volumes for env data; everything is inside the container layer.
- **Snapshots = `docker commit` + `docker push` to ghcr.io**. A snapshot is a complete frozen image of the container at a point in time. This is the primary research primitive (see Snapshot / Fork Semantics).
- **API keys are injected at runtime** (`docker run -e OPENROUTER_API_KEY=...`), never baked into the image. `docker commit` does not capture runtime env vars, so keys are never in snapshots.
- **Hosting = DO droplets**. One control droplet + one or more host droplets. No DO Block Storage Volumes, no S3, no other paid services.
- **Budget layer = OpenRouter**. One API key per env with a credit limit. All agents in the env share that key. Agents query remaining budget via `GET /api/v1/key`.
- **Orchestration = Python CLI on the control droplet**, calling Docker (over SSH to host droplets), `doctl`, and the OpenRouter REST API. SQLite for local state.
- **Base runtime image**: built once from the Dockerfile (OS + Node + OpenClaw, no data). Stored locally. Not pushed to ghcr.io. Used as the starting point when creating a new world snap.
- **Image registry = ghcr.io** (free for public repos). Holds snapshot images only. Snaps are the only user-facing artifact.

## Sharing & Open Source

- **GitHub repo** (`https://github.com/sfgeekgit/agentspace`) holds: Dockerfile, control CLI, agent config templates, scenario definitions, setup scripts. No keys, no large data, no snapshots.
- **ghcr.io** holds: snapshot images only. This includes world snaps (runtime + corpus, agents never yet run) and experiment snaps (runtime + corpus + accumulated agent state).
- Friends can `git clone` and run their own copy. They need: a machine with Docker, an OpenRouter key, optionally a DO account. No dependency on the original author's accounts.
- A specific env state can be shared by pointing at a ghcr.io snapshot tag. The recipient `docker pull`s it and runs it.

## Directory Structure

### Control droplet — code (git repo)
```
/opt/agentspace-ctl/              ← git clone of github.com/sfgeekgit/agentspace
  Dockerfile                      ← base runtime: OS + Node + OpenClaw
  agentspace.py                   ← control CLI
  setup/
    droplet-setup.sh              ← provision a fresh droplet from scratch
  agents/
    templates/
      openclaw.json.template      ← base agent config structure
      SOUL.md.default
      budget_skill.py             ← check_budget() helper module
  scenarios/
    <scenario-name>/
      scenario.toml               ← manifest (required): active, description, min/max_agents, module_blacklist
      world.md                    ← optional shared world text
      roles/<role>.md             ← optional per-role briefings (baked into ROLE.md)
      logic.py                    ← optional hooks: assign_roles(), validate()
      data/                       ← optional corpus baked into the snap
  personas/
    <short_name>.md               ← reusable persona soul text (baked into SOUL.md at build)
  modules/                        ← optional add-ons (reserved; none yet)
  .gitignore
  secrets.env.example             ← template showing required keys, no values
```

See `authoring_scenarios.md` for how to write a scen, persona, or module. (The
legacy per-scenario `Dockerfile` + `openclaw.json` form still exists for
simple2agent; new scens use the manifest form above and the builder below.)

### Control droplet — runtime state (ephemeral, rebuildable, never committed)
```
/var/agentspace-ctl/
  db.sqlite                       ← env + snapshot registry (rebuildable from ghcr.io + OpenRouter)
  secrets.env                     ← actual API keys (never committed)
```

### ghcr.io — authoritative versioned store
```
ghcr.io/sfgeekgit/agentspace:snap-<id>     ← complete frozen env snapshot (docker commit)
```

Running env containers are managed by Docker (`docker ps`). Their internal filesystems live under `/var/lib/docker/` on the host droplet — not managed directly.

## Inside a Running Env Container

```
/data/
  corpus/                    ← large shared world data (seeded at fork_env time)
  openclaw/
    openclaw.json            ← gateway config: agents.list, model, tool policies
    agents/
      a87329/
        workspace/           ← SOUL.md, AGENTS.md, workspace files
        agent/               ← auth profiles, model registry
        sessions/            ← session JSONL logs
      a90301/
        workspace/
        agent/
        sessions/
  messages/                  ← inter-agent inbox (agentspace-level, outside openclaw/)
  scratchpads/               ← agent reasoning logs (agentspace-level)
```

`OPENCLAW_STATE_DIR=/data/openclaw` and `OPENCLAW_CONFIG_PATH=/data/openclaw/openclaw.json` direct all OpenClaw state into `/data`, fully captured by `docker commit`.

## OpenClaw Runtime (inside each env)

- **One Gateway per env, multiple agents.** Each env container runs a single `openclaw gateway` process. Agents are defined in `agents.list` in `openclaw.json`, each with its own workspace under `/data/openclaw/agents/<agentId>/`. This is OpenClaw's native multi-agent pattern.
- **No external channels** (no Telegram, Discord, WhatsApp, etc.). The env is a closed world. The only humans entering are via SSH.
- **Human → agent interface**: `docker exec -it <container> bash`, then use tmux — one pane for the gateway, one per agent:
  ```
  openclaw gateway run                                    # pane 1: gateway (foreground)
  cd /data/openclaw/agents/<agentId>/workspace && openclaw tui  # pane N: per agent
  ```
  `tui` auto-selects the agent when launched from its workspace dir. `openclaw.json` must include `gateway.mode: "local"` or the gateway refuses to start.
- **Agent → agent messaging**: `sessions_send` (native OpenClaw tool). An agent calls `sessions_list` to find the target's session key, then `sessions_send` to deliver a message. Requires `tools.agentToAgent.enabled: true` and `tools.agentToAgent.allow: [<agent_ids>]` in `openclaw.json`. Visibility caveat: with `"self"` agents couldn't message each other at all, so we currently ship `tools.sessions.visibility: "all"`. Under `"all"` the session-read tools are closed per-agent via `tools.deny` of `sessions_history`/`sessions_list`/`session_status`, and the filesystem layer is closed by per-agent sandbox containers (see Isolation Model above). Implemented as of scenario 4.0.
- **Budget access from agents**: helper module exposing `check_budget()`, added as an OpenClaw skill. Reads `OPENROUTER_API_KEY` from the env's runtime environment and queries `GET /api/v1/key`.

## Components

### 1. Control Droplet
- One always-on Debian droplet, SSH access only.
- Installed: Python 3, `doctl` (authenticated), Docker CLI (for talking to host droplets via SSH/Docker context), SQLite.
- Holds secrets in `/var/agentspace-ctl/secrets.env` (gitignored): DO API token, OpenRouter management key, SSH keys for host droplets.
- Exposes the `agentspace.py` CLI for all operations.
- At MVP scale, the control droplet and host droplet can be the same box.

### 2. Base Runtime Image
- Defined by the Dockerfile: Debian base + Node 24 + OpenClaw installed globally + `budget_skill.py`. Default CMD is `sleep infinity` so `docker run -d` keeps the container alive for `docker exec`.
- Built locally from the repo (`docker build`). Stored on the local machine only — not pushed to ghcr.io.
- Contains runtime only: no corpus, no agent configs, no scenario data, no keys.
- Used as the starting point when creating a new world snap (see World Snap Creation below).

### 3. Env Containers
- One container per env. Always started from a snapshot image — either a world snap (first run) or an experiment snap (fork).
- Started with `OPENROUTER_API_KEY` injected at runtime (not in the image).
- The container's internal filesystem accumulates all state as the env runs.
- Multiple envs run side by side on one host droplet, isolated by Docker.

### 4. Host Droplet(s)
- Debian + Docker installed.
- Runs env containers. SSH-reachable from the control droplet.

## World Snap Creation

**Scenario-content principle:** world files (world.md, PEERS.md, souls, kick
messages) tell agents what they *can* do — never enumerate denied or absent
abilities. Listing what's forbidden leaks experiment design to the agents and
plants ideas; capabilities they don't have should simply not be mentioned.

Before any experiment can run, a world snap must exist for that scenario. A world snap contains the runtime + corpus + initial agent configs, with agents never yet activated. It is the clean starting point for all forks of that experiment.

### Builder path (current)

New world roots are built from a **scen** (a manifest-based definition under
`scenarios/<name>/`) via the **builder** (`agentspace/builder.py`), driven by the
menu's "New world" wizard (`zookeeper.py`). The builder is a thin host: it
generates generic agent IDs, asks the runtime to render its native config for N
agents, composes per-agent seed files (persona → `SOUL.md`, peers, optional role
→ `ROLE.md`), bakes `world.md` + a generic kick + any `data/` corpus, then
assembles the image (`docker run` base → `docker cp` staged tree → `docker commit`
with OCI labels). Scen-specific behavior (role assignment, validation) is
delegated to the scen's optional `logic.py`. Non-secret provenance goes to OCI
labels; the full build record incl. any secret role assignment goes to
`audit.log` only. See `authoring_scenarios.md`.

### Legacy (Dockerfile) path

The original workflow still works for simple2agent — each such scenario has a
hand-written `Dockerfile` + `openclaw.json` under `scenarios/<name>/`, and the
build script stamps the OCI labels:

```bash
# 1. Build the base runtime image (if not already built)
docker build -t agentspace:base /opt/agentspace-ctl

# 2. Build the world snap from the scenario's Dockerfile
#    (copies openclaw.json, world.md, kick.txt, PEERS.md into the image and
#     sets all org.agentspace.* OCI labels)
scripts/build_scenario.sh simple2agent 3.0

# 3. Push and index
docker push ghcr.io/sfgeekgit/agentspace:snap-simple2agent-3.0
python3 zookeeper.py snap rebuild-index
```

Agent workspaces in a world snap are intentionally empty (no SOUL.md baked in):
OpenClaw scaffolds its default SOUL.md on each agent's first interaction and
overwrites any pre-baked one. Scenario-specific souls are injected at fork time
via `snap fork --soul <agentId>=<path>`.

The corpus (which may be gigabytes) travels with the snap on ghcr.io. It never needs to live on the control droplet. All subsequent forks of this scenario pull from ghcr.io and get the corpus automatically.

## Snapshot / Fork Semantics

**Snapshots are the primary research primitive**, not just a backup mechanism. The core experimental workflow is: run → snapshot → edit → fork → compare.

A snapshot is a complete `docker commit` of a running (or stopped) container, pushed to ghcr.io. It captures the entire container filesystem — corpus, agent memories, session logs, inter-agent messages, scenario state, everything — at that moment in time. Because API keys are injected at runtime and not baked in, they are not present in snapshots.

### Taking a snapshot
```bash
docker commit <container-id> ghcr.io/sfgeekgit/agentspace:snap-<id>
docker push ghcr.io/sfgeekgit/agentspace:snap-<id>
```
Snapshots should be taken at clean pause points — between agent turns, not mid-inference — to avoid torn state.

### Forking from a snapshot
```bash
docker pull ghcr.io/sfgeekgit/agentspace:snap-<id>
docker run -d -e OPENROUTER_API_KEY=sk-or-... --name <new-env-name> ghcr.io/sfgeekgit/agentspace:snap-<id>
```
The forked env starts with identical state. Agents have full memory of everything up to the snapshot. They don't know they're a fork. They restart from idle — the gateway initializes from stored state and waits for the first trigger.

### Editing a snapshot (without starting it)
`docker create` allocates the container filesystem without running any processes:
```bash
docker create --name edit-tmp ghcr.io/sfgeekgit/agentspace:snap-<id>
docker cp edit-tmp:/data/openclaw/agents/a87329/memory.md ./
# edit locally
docker cp ./memory.md edit-tmp:/data/openclaw/agents/a87329/memory.md
docker commit edit-tmp ghcr.io/sfgeekgit/agentspace:snap-<id>-modified
docker rm edit-tmp
```

What can be edited this way:
- Agent memory files (markdown)
- `SOUL.md`, `AGENTS.md`, `openclaw.json`
- SQLite session stores (copy out, use `sqlite3` CLI, copy back)
- Inter-agent message inboxes
- Scenario/world state files
- Scratchpads

### Snapshot lineage
Each snapshot image carries OCI labels recording: parent snapshot ID, env name, timestamp, notes. This gives a queryable lineage tree without needing to pull the full image.

Example fork tree:
```
snap-a90301-a87329-world-v1  (world snap: corpus loaded, agents never run)
    │
    └─ snap-001 (end of round 1, budget at 40%)
            │
            ├─ snap-001-a  (unmodified → fork A, continues normally)
            │
            ├─ snap-001-b  (a87329's memory edited: removed knowledge of deal)
            │       └─ fork B: what if a87329 forgot?
            │
            └─ snap-001-c  (SOUL.md edited: increased budget anxiety)
                    └─ fork C: what if agents were more stressed?
```

## Control CLI verbs (v1)

- `fork_env(name, world_snap_id, budget_usd, host=...)` — mint OpenRouter key with credit limit, pull world snap from ghcr.io, run as new container with key injected, record in SQLite.
- `fork_from_snapshot(snapshot_id, new_name, budget_usd)` — pull snapshot image from ghcr.io, run as new container with fresh key injected.
- `snapshot_env(name, notes=...)` — `docker commit` the container, push to ghcr.io, record lineage in SQLite.
- `edit_snapshot(snapshot_id)` — `docker create` from snapshot, open a shell for editing, `docker commit` to new tag on exit.
- `list_envs()` — name, host, container ID, OpenRouter key, budget, usage, status.
- `list_snapshots()` — ID, parent, env name, timestamp, notes, ghcr.io tag.
- `topup_budget(name, additional_usd)` — increase the OpenRouter key's credit limit.
- `kill_env(name)` — stop + remove container, optionally disable the key.

## Budget Layer

- One OpenRouter account funded with credits.
- Per env: one API key created via OpenRouter's key-provisioning API with a `limit` (USD).
- Agents in the env share that key. Shared budget by construction.
- Remaining = `limit - usage` from `GET /api/v1/key` (free).
- Top-ups = PATCH the key's limit.

## Full Reproducibility

Everything except API keys lives in GitHub + ghcr.io. Complete disaster recovery:

1. New DO account, new droplets, run `setup/droplet-setup.sh`.
2. Paste in API keys (DO token, OpenRouter management key).
3. `agentspace list_snapshots` — pulls manifest from ghcr.io, rebuilds SQLite.
4. `agentspace fork_from_snapshot <id>` to restore any env.

The control droplet's SQLite is rebuildable from OpenRouter (list keys) + Docker (list containers) + ghcr.io (list snapshot images), so it is not critical to back up.

## Open Questions for Claude Code

- Verify current OpenRouter endpoint + auth pattern for programmatic key creation with credit limits.
- Pick the cleanest way for the control droplet to drive Docker on host droplets (Docker contexts over SSH vs. SSH + raw `docker` commands).
- SQLite schema: one `envs` table and one `snapshots` table for v1.

*Resolved: agent-to-agent comms is `sessions_send` (native OpenClaw tool). No filesystem inbox needed.*

## Out of Scope for MVP

- Agent-level details (number of agents per env, exact inter-agent comms protocol, scenario design, experimental design).
- Web UI / dashboards.
- Multi-user / team support.
- Local GPU / open-source model integration.
- Cross-host orchestration beyond "control SSHs into host and runs `docker`."
- Third-party OpenClaw tooling. MVP uses only stock OpenClaw.

---

## Appendix A: Observability

Clean observability is a first-class research requirement: we need to be able to see what every agent did, said, and (where possible) thought, both live and post-hoc.

**Observability principle: runtime logs are ground truth.** Gateway/runtime
logs, session transcripts, and direct inspection of containers and files are
evidence; an agent's self-report about its own configuration or capabilities is
not. Agents confabulate plausible-sounding wrong explanations of their own
setup — verify against logs and source, never against what an agent says about
itself.

### A.1 Model-level reasoning (chain-of-thought)

Availability depends on model:
- **Anthropic (Claude)** — exposes `thinking` blocks via API when extended thinking is enabled. Passes through OpenRouter.
- **OpenAI (o-series)** — only summarized reasoning exposed; raw CoT is policy-hidden.
- **DeepSeek, Qwen w/ thinking modes** — typically expose full raw reasoning.

For experiments that depend on inspecting raw reasoning, prefer models that expose it. Treat "what reasoning is visible" as an experimental variable, not a constant.

### A.2 OpenClaw-level event logs

OpenClaw writes a per-session append-only JSONL event log under `/data/openclaw/agents/<agentId>/sessions/`. Every turn, tool call, tool result, message, and thinking block is recorded. Primary post-hoc observability source. Replayable and diffable across runs.

Because all state is inside `/data` and snapshots capture the full container filesystem, every snapshot is a complete observability bundle.

### A.3 Prompted scratchpads

Agents are instructed via `SOUL.md` to write working reasoning to `/data/scratchpads/<agentId>.md` before taking actions. Append-only.

This is a distinct signal from model-level CoT:
- **Model-level CoT** = what the model actually reasoned (where exposed).
- **Prompted scratchpad** = what the agent chose to articulate.

The gap between the two is itself data.

### A.4 Inter-agent communications log

All `sessions_send` messages appear in the session JSONL logs of both sender and receiver. Append-only. Complete audit trail of who said what to whom.

### A.5 Budget-event log

A small log at `/data/budget_events.log` recording every `check_budget()` call: timestamp, agent, `{limit, usage, remaining}`. Cheap and captures "who knew what about the shared budget when."

### A.6 Live observation

While an env is running, the control droplet can:
- `docker exec` into the env to `tail -f` any log or scratchpad file.
- Query OpenRouter's `/api/v1/key` for live budget state without touching the env.

