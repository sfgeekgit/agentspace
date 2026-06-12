# OpenClaw runtime — operational notes, quirks, and known bugs

Scope: everything operators need to know about running the **openclaw runtime**
(`runtimes/openclaw.py`). The agentspace platform itself is runtime-agnostic; all
OpenClaw-specific knowledge is quarantined here and in code comments. A future
runtime gets its own `docs/runtime_<name>.md`.

Facts below were verified on **OpenClaw 2026.5.12** (the version baked into current
snaps) in May–June 2026. Newer OC versions may differ — re-verify before assuming.

**Meta-rule (re-proven repeatedly): trust gateway logs, direct tests, and OC source.
Never trust an agent's self-report about its own configuration — agents confabulate
plausible-sounding wrong explanations.**

## 1. Cross-agent messaging & session visibility

- `sessions_send` cross-agent REQUIRES `tools.sessions.visibility: "all"`. The
  values `"self"` and `"tree"` hard-block it gateway-side ("Session send visibility
  is restricted" / "No session found") — even when the sender knows the peer's exact
  session key, and even with agentToAgent enabled.
- Visibility values: `self` | `tree` (default; lineage-scoped) | `agent` (own
  sessions across channels) | `all`. Note `"agent"` is NOT cross-agent.
- Also required: `tools.agentToAgent.enabled: true` and
  `tools.agentToAgent.allow: [<agent ids>]`.
- `"all"` does NOT auto-broadcast session context between agents. The real exposure
  is the session tools themselves; close them per-agent with
  `agents.list[].tools.deny: ["sessions_history","sessions_list","session_status"]`.
  Tool filtering order: global → agent → sandbox → subagent; each level can only
  restrict further; deny beats allow.
- With `sessions_list` denied, agents can't discover peers. Give them the canonical
  session key directly (e.g. in a workspace file): `agent:<agentId>:main` — keys are
  deterministic. `sessions_send` with an explicit key works.
- `sessions_send` wakes a dormant recipient (no TUI needed), but sessions must EXIST
  first → kick each agent once (`openclaw agent --agent <id> --message "<txt>"
  --deliver`) before agents can reach each other.

## 2. BUG (2026.5.12): sessionToolsVisibility read only from defaults

With sandboxing enabled, OC clamps session visibility to "tree" unless
`sandbox.sessionToolsVisibility: "all"` — and it reads that key ONLY from
`agents.defaults.sandbox`. The per-agent
`agents.list[].sandbox.sessionToolsVisibility` is SILENTLY IGNORED (verified in
dist source). Symptom: every cross-agent send fails "No session found" for sessions
that demonstrably exist. Fix: set it at the defaults level; keep
mode/scope/workspaceAccess per-agent.

## 3. Footgun (2026.5.12): automatic A2A ping-pong + reply harvesting

EVERY cross-agent `sessions_send` launches an automatic flow (verified in source):
(1) target's turn runs, reply captured; (2) up to
`session.agentToAgent.maxPingPongTurns` (DEFAULT 5, max 20) automatic alternating
reply-turns between the agents (an agent stops it by replying exactly
`REPLY_SKIP`); (3) one "announce step" turn on the target (`ANNOUNCE_SKIP` to stay
silent).

Consequences observed: rapid message storms (budget burn), and a PRIVACY LEAK — the
flow harvests the target session's LATEST assistant reply, so if a human chats with
the target agent during the window, that human-directed reply is delivered to the
peer agent. Race-prone by design.

Fix: `session.agentToAgent.maxPingPongTurns: 0`. Verified clean (single delivery,
no cascade, no leak). Residual with 0: the target still gets ONE announce-step turn
per send (not configurable in 2026.5.12; harmless without external channels; costs
one model turn). Cosmetic: A2A-path assistant entries are logged twice in the
session jsonl.

## 4. Sandboxing (per-agent filesystem isolation)

- Config: per-agent `sandbox: { mode:"all", scope:"agent", workspaceAccess:"rw" }`
  plus the defaults-level `sessionToolsVisibility` (§2).
- Effect: that agent's exec/read/write/edit/apply_patch/process run in a DEDICATED
  container mounting ONLY its own workspace at /workspace. Verified: peer workspaces
  and the gateway's state dir are unreachable from inside; the docker socket and
  gateway config are invisible. Session tools are NOT sandboxed (they run
  gateway-side).
- Topology is DooD (docker-outside-of-docker): the gateway uses the HOST docker
  daemon via a mounted `/var/run/docker.sock`; sandbox containers are SIBLINGS of
  the env container, named `openclaw-sbx-agent-<id>-<hash>` (deterministic; gateway
  restarts REUSE them; they OUTLIVE the env container — lifecycle code must remove
  them explicitly).
- PATH RULE: the host daemon resolves mount paths in the HOST namespace → agent
  workspaces must live on the host AND be mounted into the env container at the
  IDENTICAL absolute path; openclaw.json must use those host paths.
- OC shells out to the `docker` CLI (error if absent: "Sandbox mode requires
  Docker..."); the static client binary alone suffices — no daemon inside the env.
- The default sandbox image `openclaw-sandbox:bookworm-slim` is LOCAL-ONLY (not on
  any registry; the npm package doesn't ship the build script). Build it on the host
  daemon from the official Dockerfile: debian:bookworm-slim + bash, ca-certificates,
  curl, git, jq, python3, ripgrep; user `sandbox`; CMD sleep infinity. Plain
  bookworm-slim is REFUSED (python3 needed for write/edit helpers). A custom image
  is configurable via the sandbox `docker.image` key.
- The sandbox writes into mounted workspace dirs as root → host-side tar/find of
  workspaces needs root.

## 5. Sessions & memory

- Canonical session key: `agent:<agentId>:main`. Session JSONLs and the
  sessions.json index live at `/data/openclaw/agents/<id>/sessions/` INSIDE the env
  container — the `agentDir` config is NOT honored for sessions in 2026.5.12 (only
  models.json lands in the configured agentDir). Snapshot design implication:
  committing the container captures sessions; capturing host-mounted workspaces
  requires a separate tar.
- DAILY SESSION RESET (by design): `session.reset.mode` defaults to "daily" — the
  `:main` session rolls to a new one across idle/day boundaries. Conversational
  memory does NOT persist across idle gaps, even within one container. Durable agent
  memory must be written to workspace files (soul/memory/journal); the reset mode is
  configurable if longer sessions are wanted.
- Sessions survive docker stop/start and commit/re-run (verified; old jsonl remains
  intact). Only the reset policy ends them.

## 6. Heartbeat (idle budget burn)

- Default: every 30 minutes PER AGENT, and each poll is a real model call. An idle
  2-agent env burned ~$2/night this way.
- Config: `agents.defaults.heartbeat.every` (or per-agent). `"0"` disables (verified
  in source: falsy interval → disabled). This project's scenario default is
  `"240m"`. Disabling does NOT affect kicks, TUI chat, or sessions_send — gateway
  routing wakes agents regardless.
- Cheap-idle knobs also exist: `heartbeat.model` (route polls to a cheaper model),
  `activeHours`, `lightContext`, `skipWhenBusy`, `includeSystemPromptSection`.
- An empty HEARTBEAT.md does NOT prevent the model call. No heartbeat CLI in
  2026.5.12; config-only.

## 7. Gateway lifecycle

- Start: `openclaw gateway run` (foreground; background it, log to a file).
  Readiness: grep `[gateway] ready` in the log — `openclaw gateway status` ALWAYS
  exits 0 and is useless as a probe. Cold start ~30s (use 90s timeouts). Binds
  loopback `ws://127.0.0.1:18789`. Requires `gateway.mode: "local"` or it refuses
  to start.
- RESTART CORRECTLY: the process name is `openclaw` (not "openclaw gateway"), so
  `pkill -f "openclaw gateway"` MISSES it. Use `pgrep -x openclaw`, kill that pid,
  rerun. A second `gateway run` while one lives fails ("already running, lock
  timeout", port busy) without harming the original.
- HOT RELOAD IS A LIE: the gateway logs "[reload] config change detected" but the
  change does NOT take effect (verified). Always restart the gateway after config
  changes. (`kill -9` leaves a harmless zombie if PID1 doesn't reap.)

## 8. Config file gotchas

- `openclaw config set` REWRITES openclaw.json and STRIPS all JSON5 comments; OC
  rejects writes that shrink the file by more than roughly half ("Config write
  rejected (size-drop)", saving a .rejected file). Keep comments in baked configs
  SHORT — a large comment block can make the first programmatic write fail.
  (Observed: 56% shrink rejected; 31–39% fine.)
- Protected paths (e.g. `tools.sessions.visibility`): `config patch` refuses;
  `config set` silently fails while the gateway is running. Bake such settings into
  openclaw.json BEFORE gateway start.
- `openclaw config validate` works inside the image — cheap build-time check.
- Tokens are auto-redacted in `config get` output (`__OPENCLAW_REDACTED__`) — read
  the file directly to inspect real values.

## 9. Tools & profiles

- DEADLY FOOTGUN: top-level `tools.allow` is a REPLACEMENT allowlist — setting it
  silently removes every other tool, including messaging. Never write it from
  feature flags. (`tools.agentToAgent.allow` is a different, safe namespace.)
- Default profile "coding": group:fs, group:runtime, group:web, group:sessions,
  group:memory, plus cron/image/etc. Profile "messaging": session tools, no
  subagent spawning.
- Without sandboxing, ALL agents' fs/exec tools run as one OS user in one container
  → agents can read each other's workspaces and session files. This is why the
  sandbox exists for multi-agent privacy.

## 10. Auth & device pairing

- `gateway.auth`: mode "token" + a static token. Without a static token the gateway
  mints a random one per start and the TUI can't connect.
- Device pairing (2026.5.12) is chicken-and-egg in fresh containers (the approve
  command itself needs pairing scope) → set
  `gateway.controlUi.dangerouslyDisableDeviceAuth: true`. Acceptable here: the
  gateway is loopback-only inside an isolated container. NEVER copy pairing files
  (devices/paired.json, identity/device*.json) into published snaps — that leaks a
  private key.

## 11. Driving agents manually

- `openclaw tui --token <token>` from inside the agent's workspace dir (the TUI has
  no --agent flag; CWD selects the agent). `openclaw chat` is an alias for
  `tui --local` = embedded mode WITHOUT gateway tools — don't use it here.
- One-shot turn / kick: `openclaw agent --agent <id> --message "<txt>" --deliver`.
- First interaction scaffolds the workspace (SOUL.md, IDENTITY.md, BOOTSTRAP.md,
  AGENTS.md, HEARTBEAT.md, TOOLS.md, USER.md, .openclaw/) — expect a slow first
  turn. Pre-existing extra files (e.g. PEERS.md) SURVIVE scaffolding (verified). A
  pre-baked SOUL.md gets OVERWRITTEN — ship world snaps with empty workspaces and
  inject custom souls at fork time (`snap fork --soul`; whether the injected soul
  survives scaffolding is still untested). The scaffolded default soul is OC's stock
  template.
- `NO_REPLY` / `REPLY_SKIP` / `ANNOUNCE_SKIP` are suppress-delivery tokens agents
  emit; the TUI may display them truncated — cosmetic.
- Agent budget self-check (works in-container with the injected key):
  `curl -s https://openrouter.ai/api/v1/auth/key -H "Authorization: Bearer $OPENROUTER_API_KEY"`

## 12. Verification status

All of the above marked "verified" was confirmed 2026-06-11 on a hand-built
prototype env (OC 2026.5.12, two agents, per-agent sandboxes): cross-agent
messaging clean with both §2 and §3 fixes applied; peer history tools absent;
peer workspaces unreadable from inside a sandbox; docker socket and gateway config
invisible; snapshot round-trip (container commit + workspace tar) restores a
working env with state intact.
