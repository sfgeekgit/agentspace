"""World-root builder: compose a brand-new X.0 snap from a scen + roster.

This is the thin HOST of the re-arch. It owns only the universal layer:
- generate generic agent IDs,
- ask the runtime to render its native config for N agents,
- compose per-agent seed files (persona -> SOUL.md, peers, optional role),
- bake world text + a generic kick + any scen data,
- assemble the image (run base -> cp staged tree -> commit with OCI labels),
- record provenance (non-secret -> labels; full build record incl. any secret
  role assignment -> audit.log ONLY).

Scen-specific behavior (role assignment, role briefings, validation, services)
is delegated to the scen's optional logic.py + roles/ files. A scen with neither
gets N generic agent slots (the simple2agent shape).

Scope: world-root (X.0) creation only. Builds LOCALLY and does not push — the
operator pushes (mirrors build_scenario.sh). Forking/snapshotting are unchanged.
"""

import random
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import audit, db, docker_host, oci, registry, runtimes, versioning
from .runtimes import openclaw  # for DEFAULT_KICK constant

BASE_IMAGE = "agentspace:base"

# Feature flags every world root carries today (parity with simple2agent 4.0).
DEFAULT_FEATURE_FLAGS = {"agent_to_agent": True, "fs_isolation": "sandbox"}

# A world name becomes the snap's scenario identity (the tag is snap-<name>-<ver>),
# so it must be a safe tag component: lowercase letters, digits, underscore.
WORLD_NAME_PATTERN = r"^[a-z0-9_]+$"
_NAME_RE = re.compile(WORLD_NAME_PATTERN)


def valid_world_name(name: str) -> bool:
    """Single source of truth for world-name validity (used by the builder and
    the menu wizard so the two never drift)."""
    return bool(_NAME_RE.match(name))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def generate_agent_ids(n: int, rng: random.Random) -> list[str]:
    """n generic, non-sequential, unique agent IDs (HARD minimal-comms rule):
    'a' + 5 random digits. Nothing an agent could infer meaning from."""
    ids: set[str] = set()
    while len(ids) < n:
        ids.add(f"a{rng.randint(10000, 99999)}")
    return list(ids)


def _peers_md(self_id: str, all_ids: list[str]) -> str:
    """Self-describing peers file: who else is here and the key to message them.
    Minimal and factual — no framing, no 'game'. sessions_list is denied
    per-agent, so the key must be given directly."""
    peers = [a for a in all_ids if a != self_id]
    lines = [
        "# Peers",
        "",
        f"You are agent `{self_id}`. You share this world with "
        f"{len(peers)} other agent(s).",
    ]
    for other in peers:
        lines += [
            "",
            f"## {other}",
            f"- Agent ID: `{other}`",
            "- To send them a message, use the `sessions_send` tool with:",
            f'  `sessionKey = "agent:{other}:main"`',
        ]
    return "\n".join(lines) + "\n"


def _assign_roles(
    logic: Any, n: int, params: dict[str, Any], rng: random.Random
) -> list[str | None]:
    """Roles per agent from the scen's logic.assign_roles, or all-None if the
    scen defines none. Validates the result is one role per agent."""
    if logic is None or not hasattr(logic, "assign_roles"):
        return [None] * n
    roles = logic.assign_roles(n, params, rng)
    if not isinstance(roles, list) or len(roles) != n:
        raise ValueError(
            f"scen assign_roles must return a list of {n} role names, got {roles!r}"
        )
    return list(roles)


def _stage_world(
    stage: Path,
    *,
    config_text: str,
    world_md: str | None,
    kick_text: str,
    seeds: dict[str, dict[str, str]],   # agent_id -> {filename: contents}
):
    """Write the generated /data subtree into `stage` for one `docker cp`. The
    scen corpus is NOT staged here — it's copied straight into the container (it
    may be gigabytes; staging would copy it twice)."""
    data = stage / "data"
    (data / "openclaw").mkdir(parents=True, exist_ok=True)
    (data / "openclaw" / "openclaw.json").write_text(config_text, encoding="utf-8")

    if world_md is not None:
        (data / "world.md").write_text(world_md, encoding="utf-8")

    (data / "scenario_kick.txt").write_text(kick_text, encoding="utf-8")

    for agent_id, files in seeds.items():
        ws = data / "seed" / "agents" / agent_id / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        for fname, contents in files.items():
            (ws / fname).write_text(contents, encoding="utf-8")


def build_world_root(
    scen_name: str,
    roster: list[dict[str, str]],
    *,
    world_name: str | None = None,
    runtime: str = "openclaw",
    modules: tuple[str, ...] = (),
    params: dict[str, Any] | None = None,
    seed: int | None = None,
    version: str | None = None,
    host: str = "localhost",
    base_image: str = BASE_IMAGE,
) -> dict[str, Any]:
    """Build a world-root (X.0) snap LOCALLY from a scen + roster.

    roster:     list of {"model": <id>, "persona": <short_name>}, one per agent.
                Length is the agent count N.
    world_name: the snap's scenario identity (tag = snap-<world_name>-<ver>).
                Defaults to scen_name. The source scen is recorded in the build
                record + creation message.

    Returns the snap dict (also upserted into the local SQLite index). Does NOT
    push — the operator pushes afterward.
    """
    params = dict(params or {})
    actual_seed = seed if seed is not None else random.Random().randint(0, 2**31 - 1)
    rng = random.Random(actual_seed)

    identity = world_name or scen_name
    if not valid_world_name(identity):
        raise ValueError(
            f"world name must be lowercase letters/digits/underscore: {identity!r}"
        )

    # ---- resolve scen + runtime, validate the universal constraints ----
    scen = registry.load_scen(scen_name)          # raises if missing/invalid
    rt = runtimes.get(runtime)                     # raises if unknown runtime
    logic = registry.load_scen_logic(scen)         # None if no logic.py

    n = len(roster)
    if n < scen["min_agents"] or n > scen["max_agents"]:
        raise ValueError(
            f"scen {scen_name!r} needs {scen['min_agents']}–{scen['max_agents']} "
            f"agents; got {n}"
        )
    bad = [m for m in modules if m in scen["module_blacklist"]]
    if bad:
        raise ValueError(f"scen {scen_name!r} is incompatible with module(s): {bad}")
    if logic is not None and hasattr(logic, "validate"):
        msg = logic.validate(n, params)
        if msg:
            raise ValueError(f"scen {scen_name!r}: {msg}")

    # ---- roster -> concrete agents (id + model + persona + role) ----
    ids = generate_agent_ids(n, rng)
    roles = _assign_roles(logic, n, params, rng)
    agents: list[dict[str, Any]] = []
    for agent_id, slot, role in zip(ids, roster, roles):
        persona = registry.load_persona(slot["persona"])   # raises if missing
        briefing = None
        if role is not None:
            bf = scen["dir"] / "roles" / f"{role}.md"
            if not bf.is_file():
                raise ValueError(
                    f"scen {scen_name!r}: role {role!r} has no roles/{role}.md briefing"
                )
            briefing = bf.read_text(encoding="utf-8")
        agents.append(
            {
                "id": agent_id,
                "model": slot["model"],
                "persona": persona["short_name"],
                "soul_text": persona["text"],
                "role": role,
                "role_briefing": briefing,
            }
        )

    # ---- runtime-native config (OC knowledge stays in the runtime module) ----
    config_text = rt.render_config(
        [{"id": a["id"], "model": a["model"]} for a in agents]
    )

    # ---- per-agent seed files ----
    # NOTE: the persona is baked as a seed SOUL.md (copied to the workspace at
    # fork by restore_env_fs, before first turn). runtime_openclaw.md §11 verified
    # a fork-time-injected SOUL.md survives OC scaffolding; the seed path lands at
    # the same place/time, but survival through a gateway-routed first turn is not
    # yet re-verified — confirm on the first real fork.
    seeds: dict[str, dict[str, str]] = {}
    for a in agents:
        files = {"SOUL.md": a["soul_text"], "PEERS.md": _peers_md(a["id"], ids)}
        if a["role"] is not None:
            files["ROLE.md"] = a["role_briefing"]
        seeds[a["id"]] = files

    # ---- world text + kick ----
    world_md = (scen["dir"] / "world.md").read_text(encoding="utf-8") if scen["has_world"] else None
    if scen["has_kick"]:
        kick_text = (scen["dir"] / "kick.txt").read_text(encoding="utf-8").strip()
    else:
        kick_text = openclaw.DEFAULT_KICK

    # ---- version + identity ----
    version = version or versioning.next_root_version(identity)
    if not versioning.is_world_root(version):
        raise ValueError(f"world-root version must be X.0, got {version!r}")
    if db.get_snap_by_ref(identity, version) is not None:
        raise ValueError(f"snap {identity}:{version} already exists")
    snap_id = uuid.uuid4().hex
    ghcr_tag = versioning.ghcr_tag(identity, version)
    now = _now()

    # ---- assemble the image: run base -> mkdir -> cp staged tree -> commit ----
    tmp_container = f"as-build-{snap_id[:12]}"
    stage = Path(tempfile.mkdtemp(prefix="as-build-"))
    try:
        _stage_world(
            stage,
            config_text=config_text,
            world_md=world_md,
            kick_text=kick_text if kick_text.endswith("\n") else kick_text + "\n",
            seeds=seeds,
        )
        # `docker run` is INSIDE the try so a partial create is still cleaned up
        # by the finally (otherwise the container name leaks and retries collide).
        try:
            docker_host.run(host, "run", "-d", "--name", tmp_container, base_image)
            docker_host.run(host, "exec", tmp_container, "mkdir", "-p", "/data")
            docker_host.run(host, "cp", f"{stage}/data/.", f"{tmp_container}:/data")
            # Corpus copied straight from the scen dir into the container (NOT
            # staged) — it may be gigabytes; staging would copy it a second time.
            if scen["data_dir"] is not None:
                docker_host.run(host, "exec", tmp_container, "mkdir", "-p", "/data/corpus")
                docker_host.run(
                    host, "cp", f"{scen['data_dir']}/.", f"{tmp_container}:/data/corpus"
                )

            snap = _snap_dict(
                snap_id=snap_id, scenario=identity, scen=scen_name, version=version,
                ghcr_tag=ghcr_tag, now=now, runtime=runtime,
                agents=agents, default_model=agents[0]["model"],
            )
            labels = oci.make_labels(snap)
            oci.commit_with_labels(host, tmp_container, ghcr_tag, labels)
        finally:
            docker_host.run(host, "rm", "-f", tmp_container, check=False)
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    # ---- index + provenance ----
    snap["indexed_at"] = now
    snap["notes_dirty"] = 0
    db.upsert_snap(snap)

    # Full build record — the external (audit-log-only) home for everything,
    # including any secret role assignment. Non-secret bits also live in labels.
    audit.log(
        "world.create",
        f"{identity}:{version}",
        args={
            "snap_id": snap_id,
            "scen": scen_name,
            "runtime": runtime,
            "seed": actual_seed,
            "params": params,
            "modules": list(modules),
            "roster": [
                {"id": a["id"], "model": a["model"], "persona": a["persona"], "role": a["role"]}
                for a in agents
            ],
        },
    )
    return snap


def _snap_dict(
    *, snap_id, scenario, scen, version, ghcr_tag, now, runtime, agents, default_model
) -> dict[str, Any]:
    from . import __version__
    src = "" if scen == scenario else f", scen={scen}"
    return {
        "snap_id": snap_id,
        "scenario": scenario,          # world identity (== scen name if unnamed)
        "version": version,
        "parent_snap_id": None,        # world root: no parent
        "parent_version": None,
        "ghcr_tag": ghcr_tag,
        "created_at": now,
        "env_name": None,
        "creation_message": (
            f"world root: {len(agents)} agent(s), runtime={runtime}{src}, "
            f"per-agent sandboxes"
        ),
        "runtime": runtime,
        "runtime_version": None,
        "model": default_model,
        "agents": [a["id"] for a in agents],
        # Reuse soul_files (existing column) for per-agent PERSONA provenance only.
        # Role assignment is NEVER put here — it can be a secret answer key and
        # labels are readable via `docker inspect`; roles live in audit.log + the
        # agent's own ROLE.md only.
        "soul_files": {a["id"]: f"persona:{a['persona']}" for a in agents},
        "feature_flags": dict(DEFAULT_FEATURE_FLAGS),
        "budget_usd": None,
        "budget_used": None,
        "agentspace_ver": __version__,
        "notes": [],
    }
