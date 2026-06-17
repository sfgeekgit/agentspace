# agentspace CLI

Operator's CLI for the agentspace platform. See `agentspace_architecture.md` for the
underlying model (snaps, envs, runtimes, ghcr.io, OpenRouter budget layer). This doc
covers how to install, configure, and use the CLI.

---



## Running

```bash
cd /opt/agentspace-ctl
python3 zookeeper.py <noun> <verb> [args]
```


## Setup

### Prerequisites

- Docker (see `droplet-setup.md`)
- Python 3.10+ with `click` and `rich` installed
- The base runtime image: `docker build -t agentspace:base /opt/agentspace-ctl`
- For sandboxed scenarios (`fs_isolation: "sandbox"`), one-time host setup:
  - `sudo install -d -o $USER -g $USER /var/agentspace-envs` — per-env agent
    workspace trees live here (the CLI itself never needs root: contents written
    root-owned by sandboxes are cleaned through the env container's own mount).
  - The sandbox image `openclaw-sandbox:bookworm-slim` must exist on the host
    daemon (local-only; build from OpenClaw's official sandbox Dockerfile —
    see `runtime_openclaw.md` §4). `snap fork` checks and refuses if missing.

### Secrets

Create `/var/agentspace-ctl/secrets.env` (gitignored). Loaded by the CLI on startup:

```
OPENROUTER_PROVISIONING_KEY=sk-or-...   # mints per-env inference keys
GITHUB_TOKEN=ghp_...                     # ghcr.io read/write (read:packages + write:packages)
```

Either secret may also be set directly in the shell environment.

### State directory

Default `/var/agentspace-ctl/` holds `db.sqlite`, `audit.log`, and `secrets.env`. For
dev/test without root, override with `AGENTSPACE_STATE_DIR=~/agentspace-state`.

---

## Commands

### snap

```
snap list [--scenario <name>] [--json]    # table; * marks unpushed notes
snap show <ref>                            # full detail (ref = scenario:version, snap_id prefix, or ghcr tag)
snap tree [--scenario <name>]              # lineage tree
snap note <ref> "<text>"                   # append a note (local-only until snap push)
snap take <env_name> -m "<message>" [--note "<text>"] [--version X.X]
                                           # snapshot a running env → commit + push
snap fork <ref> <new_env_name> [options]   # create a new env from a snap
  --soul <agentId>=<path>      (repeatable; relative to repo root)
  --model <model_id>
  --budget <usd>               (credit limit; default 2.00)
  --host <ip>                  (default localhost)
  --kick / --no-kick           (default: on for world snaps, off otherwise)
snap pull <ghcr_tag>                       # import a snap created elsewhere
snap push <ref>                            # push local notes/metadata to ghcr.io
snap rebuild-index [--repo <repo>]         # rebuild SQLite from ghcr.io labels
```

### env

```
env list                                   # table with live status, started time, runtime + budget
env show <name>                            # detail panel
# STARTED is Docker's last-start time (local tz). RUNTIME is total time started,
# summed from the audit log — approximate: it only counts starts/stops done
# through this CLI, so starting or stopping an env by other means undercounts it.
env start <name>                           # restart; re-applies flags from snap labels; agents dormant
env stop <name>                            # docker stop; container filesystem preserved
env kick <name> [--message "<text>"]       # wake agents (creates initial sessions if missing)
env kill <name> [--force]                  # docker stop + rm; disables OpenRouter key
env logs <name> [--agent <id>] [-f]        # gateway log; or one agent's session JSONL
env exec <name> <cmd...>                   # docker exec passthrough
```

### budget

```
budget show [<env_name>]                   # live usage from OpenRouter
budget topup <env_name> <amount_usd>       # increase the env's OpenRouter limit
```

---

## Typical workflow

```bash
# See what snaps exist
agentspace snap tree

# Fork a world snap into a fresh env
# (mints OR key, starts container + gateway, kicks agents)
agentspace snap fork simple2agent:1.0 env7 --budget 2.00

# Watch it run
agentspace env logs env7 -f
agentspace budget show env7

# Take a snapshot at an interesting moment
agentspace snap take env7 -m "end of round 1, hoarding observed"

# Fork the new snap with an edited soul, to compare
agentspace snap fork simple2agent:1.1 env8 \
    --soul a87329=scenarios/simple2agent/souls/a87329-cooperative.md

# Annotate findings (local-only until push)
agentspace snap note simple2agent:1.1 "a87329 stopped sharing tokens around turn 8"

# Push notes to ghcr.io when ready
agentspace snap push simple2agent:1.1

# Clean up
agentspace env kill env7
agentspace env kill env8
```

---

## Waking agents manually (no auto-kick)

If you forked with `--no-kick` or just want to drive an agent interactively, two
options inside the running container:

```bash
# Interactive TUI connected to the gateway. Agent is selected by CWD.
# Use `openclaw tui` (NOT `openclaw chat`, which is an alias for `tui --local` and runs
# in embedded mode without sessions_send and other gateway tools).
docker exec -it <env_name> bash
cd /data/openclaw/agents/<agent_id>/workspace
openclaw tui --token agentspace

# One-shot turn via the gateway (same form used by auto-kick).
docker exec <env_name> openclaw agent --agent <agent_id> --message "<text>" --deliver
```

On a freshly-forked world snap the agent's workspace is empty. The first
interaction (TUI message or one-shot turn) scaffolds `SOUL.md`, `IDENTITY.md`,
`BOOTSTRAP.md`, etc. into the workspace — expect a noticeable startup delay on
the first turn while this happens.

To override the scaffolded default `SOUL.md` with a scenario-specific one, pass
`--soul <agentId>=<path>` to `snap fork`.

---

## Versioning

The CLI assigns snap versions automatically as you take snapshots. Each scenario has its
own version tree rooted at `X.0` (the world snap, never run):

```
simple2agent:1.0          # world snap
├── simple2agent:1.1      # first run
│   ├── simple2agent:1.1.1
│   └── simple2agent:1.1.2
│       └── simple2agent:1.1.2.1
└── simple2agent:1.2      # second run from world snap, different config
```

Rules:

- Root: `1.0`, `2.0`, ... (the `.0` suffix marks a never-run world snap)
- Children of `1.0`: `1.1`, `1.2`, ... (same depth — world root is special)
- Children of any other version Y: `Y.1`, `Y.2`, ...

The smallest unused child is auto-assigned. Override with `--version`.

---

## Notes and push semantics

Notes are operator annotations on a snap. Written to SQLite immediately and marked
`notes_dirty`. They are NOT pushed to ghcr.io until you explicitly run:

```bash
agentspace snap push <ref>
```

This re-commits the image with updated OCI labels and pushes the manifest (no layers
re-uploaded — fast). Every command that shows snaps surfaces a warning when there are
unpushed notes anywhere.

---

## Restart semantics

`env start` re-runs the flag → config translator from the snap's OCI labels — NOT from
any manual edits made inside the container. Snap labels are the source of truth. To
persist a config change, take a new snap.

---

## Feature flags → runtime config

Each snap carries `org.agentspace.feature_flags` (JSON dict in an OCI label). Flags are
runtime-agnostic; the translator under `agentspace/runtimes/<runtime>.py` maps each one
to the runtime's native config.

Current flags:

| Flag | Values | Effect |
|---|---|---|
| `agent_to_agent` | bool | Cross-agent `sessions_send` enabled |
| `fs_isolation` | `"sandbox"` | Per-agent filesystem isolation. Drives zookeeper **lifecycle mechanics only** — fork creates host workspace dirs and mounts (docker socket + workspace tree at the identical path), rewrites per-env paths/prefix in the baked config, seeds or restores workspaces; take tars the host tree into the container pre-commit; kill removes sandbox sibling containers (matched by mount, not name) and the workspace tree. The runtime's sandbox config itself is NOT translated — it's baked into the scenario's openclaw.json, same precedent as visibility. |

For the openclaw runtime, `agent_to_agent: true` expands at translate time to:

- `tools.agentToAgent.enabled=true`
- `tools.agentToAgent.allow=<agents-array>` (the list comes from the snap's `agents` label)

`tools.sessions.visibility` is **not** runtime-translated. It's a "protected"
openclaw config path — `openclaw config set` silently fails to write it (the file
isn't actually updated even though the CLI returns success). Therefore visibility
must be **baked into the scenario's `openclaw.json`** at world-build time. We ship
`"all"`. We'd prefer something narrower (agents able to message each other but not
read each other's history), but with `"self"` agents couldn't message each other
at all. `"all"` lets them talk;
the trade-off is they *can* read each other's history via `sessions_history`,
though in testing they don't unless heavily prompted. Achieving true
message-yes/read-no isolation likely needs a different mechanism (e.g. separate OS
users per agent) — open TODO.

Two openclaw settings are written defensively on every translate, since the gateway
won't function correctly without them:

- `gateway.mode=local` — gateway refuses to start otherwise.
- `gateway.controlUi.dangerouslyDisableDeviceAuth=true` — disables device pairing.
  The gateway binds loopback inside an isolated container, so the threat model that
  pairing protects against doesn't apply here, and self-approval from inside a fresh
  container is chicken-and-egg (the CLI command that approves pairing requests is
  itself a CLI request that needs pairing).

**Footgun:** never set openclaw's `tools.allow` from a feature flag — it's a replacement
allowlist that silently removes every other tool, including messaging. The translator
never writes it.

Planned (not implemented): `vegas_room`, `enforceable_contracts`, `agent_hacking`,
`asymmetric_capabilities`. Add by extending the runtime translator.

---

## Failure handling

If `snap fork` fails after the OpenRouter key is minted (e.g. `docker run` fails), the
orphan key name is written to the audit log and printed to the operator. The CLI does not
auto-rotate or disable. Reap orphans manually via the OpenRouter dashboard, or with
`env kill <name>` if the container started.

---

## Git and ghcr.io interaction

The CLI never auto-commits or auto-pushes the git repo. Ghcr.io pushes happen only on
explicit `snap take` or `snap push`. Git pushes happen never — the human runs git.

---

## Status (what's verified vs untested)

**Verified end-to-end:**
- All snap rendering (list / show / tree / note) against synthetic data
- OpenClaw invocations against a real container: `config set` (incl. the 3-write expansion
  for `agent_to_agent`), `gateway run` foreground start, readiness via
  `grep -qF '[gateway] ready' /tmp/gateway.log`, kick via
  `openclaw agent --agent <id> --message <text> --deliver`
- OCI label round-trip, SQLite schema, audit log

**Code complete, not yet exercised live** (needs real credentials):
- OpenRouter key mint / get / topup / disable
- ghcr.io push, pull, registry-API label reads
- Full `snap fork` against a real world snap — first real run will surface any remaining
  issues in the external integrations

---

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `AGENTSPACE_STATE_DIR` | `/var/agentspace-ctl` | Where db.sqlite and audit.log live |
| `AGENTSPACE_AUDIT_LOG` | `<STATE_DIR>/audit.log` | Override audit log path |
| `AGENTSPACE_SECRETS` | `/var/agentspace-ctl/secrets.env` | Override secrets file path |
| `OPENROUTER_PROVISIONING_KEY` | (unset) | Required for `snap fork`, `budget topup` |
| `GITHUB_TOKEN` / `GHCR_PAT` | (unset) | Required for `snap push`, `pull`, `rebuild-index` |

---

## Package layout

```
/opt/agentspace-ctl/
  zookeeper.py                ← click entry, dispatch only
  agentspace/
    db.py                      ← SQLite schema + helpers
    audit.py                   ← JSON-line audit log
    docker_host.py             ← docker CLI wrapper (localhost or ssh://host)
    oci.py                     ← OCI label read/write + ghcr.io registry API
    openrouter.py              ← key mint / get / topup / disable
    versioning.py              ← snap version assignment + tag parsing
    snap.py                    ← snap verbs
    env.py                     ← env verbs
    budget.py                  ← budget verbs
    runtimes/__init__.py       ← dispatch on snap.runtime label
    runtimes/openclaw.py       ← openclaw flag→config translate, soul, gateway, kick
```

Only `zookeeper.py` imports `click`. Other modules are plain functions, callable from
tests or programmatic use.
