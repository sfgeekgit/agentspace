FROM debian:trixie-slim

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_24.x | bash - && \
    apt-get install -y --no-install-recommends nodejs emacs-nox procps && \
    npm install -g openclaw@latest && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Docker CLI only (static client, no daemon) — OC sandbox mode shells out to
# `docker`, which talks to the HOST daemon via the socket mounted at fork time.
RUN curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz \
    | tar -xz -C /usr/local/bin --strip-components=1 docker/docker

ENV OPENCLAW_STATE_DIR=/data/openclaw
ENV OPENCLAW_CONFIG_PATH=/data/openclaw/openclaw.json

RUN echo '(setq make-backup-files nil)' > /root/.emacs && \
    mkdir -p /data/openclaw /data/messages /data/scratchpads

WORKDIR /data

# Keep the container alive so gateway and TUI can be started via docker exec.
CMD ["sleep", "infinity"]
