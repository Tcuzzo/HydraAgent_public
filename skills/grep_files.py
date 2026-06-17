"""skills.grep_files — find regex matches in files under a root.

Mirror of OpenMono's `GrepTool` (`src/OpenMono.Cli/Tools/GrepTool.cs`),
adapted to HydraAgent's skill protocol. Named `grep_files` so it
doesn't collide with anything named `grep` elsewhere.

Constraints (enforced, not advised):
  - Files to search are selected by a glob `path_pattern` (default
    `**/*`) under `root`. Absolute and `..` patterns are refused.
  - Pattern is a Python `re` regex by default; `regex=False` treats
    it as a literal substring.
  - Bounded by `max_results` total matches across all files.
  - Bounded by `max_file_bytes` per file (default 256 KiB) — bigger
    files are skipped (not silently truncated mid-line).
  - Binary files (UnicodeDecodeError) are skipped; not an error.

Returns: `ok`, `matches` (list of {path, line_number, line, span}),
`count`, `files_scanned`, `files_skipped_binary`, `files_skipped_size`,
`truncated`, `pattern`, `path_pattern`, `regex`.

Maturity: SCAFFOLDED. Promoted by §10.28.
"""
from __future__ import annotations

import re
from pathlib import Path

from skills.fs_read import SkillError

DEFAULT_MAX_RESULTS = 200
DEFAULT_MAX_FILE_BYTES = 256 * 1024


def run(
    pattern: str,
    root: str | Path,
    *,
    path_pattern: str = "**/*",
    regex: bool = True,
    case_insensitive: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> dict:
    """Grep `pattern` across files under `root` matching `path_pattern`."""
    if not isinstance(pattern, str) or pattern == "":
        raise SkillError("pattern must be a non-empty string")
    if not isinstance(path_pattern, str) or not path_pattern.strip():
        raise SkillError("path_pattern must be a non-empty string")
    if path_pattern.startswith("/"):
        raise SkillError(
            f"absolute path_pattern refused: {path_pattern!r}"
        )
    if ".." in Path(path_pattern).parts:
        raise SkillError(
            f"`..` in path_pattern refused: {path_pattern!r}"
        )
    if max_results <= 0:
        raise SkillError(f"max_results must be positive, got {max_results}")
    if max_file_bytes <= 0:
        raise SkillError(
            f"max_file_bytes must be positive, got {max_file_bytes}"
        )

    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")

    flags = re.IGNORECASE if case_insensitive else 0
    if regex:
        try:
            rx = re.compile(pattern, flags)
        except re.error as e:
            raise SkillError(f"invalid regex {pattern!r}: {e}") from e
    else:
        rx = re.compile(re.escape(pattern), flags)

    matches: list[dict] = []
    files_scanned = 0
    files_skipped_binary = 0
    files_skipped_size = 0
    truncated = False

    for hit in sorted(root_resolved.glob(path_pattern)):
        if not hit.is_file():
            continue
        try:
            rel = hit.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        try:
            size = hit.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            files_skipped_size += 1
            continue
        try:
            text = hit.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            files_skipped_binary += 1
            continue
        files_scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = rx.search(line)
            if not m:
                continue
            matches.append(
                {
                    "path": rel.as_posix(),
                    "line_number": lineno,
                    "line": line,
                    "span": [m.start(), m.end()],
                }
            )
            if len(matches) >= max_results:
                truncated = True
                break
        if truncated:
            break

    return {
        "ok": True,
        "matches": matches,
        "count": len(matches),
        "files_scanned": files_scanned,
        "files_skipped_binary": files_skipped_binary,
        "files_skipped_size": files_skipped_size,
        "truncated": truncated,
        "pattern": pattern,
        "path_pattern": path_pattern,
        "regex": regex,
    }
