"""OCI label read/write and ghcr.io registry API.

Labels are the canonical metadata store. They live on the image manifest, travel with the
image to ghcr.io, and are NOT readable from inside a running container.
"""

import base64
import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

from . import docker_host

LABEL_PREFIX = "org.agentspace."

# Snap metadata fields that need JSON encoding when written to OCI labels.
JSON_FIELDS = {"agents", "soul_files", "feature_flags", "notes"}

# Numeric fields that need string<->float conversion.
NUMERIC_FIELDS = {"budget_usd", "budget_used"}

# Fields in the snap dict that map 1:1 to OCI labels (after the prefix).
LABEL_FIELDS = [
    "snap_id",
    "scenario",
    "version",
    "parent_snap_id",
    "parent_version",
    "ghcr_tag",
    "created_at",
    "env_name",
    "creation_message",
    "runtime",
    "runtime_version",
    "model",
    "agents",
    "soul_files",
    "feature_flags",
    "budget_usd",
    "budget_used",
    "agentspace_ver",
    "notes",
]


def make_labels(snap: dict[str, Any]) -> dict[str, str]:
    """Convert a snap metadata dict into OCI label key/value strings."""
    out: dict[str, str] = {}
    for field in LABEL_FIELDS:
        value = snap.get(field)
        if value is None:
            continue
        key = LABEL_PREFIX + field
        if field in JSON_FIELDS and not isinstance(value, str):
            out[key] = json.dumps(value, separators=(",", ":"))
        elif field in NUMERIC_FIELDS:
            out[key] = f"{float(value):.2f}"
        else:
            out[key] = str(value)
    return out


def parse_labels(labels: dict[str, str] | None) -> dict[str, Any]:
    """Inverse of make_labels. Pulls agentspace fields out of a full label dict."""
    if not labels:
        return {}
    out: dict[str, Any] = {}
    for key, value in labels.items():
        if not key.startswith(LABEL_PREFIX):
            continue
        field = key[len(LABEL_PREFIX):]
        if field in JSON_FIELDS:
            try:
                out[field] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                out[field] = value
        elif field in NUMERIC_FIELDS:
            try:
                out[field] = float(value)
            except (ValueError, TypeError):
                out[field] = None
        else:
            out[field] = value
    return out


def change_args(labels: dict[str, str]) -> list[str]:
    """Build `--change 'LABEL k=v'` args for `docker commit`."""
    args: list[str] = []
    for k, v in labels.items():
        # The --change value is parsed by the daemon as a Dockerfile LABEL
        # instruction, so values with spaces need Dockerfile-style quoting
        # (subprocess argv quoting alone is not enough).
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        args.extend(["--change", f'LABEL {k}="{escaped}"'])
    return args


def commit_with_labels(
    host: str,
    container: str,
    image_ref: str,
    labels: dict[str, str],
) -> str:
    """`docker commit --change ...` and return the new image ID."""
    out = docker_host.stdout(
        host, "commit", *change_args(labels), container, image_ref
    ).strip()
    return out.split(":")[-1] if ":" in out else out


def inspect_image_labels(host: str, image_ref: str) -> dict[str, str]:
    """Read labels from a local image (after a pull or commit)."""
    raw = docker_host.stdout(
        host, "inspect", "--format", "{{json .Config.Labels}}", image_ref
    ).strip()
    if not raw or raw == "null":
        return {}
    try:
        return json.loads(raw) or {}
    except json.JSONDecodeError:
        return {}


# ---- ghcr.io registry API ----

GHCR_HOST = "ghcr.io"

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    ]
)


def _ghcr_token(repo: str) -> str | None:
    """Get a pull-scoped bearer token. Uses GITHUB_TOKEN as basic auth if present."""
    url = f"https://{GHCR_HOST}/token?service={GHCR_HOST}&scope=repository:{repo}:pull"
    req = urllib.request.Request(url)
    pat = os.environ.get("GITHUB_TOKEN") or os.environ.get("GHCR_PAT")
    if pat:
        creds = base64.b64encode(f"x:{pat}".encode()).decode()
        req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("token") or data.get("access_token")
    except urllib.error.URLError:
        return None


def _registry_get(repo: str, path: str, accept: str | None = None) -> bytes:
    token = _ghcr_token(repo)
    url = f"https://{GHCR_HOST}/v2/{repo}/{path}"
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_registry_labels(repo: str, tag: str) -> dict[str, str]:
    """Read OCI labels from a remote image without pulling layers.

    repo example: "sfgeekgit/agentspace"
    tag example:  "snap-simple2agent-1.1"
    """
    manifest_bytes = _registry_get(repo, f"manifests/{quote(tag)}", _MANIFEST_ACCEPT)
    manifest = json.loads(manifest_bytes)
    # Handle multi-arch index by picking the first manifest.
    if manifest.get("manifests"):
        sub_digest = manifest["manifests"][0]["digest"]
        manifest_bytes = _registry_get(
            repo, f"manifests/{quote(sub_digest)}", _MANIFEST_ACCEPT
        )
        manifest = json.loads(manifest_bytes)
    config_digest = manifest["config"]["digest"]
    config_bytes = _registry_get(repo, f"blobs/{quote(config_digest)}")
    config = json.loads(config_bytes)
    labels = (config.get("config") or {}).get("Labels") or {}
    return labels


def list_registry_tags(repo: str) -> list[str]:
    """List all tags in a ghcr.io repo. Paginates if needed."""
    tags: list[str] = []
    path = "tags/list"
    while True:
        token = _ghcr_token(repo)
        url = f"https://{GHCR_HOST}/v2/{repo}/{path}"
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            link = resp.headers.get("Link", "")
        tags.extend(data.get("tags") or [])
        if "rel=\"next\"" not in link:
            break
        # Parse next path out of the Link header.
        next_path = link.split(";", 1)[0].strip().lstrip("<").rstrip(">")
        path = next_path.split("/v2/" + repo + "/", 1)[-1]
    return tags
