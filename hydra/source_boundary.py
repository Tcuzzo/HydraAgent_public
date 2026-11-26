"""Resolve automatically consumed data inside its selected owner directory."""
from pathlib import Path


def confined_source(path: Path, owner: Path) -> Path:
    """Allow internal aliases without following a source outside its owner."""
    try:
        resolved = path.expanduser().resolve()
        boundary = owner.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("source path cannot be resolved inside its selected root") from exc
    if not resolved.is_relative_to(boundary):
        raise ValueError("source path escapes its selected root")
    return resolved
