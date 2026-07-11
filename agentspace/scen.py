"""Scen environment verbs: freeze, build (world-authoring design §5.1–5.3).

A scen may pin a `source_image` in its manifest — the environment its worlds
are built on. These verbs are the two PRODUCERS of that digest:

  freeze — commit a banged-on workshop container (the default authoring path)
  build  — docker build the scen's env.Dockerfile (the optional recipe path)

Both end in the same publish tail: credential/config scan, provenance labels,
push, REMOTE registry digest readback + pull verify, scenario.toml update.
The freeze verb is the v1 integrity boundary — the manual
commit/tag/push/edit sequence is exactly where the workflow goes wrong.

The builder only CONSUMES the digest (builder.py §5.1); it never builds
environments.
"""

import json
import re
import subprocess
import uuid

import click
from rich.console import Console

from . import audit, builder, docker_host, oci, registry, runtimes, versioning
from .snap import scan_for_key_leak

console = Console()

ENV_TAG_PREFIX = "env-"  # distinct from SNAP_TAG_PREFIX: never indexed as a snap


def _next_env_tag(scen_name: str, repo: str) -> str:
    """env-<scen>-<n> with the smallest unused n, checked against the registry
    (best effort — offline falls back to local images) AND local tags."""
    taken: set[int] = set()
    pat = re.compile(rf"^{ENV_TAG_PREFIX}{re.escape(scen_name)}-(\d+)$")
    try:
        tags = oci.list_registry_tags(repo)
    except Exception as e:
        console.print(f"[yellow]⚠ could not list registry tags ({e}); "
                      "numbering from local images only[/yellow]")
        tags = []
    local = docker_host.stdout(
        "localhost", "image", "ls", f"ghcr.io/{repo}",
        "--format", "{{.Tag}}", check=False).split()
    for t in list(tags) + local:
        m = pat.match(t)
        if m:
            taken.add(int(m.group(1)))
    n = 1
    while n in taken:
        n += 1
    return f"ghcr.io/{repo}:{ENV_TAG_PREFIX}{scen_name}-{n}"


def _provenance_labels(host: str, scen: dict) -> dict[str, str]:
    """§5.2a advisory labels. runtime_base records the LOCAL image ID of the
    current runtime base (the base is never pushed — decided 2026-07-10);
    enough for stranded-base detection, honest about not being pullable."""
    rt = runtimes.get(scen["runtime"])
    base_id = docker_host.stdout(
        host, "image", "inspect", "-f", "{{.Id}}", rt.BASE_IMAGE,
        check=False).strip() or "unknown"
    return {
        "org.agentspace.kind": "scen-environment",
        "org.agentspace.runtime": scen["runtime"],
        "org.agentspace.runtime_base": base_id,
        "org.agentspace.scen": scen["name"],
    }


def _scan_unintended(host: str, image: str):
    """The HARD-rule sweep the freeze verb carries (§5.2): shell histories and
    config env vars. WARNS — only key-shaped strings hard-fail (the caller's
    scan_for_key_leak). The prejudice judgment stays human."""
    hist = docker_host.stdout(
        host, "run", "--rm", "--entrypoint", "sh", image, "-c",
        "ls /root/.*history /home/*/.*history 2>/dev/null; true").split()
    if hist:
        console.print(f"[yellow]⚠ shell history in image: {', '.join(hist)} — "
                      "agents can read it; consider removing and re-freezing[/yellow]")
    env = json.loads(docker_host.inspect(host, image, format="{{json .Config.Env}}") or "[]")
    extra = [e for e in env if not e.startswith(("PATH=", "OPENCLAW_", "NODE_", "DEBIAN_"))]
    if extra:
        console.print("[yellow]⚠ image config carries env vars (docker commit "
                      f"preserves them): {', '.join(extra)} — review for secrets[/yellow]")


def _publish(host: str, image: str, scen: dict, repo: str, allow_key_leak: bool):
    """Shared tail of freeze/build: scan → push → remote digest readback →
    pull verify → scenario.toml update → audit."""
    console.print(f"[dim]scanning {image} …[/dim]")
    leaks = scan_for_key_leak(host, image)
    if leaks:
        msg = f"image carries an OpenRouter key in: {', '.join(leaks)}"
        if not allow_key_leak:
            raise click.ClickException(f"{msg}. Refused (--allow-key-leak to override).")
        console.print(f"[yellow]⚠ {msg} — continuing (--allow-key-leak).[/yellow]")
    _scan_unintended(host, image)

    console.print(f"[dim]pushing {image} …[/dim]")
    docker_host.run(host, "push", image)

    # The REGISTRY manifest digest is the pin — a local image ID cannot be
    # pulled by another machine (the round-2 identifier bug).
    digests = json.loads(docker_host.inspect(host, image, format="{{json .RepoDigests}}"))
    pinned = next((d for d in digests if d.startswith(f"ghcr.io/{repo}@")), None)
    if pinned is None:
        raise click.ClickException(f"no registry digest for {image} after push: {digests}")
    console.print(f"[dim]verifying {pinned} pulls …[/dim]")
    docker_host.run(host, "pull", pinned)

    prev = scen["source_image"]
    _write_source_image(scen, pinned)
    audit.log("scen.env_publish", scen["name"],
              args={"image": image, "digest": pinned, "previous": prev})
    console.print(f"[green]✓[/green] {scen['name']} source_image updated")
    console.print(f"    previous: {prev or '(none — was the bare runtime base)'}")
    console.print(f"    new:      {pinned}")
    return pinned


def _write_source_image(scen: dict, ref: str):
    """Set source_image in the scen's manifest: replace the existing line or
    insert after the runtime line (both are top-level keys by construction)."""
    path = scen["dir"] / registry.SCEN_MANIFEST
    text = path.read_text(encoding="utf-8")
    line = f'source_image = "{ref}"'
    new, n = re.subn(r'(?m)^source_image\s*=.*$', line, text, count=1)
    if n == 0:
        new, n = re.subn(r'(?m)^(runtime\s*=.*)$', rf'\1\n{line}', text, count=1)
    if n == 0:
        raise click.ClickException(f"could not place source_image in {path}")
    path.write_text(new, encoding="utf-8")


# ---- freeze (the default authoring path: workshop container → pinned digest) ----

def cmd_freeze(scen_name: str, container: str, host: str = "localhost",
               allow_key_leak: bool = False):
    """Freeze a container into the scen's pinned environment image."""
    scen = registry.load_scen(scen_name)

    if not docker_host.container_exists(host, container):
        raise click.ClickException(f"no container named {container!r} on {host}")

    # Volume/bind mounts: docker commit silently EXCLUDES their contents — the
    # frozen env would be missing exactly the files that live there. tmpfs is
    # fine (that exclusion is the key-delivery invariant working as intended).
    mounts = json.loads(docker_host.inspect(host, container, format="{{json .Mounts}}") or "[]")
    bad = [m for m in mounts if m.get("Type") in ("volume", "bind")]
    if bad:
        names = ", ".join(m.get("Destination", "?") for m in bad)
        raise click.ClickException(
            f"container has volume/bind mounts ({names}); docker commit silently "
            "excludes their contents. Freeze a mount-free workshop container.")

    # Dirty-source warning (freezes anything, but says loudly what carries —
    # what bake later RESETs vs CARRIES is documented in HOW_TO_MAKE_WORLDS).
    dirty = docker_host.stdout(
        host, "exec", container, "sh", "-c", builder.DIRTY_SOURCE_PROBE,
        check=False).split()
    if dirty:
        console.print(
            f"[yellow]⚠ not a clean workshop container ({', '.join(dirty)}): "
            "agent homes/corpus will CARRY into every world built on this env "
            "(runtime users/state are reset at build).[/yellow]")

    repo = versioning.GHCR_REPO_DEFAULT
    image = _next_env_tag(scen_name, repo)
    console.print(f"[dim]committing {container} → {image} …[/dim]")
    oci.commit_with_labels(host, container, image, _provenance_labels(host, scen))
    return _publish(host, image, scen, repo, allow_key_leak)


# ---- shell (workshop sugar: a container on the scen's resolved environment) ----

def cmd_shell(scen_name: str):
    """Open an interactive workshop container on the scen's resolved
    environment (source_image if pinned, else the runtime base). The
    container is kept after exit so it can be frozen."""
    scen = registry.load_scen(scen_name)
    image = scen["source_image"] or runtimes.get(scen["runtime"]).BASE_IMAGE
    name = f"workshop-{scen_name}-{uuid.uuid4().hex[:6]}"
    console.print(f"[dim]workshop container [bold]{name}[/bold] on {image}\n"
                  f"bang on it; when it works: "
                  f"zookeeper.py scen env freeze {scen_name} {name}[/dim]")
    if docker_host.run("localhost", "image", "inspect", image,
                       check=False).returncode != 0:
        docker_host.run("localhost", "pull", image)
    # Interactive by nature — hand the terminal to docker (localhost only).
    subprocess.call(["docker", "run", "-it", "--name", name, image, "bash"])
    console.print(f"[dim]container {name} kept (stopped) — freeze or "
                  f"`docker rm {name}` when done.[/dim]")


# ---- build (the optional recipe path: env.Dockerfile → the same pinned digest) ----

def cmd_build(scen_name: str, host: str = "localhost", allow_key_leak: bool = False):
    """docker build the scen's env.Dockerfile and publish it as the pinned env."""
    scen = registry.load_scen(scen_name)
    dockerfile = scen["dir"] / "env.Dockerfile"
    if not dockerfile.is_file():
        raise click.ClickException(
            f"scen {scen_name!r} has no env.Dockerfile (the recipe path is optional "
            "graduation — most scens freeze a workshop container instead).")

    rt = runtimes.get(scen["runtime"])
    repo = versioning.GHCR_REPO_DEFAULT
    image = _next_env_tag(scen_name, repo)
    label_args = []
    for k, v in _provenance_labels(host, scen).items():
        label_args += ["--label", f"{k}={v}"]
    console.print(f"[dim]building {image} from env.Dockerfile "
                  f"(AGENTSPACE_BASE={rt.BASE_IMAGE}) …[/dim]")
    docker_host.run(host, "build", "-f", str(dockerfile),
                    "--build-arg", f"AGENTSPACE_BASE={rt.BASE_IMAGE}",
                    *label_args, "-t", image, str(scen["dir"]), capture=False)
    return _publish(host, image, scen, repo, allow_key_leak)
