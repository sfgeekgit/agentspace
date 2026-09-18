#!/bin/bash
# Plain-mode gate world (root, throwaway container, repo ro at /repo): the REAL
# agentd as on_wake, a fake Pi binary, one agent, a two-wake dispatcher.
set -euo pipefail
mkdir -p /runtime_pi /run/svc
cp /repo/runtime_pi/pi_gateway.py /repo/runtime_pi/pi_gateway_client.py \
   /repo/runtime_pi/agentd.py /repo/runtime_pi/dispatchd.py /runtime_pi/
cp /repo/agentspace/dispatchlib.py /runtime_pi/dispatchlib.py
cp /repo/runtime_pi/plain_gate/fake_pi.py /runtime_pi/fake_pi.py; chmod 0755 /runtime_pi/fake_pi.py
cp /repo/runtime_pi/shims/gateway /repo/runtime_pi/shims/submit /usr/local/bin/; chmod 0755 /usr/local/bin/gateway /usr/local/bin/submit
echo dummy-key > /run/svc/openrouter_key; chmod 0644 /run/svc/openrouter_key

mkdir -p /world
printf '%s\n' '{"has_dispatch": true, "plain": true, "model": "fake/model", "pi_bin": "/runtime_pi/fake_pi.py", "params": {}}' > /world/world.json

mkdir -p /agents/a1/inbox
useradd --no-user-group -M -d /agents/a1 -s /usr/sbin/nologin u_a1 2>/dev/null || true
printf '# SOUL.md content\n\nYou are curious.\n' > /agents/a1/SOUL.md
printf '# The game\n\nEverything you write is your reply.\n' > /agents/a1/WORLD.md
printf '# You\n\nYou are the player.\n' > /agents/a1/ROLE.md
printf 'Welcome, this is your birth message.\n' > /agents/a1/FIRST_WAKE.md
printf 'I look around.\nI walk north, whistling.\n' > /agents/a1/moves
printf '#!/bin/sh\nexec python3 /runtime_pi/agentd.py\n' > /agents/a1/on_wake
chown -R u_a1 /agents/a1; chmod 700 /agents/a1 /agents/a1/on_wake

useradd --no-user-group -M -d /dispatch dispatch 2>/dev/null || true
mkdir -p /dispatch/code
cp /repo/runtime_pi/plain_gate/fixture_dispatch.py /dispatch/code/main.py
chown -R dispatch /dispatch; chmod 0700 /dispatch

mkdir -p /data/gateway /run/gateway; chmod 700 /data/gateway
python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &
for _ in $(seq 1 50); do [ -S /run/gateway/gateway.sock ] && break; sleep 0.1; done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed"; cat /var/log/gateway.log; exit 1; }
echo "plain gate world up"
