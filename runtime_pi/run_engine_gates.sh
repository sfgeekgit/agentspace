#!/bin/bash
# All ENGINE gates (scen gates are separate and run only when a scen changes —
# e.g. scenarios/mafia/gate/run.sh). Prefer running just the gate that covers
# what you touched (see docs/runtime_pi.md "Gates"); this is the full sweep
# for gateway/gmlib/builder-wide changes. Zero tokens, a few minutes.
set -euo pipefail
HERE="$(dirname "$0")"
bash "$HERE/checklist/run_checklist.sh"
bash "$HERE/gm_gate/run_gm_gate.sh"
bash "$HERE/gm_gate/run_policy_gate.sh"
bash "$HERE/gm_gate/run_build_gate.sh"
python3 "$HERE/key_gate.py"
echo "ENGINE GATES: ALL PASS"
