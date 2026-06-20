"""Discovery of authored, git-tracked building blocks: scens, personas, modules.

Source of truth for these is the FILESYSTEM (the git repo) — NOT SQLite. SQLite
here is a rebuildable cache for snaps/envs (see db.py); authored content like
scen definitions and personas belongs in git and is found by scanning dirs under
the repo root.

This module is intentionally a thin read-only scanner + manifest loader. It does
NOT build worlds, validate rosters, or know anything runtime-specific — those
live in the builder and the runtime/scen code respectively.

Layout (see docs / re-arch plan):
    scenarios/<name>/scenario.toml   ← minimal manifest (required to be a scen)
    personas/<short_name>.md         ← one reusable persona; body = soul text
    modules/<name>/                  ← optional add-on (none exist yet)
"""

import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent  # /opt/agentspace-ctl
SCENARIOS_DIR = REPO_ROOT / "scenarios"
PERSONAS_DIR = REPO_ROOT / "personas"
MODULES_DIR = REPO_ROOT / "modules"

SCEN_MANIFEST = "scenario.toml"

# Sentinel upper bound when a scen omits max_agents. Generous; a scen that truly
# cares pins its own max. (Kept finite so range checks and menus stay sane.)
DEFAULT_MAX_AGENTS = 1000


class RegistryError(Exception):
    """Raised on a malformed/missing manifest when loading a specific item by name.

    List functions never raise: they skip unrecognized or invalid entries so a
    single bad directory can't break discovery.
    """


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# scens
# ---------------------------------------------------------------------------

def _normalize_scen(name: str, scen_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a raw manifest into the canonical scen dict.

    Only the minimal v1 fields are interpreted (active/description/min_agents/
    max_agents/module_blacklist). Presence of optional parts (world.md, logic.py,
    roles/, data/, kick.txt) is surfaced as booleans/paths so callers don't each
    re-stat the directory. Adding new manifest fields later is backward-safe:
    unknown keys are ignored here.
    """
    try:
        min_agents = int(data.get("min_agents", 1))
        max_agents = int(data.get("max_agents", DEFAULT_MAX_AGENTS))
    except (TypeError, ValueError) as e:
        raise RegistryError(f"scen {name!r}: min/max_agents must be integers ({e})")
    if min_agents < 1:
        raise RegistryError(f"scen {name!r}: min_agents must be >= 1")
    if max_agents < min_agents:
        raise RegistryError(
            f"scen {name!r}: max_agents ({max_agents}) < min_agents ({min_agents})"
        )

    blacklist = data.get("module_blacklist", [])
    if not isinstance(blacklist, list) or not all(isinstance(m, str) for m in blacklist):
        raise RegistryError(f"scen {name!r}: module_blacklist must be a list of strings")

    return {
        "name": name,
        "dir": scen_dir,
        "active": bool(data.get("active", True)),
        "description": str(data.get("description", "")),
        "min_agents": min_agents,
        "max_agents": max_agents,
        "module_blacklist": list(blacklist),
        # optional parts (presence only; the builder reads contents later)
        "has_world": (scen_dir / "world.md").is_file(),
        "has_logic": (scen_dir / "logic.py").is_file(),
        "has_kick": (scen_dir / "kick.txt").is_file(),
        "roles_dir": (scen_dir / "roles") if (scen_dir / "roles").is_dir() else None,
        "data_dir": (scen_dir / "data") if (scen_dir / "data").is_dir() else None,
    }


def load_scen(name: str) -> dict[str, Any]:
    """Load and normalize one scen by directory name. Raises RegistryError if the
    directory or manifest is missing/invalid. Note: this does NOT filter on
    `active` — loading a named scen always works (the menu filters on active; a
    direct/by-name load may legitimately want an inactive one)."""
    scen_dir = SCENARIOS_DIR / name
    manifest = scen_dir / SCEN_MANIFEST
    if not scen_dir.is_dir():
        raise RegistryError(f"no scen directory: scenarios/{name}")
    if not manifest.is_file():
        raise RegistryError(f"scen {name!r} has no {SCEN_MANIFEST}")
    try:
        data = _load_toml(manifest)
    except tomllib.TOMLDecodeError as e:
        raise RegistryError(f"scen {name!r}: invalid {SCEN_MANIFEST}: {e}")
    return _normalize_scen(name, scen_dir, data)


def list_scens(include_inactive: bool = False) -> list[dict[str, Any]]:
    """All discoverable scens, sorted by name. A directory is a scen iff it
    contains a readable scenario.toml — legacy/non-scen dirs are silently
    skipped, as are manifests that fail to parse (a single bad scen can't break
    the menu). Inactive scens are excluded unless include_inactive=True."""
    if not SCENARIOS_DIR.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for scen_dir in sorted(SCENARIOS_DIR.iterdir()):
        if not scen_dir.is_dir() or not (scen_dir / SCEN_MANIFEST).is_file():
            continue
        try:
            scen = load_scen(scen_dir.name)
        except RegistryError:
            continue
        if scen["active"] or include_inactive:
            out.append(scen)
    return out


# ---------------------------------------------------------------------------
# personas
# ---------------------------------------------------------------------------

def _persona_summary(text: str) -> str:
    """A short label for menus: the first meaningful line, skipping any leading
    YAML frontmatter block, markdown headings, and horizontal rules."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":  # leading frontmatter block
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1  # step past the closing ---
    for line in lines[i:]:
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= set("-*_"):
            continue  # blank, heading, or rule (---, ***, ___)
        return s
    return ""


def load_persona(short_name: str) -> dict[str, Any]:
    """Load one persona by short_name (= filename stem). Raises RegistryError if
    missing. `text` is the full soul body baked into SOUL.md at build time."""
    path = PERSONAS_DIR / f"{short_name}.md"
    if not path.is_file():
        raise RegistryError(f"no persona: personas/{short_name}.md")
    text = path.read_text(encoding="utf-8")
    return {
        "short_name": short_name,
        "path": path,
        "text": text,
        "summary": _persona_summary(text),
    }


def list_personas() -> list[dict[str, Any]]:
    """All personas, sorted by short_name. Empty if the dir is absent. Skips
    README.md and underscore/dot-prefixed files (docs/helpers, not personas)."""
    if not PERSONAS_DIR.is_dir():
        return []
    return [
        load_persona(p.stem)
        for p in sorted(PERSONAS_DIR.glob("*.md"))
        if p.stem.lower() != "readme" and not p.name.startswith(("_", "."))
    ]


# ---------------------------------------------------------------------------
# modules (reserved — none exist yet)
# ---------------------------------------------------------------------------

def list_modules() -> list[dict[str, Any]]:
    """All modules, sorted by name. Currently always empty (no modules authored).
    The New-World wizard still shows a Modules step driven by this list so the
    path can't be forgotten; a module is a directory under modules/."""
    if not MODULES_DIR.is_dir():
        return []
    return [
        {"name": d.name, "dir": d}
        for d in sorted(MODULES_DIR.iterdir())
        if d.is_dir()
    ]
