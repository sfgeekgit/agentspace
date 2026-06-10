"""SQLite cache. Source of truth is OCI labels on ghcr.io — this is rebuildable."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

STATE_DIR = Path(os.environ.get("AGENTSPACE_STATE_DIR", "/var/agentspace-ctl"))
DB_PATH = STATE_DIR / "db.sqlite"

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snaps (
    snap_id          TEXT PRIMARY KEY,
    scenario         TEXT NOT NULL,
    version          TEXT NOT NULL,
    parent_snap_id   TEXT,
    parent_version   TEXT,
    ghcr_tag         TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    env_name         TEXT,
    creation_message TEXT,
    runtime          TEXT,
    runtime_version  TEXT,
    model            TEXT,
    agents           TEXT,
    soul_files       TEXT,
    feature_flags    TEXT,
    budget_usd       REAL,
    budget_used      REAL,
    agentspace_ver   TEXT,
    notes            TEXT,
    notes_dirty      INTEGER NOT NULL DEFAULT 0,
    indexed_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snaps_scenario  ON snaps(scenario);
CREATE INDEX IF NOT EXISTS idx_snaps_parent    ON snaps(parent_snap_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_snaps_scenario_version
    ON snaps(scenario, version);

CREATE TABLE IF NOT EXISTS envs (
    name             TEXT PRIMARY KEY,
    snap_id          TEXT NOT NULL,
    container_id     TEXT,
    openrouter_key   TEXT,
    budget_usd       REAL,
    host             TEXT NOT NULL DEFAULT 'localhost',
    status           TEXT,
    created_at       TEXT NOT NULL,
    FOREIGN KEY (snap_id) REFERENCES snaps(snap_id)
);

CREATE INDEX IF NOT EXISTS idx_envs_snap ON envs(snap_id);
"""

_conn: sqlite3.Connection | None = None

JSON_FIELDS = {"agents", "soul_files", "feature_flags", "notes"}


def _ensure_state_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _ensure_state_dir()
        _conn = sqlite3.connect(DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    cur = conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()


def _encode_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for f in JSON_FIELDS:
        if f in out and not isinstance(out[f], (str, type(None))):
            out[f] = json.dumps(out[f])
    return out


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if d.get(f):
            try:
                d[f] = json.loads(d[f])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


# ---- snaps ----

def upsert_snap(snap: dict[str, Any]):
    snap = _encode_row(snap)
    cols = list(snap.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "snap_id")
    sql = (
        f"INSERT INTO snaps ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(snap_id) DO UPDATE SET {updates}"
    )
    conn = get_conn()
    conn.execute(sql, [snap[c] for c in cols])
    conn.commit()


def get_snap_by_id(snap_id: str) -> dict[str, Any] | None:
    row = get_conn().execute(
        "SELECT * FROM snaps WHERE snap_id = ?", (snap_id,)
    ).fetchone()
    return _decode_row(row)


def get_snap_by_ref(scenario: str, version: str) -> dict[str, Any] | None:
    row = get_conn().execute(
        "SELECT * FROM snaps WHERE scenario = ? AND version = ?",
        (scenario, version),
    ).fetchone()
    return _decode_row(row)


def get_snap_by_id_prefix(prefix: str) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM snaps WHERE snap_id LIKE ?", (f"{prefix}%",)
    ).fetchall()
    return [_decode_row(r) for r in rows]


def list_snaps(scenario: str | None = None) -> list[dict[str, Any]]:
    if scenario:
        rows = get_conn().execute(
            "SELECT * FROM snaps WHERE scenario = ? ORDER BY created_at",
            (scenario,),
        ).fetchall()
    else:
        rows = get_conn().execute(
            "SELECT * FROM snaps ORDER BY scenario, created_at"
        ).fetchall()
    return [_decode_row(r) for r in rows]


def get_snap_children(snap_id: str) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM snaps WHERE parent_snap_id = ? ORDER BY created_at",
        (snap_id,),
    ).fetchall()
    return [_decode_row(r) for r in rows]


def set_notes_dirty(snap_id: str, dirty: bool):
    conn = get_conn()
    conn.execute(
        "UPDATE snaps SET notes_dirty = ? WHERE snap_id = ?",
        (1 if dirty else 0, snap_id),
    )
    conn.commit()


def update_snap_notes(snap_id: str, notes: list[dict[str, Any]], dirty: bool):
    conn = get_conn()
    conn.execute(
        "UPDATE snaps SET notes = ?, notes_dirty = ? WHERE snap_id = ?",
        (json.dumps(notes), 1 if dirty else 0, snap_id),
    )
    conn.commit()


def dirty_snaps() -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM snaps WHERE notes_dirty = 1 ORDER BY scenario, version"
    ).fetchall()
    return [_decode_row(r) for r in rows]


# ---- envs ----

def upsert_env(env: dict[str, Any]):
    cols = list(env.keys())
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "name")
    sql = (
        f"INSERT INTO envs ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(name) DO UPDATE SET {updates}"
    )
    conn = get_conn()
    conn.execute(sql, [env[c] for c in cols])
    conn.commit()


def get_env(name: str) -> dict[str, Any] | None:
    row = get_conn().execute(
        "SELECT * FROM envs WHERE name = ?", (name,)
    ).fetchone()
    return dict(row) if row else None


def list_envs() -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM envs ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def delete_env(name: str):
    conn = get_conn()
    conn.execute("DELETE FROM envs WHERE name = ?", (name,))
    conn.commit()


def set_env_status(name: str, status: str, container_id: str | None = None):
    conn = get_conn()
    if container_id is None:
        conn.execute("UPDATE envs SET status = ? WHERE name = ?", (status, name))
    else:
        conn.execute(
            "UPDATE envs SET status = ?, container_id = ? WHERE name = ?",
            (status, container_id, name),
        )
    conn.commit()


# ---- maintenance ----

def reconcile_snaps(snaps: Iterable[dict[str, Any]]):
    """Used by rebuild-index after reading ghcr.io.

    Upserts all snaps from the registry, then deletes any local-only rows that
    no longer exist remotely AND have no envs referencing them. Snaps referenced
    by an active env are preserved even if they've disappeared from the registry —
    deleting them would orphan the env (and SQLite blocks it via foreign key).
    """
    conn = get_conn()
    new_snaps = list(snaps)
    new_ids = {s["snap_id"] for s in new_snaps}

    for s in new_snaps:
        upsert_snap(s)

    referenced_ids = {
        row[0] for row in conn.execute("SELECT DISTINCT snap_id FROM envs").fetchall()
    }
    for row in conn.execute("SELECT snap_id FROM snaps").fetchall():
        sid = row[0]
        if sid not in new_ids and sid not in referenced_ids:
            conn.execute("DELETE FROM snaps WHERE snap_id = ?", (sid,))

    conn.commit()
