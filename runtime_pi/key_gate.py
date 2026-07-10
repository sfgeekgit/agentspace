#!/usr/bin/env python3
"""Key-leak gate (engine, host-side): the "keys never in snaps" invariant,
through the REAL machinery (docker_host.start_env_container + the take/push
scanner) with a FAKE key — zero tokens, ~15s. Asserts a committed env image
is clean in BOTH leak channels (filesystem and .Config env/labels/cmd) and
that /run/svc never reaches the image; then a positive control proves
the scanner actually catches a planted key.
"""
import sys
import uuid

sys.path.insert(0, "/opt/agentspace-ctl")
from agentspace import docker_host                # noqa: E402
from agentspace.snap import scan_for_key_leak     # noqa: E402

HOST = "localhost"
IMG = sys.argv[1] if len(sys.argv) > 1 else "openclaw-sandbox:bookworm-slim"
FAKE_KEY = "sk-or-v1-" + "0" * 64
FAIL = []


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  :: {extra}" if not cond else ""))
    if not cond:
        FAIL.append(name)


name = f"keygate-{uuid.uuid4().hex[:6]}"
snap_tag = f"{name}-snap"
try:
    docker_host.start_env_container(HOST, name, IMG, FAKE_KEY)
    got = docker_host.exec_(HOST, name, "cat", docker_host.KEY_PATH).strip()
    check("key delivered to tmpfs", got == FAKE_KEY)

    docker_host.run(HOST, "commit", name, snap_tag)
    leaks = scan_for_key_leak(HOST, snap_tag)
    check("committed image is key-clean (fs + config)", not leaks, str(leaks))
    empty = docker_host.stdout(
        HOST, "run", "--rm", "--entrypoint", "sh", snap_tag, "-c",
        f"ls -A {docker_host.KEY_DIR} 2>/dev/null; :")
    check(f"no {docker_host.KEY_DIR} content in image", empty.strip() == "", empty)

    # Positive control: plant the key ON the container fs; the scanner must fire.
    docker_host.exec_(HOST, name, "sh", "-c", f"cp {docker_host.KEY_PATH} /tmp/leaked")
    docker_host.run(HOST, "commit", name, snap_tag)
    check("scanner catches a planted key", scan_for_key_leak(HOST, snap_tag) != [])
finally:
    docker_host.run(HOST, "rm", "-f", name, check=False)
    docker_host.run(HOST, "rmi", "-f", snap_tag, check=False)

print("KEY GATE: " + ("ALL PASS" if not FAIL else f"FAILED {FAIL}"))
sys.exit(1 if FAIL else 0)
