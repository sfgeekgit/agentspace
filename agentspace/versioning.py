"""Version assignment for the snap lineage tree.

Each scenario has its own tree rooted at `1.0` (world snap). Children of a parent
extend the parent's version by one dotted component:

    1.0  → children are 1.1, 1.2, 1.3 ...
    1.1  → children are 1.1.1, 1.1.2 ...

The version string is the path through the tree. Assignment queries SQLite *and* the
ghcr.io registry so two operators sharing a registry don't collide.
"""

import re
from typing import Iterable

from . import db, oci

GHCR_REPO_DEFAULT = "sfgeekgit/agentspace"
SNAP_TAG_PREFIX = "snap-"

# matches: snap-<scenario>-<version> where version is digits separated by dots
_TAG_RE = re.compile(r"^snap-(?P<scenario>[^\-].*?)-(?P<version>\d+(?:\.\d+)+)$")


def ghcr_tag(scenario: str, version: str, repo: str = GHCR_REPO_DEFAULT) -> str:
    return f"ghcr.io/{repo}:{SNAP_TAG_PREFIX}{scenario}-{version}"


def parse_tag(tag: str) -> tuple[str, str] | None:
    """Inverse of ghcr_tag. Returns (scenario, version) or None."""
    short = tag.rsplit(":", 1)[-1]
    m = _TAG_RE.match(short)
    if not m:
        return None
    return m.group("scenario"), m.group("version")


def is_world_root(version: str) -> bool:
    """A version like '1.0' or '2.0' is a world-snap root (no agents have run yet)."""
    parts = version.split(".")
    return len(parts) == 2 and parts[1] == "0"


def is_child_version(parent: str, candidate: str) -> bool:
    """Children of `M.0` are `M.1, M.2, ...` (same depth — world root is special).
    Children of any other version Y are `Y.N` (appended component).
    """
    parent_parts = parent.split(".")
    cand_parts = candidate.split(".")
    if is_world_root(parent):
        return (
            len(cand_parts) == 2
            and cand_parts[0] == parent_parts[0]
            and cand_parts[1] != "0"
        )
    return (
        len(cand_parts) == len(parent_parts) + 1
        and cand_parts[:-1] == parent_parts
    )


def existing_children(
    scenario: str, parent_version: str, repo: str = GHCR_REPO_DEFAULT
) -> list[str]:
    """Children of parent_version known either locally (SQLite) or remotely (ghcr.io)."""
    versions: set[str] = set()

    parent = db.get_snap_by_ref(scenario, parent_version)
    if parent:
        for child in db.get_snap_children(parent["snap_id"]):
            versions.add(child["version"])

    try:
        tags = oci.list_registry_tags(repo)
    except Exception:
        tags = []
    for t in tags:
        parsed = parse_tag(t)
        if parsed is None:
            continue
        s, v = parsed
        if s == scenario and is_child_version(parent_version, v):
            versions.add(v)

    return sorted(versions, key=_version_key)


def next_child_version(
    scenario: str, parent_version: str, repo: str = GHCR_REPO_DEFAULT
) -> str:
    """Smallest unused child version of the parent."""
    children = existing_children(scenario, parent_version, repo)
    used_last_components = {int(v.split(".")[-1]) for v in children}
    n = 1
    while n in used_last_components:
        n += 1
    if is_world_root(parent_version):
        major = parent_version.split(".")[0]
        return f"{major}.{n}"
    return f"{parent_version}.{n}"


def next_root_version(scenario: str, repo: str = GHCR_REPO_DEFAULT) -> str:
    """Root version for a new scenario tree. Usually '1.0'; collides only if reused."""
    existing: set[str] = set()
    for s in db.list_snaps(scenario=scenario):
        if s["parent_version"] is None or s["parent_version"] == "":
            existing.add(s["version"])
    try:
        for t in oci.list_registry_tags(repo):
            parsed = parse_tag(t)
            if parsed and parsed[0] == scenario:
                # root candidate: a single dot like "1.0", "2.0"
                if parsed[1].count(".") == 1:
                    existing.add(parsed[1])
    except Exception:
        pass

    if "1.0" not in existing:
        return "1.0"
    # rare: roots like 2.0, 3.0 ... pick the next unused.
    n = 2
    while f"{n}.0" in existing:
        n += 1
    return f"{n}.0"


def _version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in v.split("."))
    except ValueError:
        return (0,)


def sort_versions(versions: Iterable[str]) -> list[str]:
    return sorted(versions, key=_version_key)
