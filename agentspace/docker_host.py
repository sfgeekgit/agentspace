"""Thin wrapper over the `docker` CLI. Handles localhost and remote SSH hosts uniformly."""

import shlex
import subprocess
from typing import Sequence

LOCALHOST = "localhost"


class DockerError(RuntimeError):
    def __init__(self, cmd: Sequence[str], returncode: int, stderr: str):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"docker exited {returncode}: {' '.join(shlex.quote(c) for c in cmd)}\n{stderr}"
        )


def _base_cmd(host: str) -> list[str]:
    if host == LOCALHOST:
        return ["docker"]
    return ["docker", "--host", f"ssh://root@{host}"]


def run(
    host: str,
    *args: str,
    input: str | bytes | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Run `docker <args>`. Raises DockerError on non-zero exit unless check=False."""
    cmd = _base_cmd(host) + list(args)
    if isinstance(input, str):
        input_bytes = input.encode("utf-8")
    else:
        input_bytes = input
    result = subprocess.run(
        cmd,
        input=input_bytes,
        capture_output=capture,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        raise DockerError(cmd, result.returncode, stderr)
    return result


def stdout(host: str, *args: str, **kwargs) -> str:
    result = run(host, *args, **kwargs)
    return (result.stdout or b"").decode("utf-8", errors="replace")


def stream(host: str, *args: str) -> subprocess.Popen:
    """For follow-style commands (`logs -f`, `events`). Caller is responsible
    for terminating. stdin is /dev/null: an inherited terminal stdin lets the
    docker client compete with the caller's UI for keystrokes."""
    cmd = _base_cmd(host) + list(args)
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


# ---- key delivery (the invariant: keys are NEVER in committed snaps) ----
#
# The env's OpenRouter key lives ONLY on a tmpfs (memory — structurally
# invisible to `docker commit`) and travels ONLY over stdin (never argv,
# never container env: `docker run -e` values persist in the image's
# .Config.Env across commit). tmpfs empties on container stop, so every
# container-start path must re-inject (fork, `env start`).

KEY_DIR = "/run/svc"  # bland on purpose: agent-visible, must not name the platform
KEY_PATH = f"{KEY_DIR}/openrouter_key"


def start_env_container(host: str, name: str, image: str, key: str,
                        extra_args: Sequence[str] = ()) -> None:
    """The ONE way env containers are started: tmpfs mounted, key injected."""
    run(host, "run", "-d", "--tmpfs", f"{KEY_DIR}:mode=755",
        "--name", name, *extra_args, image)
    inject_key(host, name, key)


def inject_key(host: str, container: str, key: str) -> None:
    """World-readable in-container by design (shared budget, check_budget).
    -u 0: the tmpfs is root-owned regardless of the image's default user."""
    run(host, "exec", "-i", "-u", "0", container, "sh", "-c",
        f"cat > {KEY_PATH} && chmod 444 {KEY_PATH}", input=key)


# ---- convenience wrappers (kept thin; snap.py / env.py do the real work) ----

def inspect(host: str, ref: str, format: str | None = None) -> str:
    args = ["inspect"]
    if format:
        args.extend(["--format", format])
    args.append(ref)
    return stdout(host, *args)


def container_running(host: str, name: str) -> bool:
    out = stdout(
        host,
        "ps",
        "--filter",
        f"name=^{name}$",
        "--filter",
        "status=running",
        "--format",
        "{{.Names}}",
        check=False,
    )
    return name in out.split()


def container_exists(host: str, name: str) -> bool:
    out = stdout(
        host,
        "ps",
        "-a",
        "--filter",
        f"name=^{name}$",
        "--format",
        "{{.Names}}",
        check=False,
    )
    return name in out.split()


def exec_(host: str, container: str, *cmd: str, check: bool = True) -> str:
    return stdout(host, "exec", container, *cmd, check=check)


def enter_command(host: str, name: str) -> str:
    """The pasteable shell command to drop into a running env's container."""
    return " ".join(_base_cmd(host or LOCALHOST)) + f" exec -it {name} bash"
