#!/bin/bash
# Policy gate runner (engine): live phase physics, dispatch_activity, fan-out at N=5.
# Zero tokens, zero network, throwaway container, ~seconds.
set -euo pipefail
IMG="${1:-openclaw-sandbox:bookworm-slim}"
exec docker run --rm --network none --user 0:0 \
    -v /opt/agentspace-ctl:/repo:ro "$IMG" \
    bash -c 'bash /repo/runtime_pi/dispatch_gate/setup_world.sh \
                 runtime_pi/dispatch_gate/policy_driver.py \
                 "{\"has_dispatch\": true, \"params\": {}}" \
                 runtime_pi/dispatch_gate/policy_moves \
                 "{\"note\": \"engine-gate secret\"}" \
             && python3 /repo/runtime_pi/dispatch_gate/policy_gate.py'
