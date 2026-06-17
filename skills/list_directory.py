"""skills.list_directory — describe a directory's immediate children.

Mirror of OpenMono's `ListDirectoryTool`. Faster than running
`glob('*')` when an agent just wants to see what's at a path.

Constraints:
  - `path` is resolved under `root`. Absolute / `..` escapes refused.
  - Entries report `name`, `type` (`file`/`dir`/`symlink`/`other`),
    and `size` (bytes for files, None for dirs / unknown).
  - Bounded by `max_entries` (default 500) with `truncated` flag.
  - Entries returned sorted by name for stable output.
  - Hidden files are included by default (the operator's glob /
    .gitignore policy is layered on top by the caller).

Returns: `ok`, `path`, `entries`, `count`, `truncated`.

Maturity: SCAFFOLDED. Promoted by §10.31.
"""
from __future__ import annotations

from pathlib import Path

from skills.fs_read import SkillError

DEFAULT_MAX_ENTRIES = 500


def _entry_type(p: Path) -> str:
    if p.is_symlink():
        return "symlink"
    if p.is_dir():
        return "dir"
    if p.is_file():
        return "file"
    return "other"


def run(
    path: str | Path = ".",
    root: str | Path | None = None,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    include_hidden: bool = True,
) -> dict:
    """List immediate children of `path` under `root`. Raise `SkillError`
    on refusal."""
    if root is None:
        raise SkillError("root is required")
    if max_entries <= 0:
        raise SkillError(f"max_entries must be positive, got {max_entries}")

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
    if not target_resolved.is_dir():
        raise SkillError(f"not a directory: {target_resolved}")

    entries: list[dict] = []
    truncated = False
    children = sorted(target_resolved.iterdir(), key=lambda p: p.name)
    for child in children:
        if not include_hidden and child.name.startswith("."):
            continue
        if len(entries) >= max_entries:
            truncated = True
            break
        et = _entry_type(child)
        size: int | None = None
        if et == "file":
            try:
                size = child.stat().st_size
            except OSError:
                size = None
        entries.append({"name": child.name, "type": et, "size": size})

    return {
        "ok": True,
        "path": str(target_resolved.relative_to(root_resolved)) or ".",
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }
