#!/bin/bash
# Shared gate-world setup (run as root in a throwaway container, repo ro at
# /repo). Assembles ANY scripted dispatcher world: $1 = the dispatcher entry file
# (repo-relative; installed as /dispatch/code/main.py — a scen's dispatch/main.py or a
# single-file gate fixture), $2 = world.json content, $3 = moves dir
# (repo-relative; one <id>.moves file per agent — the roster is derived from
# it), $4 = optional /dispatch/secrets.json content. Agents run
# dummy_scripted_agent.sh. Zero tokens.
set -euo pipefail
DISPATCH_PY="$1"; WORLD_JSON="$2"; MOVES_DIR="$3"; SECRETS_JSON="${4:-}"

mkdir -p /runtime_pi
cp /repo/runtime_pi/pi_gateway.py /repo/runtime_pi/pi_gateway_client.py \
   /repo/runtime_pi/agentd.py /repo/runtime_pi/dispatchd.py /runtime_pi/
cp /repo/agentspace/dispatchlib.py /runtime_pi/dispatchlib.py          # scen `import dispatchlib` resolves here

cp /repo/runtime_pi/shims/gateway /repo/runtime_pi/shims/submit /usr/local/bin/
chmod 0755 /usr/local/bin/gateway /usr/local/bin/submit

mkdir -p /world
printf '%s\n' "$WORLD_JSON" > /world/world.json

for f in /repo/"$MOVES_DIR"/*.moves; do
    id=$(basename "$f" .moves)
    mkdir -p "/agents/$id/inbox"
    useradd --no-user-group -M -d "/agents/$id" -s /usr/sbin/nologin "u_$id" 2>/dev/null || true
    cp "$f" "/agents/$id/moves"
    cp /repo/runtime_pi/dispatch_gate/dummy_scripted_agent.sh "/agents/$id/on_wake"
    chown -R "u_$id" "/agents/$id"
    chmod 700 "/agents/$id" "/agents/$id/on_wake"
done

useradd --no-user-group -M -d /dispatch dispatch 2>/dev/null || true
mkdir -p /dispatch/code
cp "/repo/$DISPATCH_PY" /dispatch/code/main.py
[ -n "$SECRETS_JSON" ] && printf '%s\n' "$SECRETS_JSON" > /dispatch/secrets.json
chown -R dispatch /dispatch; chmod 0700 /dispatch

mkdir -p /data/gateway /run/gateway; chmod 700 /data/gateway
python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &
for _ in $(seq 1 50); do [ -S /run/gateway/gateway.sock ] && break; sleep 0.1; done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed"; cat /var/log/gateway.log; exit 1; }
echo "gate world up: $DISPATCH_PY"
