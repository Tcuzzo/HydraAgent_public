"""Cross-process file lock for serialized read-modify-write of shared JSONL.

HydraAgent runs the interactive chat process AND an always-on telegram listener
(systemd ``hydra-telegram.service``) at the same time. Both read the SAME
``~/.hydra-sessions/default_chat_session.jsonl`` and the SAME workbench/approval
queues, then write them back. The writers used an atomic ``os.replace`` — which
makes a single WRITE atomic — but nothing held a lock across the *read* and the
*write*, so two processes could each load the old state, each append their own
update, and the second write would clobber the first (a lost update). That is
the operator's "race-time" symptom.

This module provides a blocking advisory ``fcntl.flock`` (POSIX) so that a
process *waits its turn* for the whole read-modify-write critical section. On
platforms without ``fcntl`` (e.g. native Windows) it degrades to a no-op so the
runtime still works single-process.

The lock is held on a sidecar ``<path>.lock`` file (NOT the data file itself), so
the data file can still be swapped via ``os.replace`` while the lock is held —
``os.replace`` replaces the inode, and we hold the lock on a stable sidecar inode.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO, Iterator, Optional, Tuple

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False


def lock_path_for(path: Path) -> Path:
    """The sidecar lock file path for a given data file path."""
    return path.with_name(path.name + ".lock")


@contextlib.contextmanager
def locked_path(path: Path) -> Iterator[Path]:
    """Hold an exclusive cross-process lock for the lifetime of the block.

    ``path`` is the data file being guarded; the lock is taken on
    ``<path>.lock`` so atomic ``os.replace`` of the data file keeps working.
    The call BLOCKS until the lock is acquired, so concurrent writers serialize
    instead of clobbering each other. Always released on exit (including errors).
    """
    path = Path(path)
    if not _HAVE_FCNTL:  # pragma: no cover - non-POSIX
        yield path
        return

    lp = lock_path_for(path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    # Open (create) the lock sidecar; keep the fd for the whole critical section.
    fd = open(lp, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)  # blocks until acquired
        try:
            yield path
        finally:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
    finally:
        fd.close()


def acquire_singleton_lock(path: Path) -> Tuple[bool, Optional[IO], Optional[int]]:
    """Try to become the SOLE holder of ``path`` for this process's lifetime.

    Telegram allows exactly ONE ``getUpdates`` consumer per bot. If two pollers
    run, both get HTTP 409 ``Conflict: terminated by other getUpdates request``
    and neither receives the operator's Approve/Deny taps -- which silently
    breaks the approval re-execution seam. This guard makes a second poller
    detect the live one and refuse, so one bot is always exactly one poller.

    Uses a NON-blocking exclusive ``fcntl.flock``. The lock is an attribute of
    the open file description and is released by the kernel automatically when
    the holder exits or crashes, so a stale lock file from a dead/orphaned
    poller never blocks a fresh start (no manual pid-liveness check needed).

    Returns ``(acquired, fd, holder_pid)``:
      * acquired=True  -> we own it; KEEP ``fd`` open for the whole process
        lifetime (do not close it, or the lock releases). ``holder_pid`` is our
        own pid.
      * acquired=False -> another live poller holds it; ``fd`` is None and
        ``holder_pid`` is the live holder's pid if it could be read (else None).

    On platforms without ``fcntl`` this degrades to always-acquired (single
    process assumption), matching ``locked_path``.
    """
    path = Path(path)
    if not _HAVE_FCNTL:  # pragma: no cover - non-POSIX
        return True, None, os.getpid()

    lp = lock_path_for(path)
    lp.parent.mkdir(parents=True, exist_ok=True)
    fd = open(lp, "a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another process holds the lock. Read its recorded pid for a clear
        # message; never raises if the file is empty/garbled.
        holder_pid: Optional[int] = None
        try:
            fd.seek(0)
            raw = fd.read().strip()
            if raw:
                holder_pid = int(raw.splitlines()[0].strip())
        except (ValueError, OSError):
            holder_pid = None
        fd.close()
        return False, None, holder_pid

    # We own it. Record our pid so a refused second poller can name us.
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(f"{os.getpid()}\n")
        fd.flush()
    except OSError:
        pass
    return True, fd, os.getpid()
