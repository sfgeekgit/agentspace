#!/bin/bash
# recess_borgo_mean scen gate: a scripted 7-turn game against the real engine in a
# throwaway container. Zero tokens. Covers: talk by display name, multi-hop move
# across the big map, premature ending flag refused, voiced-NPC note, ending on
# a flag past its min_turn (left_on_the_bus has min_turn 20 -> refused here too).
set -euo pipefail
IMG="${1:-$(cd /opt/agentspace-ctl && python3 -c "
from agentspace import registry, runtimes
s = registry.load_scen('recess_borgo_mean')
print(s['source_image'] or runtimes.get(s['runtime']).BASE_IMAGE)")}"
docker image inspect "$IMG" >/dev/null 2>&1 || docker pull "$IMG"
SECRETS='{"roles": {"p1": "player", "g1": "gm", "n1": "npc_nunzia", "n2": "npc_dario", "r1": "reserve"}}'
WJ='{"has_dispatch": true, "params": {"max_turns": 7, "attributes": "honesty,curiosity", "npcs": "nunzia,dario"}}'
docker run --rm --network none --user 0:0 -v /opt/agentspace-ctl:/repo:ro "$IMG" \
    bash -c "bash /repo/runtime_pi/dispatch_gate/setup_world.sh \
                 scenarios/recess_borgo_mean/dispatch/main.py '$WJ' scenarios/recess_borgo_mean/gate/moves '$SECRETS' \
             && cp -r /repo/scenarios/recess_borgo_mean/dispatch/. /dispatch/code/ && chown -R dispatch /dispatch \
             && python3 /repo/scenarios/recess_borgo_mean/gate/gate.py"
echo "RECESS_BORGO_MEAN GATE: ALL PASS"
