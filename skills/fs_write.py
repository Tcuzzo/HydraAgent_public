"""skills.fs_write — bounded write to the local filesystem.

Mirror image of `skills.fs_read` (§10.6-fs-read). Same constraints,
same return shape style, same `SkillError` discipline:

  - The write target is resolved under a declared `root` and any path
    that escapes that root via symlink, `..`, or absolute traversal is
    refused.
  - The payload is bounded by `max_bytes` (default 1 MiB). Larger
    payloads are refused; no partial writes.
  - `overwrite=False` (the default) refuses to clobber an existing
    file; the operator must explicitly opt in. Mirrors OpenMono's
    `FileWriteTool` "create-only by default" stance.
  - Parent directories are created on demand; the create path is
    confined to `root` for the same scope-escape reasons.

Returns a dict (`ok`, `path`, `bytes_written`, `created_parents`,
`overwrote`). Raises `SkillError` on refusal — never silently writes
something the caller did not explicitly authorize.

The §10.7 `worktree_scope` hook is the right way to limit which roots
an agent can write into; this skill enforces the *path-within-root*
invariant on top of that.

Maturity: SCAFFOLDED. Promoted by §10.21.
"""
from __future__ import annotations

import os
from pathlib import Path

from skills.fs_read import SkillError

DEFAULT_MAX_BYTES = 1024 * 1024  # 1 MiB


def run(
    path: str | Path,
    content: str | bytes,
    root: str | Path,
    *,
    overwrite: bool = False,
    create_parents: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Write `content` to `path` resolved under `root`. Raise
    `SkillError` on refusal — never half-write."""
    if max_bytes <= 0:
        raise SkillError(f"max_bytes must be positive, got {max_bytes}")

    if isinstance(content, str):
        payload = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        payload = bytes(content)
    else:
        raise SkillError(
            f"content must be str or bytes, got {type(content).__name__}"
        )

    if len(payload) > max_bytes:
        raise SkillError(
            f"payload {len(payload)} bytes exceeds max_bytes {max_bytes}"
        )

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

    if (
        root_resolved not in target_resolved.parents
        and target_resolved != root_resolved
    ):
        raise SkillError(
            f"path {target_resolved} escapes root {root_resolved}"
        )

    if target_resolved == root_resolved:
        raise SkillError(
            f"refusing to write to the root directory itself: {root_resolved}"
        )

    overwrote = False
    if target_resolved.exists():
        if not target_resolved.is_file():
            raise SkillError(
                f"target exists and is not a regular file: {target_resolved}"
            )
        if not overwrite:
            raise SkillError(
                f"refusing to overwrite existing file {target_resolved} — "
                f"pass overwrite=True to allow"
            )
        overwrote = True

    created_parents: list[str] = []
    parent = target_resolved.parent
    if not parent.exists():
        if not create_parents:
            raise SkillError(
                f"parent directory does not exist: {parent} "
                f"(pass create_parents=True to auto-create)"
            )
        # Walk the chain of missing parents up to root_resolved so we
        # can report exactly which dirs we created (operator audit).
        to_create: list[Path] = []
        cursor = parent
        while not cursor.exists():
            if (
                root_resolved not in cursor.parents
                and cursor != root_resolved
            ):
                raise SkillError(
                    f"parent {cursor} escapes root {root_resolved}"
                )
            to_create.append(cursor)
            cursor = cursor.parent
        for d in reversed(to_create):
            d.mkdir()
            created_parents.append(
                str(d.relative_to(root_resolved))
            )

    # Atomic write: write to a sibling temp file then os.replace().
    # Prevents readers from seeing a partially-written file.
    tmp = target_resolved.with_name(target_resolved.name + ".hydra-tmp")
    try:
        with tmp.open("wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target_resolved)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise SkillError(f"write failed for {target_resolved}: {e}") from e

    return {
        "ok": True,
        "path": str(target_resolved.relative_to(root_resolved)),
        "bytes_written": len(payload),
        "created_parents": created_parents,
        "overwrote": overwrote,
    }
