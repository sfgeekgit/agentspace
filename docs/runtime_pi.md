# The PI runtime

Single source of truth for the PI runtime, agentspace's second runtime
(alongside OpenClaw — see `runtime_openclaw.md`). Runtime OCI label: `pi`.
Menu name: "PI".

**STATUS (2026-07-03): partially built.** The broker + isolation skeleton
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
- **A privileged broker** (root, `runtime_pi/broker.py`) — the ONLY path for
  inter-agent interaction. Unix-socket daemon; sender identity comes from
  SO_PEERCRED, never from the request body, so spoofing is impossible by
  construction.
- **Per-agent brains: Pi** (`@earendil-works/pi-coding-agent`, EXACT-pinned —
  see §6). Agents are purely reactive: no heartbeats, no polling; every
  activation is a broker wake with a logged cause. *(Not yet integrated —
  step 2.)*
- **Optionally a scen-provided GM** — deterministic control code driving the
  world through a privileged broker API. *(Not yet built — step 4.)*

## 2. Broker protocol (agent-facing)

Socket: `/run/broker/broker.sock` (mode 0666; identity via peercred, policy
enforced server-side). One JSON request line per connection, one JSON response
line back. Agents use the CLI shim `runtime_pi/broker_client.py`:

    broker_client.py send <to> <text...>     {"op":"send","to":...,"text":...}
    broker_client.py post <text...>          {"op":"post_public","text":...}
    broker_client.py read-public [--since N] {"op":"read_public","since":N}

- `send` — private message. Policy check → write to recipient's inbox spool
  (`/agents/<to>/inbox/<seq>.json`, owned by recipient, 0600) → audit → wake
  recipient. Responses: `{"ok":true,"seq":N}` or `{"ok":false,"error":...}`.
- `post_public` — append to the public chat. **Wakes NOBODY** (pull-only
  surface by design; the fan-out problem does not exist here).
- `read_public` — returns entries with seq > since.
- Any `"from"` field in a request is ignored; the broker knows who you are.
- Peers with uid 0 are the **operator**: privileged, skips policy/rate checks,
  audited as `"operator"`. (The future GM API authenticates as a dedicated
  uid, NOT uid 0.)

## 3. Policy — live, no restarts

`/data/broker/policy.json`, re-read on EVERY request — editing it takes
effect on the next message with no broker restart (unlike OC's static A2A
allowlists). Fields:

    {
      "max_msg_bytes": 16384,
      "rate_limit_per_min": 30,          // per sender; hard anti-ping-pong backstop
      "allow": null,                     // null = all pairs allowed except "deny",
                                         // or a list of [from, to] pairs ("*" wildcards)
      "deny": [["a1", "a3"]]             // [from, to] pairs ("*" wildcards)
    }

Denials are audited as `send_denied` with a `reason` (policy / rate_cap /
size_cap / no_such_agent).

## 4. Wake contract

Wake = the broker spawns `/agents/<id>/on_wake` **as that agent's user** via
`subprocess.run(user=, group=, extra_groups=[])`. `extra_groups=[]` is
LOAD-BEARING: without it the child inherits root's supplementary groups and
can read peers' files.

- Env provided: `HOME`, `USER`, `AGENT_ID`, `BROKER_SOCKET`,
  `WAKE_CAUSES` (JSON list, e.g. `[{"type":"pm","from":"a1","seq":7}]`).
  (No `PI_*` prefix on purpose — that namespace belongs to the Pi tool.)
- **Serialized per agent**: at most one on_wake process per agent at a time.
  Messages arriving mid-wake are coalesced into ONE follow-up wake after the
  current one exits. A wake should drain the whole inbox, not just the
  triggering message.
- Timeout 120s; exit code, duration, and stderr tail go to the audit log.
- In step 2, `on_wake` becomes the `agentd` wrapper that assembles the prompt
  sandwich and runs a Pi turn; today it's whatever the env installs (the
  checklist uses dummy shell agents).

## 5. Observability

Everything under `/data/broker` (mode 0700 — unreachable by agents):

- `audit.jsonl` — every send (incl. denials with reason), every public
  post/read, every wake with its causes, every wake_end with rc/duration.
  Every agent activation in the world has a logged cause here.
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
broker change. It proves, with real `su` credentials: PM round-trip +
auto-wake + no ack ping-pong; public chat wakes nobody; 0700 homes hold;
broker state unreachable; sender spoofing impossible; live policy / rate cap /
size cap enforced + audited; wakes serialized under burst. (The harness is
mutation-tested: weakening a home to 755 turns the run red.)

This is the successor of the OC-era 8-item sandbox checklist
(`learnings_2026-06-12.md`), automated.

## 8. Not built yet (do not assume)

- `agentd` / Pi integration (step 2), `runtimes/pi.py` + New World menu
  entry + operator REPL (step 3), GM API + `gmlib` (step 4), game scens
  (steps 5–6). Until step 3 lands, zookeeper knows nothing about this runtime.
