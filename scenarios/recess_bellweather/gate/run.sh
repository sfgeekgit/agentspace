#!/bin/bash
# Disposable real-gateway integration, no inference or network; never touches live worlds.
set -euo pipefail
REPO=$(cd "$(dirname "$0")/../../.." && pwd)
IMAGE=${1:-pi-world:base}
docker image inspect "$IMAGE" >/dev/null
WORLD='{"has_dispatch":true,"params":{"max_turns":3,"attributes":"honesty,compassion,curiosity,mischief,generosity","npcs":"mara"}}'
SECRETS='{"roles":{"p":"player","g":"gm","m":"npc_mara"}}'
docker run --rm --network none --user 0:0 -v "$REPO:/repo:ro" "$IMAGE" \
    bash -c 'bash /repo/runtime_pi/dispatch_gate/setup_world.sh \
        scenarios/recess_bellweather/dispatch/main.py "$1" scenarios/recess_bellweather/gate/moves "$2" &&
      cp -r /repo/scenarios/recess_bellweather/dispatch/. /dispatch/code/ &&
      chown -R dispatch /dispatch &&
      python3 -B /repo/scenarios/recess_bellweather/gate/integration.py' _ "$WORLD" "$SECRETS"
