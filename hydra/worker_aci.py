"""Hydra-native Agent-Computer Interface commands for worker jobs."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hydra.apply_patch import apply_patch, PatchFailure
from hydra.exec_backend import run_sandboxed_shell

if TYPE_CHECKING:
    from hydra.edit_checkpoints import CheckpointStore


ACI_RESULT_SCHEMA = "hydra.worker_aci_result.v1"


class WorkerAciError(Exception):
    """ACI command validation or execution failure."""


def run_aci_command(
    command: dict[str, Any],
    *,
    repo_root: Path,
    checkpoint_store: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Execute an ACI command within repo_root.

    Parameters
    ----------
    command:
        ACI command dict (must include "command" key).
    repo_root:
        Filesystem root — writes outside this directory are refused.
    checkpoint_store:
        Optional CheckpointStore for edit checkpointing (Slice 5).  When
        supplied, every sanctioned mutation records a pre-image snapshot so
        the caller can undo it later via hydra.edit_checkpoints.undo().
    """
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise WorkerAciError(f"repo_root is not a directory: {root}")
    name = command.get("command")
    if name == "inspect":
        result = _inspect(root)
    elif name == "search":
        result = _search(root, _required_str(command, "query"), limit=int(command.get("limit", 20)))
    elif name == "open":
        result = _open(root, _path(root, command.get("path")), start=int(command.get("start", 1)), limit=int(command.get("limit", 120)))
    elif name == "edit":
        # New interface: context-anchored (old_block / new_block) — routes through apply_patch.
        # Legacy interface: range-based (start / end / replacement) — kept for backward compat,
        # also routed through apply_patch via range-to-anchor translation.
        if "old_block" in command or "new_block" in command:
            result = _edit_anchored(
                root,
                _path(root, command.get("path")),
                old_block=_required_str(command, "old_block"),
                new_block=command.get("new_block", ""),
                checkpoint_store=checkpoint_store,
            )
        else:
            result = _edit_range(
                root,
                _path(root, command.get("path")),
                start=int(command.get("start", 0)),
                end=int(command.get("end", 0)),
                replacement=_required_str(command, "replacement"),
                checkpoint_store=checkpoint_store,
            )
    elif name == "test":
        result = _test(root, _required_str(command, "shell"))
    elif name == "diff":
        result = _diff(root)
    elif name == "finish":
        result = _finish(root)
    else:
        raise WorkerAciError(f"unsupported ACI command: {name!r}")
    return {"schema": ACI_RESULT_SCHEMA, "command": name, **result}


def render_aci_text(result: dict[str, Any]) -> str:
    output = result.get("output", "")
    if output:
        return str(output).rstrip() + "\n"
    return json.dumps(result, indent=2, sort_keys=True) + "\n"


def _inspect(root: Path) -> dict[str, Any]:
    files = [str(path) for path in _tracked_files(root)[:80]]
    status = _git(root, ["status", "--short"])
    return {"status": "passed", "files_count": len(_tracked_files(root)), "files": files, "output": status}


def _search(root: Path, query: str, *, limit: int) -> dict[str, Any]:
    matches = []
    for rel in _tracked_files(root):
        path = root / rel
        if not path.is_file():
            continue
        for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if query in line:
                matches.append(f"{rel}:{idx}: {line}")
                if len(matches) >= limit:
                    break
        if len(matches) >= limit:
            break
    return {"status": "passed", "matches_count": len(matches), "output": "\n".join(matches)}


def _open(root: Path, path: Path, *, start: int, limit: int) -> dict[str, Any]:
    rel = path.relative_to(root)
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(1, start)
    limit = max(1, limit)
    selected = lines[start - 1 : start - 1 + limit]
    output = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start))
    return {"status": "passed", "path": str(rel), "start": start, "lines": len(selected), "output": output}


def _edit_anchored(
    root: Path,
    path: Path,
    *,
    old_block: str,
    new_block: str,
    checkpoint_store: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Context-anchored edit routed through apply_patch (SINGLE sanctioned write path)."""
    rel = path.relative_to(root)
    patch_result = apply_patch(
        file=path,
        old_block=old_block,
        new_block=new_block,
        root=root,
        checkpoint_store=checkpoint_store,
    )
    if isinstance(patch_result, PatchFailure):
        output = patch_result.window or patch_result.reason
        return {"status": "failed", "path": str(rel), "reason": patch_result.reason, "output": output}
    return {"status": "passed", "path": str(rel), "output": _git(root, ["diff", "--", str(rel)])}


def _edit_range(
    root: Path,
    path: Path,
    *,
    start: int,
    end: int,
    replacement: str,
    checkpoint_store: "CheckpointStore | None" = None,
) -> dict[str, Any]:
    """Range-based edit (legacy backward compat) — converts range to anchor, then routes
    through apply_patch so ALL mutations go through the single sanctioned write path."""
    if start < 1 or end < start:
        raise WorkerAciError("edit requires 1-indexed start and end with end >= start")
    rel = path.relative_to(root)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    if end > len(lines):
        raise WorkerAciError(f"edit range exceeds file length for {rel}")
    # Build the old_block exactly as it appears in the file (the lines being replaced)
    old_block = "".join(lines[start - 1 : end])
    # Normalise replacement so it ends with a newline (matches original _edit behaviour)
    replacement_lines = replacement.splitlines(keepends=True)
    if replacement and not replacement.endswith("\n"):
        replacement_lines[-1] = replacement_lines[-1] + "\n"
    new_block = "".join(replacement_lines)
    patch_result = apply_patch(
        file=path,
        old_block=old_block,
        new_block=new_block,
        root=root,
        checkpoint_store=checkpoint_store,
    )
    if isinstance(patch_result, PatchFailure):
        output = patch_result.window or patch_result.reason
        return {"status": "failed", "path": str(rel), "reason": patch_result.reason, "output": output}
    return {"status": "passed", "path": str(rel), "output": _git(root, ["diff", "--", str(rel)])}


def _test(root: Path, shell: str) -> dict[str, Any]:
    result = run_sandboxed_shell(
        shell,
        workspace=root,
        network=False,
        timeout=120,
    )
    return {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "output": result.stdout[-4000:]}


def _diff(root: Path) -> dict[str, Any]:
    return {"status": "passed", "output": _git(root, ["diff", "--no-ext-diff", "--"])}


def _finish(root: Path) -> dict[str, Any]:
    diff = _git(root, ["diff", "--no-ext-diff", "--"])
    return {"status": "ready" if diff.strip() else "no-changes", "output": diff}


def _tracked_files(root: Path) -> list[Path]:
    proc = subprocess.run(["git", "ls-files", "-z"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        raise WorkerAciError(f"git ls-files failed: {proc.stdout.decode(errors='replace').strip()}")
    return [Path(raw.decode()) for raw in proc.stdout.split(b"\0") if raw]


def _is_git_repo(root: Path) -> bool:
    """Return True if root is inside a git working tree (fast check)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
    )
    return proc.returncode == 0


def _git(root: Path, args: list[str]) -> str:
    """Run a git command, returning stdout.  When root is NOT a git repo the
    call is silently suppressed and an empty string is returned — this prevents
    git from dumping a usage / error message (e.g. after `git diff -- file` on a
    non-repo tmp directory used in tests)."""
    if not _is_git_repo(root):
        return ""
    proc = subprocess.run(["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return proc.stdout


def _path(root: Path, raw: Any) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise WorkerAciError("path must be a non-empty string")
    path = (root / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    try:
        path.relative_to(root)
    except ValueError as e:
        raise WorkerAciError(f"path must stay under repo root: {raw}") from e
    if not path.is_file():
        raise WorkerAciError(f"path is not a file: {raw}")
    return path


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise WorkerAciError(f"{key} must be a non-empty string")
    return value
