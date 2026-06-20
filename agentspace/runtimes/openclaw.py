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

# ---- sandbox (fs_isolation) layout ----
# Per-agent sandbox containers are DooD siblings: the gateway drives the HOST
# docker daemon through a mounted socket, and the host daemon resolves mount
# paths in the HOST namespace. So agent workspaces live on the host under
# WORKSPACE_ROOT/<env_name>/ and are bind-mounted into the env container at the
# IDENTICAL absolute path. All root-needing file ops on that tree run as root
# INSIDE the env container, through its own mount — zookeeper itself stays a
# plain user (host-side it only mkdirs/rmdirs dirs it owns).
WORKSPACE_ROOT = "/var/agentspace-envs"
SANDBOX_IMAGE = "openclaw-sandbox:bookworm-slim"  # local-only; OC won't pull it
WORKSPACES_TAR_PATH = "/data/workspaces.tgz"  # written by snap take, pre-commit
SEED_DIR = "/data/seed/agents"  # baked by the scenario Dockerfile (world snaps)
CONFIG_PATH = "/data/openclaw/openclaw.json"

# Rewritten to the env name by rewrite_workspace_paths at fork (paths + prefix).
ENV_PLACEHOLDER = "__ENV__"

# Generic, non-prejudicial bootstrap message (the HARD minimal-comms rule). A
# scen overrides it by shipping its own kick.txt.
DEFAULT_KICK = "Read the .md files in your workspace."


# ---- world-root config generation ----
#
# render_config() produces a complete openclaw.json for a world root with N
# agents, parameterized by the agent list + per-agent model. It reproduces the
# hand-tuned simple2agent 4.0 structure exactly (per-agent docker sandboxes;
# message-yes/read-no isolation = visibility=all + per-agent deny of the
# session-read tools; A2A ping-pong 0; heartbeat 240m), so the carefully-won
# §2/§3/§4 fixes from docs/runtime_openclaw.md are preserved for any N.
#
# Emitted as plain JSON (a subset of OC's JSON5), with NO comments — so the
# fork-time `config set` rewrites carry no size-drop risk (§8).

def render_config(
    agents: list[dict[str, str]],
    *,
    default_model: str | None = None,
    env: str = ENV_PLACEHOLDER,
) -> str:
    """Render openclaw.json for `agents` (each: {"id", "model"}).

    Workspace paths and the sandbox containerPrefix carry the `env` placeholder
    (default __ENV__), rewritten per-env at fork by rewrite_workspace_paths.
    Per-agent `model.primary` overrides the defaults-level fallback.
    """
    if not agents:
        raise ValueError("render_config needs at least one agent")
    ids = [a["id"] for a in agents]
    if len(set(ids)) != len(ids):
        raise ValueError(f"agent ids must be unique: {ids}")
    if default_model is None:
        default_model = agents[0]["model"]

    def _entry(a: dict[str, str]) -> dict[str, Any]:
        aid = a["id"]
        return {
            "id": aid,
            "name": aid,  # generic; the name must never let an agent infer anything
            "model": {"primary": a["model"]},
            "workspace": f"{WORKSPACE_ROOT}/{env}/{aid}/workspace",
            "agentDir": f"{WORKSPACE_ROOT}/{env}/{aid}/agent",
            "sandbox": {"mode": "all", "scope": "agent", "workspaceAccess": "rw"},
            "tools": {"deny": ["sessions_history", "sessions_list", "session_status"]},
        }

    config: dict[str, Any] = {
        "gateway": {
            "mode": "local",
            "auth": {"mode": "token", "token": "agentspace"},
        },
        "agents": {
            "defaults": {
                "model": {"primary": default_model},
                "heartbeat": {"every": "240m"},
                # OC bug (§2): sessionToolsVisibility is read ONLY from the
                # defaults-level sandbox. containerPrefix must be per-env unique
                # (§4) — the placeholder is rewritten at fork.
                "sandbox": {
                    "sessionToolsVisibility": "all",
                    "docker": {"containerPrefix": f"openclaw-sbx-{env}-"},
                },
            },
            "list": [_entry(a) for a in agents],
        },
        # 0 kills the automatic A2A reply-loop (§3: budget storms + privacy leak).
        "session": {"agentToAgent": {"maxPingPongTurns": 0}},
        "tools": {
            "agentToAgent": {"enabled": True, "allow": ids},
            "sessions": {"visibility": "all"},
        },
    }
    return json.dumps(config, indent=2) + "\n"


def env_fs_root(env_name: str) -> str:
    return f"{WORKSPACE_ROOT}/{env_name}"


def rewrite_workspace_paths(host: str, container: str, env_name: str):
    """Point openclaw.json's host-namespace paths at this env's tree.

    World snaps ship the __ENV__ placeholder; experiment snaps carry the parent
    env's name. Both match the generic patterns. Done with sed on the raw file
    (NOT `openclaw config set`: comment-stripping + size-drop guard).

    Also rewrites sandbox.docker.containerPrefix: OC's sandbox name hash
    IGNORES the workspace path (verified 2026-06-12 — an env silently REUSED
    another env's sandbox, mounts and all), so the prefix must be per-env
    unique. Matching the VALUE ("openclaw-sbx-…") keeps it robust to config-set
    key requoting.
    """
    root = env_fs_root(env_name)
    sed = (
        f"sed -i -E "
        f"-e 's#{WORKSPACE_ROOT}/[^/\"]+#{root}#g' "
        f"-e 's#\"openclaw-sbx-[^\"]*\"#\"openclaw-sbx-{env_name}-\"#g' "
        f"{CONFIG_PATH}"
    )
    docker_host.run(host, "exec", container, "sh", "-c", sed)


def restore_env_fs(host: str, container: str, env_name: str):
    """Populate the env's host workspace tree (root inside the container,
    writing through the bind mount). Experiment snaps restore the tar captured
    at snap take; world snaps copy the baked seed files (PEERS.md etc.)."""
    root = env_fs_root(env_name)
    script = (
        f"if [ -f {WORKSPACES_TAR_PATH} ]; then tar -xzf {WORKSPACES_TAR_PATH} -C {root}; "
        f"elif [ -d {SEED_DIR} ]; then cp -a {SEED_DIR}/. {root}/; fi"
    )
    docker_host.run(host, "exec", container, "sh", "-c", script)


def capture_env_fs(host: str, container: str, env_name: str):
    """tar the host workspace tree INTO the container before `docker commit`,
    so the pushed image is the complete env (ghcr tag = complete env). The tar
    runs as root inside the container; entries are relative to the env root so
    a fork can extract them under a different env name."""
    root = env_fs_root(env_name)
    docker_host.run(
        host, "exec", container, "sh", "-c",
        f"tar -czf {WORKSPACES_TAR_PATH} -C {root} .",
    )


def clear_env_fs(host: str, container: str, env_name: str):
    """Delete root-owned workspace contents through the mount (the mount point
    itself can't be removed from inside; the caller rmdirs it host-side)."""
    root = env_fs_root(env_name)
    docker_host.run(
        host, "exec", container, "sh", "-c",
        f"find {root} -mindepth 1 -delete", check=False,
    )


def find_sandbox_containers(host: str, env_name: str) -> list[str]:
    """Sandbox siblings OUTLIVE the env container and must be removed at env
    kill. Names are OC-generated (openclaw-sbx-agent-<id>-<hash>) and agent ids
    repeat across envs forked from one snap, so match by mounted workspace path
    — unambiguous regardless of how OC derives the name hash."""
    root = env_fs_root(env_name)
    out = docker_host.stdout(
        host, "ps", "-a", "--filter", "name=openclaw-sbx-",
        "--format", "{{.Names}}", check=False,
    )
    matches = []
    for name in out.split():
        mounts = docker_host.stdout(
            host, "inspect", "--format",
            "{{range .Mounts}}{{.Source}}\n{{end}}", name, check=False,
        )
        if any(m == root or m.startswith(root + "/") for m in mounts.split()):
            matches.append(name)
    return matches


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
    # from the scenario's openclaw.json (baked into the world snap). We ship "all":
    # "self"/"tree" block cross-agent sessions_send entirely (gateway refuses). The
    # session-tool read path that "all" opens is closed per-agent in the scenario's
    # openclaw.json via agents.list[].tools.deny of sessions_history/sessions_list.
    # !! WARNING: that is only PARTIAL isolation. All agents in an env share one
    # !! container as the same OS user, so any agent with fs/exec tools can read a
    # !! peer's workspace, memory, and session JSONLs straight off the filesystem.
    # !! No openclaw config written here closes that; it needs per-agent sandboxing
    # !! or fs/exec tool denial (see docs/agentspace_cli.md "Feature flags").
    # The `sessions_visibility` feature flag overrides only if explicitly set on the snap.
    vis = feature_flags.get("sessions_visibility")
    if vis:
        writes.append(("tools.sessions.visibility", str(vis)))

    # `fs_isolation: "sandbox"` is intentionally NOT translated to openclaw
    # config. The sandbox block is baked into the scenario's openclaw.json
    # (same precedent as visibility — protected/bake-at-build settings); the
    # flag only tells zookeeper lifecycle code to do the host-mount mechanics
    # (snap.cmd_fork / cmd_take, env.cmd_kill). With it, the !! warning above
    # is CLOSED: each agent's fs/exec tools run in a dedicated container that
    # mounts only its own workspace.

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
    """Best-effort stop. The process name is `openclaw` (not "openclaw gateway"),
    so pkill must match exact name — `pkill -f 'openclaw gateway'` MISSES it.
    Hot reload is a lie: any config change requires this + start_gateway."""
    docker_host.run(
        host, "exec", container, "sh", "-c",
        "openclaw gateway stop 2>/dev/null; pkill -x openclaw || true",
        check=False,
    )


GATEWAY_READY_MARKER = "[gateway] ready"


def wait_for_gateway(host: str, container: str, timeout_s: float = 180.0):
    # 90s proved too tight: observed a healthy cold start taking 94s (http
    # server 42s + channel/warmup waits) — ready arrived just past the old cap.
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
