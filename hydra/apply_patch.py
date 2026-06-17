"""hydra/apply_patch.py — SINGLE-WRITER context-anchored patch applier.

Implements SLICE 1 (context-anchored apply) + SLICE 2 (syntax/lint gate) of
the SAFE-EDIT CORE build.

Design:
  - Accepts a (file, old_block, new_block) triple.
  - Uses EXACT-STRING matching — same contract as skills/fs_edit.py.
  - FAIL-CLOSED: if old_block is absent → PatchFailure, file untouched.
  - FAIL-CLOSED: if old_block matches > 1 place → PatchFailure (ambiguous).
  - FAIL-CLOSED: if the resulting content fails the syntax gate → PatchFailure.
  - ATOMIC WRITE via sibling-temp + os.replace (mirrors skills/fs_edit.py §10.21).
  - PATH GUARD: resolves path under root; absolute / .. escapes refused.
  - Syntax gate: ast.parse for .py; declarative checker map for others; NO-OP
    (not crash) when checker unavailable.
  - CHECKPOINT HOOK (Slice 5): both apply_patch() and create_file() have
    DEFAULT-ON checkpointing. When no explicit checkpoint_store is supplied,
    a store is auto-constructed from the repo root so EVERY production mutation
    is captured automatically. An explicit checkpoint=False opt-out is provided
    for ephemeral/sandbox writes. The hook lives HERE at the single writer so
    100% of sanctioned mutations are captured automatically.

This is the ONLY sanctioned mutation path. hydra/worker_aci._edit must route
here instead of calling path.write_text directly.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from hydra.edit_checkpoints import CheckpointStore

# Sentinel value for the `checkpoint_store` parameter to indicate that no
# store was passed by the caller (distinct from explicitly passing None).
_STORE_NOT_GIVEN = object()


# ---------------------------------------------------------------------------
# Default-on checkpoint store resolver
# ---------------------------------------------------------------------------

def _resolve_checkpoint_store(
    checkpoint: bool,
    explicit_store: "CheckpointStore | None",
    root: "Path | str",
) -> "CheckpointStore | None":
    """Return the CheckpointStore to use for this mutation.

    Logic:
      - checkpoint=False  →  None (opt-out, no recording)
      - explicit_store is not None  →  use explicit_store as-is
      - otherwise  →  auto-build a default store keyed to root

    This keeps callers clean: they never need to construct or thread a store
    to get checkpointing; it happens automatically unless explicitly opted out.
    """
    if not checkpoint:
        return None
    if explicit_store is not None:
        return explicit_store
    # Default-on: auto-build from root
    from hydra.edit_checkpoints import get_default_store
    return get_default_store(Path(root))


# ---------------------------------------------------------------------------
# Public result types
# ---------------------------------------------------------------------------

@dataclass
class PatchResult:
    """Successful patch application."""
    ok: bool = True
    path: str = ""
    replacements_made: int = 0
    bytes_written: int = 0
    bytes_before: int = 0
    # Optional rendered window for caller context
    window: str = ""


@dataclass
class PatchFailure:
    """Patch refused — file is byte-identical to before the call."""
    ok: bool = False
    reason: str = ""
    # Number of times old_block was found (0 = not found, >1 = ambiguous)
    match_count: int = 0
    # Line-numbered window around first match site (if any) for retry context
    window: str = ""


# ---------------------------------------------------------------------------
# Syntax gate: declarative checker map
# ---------------------------------------------------------------------------

def _check_python(content: str) -> tuple[bool, str]:
    """Return (ok, error_msg). Uses ast.parse — no subprocess, no imports."""
    try:
        ast.parse(content)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error: {e}"


# Map: file extension → checker function.
# Add new checkers here as needed. An extension missing from the map = no-op (pass).
_SYNTAX_CHECKERS: dict[str, Callable[[str], tuple[bool, str]]] = {
    ".py": _check_python,
}


def _run_syntax_gate(path: Path, content: str) -> tuple[bool, str]:
    """Run the appropriate syntax checker for `path`'s extension.

    Returns (ok, error_message). When no checker is registered for the extension,
    returns (True, "") — never crashes on unknown types.
    """
    checker = _SYNTAX_CHECKERS.get(path.suffix.lower())
    if checker is None:
        return True, ""
    try:
        return checker(content)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    except Exception as exc:  # checker itself crashed — fail-closed: a broken checker blocks writes
        return False, f"checker crashed: {exc}"


# ---------------------------------------------------------------------------
# Path resolution helper (mirrors skills/fs_edit.py)
# ---------------------------------------------------------------------------

def _resolve_under_root(file: Path, root: Path) -> tuple[Path | None, str]:
    """Return (resolved_path, error) — error is "" on success."""
    root_resolved = root.expanduser().resolve()
    if not root_resolved.is_dir():
        return None, f"root is not a directory: {root_resolved}"

    target = Path(file)
    if not target.is_absolute():
        target = root_resolved / target
    try:
        target_resolved = target.resolve(strict=False)
    except OSError as e:
        return None, f"cannot resolve path {file!r}: {e}"

    # Must be under root (mirrors skills/fs_edit.py lines 74–80)
    if (
        root_resolved not in target_resolved.parents
        and target_resolved != root_resolved
    ):
        return None, f"path {target_resolved} escapes root {root_resolved}"

    if not target_resolved.is_file():
        return None, f"not a regular file: {target_resolved}"

    return target_resolved, ""


# ---------------------------------------------------------------------------
# Numbered-line window helper (for retry context on failure)
# ---------------------------------------------------------------------------

def _build_window(lines: list[str], center_line: int, radius: int = 5) -> str:
    """Return a numbered window of `lines` centred on `center_line` (1-indexed)."""
    start = max(0, center_line - 1 - radius)
    end = min(len(lines), center_line + radius)
    return "\n".join(f"{idx + 1}: {ln.rstrip()}" for idx, ln in enumerate(lines[start:end], start=start))


# ---------------------------------------------------------------------------
# Core public function
# ---------------------------------------------------------------------------

def apply_patch(
    *,
    file: Path | str,
    old_block: str,
    new_block: str,
    root: Path | str,
    checkpoint_store: "CheckpointStore | None" = None,
    checkpoint: bool = True,
) -> PatchResult | PatchFailure:
    """Apply a context-anchored patch to `file` under `root`.

    Parameters
    ----------
    file:
        Path to the target file. May be relative (resolved under `root`) or
        absolute (must still reside under `root`).
    old_block:
        Exact string to find and replace. Must match EXACTLY once.
    new_block:
        Replacement text.
    root:
        Workspace root. Writes outside this directory are refused.
    checkpoint_store:
        Optional explicit CheckpointStore (hydra.edit_checkpoints). When
        supplied, this store is used directly.  When omitted (the default),
        checkpointing is STILL performed using a store auto-constructed from
        `root` — so every production mutation is captured automatically with
        no per-caller plumbing.
    checkpoint:
        Set to False to completely opt out of checkpointing.  Use this ONLY
        for ephemeral / sandbox writes where undo is genuinely not needed.
        The default (True) means checkpointing is always on.

    Returns
    -------
    PatchResult  on success (file has been atomically updated).
    PatchFailure on any refusal (file is byte-identical to before the call).
    """
    root = Path(root)
    file = Path(file)

    # --- 1. Path guard ---
    target, err = _resolve_under_root(file, root)
    if err:
        return PatchFailure(reason=err)

    # --- 2. Read current content (newline='' preserves \r\n, \r, \n as-is) ---
    text = target.read_text(encoding="utf-8", newline="")
    bytes_before = len(text.encode("utf-8"))
    pre_image_bytes = target.read_bytes()  # exact bytes for checkpoint

    # --- 3. old_block must be a non-empty string ---
    if not isinstance(old_block, str) or old_block == "":
        return PatchFailure(reason="old_block must be a non-empty string")

    # --- 4. Exact-match count check ---
    match_count = text.count(old_block)
    window = ""
    if match_count == 0:
        # Build window near start of file for retry context
        lines = text.splitlines()
        window = _build_window(lines, 1, radius=8)
        return PatchFailure(
            reason=f"old_block not found in {target.relative_to(root.resolve())}",
            match_count=0,
            window=window,
        )
    if match_count > 1:
        # Locate first occurrence for window
        first_pos = text.index(old_block)
        first_line = text[:first_pos].count("\n") + 1
        lines = text.splitlines()
        window = _build_window(lines, first_line)
        return PatchFailure(
            reason=(
                f"old_block matches {match_count} times — ambiguous edit refused. "
                "Add more surrounding context to disambiguate."
            ),
            match_count=match_count,
            window=window,
        )

    # --- 5. Build new content ---
    new_text = text.replace(old_block, new_block, 1)

    # --- 6. Syntax gate (SLICE 2) ---
    gate_ok, gate_err = _run_syntax_gate(target, new_text)
    if not gate_ok:
        # Locate the change site for window context
        first_pos = text.index(old_block)
        first_line = text[:first_pos].count("\n") + 1
        lines = new_text.splitlines()
        window = _build_window(lines, first_line)
        return PatchFailure(
            reason=f"syntax gate rejected the edit: {gate_err}",
            match_count=1,
            window=window,
        )

    # --- 7. Checkpoint: record pre-image BEFORE atomic write (Slice 5) ---
    # Default-on: if no explicit store was given and opt-out is not set,
    # auto-build a store keyed to the repo root so EVERY production mutation
    # is captured without per-caller plumbing.
    _store = _resolve_checkpoint_store(checkpoint, checkpoint_store, root)
    if _store is not None:
        _store.record(
            target_path=target,
            op="modify",
            pre_image=pre_image_bytes,
        )

    # --- 8. Atomic write (mirrors skills/fs_edit.py lines 106–125) ---
    payload = new_text.encode("utf-8")
    tmp = target.with_name(target.name + ".hydra-tmp")
    try:
        with tmp.open("wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except OSError as e:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return PatchFailure(reason=f"write failed for {target}: {e}")

    rel = str(target.relative_to(root.resolve()))
    return PatchResult(
        ok=True,
        path=rel,
        replacements_made=1,
        bytes_written=len(payload),
        bytes_before=bytes_before,
    )


# ---------------------------------------------------------------------------
# Create path: atomic create for a new file (old_block absent / empty)
# ---------------------------------------------------------------------------

def create_file(
    *,
    file: Path | str,
    content: str,
    root: Path | str,
    checkpoint_store: "CheckpointStore | None" = None,
    checkpoint: bool = True,
) -> PatchResult | PatchFailure:
    """Create a NEW file under `root` atomically, with the syntax gate applied.

    Parameters
    ----------
    file:
        Path to the new file. Must NOT already exist. May be relative (resolved
        under `root`) or absolute (must still reside under `root`).
    content:
        Full text content to write.
    root:
        Workspace root. Writes outside this directory are refused.
    checkpoint_store:
        Optional explicit CheckpointStore (hydra.edit_checkpoints). When
        supplied, this store is used directly.  When omitted, checkpointing
        is STILL performed using a store auto-constructed from `root` (default-on).
    checkpoint:
        Set to False to completely opt out of checkpointing for ephemeral writes.

    Returns
    -------
    PatchResult  on success (file has been atomically created).
    PatchFailure on any refusal (no file is created or mutated).
    """
    root = Path(root)
    file = Path(file)

    # --- 1. Path guard (without existence check, since file is new) ---
    root_resolved = root.expanduser().resolve()
    if not root_resolved.is_dir():
        return PatchFailure(reason=f"root is not a directory: {root_resolved}")

    target = file if file.is_absolute() else root_resolved / file
    try:
        target_resolved = target.resolve(strict=False)
    except OSError as e:
        return PatchFailure(reason=f"cannot resolve path {file!r}: {e}")

    if (
        root_resolved not in target_resolved.parents
        and target_resolved != root_resolved
    ):
        return PatchFailure(reason=f"path {target_resolved} escapes root {root_resolved}")

    if target_resolved.exists():
        return PatchFailure(reason=f"create_file refused: {target_resolved} already exists — use apply_patch to modify an existing file")

    # --- 2. Syntax gate ---
    gate_ok, gate_err = _run_syntax_gate(target_resolved, content)
    if not gate_ok:
        return PatchFailure(reason=f"syntax gate rejected create: {gate_err}")

    # --- 3. Checkpoint: record ABSENT pre-image BEFORE creating the file (Slice 5) ---
    # Default-on: auto-build store when no explicit store given and opt-out not set.
    _store = _resolve_checkpoint_store(checkpoint, checkpoint_store, root)
    if _store is not None:
        _store.record(
            target_path=target_resolved,
            op="create",
            pre_image=None,
        )

    # --- 4. Atomic write via sibling-temp + os.replace ---
    target_resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
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
        return PatchFailure(reason=f"create_file write failed for {target_resolved}: {e}")

    rel = str(target_resolved.relative_to(root_resolved))
    return PatchResult(
        ok=True,
        path=rel,
        replacements_made=1,
        bytes_written=len(payload),
        bytes_before=0,
    )
