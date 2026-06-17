"""Persistent session memory for HydraAgent.

This module provides persistent conversation history that survives
between separate chat sessions and command executions.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Optional

from hydra.file_lock import locked_path


SESSION_MEMORY_DIR = Path.home() / ".hydra-sessions"
SESSION_MEMORY_SCHEMA = "hydra.session_memory.v1"

# Sane default for how many prior messages to reload into the LLM context at chat
# startup. A few recent exchanges (~4-5 user/assistant pairs) is plenty for
# continuity without bloating the window before the operator types. The old
# default was 40 (chat) / 48 (telegram), which the operator flagged as "obviously
# broken". Still fully configurable via --session-history-limit and the
# HYDRA_SESSION_HISTORY_LIMIT env override.
DEFAULT_STARTUP_HISTORY_LIMIT = 10

# How many compaction/rotation backups to retain per session. Older duplicates
# are reaped; the live session file and recent backups are NEVER deleted.
DEFAULT_BACKUP_KEEP = 5

# Env override for the startup history reload count (operator-tunable without a flag).
SESSION_HISTORY_LIMIT_ENV = "HYDRA_SESSION_HISTORY_LIMIT"


def resolve_startup_history_limit() -> int:
    """Resolve the startup history reload count.

    Precedence: ``HYDRA_SESSION_HISTORY_LIMIT`` env (if a valid non-negative int)
    else ``DEFAULT_STARTUP_HISTORY_LIMIT``. The CLI ``--session-history-limit``
    flag still overrides this at parse time.
    """
    raw = os.environ.get(SESSION_HISTORY_LIMIT_ENV)
    if raw is not None:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_STARTUP_HISTORY_LIMIT
        if value >= 0:
            return value
    return DEFAULT_STARTUP_HISTORY_LIMIT


@dataclass
class SessionEntry:
    """One entry in a session memory log."""
    timestamp: str  # ISO format timestamp
    role: str       # "user", "assistant", "system", or "tool"
    content: str    # Message content
    metadata: Dict[str, Any]  # Additional metadata


class SessionMemoryError(Exception):
    """Session memory operation failed."""


def _ensure_session_dir() -> Path:
    """Ensure the session memory directory exists."""
    SESSION_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_MEMORY_DIR


def _session_file_path(session_id: str) -> Path:
    """Get the file path for a session."""
    safe_id = "".join(c for c in session_id if c.isalnum() or c in "-_.").strip(".-")
    if not safe_id:
        raise SessionMemoryError("Invalid session ID")
    return _ensure_session_dir() / f"{safe_id}.jsonl"


def create_session(session_id: str, initial_context: Optional[str] = None) -> None:
    """Create a new session with optional initial context."""
    session_file = _session_file_path(session_id)
    if session_file.exists():
        raise SessionMemoryError(f"Session {session_id} already exists")

    # Create session header
    header = {
        "schema": SESSION_MEMORY_SCHEMA,
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entries": 0
    }

    with open(session_file, "w") as f:
        f.write(json.dumps(header) + "\n")

    # Add initial context if provided
    if initial_context:
        add_message(session_id, "system", initial_context, {"source": "session_creation"})


def rotate_session(session_id: str, initial_context: Optional[str] = None) -> Dict[str, Any]:
    """Archive the current session file and create a fresh session with the same id."""
    session_file = _session_file_path(session_id)
    archive_path: Path | None = None
    # Lock spans the archive + truncate so a concurrent append/rewrite from the
    # other process cannot interleave. The follow-up add_message re-takes the
    # lock itself, so it stays OUTSIDE this block (no nested same-process flock).
    with locked_path(session_file):
        if session_file.exists():
            archive_path = _archive_session_file(session_file, reason="rotated")
        _write_session_records(session_file, session_id, [])
    if initial_context:
        add_message(session_id, "system", initial_context, {"source": "session_creation"})
    return {
        "schema": f"{SESSION_MEMORY_SCHEMA}.rotate",
        "rotated": archive_path is not None,
        "session_id": session_id,
        "archive_path": str(archive_path) if archive_path else "",
    }


def compact_session(
    session_id: str,
    *,
    keep_last: int = 24,
    max_entries: int = 80,
    max_bytes: int = 128 * 1024,
) -> Dict[str, Any]:
    """Compact a noisy session while archiving the full original JSONL first."""
    if keep_last <= 0:
        raise SessionMemoryError(f"keep_last must be positive, got {keep_last}")
    if max_entries <= 0:
        raise SessionMemoryError(f"max_entries must be positive, got {max_entries}")
    if max_bytes <= 0:
        raise SessionMemoryError(f"max_bytes must be positive, got {max_bytes}")

    session_file = _session_file_path(session_id)
    if not session_file.exists():
        raise SessionMemoryError(f"Session {session_id} does not exist")
    # The whole read -> decide -> rewrite is one cross-process critical section so
    # a concurrent append/rewrite cannot interleave and lose data.
    with locked_path(session_file):
        header, entries = _read_session_records(session_file)
        size = session_file.stat().st_size
        if len(entries) <= max_entries and size <= max_bytes:
            return {
                "schema": f"{SESSION_MEMORY_SCHEMA}.compact",
                "compacted": False,
                "session_id": session_id,
                "entries_before": len(entries),
                "entries_after": len(entries),
                "bytes_before": size,
                "backup_path": "",
            }

        backup_path = _archive_session_file(session_file, reason="compact")
        preserved_system = next((entry for entry in entries if entry.get("role") == "system"), None)
        body = [entry for entry in entries if entry is not preserved_system]
        recent = body[-keep_last:]
        omitted = max(0, len(body) - len(recent))
        compact_note = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "role": "system",
            "content": (
                f"Session compacted automatically: {omitted} older messages omitted; "
                f"{len(recent)} recent messages retained. Full archive: {backup_path}"
            ),
            "metadata": {
                "source": "session_compaction",
                "backup_path": str(backup_path),
                "omitted_entries": omitted,
                "retained_entries": len(recent),
            },
        }
        compacted_entries = []
        if preserved_system is not None:
            compacted_entries.append(preserved_system)
        compacted_entries.append(compact_note)
        compacted_entries.extend(recent)
        _write_session_records(
            session_file,
            str(header.get("session_id") or session_id),
            compacted_entries,
            created_at=str(header.get("created_at") or datetime.now(timezone.utc).isoformat()),
        )
        result = {
            "schema": f"{SESSION_MEMORY_SCHEMA}.compact",
            "compacted": True,
            "session_id": session_id,
            "entries_before": len(entries),
            "entries_after": len(compacted_entries),
            "bytes_before": size,
            "backup_path": str(backup_path),
        }
    # Reap stale backups after a successful compaction (live file + recent kept).
    try:
        reap_session_backups(session_id, keep=DEFAULT_BACKUP_KEEP)
    except SessionMemoryError:
        pass
    return result


def add_message(session_id: str, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    """Add a message to a session.

    Holds the per-session cross-process lock so an append cannot interleave with
    a concurrent full rewrite (compaction/rotation) from the other process and
    lose data.
    """
    session_file = _session_file_path(session_id)
    if not session_file.exists():
        raise SessionMemoryError(f"Session {session_id} does not exist")

    entry = SessionEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        role=role,
        content=content,
        metadata=metadata or {}
    )

    with locked_path(session_file):
        with open(session_file, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")


def append_message_locked(
    session_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Locked append that creates the session if missing.

    Convenience wrapper used by callers (and the concurrency tests) that want a
    single atomic-under-lock "ensure exists + append" against a session shared by
    the chat process and the telegram listener.
    """
    session_file = _session_file_path(session_id)
    with locked_path(session_file):
        if not session_file.exists():
            create_session(session_id)
        entry = SessionEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            role=role,
            content=content,
            metadata=metadata or {},
        )
        with open(session_file, "a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")


def get_session_messages(session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Get messages from a session, optionally limited to recent messages."""
    session_file = _session_file_path(session_id)
    if not session_file.exists():
        raise SessionMemoryError(f"Session {session_id} does not exist")

    messages = []
    with open(session_file, "r") as f:
        lines = f.readlines()

    # Skip header and process entries
    entry_lines = lines[1:]  # Skip header

    if limit is not None:
        if limit <= 0:
            return []
        entry_lines = entry_lines[-limit:]  # Get last N entries

    for line in entry_lines:
        if line.strip():
            try:
                entry_data = json.loads(line)
                messages.append({
                    "role": entry_data["role"],
                    "content": entry_data["content"],
                    "timestamp": entry_data["timestamp"]
                })
            except (json.JSONDecodeError, KeyError):
                # Skip malformed entries
                continue

    return messages


def _read_session_records(session_file: Path) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    try:
        lines = session_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SessionMemoryError(f"Could not read session {session_file}: {exc}") from exc
    if not lines:
        return {}, []
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError:
        header = {}
    entries: List[Dict[str, Any]] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return header, entries


def _write_session_records(
    session_file: Path,
    session_id: str,
    entries: List[Dict[str, Any]],
    *,
    created_at: str | None = None,
) -> None:
    header = {
        "schema": SESSION_MEMORY_SCHEMA,
        "session_id": session_id,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "entries": len(entries),
    }
    session_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [json.dumps(header), *[json.dumps(entry) for entry in entries]]
    session_file.write_text("\n".join(payload) + "\n", encoding="utf-8")


def _archive_session_file(session_file: Path, *, reason: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = session_file.with_name(f"{session_file.stem}.{reason}-{stamp}{session_file.suffix}")
    suffix = 1
    while archive.exists():
        archive = session_file.with_name(
            f"{session_file.stem}.{reason}-{stamp}-{suffix}{session_file.suffix}"
        )
        suffix += 1
    archive.write_text(session_file.read_text(encoding="utf-8"), encoding="utf-8")
    return archive


def reap_session_backups(session_id: str, *, keep: int = DEFAULT_BACKUP_KEEP) -> Dict[str, Any]:
    """Delete stale per-session backups, keeping only the most recent ``keep``.

    A "backup" is a sibling file named ``<safe_id>.<reason>-<stamp>.jsonl`` (the
    compaction/rotation/loop archives). The LIVE session file ``<safe_id>.jsonl``
    is NEVER a backup and is NEVER deleted, and only this session's backups are
    considered (other sessions are untouched). Backups are ordered by their ISO
    timestamp embedded in the filename (``...-<stamp>.jsonl``); recency is judged
    by that stamp regardless of the reason prefix, with mtime as a tiebreaker.
    """
    if keep < 1:
        raise SessionMemoryError(f"keep must be >= 1, got {keep}")
    session_file = _session_file_path(session_id)
    safe_stem = session_file.stem  # e.g. "default_chat_session"
    directory = session_file.parent
    if not directory.is_dir():
        return {
            "schema": f"{SESSION_MEMORY_SCHEMA}.reap",
            "session_id": session_id,
            "kept": 0,
            "deleted": 0,
            "deleted_paths": [],
        }
    # Backups carry an extra ".<reason>-<stamp>" segment before .jsonl; the live
    # file is exactly "<safe_stem>.jsonl" and must be excluded.
    backups = [
        p
        for p in directory.glob(f"{safe_stem}.*.jsonl")
        if p.name != session_file.name
    ]

    def _recency_key(p: Path) -> tuple[str, float]:
        # Newest LAST. Sort by the embedded ISO stamp (so a recent loop-backup is
        # not outranked by an old compact-backup just because of its prefix);
        # fall back to mtime when no stamp is present.
        match = re.search(r"(\d{8}T\d{6}Z)", p.name)
        stamp = match.group(1) if match else ""
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (stamp, mtime)

    backups.sort(key=_recency_key)
    stale = backups[:-keep] if len(backups) > keep else []
    deleted_paths: List[str] = []
    for path in stale:
        try:
            path.unlink()
            deleted_paths.append(str(path))
        except FileNotFoundError:
            continue
    return {
        "schema": f"{SESSION_MEMORY_SCHEMA}.reap",
        "session_id": session_id,
        "kept": min(len(backups), keep),
        "deleted": len(deleted_paths),
        "deleted_paths": deleted_paths,
    }


def list_sessions() -> List[Dict[str, Any]]:
    """List all available sessions."""
    session_dir = _ensure_session_dir()
    sessions = []

    for file_path in session_dir.glob("*.jsonl"):
        try:
            with open(file_path, "r") as f:
                header_line = f.readline()
                if header_line:
                    header = json.loads(header_line)
                    entries = sum(1 for line in f if line.strip())
                    sessions.append({
                        "session_id": header.get("session_id", file_path.stem),
                        "created_at": header.get("created_at"),
                        "entries": entries,
                        "file_path": str(file_path)
                    })
        except (json.JSONDecodeError, OSError):
            continue

    return sorted(sessions, key=lambda s: s.get("created_at", ""), reverse=True)


def delete_session(session_id: str) -> None:
    """Delete a session."""
    session_file = _session_file_path(session_id)
    if session_file.exists():
        session_file.unlink()


def session_exists(session_id: str) -> bool:
    """Check if a session exists."""
    try:
        session_file = _session_file_path(session_id)
        return session_file.exists()
    except SessionMemoryError:
        return False
