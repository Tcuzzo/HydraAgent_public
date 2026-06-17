"""skills.fs_edit — surgical search/replace inside files under a root.

Mirror of OpenMono's `FileEditTool` (`src/OpenMono.Cli/Tools/FileEditTool.cs`),
adapted to HydraAgent's skill protocol. Lets an agent change a small
chunk of a file without round-tripping the whole content through the
LLM — every `fs_write overwrite=True` of a large file otherwise burns
tokens proportional to the file size.

Contract:
  - `old_string` must appear in the file. If it appears zero or more
    than `count` times, the skill refuses (no ambiguous edits).
  - `new_string` replaces every occurrence found (up to `count`).
  - Passing `count="all"` replaces every occurrence.
  - Empty `old_string` is refused (would insert at every byte boundary).
  - The write is atomic via sibling temp + `os.replace`, exactly like
    `fs_write` (§10.21). Readers never see a partial file.
  - Same path-traversal / scope rules as `fs_write` — path resolves
    under `root`, absolute / `..` escapes refused.

Returns: `ok`, `path`, `replacements_made`, `bytes_written`,
`bytes_before`, `old_string_preview`, `new_string_preview`.

Maturity: SCAFFOLDED. Promoted by §10.30.
"""
from __future__ import annotations

import os
from pathlib import Path

from skills.fs_read import SkillError

DEFAULT_MAX_OUTPUT_BYTES = 1024 * 1024


def _preview(s: str, limit: int = 80) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + "..."


def run(
    path: str | Path,
    old_string: str,
    new_string: str,
    root: str | Path,
    *,
    count: int | str = 1,
    max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Replace `old_string` with `new_string` in `path` resolved under
    `root`. Raise `SkillError` on refusal — never half-write."""
    if not isinstance(old_string, str) or old_string == "":
        raise SkillError("old_string must be a non-empty string")
    if not isinstance(new_string, str):
        raise SkillError("new_string must be a string")
    replace_all = count == "all"
    if not replace_all:
        if not isinstance(count, int) or count < 1:
            raise SkillError(
                f'count must be a positive int or "all", got {count!r}'
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
    if not target_resolved.is_file():
        raise SkillError(f"not a regular file: {target_resolved}")

    text = target_resolved.read_text(encoding="utf-8", newline="")
    bytes_before = len(text.encode("utf-8"))

    occurrences = text.count(old_string)
    if occurrences == 0:
        raise SkillError(
            f"old_string not found in {target_resolved.relative_to(root_resolved)}: "
            f"{_preview(old_string)!r}"
        )
    if not replace_all and occurrences > count:
        raise SkillError(
            f"old_string occurs {occurrences} times but count={count} — "
            f"refusing ambiguous edit. Pass count='all' or narrow the match."
        )

    if replace_all:
        new_text = text.replace(old_string, new_string)
        replacements = occurrences
    else:
        new_text = text.replace(old_string, new_string, count)
        replacements = count

    payload = new_text.encode("utf-8")
    if len(payload) > max_bytes:
        raise SkillError(
            f"resulting file {len(payload)} bytes exceeds max_bytes {max_bytes}"
        )

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
        "replacements_made": replacements,
        "bytes_written": len(payload),
        "bytes_before": bytes_before,
        "old_string_preview": _preview(old_string),
        "new_string_preview": _preview(new_string),
    }
