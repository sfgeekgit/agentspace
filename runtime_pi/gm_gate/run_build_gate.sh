#!/bin/bash
# Build gate runner (engine): builder hidden-info hooks via a real throwaway
# build. Host-side (uses the local docker + snap index), zero tokens, ~30s.
set -euo pipefail
exec python3 /opt/agentspace-ctl/runtime_pi/gm_gate/build_gate.py
