#!/bin/bash
# Real dispatcher/gateway and scripted agents, no inference and no live-world edits.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
IMAGE=${1:-pi-world:base}
docker image inspect "$IMAGE" >/dev/null
WORLD='{"has_dispatch":true,"params":{"max_turns":80,"attributes":"honesty,compassion,curiosity,mischief,generosity","npcs":"samir"}}'
SECRETS='{"roles":{"p":"player","g":"gm","m":"npc_samir","r":"reserve"}}'
docker run --rm --network none --user 0:0 -v "$REPO:/repo:ro" "$IMAGE" \
    bash -c 'bash /repo/runtime_pi/dispatch_gate/setup_world.sh \
        scenarios/recess_valdilume/dispatch/main.py "$1" scenarios/recess_valdilume/gate/moves "$2" &&
      cp -r /repo/scenarios/recess_valdilume/dispatch/. /dispatch/code/ &&
      chown -R dispatch /dispatch &&
      python3 -B /repo/scenarios/recess_valdilume/gate/integration.py' _ "$WORLD" "$SECRETS"
