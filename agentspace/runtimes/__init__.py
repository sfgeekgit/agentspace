"""Runtime-specific translators. Dispatch happens on snap's `runtime` OCI label."""

from . import openclaw

REGISTRY = {
    "openclaw": openclaw,
}


def get(runtime_name: str):
    if runtime_name not in REGISTRY:
        raise ValueError(
            f"Unknown runtime {runtime_name!r}. Known: {sorted(REGISTRY)}"
        )
    return REGISTRY[runtime_name]
