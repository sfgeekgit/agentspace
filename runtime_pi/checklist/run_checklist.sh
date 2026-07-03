#!/bin/bash
# Step-1 gate runner (host side). Zero tokens, zero network.
# Spins up a throwaway container from the small local bookworm-slim image,
# builds the skeleton env (users/homes/gateway/dummy agents), runs the
# functional + isolation checklist, exits nonzero on any failure.
set -euo pipefail
IMG="${1:-openclaw-sandbox:bookworm-slim}"
exec docker run --rm --network none --user 0:0 \
    -v /opt/agentspace-ctl/runtime_pi:/runtime_pi:ro \
    "$IMG" \
    bash -c 'bash /runtime_pi/checklist/setup_env.sh && python3 /runtime_pi/checklist/checklist.py'
