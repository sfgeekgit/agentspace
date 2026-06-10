FROM agentspace:base

RUN mkdir -p /data/openclaw/agents/a87329/agent \
             /data/openclaw/agents/a87329/workspace \
             /data/openclaw/agents/a90301/agent \
             /data/openclaw/agents/a90301/workspace

COPY scenarios/simple2agent/openclaw.json /data/openclaw/openclaw.json
COPY scenarios/simple2agent/world.md /data/world.md
# Scenario-specific kick message, read by read_kick_message() at fork time.
COPY scenarios/simple2agent/kick.txt /data/scenario_kick.txt
# PEERS.md gives each agent its peer's canonical session key (agent:<id>:main),
# since sessions_list is denied per-agent in openclaw.json. OC's first-turn
# scaffolding writes its own files (SOUL.md etc.) but should leave PEERS.md alone.
COPY scenarios/simple2agent/a87329/PEERS.md /data/openclaw/agents/a87329/workspace/PEERS.md
COPY scenarios/simple2agent/a90301/PEERS.md /data/openclaw/agents/a90301/workspace/PEERS.md
# Agent workspaces are otherwise intentionally empty. OpenClaw scaffolds SOUL.md,
# IDENTITY.md, BOOTSTRAP.md, etc. on the agent's first interaction. To start with a
# non-default soul, use `snap fork --soul <agentId>=path/to/alt.md` at fork time.
