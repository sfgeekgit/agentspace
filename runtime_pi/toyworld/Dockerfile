# Step-2 toy-world image: agentspace base (python3 + node) + Pi, EXACT-pinned.
# Build:  docker build -t pi-world:step2 /opt/agentspace-ctl/runtime_pi/toyworld
FROM agentspace:base

# --save-exact: no carets — upgrades are deliberate (docs/runtime_pi.md §6).
RUN mkdir /pi && cd /pi \
    && npm init -y >/dev/null \
    && npm install --save-exact @earendil-works/pi-coding-agent@0.80.3
