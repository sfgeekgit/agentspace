#!/bin/bash
# commons_vote scen gate: a fully scripted 3-agent, 6-round PDD game through
# the REAL stack (gateway + gmd + scen GM + vendored physics) in a throwaway
# container on the scen's pinned environment, then a host-side replay in the
# UNTOUCHED cilib repo asserting the final state matches BIT-EXACTLY.
# Zero tokens, ~30s. $1 overrides the image.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
IMG="${1:-$(cd /opt/agentspace-ctl && python3 -c "
from agentspace import registry, runtimes
s = registry.load_scen('commons_vote')
print(s['source_image'] or runtimes.get(s['runtime']).BASE_IMAGE)")}"
docker image inspect "$IMG" >/dev/null 2>&1 || docker pull "$IMG"

SECRETS='{"roles": {"a1": "cooperative", "a2": "cooperative", "a3": "cooperative"}, "physics_seed": 424242}'
WJ='{"has_gm": true, "params": {"rounds": 6, "k_proposals": 4, "n_adversarial": 0, "mechanism": "pdd"}}'
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT

# setup_world.sh installs main.py; overlay the rest of gm/ (the vendored
# packages) the same way the real bake does (it copies the whole dir).
docker run --rm --network none --user 0:0 \
    -v /opt/agentspace-ctl:/repo:ro -v "$OUT:/out" "$IMG" \
    bash -c "bash /repo/runtime_pi/gm_gate/setup_world.sh \
                 scenarios/commons_vote/gm/main.py '$WJ' \
                 scenarios/commons_vote/gate/moves '$SECRETS' \
             && cp -r /repo/scenarios/commons_vote/gm/. /gm/code/ \
             && chown -R gm /gm \
             && python3 /repo/scenarios/commons_vote/gate/gate.py"

/home/cc/cilib/.venv/bin/python "$HERE/twin_check.py" "$OUT/physics.pkl"
echo "COMMONS_VOTE GATE: ALL PASS"
