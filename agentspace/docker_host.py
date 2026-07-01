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
    """For follow-style commands (`logs -f`, `events`). Caller is responsible for terminating."""
    cmd = _base_cmd(host) + list(args)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


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
