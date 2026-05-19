#!/usr/bin/env bash
# Build a scenario world snap and tag it for ghcr.io. Does NOT push.
#
# Usage:  scripts/build_scenario.sh [scenario] [version]
# Default: simple2agent 1.0
#
# After this succeeds, run `docker login ghcr.io` and then the printed push command.
# Then `python3 zookeeper.py snap rebuild-index` to import into SQLite.

set -euo pipefail

SCENARIO="${1:-simple2agent}"
VERSION="${2:-1.0}"
REPO="${GHCR_REPO:-sfgeekgit/agentspace}"
TAG="snap-${SCENARIO}-${VERSION}"
FULL_REF="ghcr.io/${REPO}:${TAG}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -f "scenarios/${SCENARIO}/Dockerfile" ]; then
    echo "error: scenarios/${SCENARIO}/Dockerfile not found" >&2
    exit 1
fi

if ! docker image inspect agentspace:base >/dev/null 2>&1; then
    echo "error: base image 'agentspace:base' not found. Build it with:" >&2
    echo "  docker build -t agentspace:base ${REPO_ROOT}" >&2
    exit 1
fi

SNAP_ID="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
CREATED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
AGENTSPACE_VER="$(python3 -c 'import sys; sys.path.insert(0, "."); from agentspace import __version__; print(__version__)' 2>/dev/null || echo "0.0.0")"

# Per-scenario metadata. Add a case here when adding a new scenario.
case "$SCENARIO" in
    simple2agent)
        AGENTS_JSON='["a87329","a90301"]'
        FEATURE_FLAGS_JSON='{"agent_to_agent":true}'
        MODEL="openrouter/anthropic/claude-haiku-4-5"
        CREATION_MSG="fresh world: 2 openclaw agents, pre-chat"
        ;;
    *)
        echo "error: unknown scenario '${SCENARIO}'. Add a case in $(basename "$0")." >&2
        exit 1
        ;;
esac

echo "Building ${FULL_REF}"
echo "  snap_id:        ${SNAP_ID}"
echo "  agentspace_ver: ${AGENTSPACE_VER}"
echo "  created_at:     ${CREATED_AT}"
echo

docker build \
    -f "scenarios/${SCENARIO}/Dockerfile" \
    -t "${FULL_REF}" \
    --label "org.agentspace.snap_id=${SNAP_ID}" \
    --label "org.agentspace.scenario=${SCENARIO}" \
    --label "org.agentspace.version=${VERSION}" \
    --label "org.agentspace.ghcr_tag=${FULL_REF}" \
    --label "org.agentspace.created_at=${CREATED_AT}" \
    --label "org.agentspace.runtime=openclaw" \
    --label "org.agentspace.agents=${AGENTS_JSON}" \
    --label "org.agentspace.feature_flags=${FEATURE_FLAGS_JSON}" \
    --label "org.agentspace.model=${MODEL}" \
    --label "org.agentspace.agentspace_ver=${AGENTSPACE_VER}" \
    --label "org.agentspace.creation_message=${CREATION_MSG}" \
    .

echo
echo "Built: ${FULL_REF}"
echo
echo "Next steps:"
echo "  1. docker login ghcr.io   # use a PAT with write:packages"
echo "  2. docker push ${FULL_REF}"
echo "  3. python3 zookeeper.py snap rebuild-index"
echo "  4. python3 zookeeper.py snap tree"
