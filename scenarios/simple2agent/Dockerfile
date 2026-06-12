FROM agentspace:base

COPY scenarios/simple2agent/openclaw.json /data/openclaw/openclaw.json
COPY scenarios/simple2agent/world.md /data/world.md
# Scenario-specific kick message, read by read_kick_message() at fork time.
COPY scenarios/simple2agent/kick.txt /data/scenario_kick.txt
# 4.0: agent workspaces live on the HOST (per-agent sandbox bind mounts), so
# seed files are baked under /data/seed and copied out to the host tree by
# `snap fork`. PEERS.md gives each agent its peer's canonical session key
# (agent:<id>:main), since sessions_list is denied per-agent in openclaw.json.
# OC's first-turn scaffolding writes its own files (SOUL.md etc.) but leaves
# PEERS.md alone. Workspaces are otherwise intentionally empty; non-default
# souls are injected at fork time via `snap fork --soul <agentId>=path`.
COPY scenarios/simple2agent/a87329/PEERS.md /data/seed/agents/a87329/workspace/PEERS.md
COPY scenarios/simple2agent/a90301/PEERS.md /data/seed/agents/a90301/workspace/PEERS.md
