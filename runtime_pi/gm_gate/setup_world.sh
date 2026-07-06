#!/bin/bash
# Shared gate-world setup (run as root in a throwaway container, repo ro at
# /repo). Assembles ANY scripted GM world: $1 = gm.py (repo-relative),
# $2 = world.json content, $3 = moves dir (repo-relative; one <id>.moves file
# per agent — the roster is derived from it), $4 = optional /gm/secrets.json
# content. Agents run dummy_scripted_agent.sh. Zero tokens.
set -euo pipefail
GM_PY="$1"; WORLD_JSON="$2"; MOVES_DIR="$3"; SECRETS_JSON="${4:-}"

mkdir -p /runtime_pi
cp /repo/runtime_pi/pi_gateway.py /repo/runtime_pi/pi_gateway_client.py \
   /repo/runtime_pi/agentd.py /repo/runtime_pi/gmd.py /runtime_pi/
cp /repo/agentspace/gmlib.py /runtime_pi/gmlib.py          # scen `import gmlib` resolves here

cp /repo/runtime_pi/shims/gateway /repo/runtime_pi/shims/submit /usr/local/bin/
chmod 0755 /usr/local/bin/gateway /usr/local/bin/submit

mkdir -p /world
cp "/repo/$GM_PY" /world/gm.py
printf '%s\n' "$WORLD_JSON" > /world/world.json

for f in /repo/"$MOVES_DIR"/*.moves; do
    id=$(basename "$f" .moves)
    mkdir -p "/agents/$id/inbox"
    useradd --no-user-group -M -d "/agents/$id" -s /usr/sbin/nologin "u_$id" 2>/dev/null || true
    cp "$f" "/agents/$id/moves"
    cp /repo/runtime_pi/gm_gate/dummy_scripted_agent.sh "/agents/$id/on_wake"
    chown -R "u_$id" "/agents/$id"
    chmod 700 "/agents/$id" "/agents/$id/on_wake"
done

useradd --no-user-group -M -d /gm gm 2>/dev/null || true
mkdir -p /gm
[ -n "$SECRETS_JSON" ] && printf '%s\n' "$SECRETS_JSON" > /gm/secrets.json
chown -R gm /gm; chmod 0700 /gm

mkdir -p /data/gateway /run/gateway; chmod 700 /data/gateway
python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &
for _ in $(seq 1 50); do [ -S /run/gateway/gateway.sock ] && break; sleep 0.1; done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed"; cat /var/log/gateway.log; exit 1; }
echo "gate world up: $GM_PY"
