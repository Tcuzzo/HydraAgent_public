"""Cross-process file lock for serialized read-modify-write of shared JSONL.

HydraAgent runs the interactive chat process AND an always-on telegram listener
(systemd ``hydra-telegram.service``) at the same time. Both read the SAME
``~/.hydra-sessions/default_chat_session.jsonl`` and the SAME workbench/approval
queues, then write them back. The writers used an atomic ``os.replace`` — which
makes a single WRITE atomic — but nothing held a lock across the *read* and the
*write*, so two processes could each load the old state, each append their own
update, and the second write would clobber the first (a lost update). That is
the operator's "race-time" symptom.

This module takes a REAL exclusive cross-process lock on every platform we ship
on: ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on native Windows. Both
sides of this lock are HydraAgent's own processes, so HydraAgent owns the whole
protocol and implements it end to end. There is deliberately NO no-op fallback:
a lock that silently does nothing loses the operator's approvals, which is worse
than not having one. If neither backend exists, we raise
``FileLockUnsupportedError`` loudly rather than pretend.

The lock is held on a sidecar ``<path>.lock`` file (NOT the data file itself), so
the data file can still be swapped via ``os.replace`` while the lock is held —
``os.replace`` replaces the inode, and we hold the lock on a stable sidecar inode.

Platform note — the two lock primitives are genuinely different, and we handle
that difference rather than paper over it:

* ``fcntl.flock`` is a whole-file advisory lock tied to the open file
  description; it has no byte ranges and does not restrict reads.
* ``msvcrt.locking`` locks a BYTE RANGE starting at the handle's current file
  position, and that range is MANDATORY — a process that does not hold it cannot
  read or write those bytes. So on Windows we lock a single byte at a fixed
  offset far past the pid record (``_WIN_LOCK_OFFSET``; Windows permits locking a
  range beyond end-of-file). The pid text at offset 0 stays unlocked, which is
  what lets a REFUSED singleton caller still read the live holder's pid — exactly
  the POSIX behavior.
* ``msvcrt``'s own blocking mode (``LK_LOCK``) gives up after ~10 seconds, which
  is not ``flock(LOCK_EX)`` semantics. To preserve "block until it is your turn"
  we poll ``LK_NBLCK`` with backoff instead.

Neither backend is re-entrant within a process, on purpose: that matches the
existing POSIX behavior, so nesting ``locked_path()`` on one path is a bug on
every platform alike rather than a platform-specific surprise.
"""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import IO, Iterator, Optional, Tuple

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - exercised on native Windows
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False

try:  # native Windows
    import msvcrt

    _HAVE_MSVCRT = True
except ImportError:  # pragma: no cover - exercised on POSIX
    msvcrt = None  # type: ignore[assignment]
    _HAVE_MSVCRT = False

# Windows byte-range lock: one byte, parked far beyond the pid record so the pid
# stays readable by a refused caller. Windows allows locking past end-of-file.
_WIN_LOCK_OFFSET = 0x10000000
_WIN_LOCK_BYTES = 1
# Poll interval bounds for emulating flock's "block until acquired" on Windows.
_WIN_POLL_MIN_S = 0.001
_WIN_POLL_MAX_S = 0.05

_UNSUPPORTED = (
    "no cross-process file lock backend available on this platform "
    "(neither fcntl nor msvcrt could be imported). HydraAgent refuses to run "
    "unlocked read-modify-write on shared state: a silent no-op lock loses "
    "operator approvals."
)


class FileLockUnsupportedError(RuntimeError):
    """Raised when no real lock backend exists. Never degrade to a no-op."""


def lock_path_for(path: Path) -> Path:
    """The sidecar lock file path for a given data file path."""
    return path.with_name(path.name + ".lock")


def _open_lock_file(lock_file: Path) -> IO[bytes]:
    """Open (creating if needed) the sidecar lock file for read+write.

    Binary and explicitly NOT append mode, so ``seek``/``truncate``/``write``
    mean the same thing on POSIX and Windows (append mode would force every
    write to end-of-file on Windows, and text mode would rewrite newlines).
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(str(lock_file), flags, 0o644)
    try:
        return os.fdopen(fd, "r+b")
    except BaseException:  # pragma: no cover - fdopen failure is not reachable in tests
        os.close(fd)
        raise


def _lock_exclusive(handle: IO[bytes], *, blocking: bool) -> bool:
    """Take the exclusive cross-process lock. True if acquired.

    ``blocking=True`` waits its turn indefinitely (flock LOCK_EX semantics) and
    therefore only returns True. ``blocking=False`` returns False when another
    live process holds it.
    """
    if _HAVE_FCNTL:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except OSError:
            if blocking:
                raise
            return False
        return True

    if _HAVE_MSVCRT:
        delay = _WIN_POLL_MIN_S
        while True:
            # msvcrt locks nbytes starting at the handle's CURRENT position.
            handle.seek(_WIN_LOCK_OFFSET)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _WIN_LOCK_BYTES)
                return True
            except OSError:
                if not blocking:
                    return False
                time.sleep(delay)
                delay = min(delay * 2, _WIN_POLL_MAX_S)

    raise FileLockUnsupportedError(_UNSUPPORTED)


def _unlock(handle: IO[bytes]) -> None:
    """Release the exclusive lock taken by ``_lock_exclusive``."""
    if _HAVE_FCNTL:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if _HAVE_MSVCRT:
        handle.seek(_WIN_LOCK_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _WIN_LOCK_BYTES)
        return
    raise FileLockUnsupportedError(_UNSUPPORTED)  # pragma: no cover - unreachable


@contextlib.contextmanager
def locked_path(path: Path) -> Iterator[Path]:
    """Hold an exclusive cross-process lock for the lifetime of the block.

    ``path`` is the data file being guarded; the lock is taken on
    ``<path>.lock`` so atomic ``os.replace`` of the data file keeps working.
    The call BLOCKS until the lock is acquired, so concurrent writers serialize
    instead of clobbering each other. Always released on exit (including errors).

    Raises ``FileLockUnsupportedError`` if the platform has no lock backend —
    it never yields unlocked.
    """
    path = Path(path)
    handle = _open_lock_file(lock_path_for(path))
    try:
        _lock_exclusive(handle, blocking=True)
        try:
            yield path
        finally:
            _unlock(handle)
    finally:
        handle.close()


def _read_holder_pid(handle: IO[bytes]) -> Optional[int]:
    """Best-effort read of the live holder's recorded pid; never raises."""
    try:
        handle.seek(0)
        raw = handle.read().strip()
        if raw:
            return int(raw.splitlines()[0].strip())
    except (ValueError, OSError):
        return None
    return None


def acquire_singleton_lock(path: Path) -> Tuple[bool, Optional[IO], Optional[int]]:
    """Try to become the SOLE holder of ``path`` for this process's lifetime.

    Telegram allows exactly ONE ``getUpdates`` consumer per bot. If two pollers
    run, both get HTTP 409 ``Conflict: terminated by other getUpdates request``
    and neither receives the operator's Approve/Deny taps -- which silently
    breaks the approval re-execution seam. This guard makes a second poller
    detect the live one and refuse, so one bot is always exactly one poller.

    Uses a NON-blocking exclusive lock (``fcntl.flock`` on POSIX,
    ``msvcrt.locking`` on Windows). On both platforms the lock belongs to the
    open file handle and the kernel drops it automatically when the holder exits
    or crashes, so a stale lock file from a dead/orphaned poller never blocks a
    fresh start (no manual pid-liveness check needed).

    Returns ``(acquired, fd, holder_pid)``:
      * acquired=True  -> we own it; KEEP ``fd`` open for the whole process
        lifetime (do not close it, or the lock releases). ``holder_pid`` is our
        own pid.
      * acquired=False -> another live poller holds it; ``fd`` is None and
        ``holder_pid`` is the live holder's pid if it could be read (else None).

    This answer is the TRUTH on every supported platform. It never reports
    "you own it" to two callers, and raises ``FileLockUnsupportedError`` rather
    than guessing on a platform with no lock backend.
    """
    path = Path(path)
    handle = _open_lock_file(lock_path_for(path))
    try:
        acquired = _lock_exclusive(handle, blocking=False)
    except BaseException:
        handle.close()
        raise

    if not acquired:
        # Another process holds the lock. Read its recorded pid for a clear
        # message; on Windows the pid bytes sit outside the locked range on
        # purpose so this read is permitted.
        holder_pid = _read_holder_pid(handle)
        handle.close()
        return False, None, holder_pid

    # We own it. Record our pid so a refused second poller can name us.
    try:
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()}\n".encode("utf-8"))
        handle.flush()
    except OSError:
        pass
    return True, handle, os.getpid()
