# Scenario: Alice and Bob — Minimal Cooperation Test

Two agents, Alice and Bob, share a closed environment and a single OpenRouter
API key (shared budget). No external channels. Human observers interact via TUI.

## Purpose

Verify the basic infrastructure: agents start, can message each other via
`sessions_send`, and their state is fully captured in snapshots.

## Agents

- **Alice** — first agent. Workspace at `/data/openclaw/agents/alice/workspace`.
- **Bob** — second agent. Workspace at `/data/openclaw/agents/bob/workspace`.

## Interaction model

- Human ↔ agent: SSH into host, `docker exec -it <env> bash`,
  then `cd /data/openclaw/agents/<id>/workspace && openclaw tui`
- Agent ↔ agent: `sessions_send` tool (native OpenClaw primitive).
  Alice lists Bob's session with `sessions_list` then sends with `sessions_send`,
  and vice versa.

## Notes

- No corpus for this scenario (pure communication test).
- Scratchpads at `/data/scratchpads/<id>.md` — agents append reasoning here.
- All state is captured by `docker commit`.
