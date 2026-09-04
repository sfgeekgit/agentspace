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

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType
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

def _optional_parts(scen_dir: Path) -> dict[str, Any]:
    """Presence of a scen's optional parts, surfaced so callers don't re-stat."""
    return {
        "has_world": (scen_dir / "world.md").is_file(),
        "has_logic": (scen_dir / "logic.py").is_file(),
        "has_kick": (scen_dir / "kick.txt").is_file(),
        "has_gm": (scen_dir / "gm" / "main.py").is_file(),   # scen ships a game master (PI)
        "roles_dir": (scen_dir / "roles") if (scen_dir / "roles").is_dir() else None,
        "data_dir": (scen_dir / "data") if (scen_dir / "data").is_dir() else None,
    }


def _normalize_scen(name: str, scen_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a raw manifest into the canonical scen dict.

    Interpreted fields: active/description/runtime/source_image/min_agents/
    max_agents/module_blacklist/params/watch. Adding new manifest fields later
    is backward-safe: unknown keys are ignored here.
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

    parts = _optional_parts(scen_dir)

    # A scen declares its runtime (REQUIRED); the wizard derives it from here
    # instead of asking. This is dispatch, not runtime knowledge: the check that
    # the name is known and supports the scen's capabilities (a gm.py needs a
    # runtime with a GM adapter) lives with the runtime modules themselves.
    runtime = data.get("runtime")
    if not isinstance(runtime, str) or not runtime:
        raise RegistryError(f'scen {name!r}: manifest must declare runtime = "<name>"')
    from . import runtimes  # lazy: keeps the scanner import-light
    try:
        rt = runtimes.get(runtime)
    except ValueError as e:
        raise RegistryError(f"scen {name!r}: {e}")
    if parts["has_gm"] and not getattr(rt, "SUPPORTS_GM", False):
        raise RegistryError(
            f"scen {name!r} ships a GM (gm/main.py) but runtime {runtime!r} has no GM support"
        )

    # OPTIONAL pinned source image (world-authoring design §5.1): any compatible
    # image, however produced; the builder pulls it and layers the scen on top.
    source_image = data.get("source_image")
    if source_image is not None and (not isinstance(source_image, str) or not source_image):
        raise RegistryError(f"scen {name!r}: source_image must be a non-empty string")

    return {
        "name": name,
        "dir": scen_dir,
        "active": bool(data.get("active", True)),
        "description": str(data.get("description", "")),
        "runtime": runtime,
        "source_image": source_image,
        "min_agents": min_agents,
        "max_agents": max_agents,
        "module_blacklist": list(blacklist),
        # Build-time param schema (decision 12): the wizard collects these; the
        # SAME scen builds different world roots per value set. Typed + defaulted;
        # see validate_params. A scen with no [[params]] gets an empty schema.
        "params_schema": list(data.get("params", [])),
        # Extra `env watch` views the scen declares ([[watch]]: name, file,
        # format, fields, filter). Declarative ONLY — no scen code runs
        # host-side; baked into world.json, interpreted by logwatch.py.
        "watch": list(data.get("watch", [])),
        **parts,
    }


def _inactive_scen_stub(name: str, scen_dir: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Minimal dict for a scen explicitly `active = false` whose manifest is
    otherwise too broken to normalize — lets it be hidden quietly instead of
    surfacing as a problem. min/max are 0 (it is never built while inactive)."""
    return {
        "name": name,
        "dir": scen_dir,
        "active": False,
        "description": str(data.get("description", "")),
        "runtime": None,
        "source_image": None,
        "min_agents": 0,
        "max_agents": 0,
        "module_blacklist": [],
        **_optional_parts(scen_dir),
    }


def load_scen(name: str) -> dict[str, Any]:
    """Load one scen by directory name. Raises RegistryError if the directory or
    manifest is missing/unparseable, or (for an ACTIVE scen) if the manifest is
    semantically invalid.

    `active = false` is honored BEFORE deep validation: a deactivated scen does
    not raise on semantic errors (so it can be hidden without first being fixed).
    An unparseable manifest still raises — its `active` can't be read."""
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
    if data.get("active") is False:
        try:
            return _normalize_scen(name, scen_dir, data)      # valid + inactive
        except RegistryError:
            return _inactive_scen_stub(name, scen_dir, data)  # broken + inactive
    return _normalize_scen(name, scen_dir, data)


def _iter_scen_dirs():
    """Yield each scenarios/ subdir that has a scenario.toml (i.e. is a scen),
    sorted by name. Non-scen dirs are not yielded."""
    if not SCENARIOS_DIR.is_dir():
        return
    for scen_dir in sorted(SCENARIOS_DIR.iterdir()):
        if scen_dir.is_dir() and (scen_dir / SCEN_MANIFEST).is_file():
            yield scen_dir


def list_scens(include_inactive: bool = False) -> list[dict[str, Any]]:
    """All loadable scens, sorted by name. Invalid scens are silently skipped (a
    single bad scen can't break discovery); use scan_scens() to also get the
    problems. Inactive scens are excluded unless include_inactive=True."""
    out: list[dict[str, Any]] = []
    for scen_dir in _iter_scen_dirs():
        try:
            scen = load_scen(scen_dir.name)
        except RegistryError:
            continue
        if scen["active"] or include_inactive:
            out.append(scen)
    return out


def scan_scens() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (active_scens, problems) in one scan.

    problems = [{"name", "reason", "can_disable"}] for scens that are NOT
    explicitly deactivated but failed to load. (active=false scens never appear,
    even if broken.) `can_disable` is True when the manifest at least parses — so
    writing active=false (deactivate_scen) will silence it; False for an
    unparseable manifest, which must be fixed/removed instead."""
    scens: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for scen_dir in _iter_scen_dirs():
        try:
            scen = load_scen(scen_dir.name)
        except RegistryError as e:
            try:
                _load_toml(scen_dir / SCEN_MANIFEST)
                can_disable = True
            except Exception:
                can_disable = False
            problems.append(
                {"name": scen_dir.name, "reason": str(e), "can_disable": can_disable}
            )
            continue
        if scen["active"]:
            scens.append(scen)
    return scens, problems


def deactivate_scen(name: str) -> None:
    """Set `active = false` in a scen's manifest via a text edit (works even when
    the manifest is semantically invalid, as long as it is a file). Replaces an
    existing `active = …` line or appends one."""
    path = SCENARIOS_DIR / name / SCEN_MANIFEST
    if not path.is_file():
        raise RegistryError(f"no manifest to edit: {path}")
    text = path.read_text(encoding="utf-8")
    new, count = re.subn(r"(?m)^(\s*)active\s*=.*$", r"\1active = false", text, count=1)
    if count == 0:
        new = text + ("" if text.endswith("\n") else "\n") + "active = false\n"
    path.write_text(new, encoding="utf-8")


def load_scen_logic(scen: dict[str, Any]) -> ModuleType | None:
    """Import a scen's optional `logic.py` and return the module, or None if the
    scen has none. Executing it runs scen-authored code — scens are first-party
    (in-repo, git-tracked), so this is intended. The module MAY define:

        assign_roles(n, params, rng) -> list[str]   # role per agent (len n)
        validate(n, params)          -> str | None  # error message, or None

    Both are optional; a scen with neither is a plain N-generic-agent scen.
    """
    if not scen.get("has_logic"):
        return None
    path = scen["dir"] / "logic.py"
    spec = importlib.util.spec_from_file_location(f"agentspace_scen_{scen['name']}", path)
    if spec is None or spec.loader is None:
        raise RegistryError(f"scen {scen['name']!r}: cannot load logic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def validate_params(schema: list[dict[str, Any]], values: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce + validate build-time params against a scen's params_schema,
    filling defaults for anything absent. Returns a clean dict; raises
    ValueError on a bad value. Handles types 'int', 'float', 'bool'; falls back
    to string — add types here as scens need them."""
    out: dict[str, Any] = {}
    values = values or {}
    for spec in schema:
        name, typ = spec["name"], spec.get("type")
        if values.get(name) is None:
            out[name] = spec.get("default")
            continue
        raw = values[name]
        if typ in ("int", "float"):
            try:
                v = int(raw) if typ == "int" else float(raw)
            except (TypeError, ValueError):
                raise RegistryError(f"param {name!r} must be a number ({typ})")
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and v < lo:
                raise RegistryError(f"param {name!r} must be >= {lo}")
            if hi is not None and v > hi:
                raise RegistryError(f"param {name!r} must be <= {hi}")
            out[name] = v
        elif typ == "bool":
            if isinstance(raw, bool):
                out[name] = raw
            elif str(raw).strip().lower() in ("true", "yes", "y", "1"):
                out[name] = True
            elif str(raw).strip().lower() in ("false", "no", "n", "0"):
                out[name] = False
            else:
                raise RegistryError(f"param {name!r} must be true/false")
        else:
            out[name] = str(raw)
    return out


# ---------------------------------------------------------------------------
# personas
# ---------------------------------------------------------------------------

SUMMARY_CHARS = 80


def _persona_summary(text: str) -> str:
    """A one-line preview for menus: meaningful body text up to SUMMARY_CHARS,
    skipping any leading YAML frontmatter block, markdown headings, and
    horizontal rules. Several lines are joined — one line alone is often a
    stray aside that says nothing about the body under it. Empty string when
    there is no body at all (the `blank` persona); callers label that."""
    lines = text.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":  # leading frontmatter block
        i = 1
        while i < len(lines) and lines[i].strip() != "---":
            i += 1
        i += 1  # step past the closing ---
    out = ""
    for line in lines[i:]:
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= set("-*_"):
            continue  # blank, heading, or rule (---, ***, ___)
        out = f"{out} {s}".strip()
        if len(out) >= SUMMARY_CHARS:
            break
    return (out[:SUMMARY_CHARS].rstrip() + "…") if len(out) > SUMMARY_CHARS else out


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
