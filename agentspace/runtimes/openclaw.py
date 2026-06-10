"""OpenClaw runtime: flag translation, soul injection, gateway lifecycle, kick.

This module is the ONLY place that knows openclaw.json key names. Snap-level feature
flags are runtime-agnostic; translation to openclaw config lives here.

Footgun documented in the spec: openclaw's `tools.allow` is a REPLACEMENT allowlist,
not additive. Setting it silently nukes every other tool, including the messaging tools
required for agent-to-agent. This translator never writes `tools.allow` from feature flags.
"""

import json
import shlex
import time
from typing import Any

from .. import docker_host

NAME = "openclaw"

GATEWAY_LOG_PATH = "/tmp/gateway.log"
KICK_FILE_PATH = "/data/scenario_kick.txt"  # set by snap.cmd_fork if present


def translate_flags(
    host: str,
    container: str,
    feature_flags: dict[str, Any],
    agents: list[str],
):
    """Apply snap-level feature flags to openclaw config inside a running container.

    Idempotent and re-runnable on every env start. Source of truth is the caller's
    flags + agents list (which come from the snap's OCI labels).
    """
    # `gateway.mode=local` is a structural requirement (gateway refuses to start otherwise).
    # `gateway.controlUi.dangerouslyDisableDeviceAuth=true` bypasses device pairing — the
    # gateway is loopback-only inside an isolated container, so the threat model device
    # pairing protects against doesn't apply, and the bootstrap is chicken-and-egg from
    # inside a fresh container (CLI can't self-approve).
    # Setting both here defensively so a snap with a clobbered openclaw.json can still boot.
    writes: list[tuple[str, str]] = [
        ("gateway.mode", "local"),
        ("gateway.controlUi.dangerouslyDisableDeviceAuth", "true"),
    ]

    if feature_flags.get("agent_to_agent"):
        writes.append(("tools.agentToAgent.enabled", "true"))
        writes.append(
            ("tools.agentToAgent.allow", json.dumps(agents, separators=(",", ":")))
        )

    # tools.sessions.visibility is intentionally NOT auto-set here. The default comes
    # from the scenario's openclaw.json (baked into the world snap). We currently ship
    # "all": with "self", agents couldn't message each other at all; "all" lets them
    # talk (the desired demo) at the cost that they CAN read each other's history,
    # though they don't unless heavily prompted. Wanting message-yes/read-no likely
    # needs a different mechanism (e.g. separate OS users per agent). The
    # `sessions_visibility` feature flag overrides only if explicitly set on the snap.
    vis = feature_flags.get("sessions_visibility")
    if vis:
        writes.append(("tools.sessions.visibility", str(vis)))

    for key, value in writes:
        docker_host.exec_(host, container, "openclaw", "config", "set", key, value)


def patch_model(host: str, container: str, model_id: str):
    """Override the gateway's model setting before starting it."""
    docker_host.exec_(host, container, "openclaw", "config", "set", "model", model_id)


def inject_soul(host: str, container: str, agent_id: str, soul_path_in_container: str):
    """Path here is the in-container path after `docker cp` has already placed the file."""
    # No-op: docker cp put it at /data/openclaw/agents/<id>/workspace/SOUL.md already.
    # Kept as a hook for runtimes that need additional registration.
    pass


def start_gateway(host: str, container: str):
    """Start the gateway detached. Logs go to /tmp/gateway.log inside the container."""
    cmd = f"openclaw gateway run > {GATEWAY_LOG_PATH} 2>&1"
    docker_host.run(host, "exec", "-d", container, "sh", "-c", cmd)


def stop_gateway(host: str, container: str):
    """Best-effort stop. OpenClaw may not have a clean shutdown; pkill as fallback."""
    docker_host.run(
        host, "exec", container, "sh", "-c",
        "openclaw gateway stop 2>/dev/null || pkill -f 'openclaw gateway' || true",
        check=False,
    )


GATEWAY_READY_MARKER = "[gateway] ready"


def wait_for_gateway(host: str, container: str, timeout_s: float = 90.0):
    """Wait for the gateway to print its ready marker to /tmp/gateway.log.

    `openclaw gateway status` always exits 0 even when the gateway isn't running, so
    we can't use it as a readiness probe. The log line is reliable.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = docker_host.run(
            host, "exec", container,
            "grep", "-qF", GATEWAY_READY_MARKER, GATEWAY_LOG_PATH,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(1.0)
    raise RuntimeError(
        f"gateway did not become ready within {timeout_s}s on {container}. "
        f"See {GATEWAY_LOG_PATH} inside the container."
    )


def kick_agent(host: str, container: str, agent_id: str, message: str):
    """Send the bootstrap message that creates the agent's initial session.

    Uses `--agent <id>` (which selects the agent to run the turn) NOT `--to <id>`
    (which is a phone-number recipient field for messaging channels).
    """
    docker_host.run(
        host, "exec", container,
        "openclaw", "agent",
        "--agent", agent_id,
        "--message", message,
        "--deliver",
    )


def read_kick_message(host: str, container: str, default: str = "begin") -> str:
    """If the snap baked in a scenario-specific kick file, use it. Else default."""
    result = docker_host.run(
        host, "exec", container, "cat", KICK_FILE_PATH, check=False,
    )
    if result.returncode == 0:
        text = (result.stdout or b"").decode("utf-8", errors="replace").strip()
        if text:
            return text
    return default


def tail_gateway_log(host: str, container: str, follow: bool = False):
    """Used by env logs (no --agent)."""
    args = ["exec", container, "tail"]
    if follow:
        args.append("-f")
    args.extend(["-n", "200", GATEWAY_LOG_PATH])
    if follow:
        return docker_host.stream(host, *args)
    return docker_host.stdout(host, *args, check=False)


def tail_agent_log(host: str, container: str, agent_id: str, follow: bool = False):
    """Tail the most recent session JSONL for one agent."""
    sessions_dir = f"/data/openclaw/agents/{agent_id}/sessions"
    cmd = (
        f"latest=$(ls -t {sessions_dir}/*.jsonl 2>/dev/null | head -n1); "
        f"if [ -n \"$latest\" ]; then "
        f"  tail {'-f ' if follow else ''}-n 200 \"$latest\"; "
        f"else echo 'no sessions yet for {shlex.quote(agent_id)}'; fi"
    )
    args = ["exec", container, "sh", "-c", cmd]
    if follow:
        return docker_host.stream(host, *args)
    return docker_host.stdout(host, *args, check=False)
