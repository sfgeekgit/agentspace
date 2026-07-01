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


def read_entries(verb: str | None = None) -> list[dict[str, Any]]:
    """Parsed audit entries oldest→newest, optionally filtered to one verb.
    Malformed lines are skipped."""
    if not AUDIT_PATH.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if verb is None or entry.get("verb") == verb:
            out.append(entry)
    return out


# ---- runtime reconstruction ----
#
# Docker keeps no history of past start/stop cycles, so total runtime is rebuilt
# from this log's env.start / env.stop / env.kill events. Only CLI-driven
# transitions are recorded — anything done outside this CLI is invisible, so
# totals are approximate.

_STOP_VERBS = {"env.stop", "env.kill"}


def env_runtime_intervals(
    since_by_name: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-env runtime from the audit log: {name: {"closed": seconds, "open_since": dt|None}}.

    "closed" sums completed start→stop/kill sessions; "open_since" is the start of a
    still-running session, or None. A duplicate start or an unmatched stop is ignored.
    ``since_by_name`` drops events before a per-name ISO timestamp, so a name reused
    after kill + refork doesn't inherit the old env's history.
    """
    since_by_name = since_by_name or {}
    acc: dict[str, dict[str, Any]] = {}
    if not AUDIT_PATH.is_file():
        return acc
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return acc

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        verb = entry.get("verb")
        if verb != "env.start" and verb not in _STOP_VERBS:
            continue
        if not str(entry.get("result", "")).startswith("ok"):
            continue
        name = entry.get("target")
        ts_raw = entry.get("ts")
        if not name or not ts_raw:
            continue
        since = since_by_name.get(name)
        # Drop a prior env's history after a name is reused; an event at created_at
        # belongs to the new env, so compare strictly. (Fixed-width UTC ISO → lexical compare is safe.)
        if since and ts_raw < since:
            continue
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue

        state = acc.setdefault(name, {"closed": 0.0, "open_since": None})
        if verb == "env.start":
            if state["open_since"] is None:
                state["open_since"] = ts
        elif state["open_since"] is not None:
            delta = (ts - state["open_since"]).total_seconds()
            if delta > 0:
                state["closed"] += delta
            state["open_since"] = None

    return acc
