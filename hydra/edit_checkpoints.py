"""hydra/edit_checkpoints.py — per-edit byte snapshots + instant undo.

Implements Slice 5 of the trusted-repo-work build.

Design
------
File tools and apply_patch record the target file's PRE-IMAGE bytes — or the
ABSENT sentinel when the file did not yet exist — before atomic publication.
The public file tools use CheckpointStore.write_bytes so failed publication
cannot leave an undo entry for a change that never happened.
Checkpointing is DEFAULT-ON: callers that do not pass an explicit
checkpoint_store get a store auto-constructed from the repo root.  An explicit
opt-out (checkpoint=False) is available for ephemeral / sandbox writes.

Storage
-------
Snapshots are kept in ~/.hydraAgent/edit_checkpoints/<root-hash>/ — NEVER inside
the target repo's tracked tree.  This is a hard requirement: the checkpoint store
must not pollute the repo being worked on.

Override: if the environment variable HYDRA_CHECKPOINTS_DIR is set, snapshots
are written under that directory instead of ~/.hydraAgent/edit_checkpoints/.
This lets test suites redirect writes to a tmp dir without touching the real
store.

Retention
---------
The store is bounded: default 200 entries OR 50 MiB of pre-image bytes total
(whichever limit is hit first).  When a new snapshot would breach the cap, the
oldest entries are GC'd until the store fits.  Ring semantics: oldest out first.

Undo
----
undo(store, n=1) restores the most-recent n checkpoints in LIFO order:
  - op=modify → restore exact pre-image bytes via the apply_patch ATOMIC write
    helper (_atomic_write_bytes) — single-writer, never raw open().
  - op=create  (pre-image == ABSENT) → delete the file that was created.

undo on an empty stack is a SAFE no-op; it returns an explanatory message and
never raises.

IMPORTANT — scope of undo
--------------------------
undo reverts FILES only.  It cannot undo external side effects such as network
calls, database writes, shell commands, or anything else that happened during the
mutation.  Callers and operators must be aware of this limitation.  This caveat
is intentional and matches the Claude Code safe-edit contract.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hydra.atomic_write import atomic_write_bytes
from hydra.file_lock import locked_path

log = logging.getLogger(__name__)

# Sentinel: the file did not exist before the mutation (i.e., it was created).
ABSENT = "__HYDRA_ABSENT__"

# Default limits
DEFAULT_MAX_ENTRIES: int = 200
DEFAULT_MAX_BYTES: int = 50 * 1024 * 1024  # 50 MiB

# Storage root under the user's home directory, keyed by repo-root hash.
# Override via HYDRA_CHECKPOINTS_DIR env var (useful for test isolation).
def _default_hydra_home() -> Path:
    override = os.environ.get("HYDRA_CHECKPOINTS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".hydraAgent" / "edit_checkpoints"


# ---------------------------------------------------------------------------
# Atomic write helper (single-writer: matches apply_patch contract)
# ---------------------------------------------------------------------------

def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write `payload` to `target` atomically (sibling-temp + os.replace).

    This is the SAME atomic pattern used by apply_patch — the single sanctioned
    write path.  Callers must not open-code raw write()s for undo.
    """
    atomic_write_bytes(target, payload)


# ---------------------------------------------------------------------------
# CheckpointStore
# ---------------------------------------------------------------------------

@dataclass
class CheckpointStore:
    """Bounded, ordered stack of pre-image snapshots for a single repo root.

    Parameters
    ----------
    repo_root:
        The workspace root being worked on.  Used to derive the storage key.
        The store lives OUTSIDE this directory.
    max_entries:
        Maximum number of checkpoint entries to retain.
    max_bytes:
        Maximum total pre-image bytes to retain.
    _base_dir:
        Override the base directory for checkpoint storage.  When None (the
        default), the base is determined by HYDRA_CHECKPOINTS_DIR env var or
        ~/.hydraAgent/edit_checkpoints.  Tests use this to redirect writes to
        a tmp dir so they never touch the real store.
    """

    repo_root: Path
    max_entries: int = DEFAULT_MAX_ENTRIES
    max_bytes: int = DEFAULT_MAX_BYTES
    _base_dir: Path | None = field(default=None, repr=False)

    # Derived on first access via property
    _storage_root: Path = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).expanduser().resolve()
        if self._base_dir is not None:
            self._base_dir = Path(self._base_dir).expanduser().resolve()

    @property
    def storage_root(self) -> Path:
        """Directory where this store's checkpoint files live.

        Located at <base>/<sha256-of-repo-root>/ so it is always OUTSIDE the
        target repo.  Base is _base_dir if set, else HYDRA_CHECKPOINTS_DIR env
        var, else ~/.hydraAgent/edit_checkpoints.
        """
        if self._storage_root is None:
            base = self._base_dir if self._base_dir is not None else _default_hydra_home()
            root_hash = hashlib.sha256(str(self.repo_root).encode()).hexdigest()[:16]
            self._storage_root = base / root_hash
            self._storage_root.mkdir(parents=True, exist_ok=True)
        return self._storage_root

    @property
    def _index_path(self) -> Path:
        return self.storage_root / "index.json"

    def _read_index(self) -> list[dict[str, Any]]:
        if not self._index_path.exists():
            return []
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []

    def _write_index(self, entries: list[dict[str, Any]]) -> None:
        payload = json.dumps(entries, indent=2).encode("utf-8")
        atomic_write_bytes(self._index_path, payload)

    def _content_path(self, seq: int) -> Path:
        return self.storage_root / f"snap-{seq:08d}.bin"

    def record(
        self,
        *,
        target_path: Path,
        op: str,
        pre_image: bytes | None,
    ) -> None:
        """Record a pre-image snapshot BEFORE the mutation is committed.

        Parameters
        ----------
        target_path:
            Absolute path of the file being mutated.
        op:
            "modify" if the file existed, "create" if it did not.
        pre_image:
            The exact bytes of the file before mutation.  Pass None (or bytes())
            for a create (pre-image ABSENT — the undo will delete the file).
        """
        with locked_path(self._index_path):
            self._record(target_path=target_path, op=op, pre_image=pre_image)

    def _record(self, *, target_path: Path, op: str, pre_image: bytes | None,
                prune: bool = True) -> None:
        entries = self._read_index()

        # Derive next seq
        seq = (entries[-1]["seq"] + 1) if entries else 1

        # Write pre-image content file
        content_file = self._content_path(seq)
        if op == "create" or pre_image is None:
            # No content file needed — sentinel in index is enough
            content_sha = ""
            content_size = 0
        else:
            content_sha = hashlib.sha256(pre_image).hexdigest()
            content_size = len(pre_image)
            atomic_write_bytes(content_file, pre_image)

        entry: dict[str, Any] = {
            "seq": seq,
            "ts": time.time(),
            "path": str(target_path.resolve()),
            "op": op,
            "pre_image_sha": content_sha,
            "pre_image_size": content_size,
            # Expose raw pre_image in-memory for callers (tests); not persisted inline
        }
        entries.append(entry)

        if prune:
            self._gc(entries)
        else:
            self._write_index(entries)

    def write_bytes(self, target: Path, payload: bytes, *, overwrite: bool = True) -> None:
        """Snapshot and publish a file under one workspace checkpoint lock."""
        target = target.resolve()
        if not target.is_relative_to(self.repo_root) or target == self.repo_root:
            raise ValueError("checkpoint target must stay inside the workspace")
        with locked_path(self._index_path):
            pre_image = self._capture_pre_image(target)
            self._record(target_path=target, op="create" if pre_image is None else "modify",
                         pre_image=pre_image, prune=False)
            try:
                atomic_write_bytes(target, payload, overwrite=overwrite)
            except OSError:
                entry = self._pop()
                if entry is not None:
                    self._delete_content(entry["seq"])
                raise
            # Keep every prior recovery point until publication succeeds.
            self._prune_after_publish()

    def _capture_pre_image(self, target: Path) -> bytes | None:
        if self.max_entries < 1 or self.max_bytes < 0:
            raise OSError("invalid undo snapshot capacity; change not applied")
        if not target.exists():
            return None
        with target.open("rb") as stream:
            pre_image = stream.read(self.max_bytes + 1)
        if len(pre_image) > self.max_bytes:
            raise OSError("file exceeds undo snapshot capacity; change not applied")
        return pre_image

    def _prune_after_publish(self) -> None:
        entries = self._read_index()
        if (len(entries) <= self.max_entries
                and sum(entry["pre_image_size"] for entry in entries) <= self.max_bytes):
            return
        try:
            self._gc(entries)
        except OSError as exc:
            log.warning("File change committed; undo retention cleanup failed; "
                        "stored snapshots remain available: %s", exc)

    def _gc(self, entries: list[dict[str, Any]]) -> None:
        """Commit bounded history before deleting any evicted snapshot bytes."""
        evicted = []
        # Cap by entry count
        while len(entries) > self.max_entries:
            evicted.append(entries.pop(0))

        # Cap by total bytes
        total = sum(e["pre_image_size"] for e in entries)
        while total > self.max_bytes and entries:
            oldest = entries.pop(0)
            total -= oldest["pre_image_size"]
            evicted.append(oldest)

        self._write_index(entries)
        self._delete_contents(evicted)

    def _delete_contents(self, entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            self._delete_content(entry["seq"])

    def _delete_content(self, seq: int) -> None:
        p = self._content_path(seq)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    def pop(self) -> dict[str, Any] | None:
        """Remove and return the most-recent entry.  Returns None on empty stack."""
        with locked_path(self._index_path):
            return self._pop()

    def _pop(self) -> dict[str, Any] | None:
        entries = self._read_index()
        if not entries:
            return None
        entry = entries.pop()
        self._write_index(entries)
        return entry

    def peek_all(self) -> list[dict[str, Any]]:
        """Return all entries (oldest first) without modifying the stack."""
        return list(self._read_index())

    def load_pre_image(self, entry: dict[str, Any]) -> bytes | None:
        """Load the pre-image bytes for an entry.

        Returns None (ABSENT) for op=create entries — the pre-image is the
        sentinel ABSENT, meaning the file did not exist before the mutation.
        The ABSENT constant is the canonical marker; None is the in-memory
        representation.
        """
        if entry["op"] == "create" or entry.get("pre_image_sha") == ABSENT or not entry["pre_image_sha"]:
            # ABSENT: file was created from scratch — undo must DELETE it
            return None
        content_file = self._content_path(entry["seq"])
        if not content_file.exists():
            return None
        return content_file.read_bytes()


# ---------------------------------------------------------------------------
# Default-store factory (used by apply_patch default-on logic)
# ---------------------------------------------------------------------------

def get_default_store(repo_root: Path) -> CheckpointStore:
    """Return a CheckpointStore for repo_root using the current env-derived base.

    This is the factory that apply_patch() and create_file() call when no
    explicit checkpoint_store is supplied.  Using a factory (rather than
    constructing inline) keeps the base-dir env-override testable without
    touching the real ~/.hydraAgent store.

    The store uses the default retention limits (200 entries / 50 MiB).
    """
    return CheckpointStore(repo_root)


# ---------------------------------------------------------------------------
# Public list_stack helper
# ---------------------------------------------------------------------------

def list_stack(store: CheckpointStore) -> list[dict[str, Any]]:
    """Return a list of stack entries enriched with the actual pre-image bytes.

    Each entry dict has:
      seq, ts, path, op, pre_image_sha, pre_image_size,
      pre_image (bytes | None — None means ABSENT / create op)

    The list is ordered oldest-first.  Tests use this to inspect state.
    """
    raw = store.peek_all()
    result = []
    for e in raw:
        enriched = dict(e)
        enriched["pre_image"] = store.load_pre_image(e)
        result.append(enriched)
    return result


# ---------------------------------------------------------------------------
# Public undo function
# ---------------------------------------------------------------------------

def undo(store: CheckpointStore, *, n: int = 1) -> str:
    """Restore the most-recent n snapshots in LIFO order.

    FILE-ONLY UNDO — this restores file bytes only.  External side effects
    (network calls, database writes, shell commands, etc.) are NOT reversed.
    The operator must be aware that undo is limited to file system state.

    Parameters
    ----------
    store:
        The CheckpointStore to pop from.
    n:
        Number of snapshots to restore (default 1).  If the stack has fewer
        than n entries, all available entries are restored — no crash.

    Returns
    -------
    str:
        Human-readable summary of what was (or was not) restored.
    """
    if n < 1:
        return "undo: nothing to do (n < 1)."
    with locked_path(store._index_path):
        return _undo_locked(store, n=n)


def _undo_locked(store: CheckpointStore, *, n: int) -> str:
    messages: list[str] = []
    messages.append(
        "NOTE: undo reverts files only — external side effects (network, db, shell) "
        "are NOT reversed."
    )

    restored = 0
    for _ in range(n):
        entry = store._pop()
        if entry is None:
            if restored == 0:
                return (
                    "undo: checkpoint stack is empty — nothing to restore.\n"
                    "NOTE: undo reverts files only — external side effects "
                    "(network, db, shell) are NOT reversed."
                )
            # Fewer entries than requested — stop cleanly
            break

        target = Path(entry["path"])
        op = entry["op"]

        if op == "create":
            # Pre-image is ABSENT: the file was created, undo by deleting it
            if target.exists():
                target.unlink()
                messages.append(f"  deleted (undo create): {target}")
            else:
                messages.append(f"  skipped (already gone): {target}")
        else:
            # op == "modify": restore exact pre-image bytes via atomic writer
            pre_image = store.load_pre_image(entry)
            if pre_image is None:
                messages.append(f"  WARNING: pre-image missing for {target} — skipped")
                continue
            # Clean up the stored snapshot file after loading
            store._delete_content(entry["seq"])
            _atomic_write_bytes(target, pre_image)
            messages.append(f"  restored: {target}")

        restored += 1

    if restored == 0:
        messages.append("undo: nothing was restored.")
    else:
        messages.append(f"undo: restored {restored} snapshot(s).")

    return "\n".join(messages)
