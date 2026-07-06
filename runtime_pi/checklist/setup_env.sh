#!/bin/bash
# Checklist container setup: create agent users + 0700 homes, install dummy
# agents, start the gateway. Run as root inside the env container with the
# repo's runtime_pi/ dir mounted at /runtime_pi.
set -euo pipefail

AGENTS="a1 a2 a3"

for id in $AGENTS; do
    mkdir -p "/agents/$id/inbox"
    useradd --no-user-group -M -d "/agents/$id" -s /usr/sbin/nologin "u_$id" 2>/dev/null || true
    cp /runtime_pi/checklist/dummy_wake.sh "/agents/$id/on_wake"
    chown -R "u_$id" "/agents/$id"
    chmod 700 "/agents/$id" "/agents/$id/inbox" "/agents/$id/on_wake"
done

mkdir -p /data/gateway /run/gateway
chmod 700 /data/gateway

python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &

for i in $(seq 1 50); do
    [ -S /run/gateway/gateway.sock ] && break
    sleep 0.1
done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed to start"; cat /var/log/gateway.log; exit 1; }
echo "setup done: agents [$AGENTS], gateway up"
