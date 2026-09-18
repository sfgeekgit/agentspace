#!/bin/bash
# Plain-mode (world.json "plain": true) gate: real agentd + gateway + dispatchd
# with a fake Pi. Asserts the bare prompt, --no-tools, and reply -> submit.
set -euo pipefail
IMG="${1:-openclaw-sandbox:bookworm-slim}"
exec docker run --rm --network none --user 0:0 -v /opt/agentspace-ctl:/repo:ro "$IMG" \
    bash -c 'bash /repo/runtime_pi/plain_gate/setup.sh && python3 /repo/runtime_pi/plain_gate/plain_gate.py'
