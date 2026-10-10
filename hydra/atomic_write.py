"""Publish complete files without reusing a caller-controlled temporary path."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def atomic_write_bytes(path: Path, payload: bytes, *, overwrite: bool = True) -> None:
    """Write and fsync a unique sibling, then atomically publish it.

    Create-only publication is exclusive even if another writer creates the
    destination after the caller's initial existence check. Existing file modes
    are retained on replacement (including executable bits).
    """
    mode = stat.S_IMODE(path.stat().st_mode) if overwrite and path.exists() else None
    fd, name = tempfile.mkstemp(prefix=".hydra-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            temporary.chmod(mode)
        if overwrite:
            os.replace(temporary, path)
        elif os.name == "nt":
            # Unlike POSIX rename, Windows rename refuses an existing target.
            os.rename(temporary, path)
        else:
            # Hard-link publication is atomic and refuses an existing target.
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
