# The PI runtime

Single source of truth for the PI runtime, agentspace's second runtime
(alongside OpenClaw — see `runtime_openclaw.md`). Runtime OCI label: `pi`.
Menu name: "PI".

**STATUS (2026-07-03): partially built.** The gateway + isolation skeleton
exists and passes its gate (27/27). Pi integration, zookeeper wiring, and the
GM interface are designed but NOT built — this runtime cannot yet be selected
in New World. Build plan and gate history live in the working notes:
`/home/cc/2026-07-03_plan_pi_runtime.md` (steps 0–1 done, steps 2–6 pending);
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
  see §6). Agents are purely reactive: no heartbeats, no polling; every
  activation is a gateway wake with a logged cause. *(Not yet integrated —
  step 2.)*
- **Optionally a scen-provided GM** — deterministic control code driving the
  world through a privileged gateway API. *(Not yet built — step 4.)*

## 2. Gateway protocol (agent-facing)

Socket: `/run/gateway/gateway.sock` (mode 0666; identity via peercred, policy
enforced server-side). One JSON request line per connection, one JSON response
line back. Agents use the CLI shim `runtime_pi/pi_gateway_client.py`:

    pi_gateway_client.py send <to> <text...>     {"op":"send","to":...,"text":...}
    pi_gateway_client.py post <text...>          {"op":"post_public","text":...}
    pi_gateway_client.py read-public [--since N] {"op":"read_public","since":N}
    pi_gateway_client.py wake <to|*>             {"op":"wake","to":...}  (operator)

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
- Timeout 120s; on_wake stdout is discarded (a chatty agent must not buffer in
  the root gateway); exit code, duration, and a bounded stderr tail go to audit.
- In step 2, `on_wake` becomes the `agentd` wrapper that assembles the prompt
  sandwich and runs a Pi turn; today it's whatever the env installs (the
  checklist uses dummy shell agents).

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

## 5. Observability

Everything under `/data/gateway` (mode 0700 — unreachable by agents):

- `audit.jsonl` — every send (incl. `send_denied` with reason and
  `send_failed`), every public post/read, every wake with its causes (incl.
  operator `wake_requested` / `wake_denied`), every wake_end with rc/duration,
  and `gateway_start` (with the recovered seq). Every agent activation in the
  world has a logged cause here.
- `public.jsonl` — the public chat, append-only.
- `policy.json` — current live policy.

Plus per-agent inbox spools (delivered message files) in each home. All under
paths captured by `docker commit` → snaps stay complete observability bundles.

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

## 7. The isolation checklist (regression gate)

    bash /opt/agentspace-ctl/runtime_pi/checklist/run_checklist.sh

Throwaway container (`openclaw-sandbox:bookworm-slim`, local, `--network
none`), ~30s, zero tokens, exits nonzero on any failure. Run it after ANY
gateway change. It proves, with real `su` credentials: PM round-trip +
auto-wake + no ack ping-pong; public chat wakes nobody; 0700 homes hold;
gateway state unreachable; sender spoofing impossible; live policy / rate cap /
size cap enforced + audited; wakes serialized under burst; the operator `wake`
primitive wakes without a message and is refused to agents; and the inbox
**symlink attack is refused** (no chown/write escape). The harness is
mutation-tested: weakening a home to 755 turns the run red.

A companion script `checklist/verify_fixes.py` covers behaviors the
single-gateway gate can't reach — **gateway restart** (seq recovery / snapshot
transparency), the **reserved-name collision** (`u_operator` refused),
**fail-closed policy** on a cold gateway with a corrupt file, and the
**public rate cap**. Run it the same way:

    docker run --rm --network none --user 0:0 \
      -v /opt/agentspace-ctl/runtime_pi:/runtime_pi:ro \
      openclaw-sandbox:bookworm-slim bash -c \
      'bash /runtime_pi/checklist/setup_env.sh >/dev/null 2>&1; \
       python3 /runtime_pi/checklist/verify_fixes.py'

This is the successor of the OC-era 8-item sandbox checklist
(`learnings_2026-06-12.md`), automated.

## 8. Not built yet (do not assume)

- `agentd` / Pi integration (step 2), `runtimes/pi.py` + New World menu
  entry + operator REPL (step 3), GM API + `gmlib` (step 4), game scens
  (steps 5–6). Until step 3 lands, zookeeper knows nothing about this runtime.
