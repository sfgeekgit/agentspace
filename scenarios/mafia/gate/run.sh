#!/bin/bash
# Mafia scen gate: the same fully scripted 6-agent game under BOTH enforcement
# modes, one throwaway container each. Zero tokens, ~seconds. Uses the engine's
# shared gate harness (runtime_pi/gm_gate/setup_world.sh + scripted dummy).
# Scens MAY ship a gate/ like this one; most don't need to.
set -euo pipefail
# Default: the scen's RESOLVED source image (source_image if pinned, else the
# runtime base) — so a GM that imports env-provided libraries gates against
# the environment it will really run on. $1 overrides.
IMG="${1:-$(cd /opt/agentspace-ctl && python3 -c "
from agentspace import registry, runtimes
s = registry.load_scen('mafia')
print(s['source_image'] or runtimes.get(s['runtime']).BASE_IMAGE)")}"
docker image inspect "$IMG" >/dev/null 2>&1 || docker pull "$IMG"
SECRETS='{"roles": {"a1": "mafia", "a2": "detective", "a3": "doctor", "a4": "villager", "a5": "villager", "a6": "villager"}}'

gate() { # mode hard_bool
    local wj="{\"has_gm\": true, \"params\": {\"discussion_passes\": 1, \"hard_enforcement\": $2}}"
    docker run --rm --network none --user 0:0 -e MODE="$1" \
        -v /opt/agentspace-ctl:/repo:ro "$IMG" \
        bash -c "bash /repo/runtime_pi/gm_gate/setup_world.sh \
                     scenarios/mafia/gm/main.py '$wj' scenarios/mafia/gate/moves '$SECRETS' \
                 && python3 /repo/scenarios/mafia/gate/gate.py"
}

gate hard true
gate soft false
echo "MAFIA GATE: ALL PASS (both modes)"
