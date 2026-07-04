#!/bin/bash
# Step-2 gate runner: 3-agent toy world with real Pi brains (COSTS TOKENS,
# ~$0.02-0.05 on haiku). Usage:
#   bash run_toyworld.sh /path/to/openrouter_key
# Builds pi-world:step2 if missing, runs the world to quiescence, verifies the
# gate, then proves snapshot completeness via docker commit. Leaves the
# container running for inspection; remove with: docker rm -f pi-toyworld
set -euo pipefail

KEY="${1:?usage: run_toyworld.sh /path/to/openrouter_key}"
RT=/opt/agentspace-ctl/runtime_pi
IMG=pi-world:step2
NAME=pi-toyworld

docker image inspect "$IMG" >/dev/null 2>&1 || docker build -t "$IMG" "$RT/toyworld"

docker rm -f "$NAME" >/dev/null 2>&1 || true
# Network ON: agents call OpenRouter. Runtime mounted ro; key mounted ro and
# copied world-readable by setup (agents run as non-root users).
docker run -d --name "$NAME" \
    -v "$RT":/runtime_pi:ro \
    -v "$KEY":/run/key_src:ro \
    "$IMG" sleep infinity >/dev/null

docker exec "$NAME" bash /runtime_pi/toyworld/setup_toyworld.sh

echo "== waking all agents (operator wake primitive as birth) =="
docker exec "$NAME" gateway wake '*'

echo "== waiting for quiescence (audit stable 30s, max 10min) =="
last=""; stable=0
for i in $(seq 1 120); do
    sleep 5
    cur=$(docker exec "$NAME" wc -c /data/gateway/audit.jsonl | cut -d' ' -f1)
    if [ "$cur" = "$last" ]; then
        stable=$((stable + 5))
        [ "$stable" -ge 30 ] && break
    else
        stable=0
    fi
    last="$cur"
done
echo "   quiet after audit reached $cur bytes"

echo "== step-2 gate =="
docker exec "$NAME" python3 /runtime_pi/toyworld/verify_toyworld.py

echo "== snapshot completeness (docker commit captures ALL state) =="
docker commit "$NAME" pi-toyworld-snap:test >/dev/null
docker run --rm pi-toyworld-snap:test bash -c '
    set -e
    test -s /data/gateway/audit.jsonl
    test -s /data/gateway/budget.jsonl
    for a in a48291 a73056 a19467; do
        ls /agents/$a/sessions/*.jsonl >/dev/null
        test -s /agents/$a/MEMORY.md
    done
    echo "   snapshot carries audit, budget, sessions, memories: OK"'
docker rmi pi-toyworld-snap:test >/dev/null

echo "DONE — container '$NAME' left running for inspection."
