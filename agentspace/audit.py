"""Append-only JSON-line audit log of state-changing CLI actions."""

import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import STATE_DIR

AUDIT_PATH = Path(
    os.environ.get("AGENTSPACE_AUDIT_LOG", str(STATE_DIR / "audit.log"))
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(
    verb: str,
    target: str,
    args: dict[str, Any] | None = None,
    result: str = "ok",
    actor: str | None = None,
):
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _now_iso(),
        "actor": actor or getpass.getuser(),
        "verb": verb,
        "target": target,
        "args": args or {},
        "result": result,
    }
    with open(AUDIT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")


def log_error(verb: str, target: str, err: str, args: dict[str, Any] | None = None):
    log(verb, target, args=args, result=f"error: {err}")
