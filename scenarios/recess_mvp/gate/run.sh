#!/bin/bash
# recess_mvp scen gate: a fully scripted 6-turn game (player, GM, one NPC talk,
# one bad GM submission, a new stat, a reserve assignment, a map add, an
# ending) against the real engine in a throwaway container. Zero tokens.
set -euo pipefail
IMG="${1:-$(cd /opt/agentspace-ctl && python3 -c "
from agentspace import registry, runtimes
s = registry.load_scen('recess_mvp')
print(s['source_image'] or runtimes.get(s['runtime']).BASE_IMAGE)")}"
docker image inspect "$IMG" >/dev/null 2>&1 || docker pull "$IMG"
SECRETS='{"roles": {"p1": "player", "g1": "gm", "n1": "npc_merrow", "n2": "npc_tobin", "r1": "reserve"}}'
WJ='{"has_dispatch": true, "params": {"max_turns": 7, "attributes": "honesty,curiosity", "npcs": "merrow,tobin"}}'
docker run --rm --network none --user 0:0 -v /opt/agentspace-ctl:/repo:ro "$IMG" \
    bash -c "bash /repo/runtime_pi/dispatch_gate/setup_world.sh \
                 scenarios/recess_mvp/dispatch/main.py '$WJ' scenarios/recess_mvp/gate/moves '$SECRETS' \
             && cp -r /repo/scenarios/recess_mvp/dispatch/. /dispatch/code/ && chown -R dispatch /dispatch \
             && python3 /repo/scenarios/recess_mvp/gate/gate.py"
echo "RECESS_MVP GATE: ALL PASS"
