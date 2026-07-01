"""OpenRouter API client: provision per-env keys with credit limits.

Two key types involved:
- Provisioning key: read from OPENROUTER_PROVISIONING_KEY env var. Lets us mint and manage
  inference keys. Never leaves the control droplet.
- Inference key: minted per env, injected into the container at `docker run` time. The
  agents inside use this for all model calls. Has a credit limit.

Per-env key naming convention: "agentspace-<env_name>".
"""

import json
import os
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://openrouter.ai/api/v1"


class OpenRouterError(RuntimeError):
    pass


def _provisioning_key() -> str:
    key = os.environ.get("OPENROUTER_PROVISIONING_KEY")
    if not key:
        raise OpenRouterError(
            "OPENROUTER_PROVISIONING_KEY not set. Add it to secrets.env."
        )
    return key


def _request(
    method: str,
    path: str,
    auth: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {auth}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        raise OpenRouterError(f"{method} {path}: HTTP {e.code} {body_text}") from e
    except urllib.error.URLError as e:
        raise OpenRouterError(f"{method} {path}: {e.reason}") from e


def list_models() -> list[str]:
    """The public OpenRouter model catalog (no auth). Returns model ids
    (unprefixed, e.g. 'anthropic/claude-haiku-4-5')."""
    req = urllib.request.Request(f"{API_BASE}/models", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise OpenRouterError(f"GET /models: {e}") from e
    return [m["id"] for m in (data.get("data") or []) if m.get("id")]


def mint_key(env_name: str, limit_usd: float) -> dict[str, Any]:
    """Create a new inference key with a credit limit.

    Returns the raw OpenRouter response, which includes the key value (only shown once).
    """
    resp = _request(
        "POST",
        "/keys",
        auth=_provisioning_key(),
        body={"name": f"agentspace-{env_name}", "limit": float(limit_usd)},
    )
    return resp


def get_key_info(inference_key: str) -> dict[str, Any]:
    """Use the inference key itself to read its own limit/usage."""
    return _request("GET", "/key", auth=inference_key)


def list_keys() -> list[dict[str, Any]]:
    """List all keys under the provisioning account."""
    resp = _request("GET", "/keys", auth=_provisioning_key())
    return resp.get("data") or resp.get("keys") or []


def find_key_by_name(name: str) -> dict[str, Any] | None:
    for k in list_keys():
        if k.get("name") == name:
            return k
    return None


def topup(env_name: str, additional_usd: float) -> dict[str, Any]:
    """Increase the credit limit on the env's key."""
    name = f"agentspace-{env_name}"
    info = find_key_by_name(name)
    if info is None:
        raise OpenRouterError(f"No OpenRouter key found with name {name!r}")
    key_hash = info.get("hash") or info.get("key_hash") or info.get("id")
    if not key_hash:
        raise OpenRouterError(f"Key {name!r} has no hash field; response: {info}")
    current_limit = float(info.get("limit") or 0.0)
    new_limit = current_limit + float(additional_usd)
    return _request(
        "PATCH",
        f"/keys/{key_hash}",
        auth=_provisioning_key(),
        body={"limit": new_limit},
    )


def disable_key(env_name: str):
    name = f"agentspace-{env_name}"
    info = find_key_by_name(name)
    if info is None:
        return
    key_hash = info.get("hash") or info.get("key_hash") or info.get("id")
    if not key_hash:
        return
    _request(
        "PATCH",
        f"/keys/{key_hash}",
        auth=_provisioning_key(),
        body={"disabled": True},
    )
