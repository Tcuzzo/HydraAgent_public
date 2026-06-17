"""skills.fs_read — bounded read from the local filesystem.

Constraints (enforced, not advised):
  - Resolves the requested path under a declared root and refuses any path
    that escapes that root via symlink, `..`, or absolute traversal.
  - Reads at most `max_bytes` (default 64 KiB). No streaming, no partial
    state — either the read fits in budget or it raises.

Returns a dict, not a string. The agent layer must see structured data
(`ok`, `path`, `bytes_read`, `content`, `truncated`) so the verifier can
audit usage in §10.7. Raises `SkillError` on refusal, so callers cannot
mistake a sandbox violation for an empty file.

Maturity: SCAFFOLDED. Promoted by §10.6-fs-read eval.
"""
from __future__ import annotations

from pathlib import Path


class SkillError(Exception):
    """A skill refused the request. Carries a plain-English reason."""


DEFAULT_MAX_BYTES = 64 * 1024


def run(
    path: str | Path,
    root: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Read `path` resolved under `root`. Raise SkillError on refusal."""
    if max_bytes <= 0:
        raise SkillError(f"max_bytes must be positive, got {max_bytes}")
    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")
    target = Path(path)
    if not target.is_absolute():
        target = root_resolved / target
    try:
        target_resolved = target.resolve(strict=False)
    except OSError as e:
        raise SkillError(f"cannot resolve path {path!r}: {e}") from e
    if root_resolved not in target_resolved.parents and target_resolved != root_resolved:
        raise SkillError(
            f"path {target_resolved} escapes root {root_resolved}"
        )
    if not target_resolved.is_file():
        raise SkillError(f"not a regular file: {target_resolved}")
    size = target_resolved.stat().st_size
    truncated = size > max_bytes
    with target_resolved.open("rb") as f:
        raw = f.read(max_bytes)
    return {
        "ok": True,
        "path": str(target_resolved.relative_to(root_resolved)),
        "bytes_read": len(raw),
        "size_on_disk": size,
        "truncated": truncated,
        "content": raw.decode("utf-8", errors="replace"),
    }
