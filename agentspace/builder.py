"""World-root builder: compose a brand-new X.0 snap from a scen + roster.

This is the thin HOST of the re-arch. It owns only the universal layer:
- generate generic agent IDs,
- ask the runtime to render its native config for N agents,
- compose per-agent seed files (persona -> SOUL.md, peers, optional role),
- bake world text + a generic kick + any scen data,
- assemble the image (resolve the source image — the scen's pinned
  source_image or the bare runtime base -> cp staged tree -> commit with OCI
  labels + normalized container config),
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

import uuid
from datetime import datetime, timezone
from typing import Any

from . import audit, db, docker_host, oci, registry, runtimes, versioning

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

# One `sh -c` probe for prior-world state in a container/image (dirty source,
# §5.1): emits one marker word per finding. Shared with `scen env freeze`.
DIRTY_SOURCE_PROBE = (
    "grep -q '^u_' /etc/passwd && echo agent-users; "
    "[ -e /world/world.json ] && echo world-state; "
    "[ -d /data/openclaw/agents ] && echo oc-agent-state; true")

def generate_agent_ids(n: int, rng: random.Random) -> list[str]:
    """n generic, non-sequential, unique agent IDs (HARD minimal-comms rule):
    'a' + 5 random digits. Nothing an agent could infer meaning from."""
    ids: set[str] = set()
    while len(ids) < n:
        ids.add(f"a{rng.randint(10000, 99999)}")
    return sorted(ids)   # NOT list(): str hashing is per-process, so set order
                         # would re-pair ids to roles differently on a replay

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

def plan_roster(
    scen_name: str,
    n: int,
    params: dict[str, Any] | None = None,
    seed: int | None = None,
) -> tuple[int, list[str], list[str | None]]:
    """Preview the seeded half of a build: (seed, agent ids, roles).

    Pure — no docker, no writes. `build_world_root(..., seed=<returned seed>)`
    re-derives exactly these values, because it mints the same rng and makes the
    same two draws in the same order (ids, then roles). That lets the wizard
    tell the operator each agent's ROLE before collecting the roster it will be
    zipped against — without a seed or a half-consumed rng crossing the boundary.

    Anything added to build_world_root's rng draws must stay AFTER those two.
    """
    scen = registry.load_scen(scen_name)
    params = registry.validate_params(scen["params_schema"], params)
    logic = registry.load_scen_logic(scen)
    actual_seed = seed if seed is not None else random.Random().randint(0, 2**31 - 1)
    rng = random.Random(actual_seed)
    ids = generate_agent_ids(n, rng)          # draw 1 — must match build order
    roles = _assign_roles(logic, n, params, rng)   # draw 2
    return actual_seed, ids, roles


def build_world_root(
    scen_name: str,
    roster: list[dict[str, str]],
    *,
    world_name: str | None = None,
    modules: tuple[str, ...] = (),
    params: dict[str, Any] | None = None,
    seed: int | None = None,
    version: str | None = None,
    host: str = "localhost",
) -> dict[str, Any]:
    """Build a world-root (X.0) snap LOCALLY from a scen + roster.

    roster:     list of {"model": <id>, "persona": <short_name>}, one per agent.
                Length is the agent count N.
    world_name: the snap's scenario identity (tag = snap-<world_name>-<ver>).
                Defaults to scen_name. The source scen is recorded in the build
                record + creation message.
    seed:       reuse a seed from plan_roster() to get the ids/roles it previewed
                (see there); omitted = fresh random seed, recorded in audit.log.

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
    runtime = scen["runtime"]                      # declared in scenario.toml
    rt = runtimes.get(runtime)
    logic = registry.load_scen_logic(scen)         # None if no logic.py
    # Coerce/validate build-time params + fill defaults before anyone reads them.
    params = registry.validate_params(scen["params_schema"], params)

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
    # Optional scen hooks for hidden-information games (Mafia): briefings may
    # be templates instantiated per agent (partners' names filled in), and the
    # GM may need the role answer key at run time (baked to /gm/secrets.json,
    # unreadable by agents — same trust boundary as GM state).
    ids_roles = dict(zip(ids, roles))
    gm_secrets = (logic.gm_secrets(ids_roles, params, rng)
                  if logic is not None and hasattr(logic, "gm_secrets") else None)
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
            if logic is not None and hasattr(logic, "fill_briefing"):
                briefing = logic.fill_briefing(briefing, agent_id, ids_roles, params, rng)
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

    # ---- per-agent seed files (universal; runtime-specific extras — e.g.
    # OC's PEERS.md — are added by rt.bake) ----
    seeds: dict[str, dict[str, str]] = {}
    for a in agents:
        files = {"SOUL.md": a["soul_text"]}
        if a["role"] is not None:
            files["ROLE.md"] = a["role_briefing"]
        seeds[a["id"]] = files

    # ---- world text + kick ----
    world_md = (scen["dir"] / "world.md").read_text(encoding="utf-8") if scen["has_world"] else None
    if scen["has_kick"]:
        kick_text = (scen["dir"] / "kick.txt").read_text(encoding="utf-8").strip()
    else:
        kick_text = rt.DEFAULT_KICK

    # ---- version + identity ----
    version = version or versioning.next_root_version(identity)
    if not versioning.is_world_root(version):
        raise ValueError(f"world-root version must be X.0, got {version!r}")
    if db.get_snap_by_ref(identity, version) is not None:
        raise ValueError(f"snap {identity}:{version} already exists")
    snap_id = uuid.uuid4().hex
    ghcr_tag = versioning.ghcr_tag(identity, version)
    now = _now()

    # ---- resolve the source image (world-authoring design §5.1): exactly two
    # paths — scen pins a source_image (pulled; any compatible image, however
    # produced), or the bare runtime base (text-only scens: zero tax) ----
    source_image = scen["source_image"]
    if source_image:
        if docker_host.run(host, "pull", source_image, check=False).returncode != 0:
            if docker_host.run(host, "image", "inspect", source_image,
                               check=False).returncode != 0:
                raise ValueError(
                    f"source image {source_image!r}: pull failed and no local copy"
                )
            print(f"  ⚠ pull of {source_image} failed; using the local copy")
        # Advisory label check (§5.2a): warn, never gate — legal sources
        # (snaps, hand-made images) may carry no labels at all.
        src_rt = oci.parse_labels(
            oci.inspect_image_labels(host, source_image)).get("runtime")
        if src_rt is None:
            print("  ⚠ source image carries no agentspace labels — provenance unknown")
        elif src_rt != runtime:
            print(f"  ⚠ source image is labeled runtime={src_rt!r}; scen wants {runtime!r}")
        base_image = source_image
    else:
        base_image = rt.BASE_IMAGE

    # ---- assemble the image: run source -> rt.bake -> commit ----
    tmp_container = f"as-build-{snap_id[:12]}"
    # `docker run` is INSIDE the try so a partial create is still cleaned up
    # by the finally (otherwise the container name leaks and retries collide).
    try:
        # Assembly hardening (§5.4): a source image's USER/ENTRYPOINT/CMD must
        # not break root assembly; the commit below normalizes the config back.
        docker_host.run(host, "run", "-d", "--name", tmp_container,
                        "--user", "root", "--entrypoint", "/bin/sh",
                        base_image, "-c", "exec sleep infinity")
        # Hard compatibility check: the source must actually contain the
        # runtime — fail early and clearly instead of committing a broken root.
        if docker_host.run(host, "exec", tmp_container, "test", "-e",
                           rt.RUNTIME_MARKER, check=False).returncode != 0:
            raise ValueError(
                f"source image {base_image!r} does not contain the {runtime!r} "
                f"runtime (missing {rt.RUNTIME_MARKER})"
            )
        # Dirty-source warning (§5.1): prior world state in the source carries
        # forward into every root built on it. Legal — sometimes the point —
        # but never silent. (What bake resets vs. carries: HOW_TO_MAKE_WORLDS.)
        dirty = docker_host.stdout(
            host, "exec", tmp_container, "sh", "-c", DIRTY_SOURCE_PROBE).split()
        if dirty:
            print(f"  ⚠ dirty source ({', '.join(dirty)}): prior world state "
                  "carries forward into this root")
        # The runtime owns its staging layout (openclaw.json + seed
        # workspaces for OC; /world + per-agent Linux users for PI).
        rt.bake(
            host, tmp_container,
            agents=[{"id": a["id"], "model": a["model"]} for a in agents],
            seeds=seeds,
            world_md=world_md,
            kick_text=kick_text if kick_text.endswith("\n") else kick_text + "\n",
            gm_dir=(scen["dir"] / "gm") if scen["has_gm"] else None,
            params=params,
            gm_secrets=gm_secrets,
            watch=scen["watch"],
            runtime_flags=scen["runtime_flags"],
        )
        # Corpus copied straight from the scen dir into the container (NOT
        # staged) — it may be gigabytes; staging would copy it a second time.
        if scen["data_dir"] is not None:
            docker_host.run(host, "exec", tmp_container, "mkdir", "-p", "/data/corpus")
            docker_host.run(
                host, "cp", f"{scen['data_dir']}/.", f"{tmp_container}:/data/corpus"
            )

        # Snap-level `model` is a display field; a world can run different
        # models per agent, so show the shared model if uniform else "mixed"
        # (per-agent models live in the build record + baked openclaw.json).
        distinct_models = {a["model"] for a in agents}
        model_label = next(iter(distinct_models)) if len(distinct_models) == 1 else "mixed"
        snap = _snap_dict(
            snap_id=snap_id, scenario=identity, scen=scen_name, version=version,
            ghcr_tag=ghcr_tag, now=now, runtime=runtime,
            agents=agents, model_label=model_label, source_image=source_image,
        )
        labels = oci.make_labels(snap)
        # Commit-time config normalization (§5.4): the assembly hardening above
        # (and anything a source image set) must not leak into the root.
        oci.commit_with_labels(host, tmp_container, ghcr_tag, labels,
                               changes=rt.COMMIT_CHANGES)
    finally:
        docker_host.run(host, "rm", "-f", tmp_container, check=False)

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
            "source_image": source_image,
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
    *, snap_id, scenario, scen, version, ghcr_tag, now, runtime, agents,
    model_label, source_image=None
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
        "source_image": source_image,  # pinned scen env, if any (§5.1 provenance)
        "model": model_label,
        "agents": [a["id"] for a in agents],
        # Reuse soul_files (existing column) for per-agent PERSONA provenance only.
        # Role assignment is NEVER put here — it can be a secret answer key and
        # labels are readable via `docker inspect`; roles live in audit.log + the
        # agent's own ROLE.md only.
        "soul_files": {a["id"]: f"persona:{a['persona']}" for a in agents},
        "feature_flags": dict(runtimes.get(runtime).DEFAULT_FEATURE_FLAGS),
        "budget_usd": None,
        "budget_used": None,
        "agentspace_ver": __version__,
        "notes": [],
    }
