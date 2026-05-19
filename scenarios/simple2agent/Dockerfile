FROM agentspace:base

RUN mkdir -p /data/openclaw/agents/a87329/agent \
             /data/openclaw/agents/a87329/workspace \
             /data/openclaw/agents/a90301/agent \
             /data/openclaw/agents/a90301/workspace

COPY scenarios/simple2agent/openclaw.json /data/openclaw/openclaw.json
COPY scenarios/simple2agent/world.md /data/world.md
# Agent workspaces are intentionally empty. OpenClaw scaffolds SOUL.md, IDENTITY.md,
# BOOTSTRAP.md, etc. on the agent's first interaction. To start with a non-default
# soul, use `snap fork --soul <agentId>=path/to/alt.md` at fork time.
