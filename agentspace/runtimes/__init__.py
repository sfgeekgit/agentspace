"""Runtime-specific translators. Dispatch happens on snap's `runtime` OCI label."""

from . import openclaw, pi

REGISTRY = {
    "openclaw": openclaw,
    "pi": pi,
}


def get(runtime_name: str):
    if runtime_name not in REGISTRY:
        raise ValueError(
            f"Unknown runtime {runtime_name!r}. Known: {sorted(REGISTRY)}"
        )
    return REGISTRY[runtime_name]


def for_snap(snap: dict | None):
    """The runtime module for a snap dict (or env's snap). Missing label →
    openclaw (every pre-multi-runtime snap is an OC snap)."""
    return get(((snap or {}).get("runtime")) or "openclaw")
