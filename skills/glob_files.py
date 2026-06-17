"""skills.glob_files — list files under a root matching a glob pattern.

Mirror of OpenMono's `GlobTool` (`src/OpenMono.Cli/Tools/GlobTool.cs`),
adapted to HydraAgent's skill protocol. The module is named
`glob_files` (not `glob`) so it doesn't shadow stdlib `glob`.

Constraints (enforced, not advised):
  - Pattern is resolved relative to `root`. Absolute patterns and
    `..` traversal are refused.
  - Returns at most `max_results` matches (default 1000); the
    `truncated` flag tells the caller more existed.
  - Paths are returned as POSIX-style strings relative to `root`,
    sorted for stable output. The agent layer is allowed to see
    only what's under the worktree.

Returns: `ok`, `matches` (list[str]), `truncated`, `root`, `pattern`,
`count`.

Maturity: SCAFFOLDED. Promoted by §10.27.
"""
from __future__ import annotations

from pathlib import Path

from skills.fs_read import SkillError

DEFAULT_MAX_RESULTS = 1000


def run(
    pattern: str,
    root: str | Path,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    include_dirs: bool = False,
) -> dict:
    """Return paths under `root` matching `pattern`. `pattern` follows
    `Path.glob` semantics — `*` (one path component), `**` (any depth),
    `?`, `[abc]`. Raise `SkillError` on refusal."""
    if not isinstance(pattern, str) or not pattern.strip():
        raise SkillError("pattern must be a non-empty string")
    if pattern.startswith("/"):
        raise SkillError(f"absolute glob pattern refused: {pattern!r}")
    if ".." in Path(pattern).parts:
        raise SkillError(f"`..` in glob pattern refused: {pattern!r}")
    if max_results <= 0:
        raise SkillError(f"max_results must be positive, got {max_results}")

    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")

    matches: list[str] = []
    truncated = False
    # Path.glob yields lazily; we cap at max_results+1 so we know we
    # truncated without enumerating the whole tree.
    for hit in root_resolved.glob(pattern):
        if not include_dirs and not hit.is_file():
            continue
        try:
            rel = hit.resolve().relative_to(root_resolved)
        except ValueError:
            # Symlink escaping root — refuse silently (don't leak it
            # into the result; not a SkillError since the caller asked
            # for a benign pattern).
            continue
        if len(matches) >= max_results:
            truncated = True
            break
        matches.append(rel.as_posix())

    matches.sort()
    return {
        "ok": True,
        "matches": matches,
        "count": len(matches),
        "truncated": truncated,
        "root": str(root_resolved),
        "pattern": pattern,
    }
