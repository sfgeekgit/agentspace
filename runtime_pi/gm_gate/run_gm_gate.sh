#!/bin/bash
# GM-machinery gate runner (host side). Zero tokens, zero network. Throwaway
# container from the small local image; repo mounted ro. ~seconds.
set -euo pipefail
IMG="${1:-openclaw-sandbox:bookworm-slim}"
exec docker run --rm --network none --user 0:0 \
    -v /opt/agentspace-ctl:/repo:ro \
    "$IMG" \
    bash -c 'bash /repo/runtime_pi/gm_gate/setup_gm.sh && python3 /repo/runtime_pi/gm_gate/gm_gate.py'
