#!/bin/bash
# Toy-world env setup — runs as root INSIDE the pi-world:step2 container.
# Expects /runtime_pi mounted (ro) and the OpenRouter key at /run/key_src (ro).
# Creates 3 agents with seeded homes, installs the CLI shims, starts the
# gateway. (Step 3's builder.py will do this properly at image build time.)
set -euo pipefail

AGENTS="a48291 a73056 a19467"
TW=/runtime_pi/toyworld

# --- /world: config + seeds + key (key world-readable like OC's env key) ---
mkdir -p /world
cp "$TW/world.json" /world/
cp -r "$TW/persona_default" /world/
install -m 0444 /run/key_src /world/openrouter_key

# --- CLI shims on PATH (the physics the preamble documents) ---
cat > /usr/local/bin/gateway <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /runtime_pi/pi_gateway_client.py "$@"
EOF
cat > /usr/local/bin/check_budget <<'EOF'
#!/usr/bin/env python3
import json, urllib.request
key = open("/world/openrouter_key").read().strip()
req = urllib.request.Request("https://openrouter.ai/api/v1/auth/key",
                             headers={"Authorization": "Bearer " + key})
d = json.load(urllib.request.urlopen(req, timeout=15))["data"]
print(json.dumps({"usage_usd": d.get("usage"), "limit_usd": d.get("limit")}))
EOF
chmod 0755 /usr/local/bin/gateway /usr/local/bin/check_budget

# --- agents: user + 0700 home + seeds + on_wake -> agentd ---
for A in $AGENTS; do
    useradd --no-user-group -M -d "/agents/$A" "u_$A" 2>/dev/null || true
    mkdir -p "/agents/$A"
    # Seed ROLE + FIRST_WAKE (build-time content); SOUL comes from the world seed
    # on first wake via agentd scaffolding (never-overwrite path exercised).
    cp "$TW/agents/$A/ROLE.md" "$TW/agents/$A/FIRST_WAKE.md" "/agents/$A/"
    cat > "/agents/$A/on_wake" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /runtime_pi/agentd.py
EOF
    chmod 0700 "/agents/$A/on_wake"
    chown -R "u_$A" "/agents/$A"
    chmod 0700 "/agents/$A"
done

# --- gateway ---
mkdir -p /data/gateway /run/gateway
chmod 700 /data/gateway
python3 /runtime_pi/pi_gateway.py >/var/log/gateway.log 2>&1 &
for i in $(seq 1 50); do
    [ -S /run/gateway/gateway.sock ] && break
    sleep 0.2
done
[ -S /run/gateway/gateway.sock ] || { echo "gateway failed to start"; cat /var/log/gateway.log; exit 1; }
echo "toyworld setup done: agents [$AGENTS], gateway up"
