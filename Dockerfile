FROM debian:trixie-slim

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    npm install -g openclaw@latest && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

ENV OPENCLAW_STATE_DIR=/data/openclaw
ENV OPENCLAW_CONFIG_PATH=/data/openclaw/openclaw.json

RUN mkdir -p /data/openclaw /data/messages /data/scratchpads

WORKDIR /data

# Keep the container alive so gateway and TUI can be started via docker exec.
CMD ["sleep", "infinity"]
