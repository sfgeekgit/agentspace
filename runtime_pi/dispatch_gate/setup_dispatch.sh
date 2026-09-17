#!/bin/bash
# dispatcher-gate container setup (run as root, repo mounted ro at /repo). Mirrors what
# rt.bake produces: runtime + dispatchlib at /runtime_pi, the PD dispatcher at /dispatch/code, two
# dummy agents, the dedicated dispatch user + /dispatch, gateway up. Zero tokens.
set -euo pipefail

mkdir -p /runtime_pi
cp /repo/runtime_pi/pi_gateway.py /repo/runtime_pi/pi_gateway_client.py \
   /repo/runtime_pi/agentd.py /repo/runtime_pi/dispatchd.py /runtime_pi/
cp /repo/agentspace/dispatchlib.py /runtime_pi/dispatchlib.py          # scen `import dispatchlib` resolves here

cp /repo/runtime_pi/shims/gateway /repo/runtime_pi/shims/submit /usr/local/bin/
chmod 0755 /usr/local/bin/gateway /usr/local/bin/submit

mkdir -p /world
printf '{"has_dispatch": true, "params": {"rounds": 3}}\n' > /world/world.json

# Two dummy players: a1 always X, a2 always Y  (so each round is X vs Y).
for spec in a1:X a2:Y; do
    id=${spec%%:*}; mv=${spec##*:}
    mkdir -p "/agents/$id/inbox"
    useradd --no-user-group -M -d "/agents/$id" -s /usr/sbin/nologin "u_$id" 2>/dev/null || true
    printf '%s' "$mv" > "/agents/$id/move"
    cp /repo/runtime_pi/dispatch_gate/dummy_dispatch_agent.sh "/agents/$id/on_wake"
    chown -R "u_$id" "/agents/$id"
    chmod 700 "/agents/$id" "/agents/$id/on_wake"
done

useradd --no-user-group -M -d /dispatch dispatch 2>/dev/null || true
mkdir -p /dispatch/code
cp /repo/scenarios/pd/dispatch/main.py /dispatch/code/main.py
chown -R dispatch /dispatch; chmod 700 /dispatch

mkdir -p /data/gateway /run/gateway; chmod 700 /data/gateway
python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &
for _ in $(seq 1 50); do [ -S /run/gateway/gateway.sock ] && break; sleep 0.1; done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed"; cat /var/log/gateway.log; exit 1; }
echo "dispatch gate setup done"
