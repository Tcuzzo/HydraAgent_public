"""Regression tests for the cross-platform lock backend.

The bug: ``hydra/file_lock.py`` imported ``fcntl`` in a try/except and, when it
was missing (native Windows), ``locked_path()`` silently became a NO-OP that
just yielded. Concurrent writers then clobbered each other and the operator's
approvals were LOST — the exact race the lock exists to prevent. Windows CI had
been aborting during collection, so the failure was invisible until collection
was fixed and ``test_approval_queue_concurrent_appends_lose_no_update`` failed
on Windows with ``lost an approval``.

``acquire_singleton_lock()`` was worse: it returned "you own it" to EVERY caller
on Windows, so two Telegram pollers each believed they were the only one and
409'd against each other, silently breaking the approval seam itself.

Both sides of this lock are HydraAgent's own processes, so the protocol is ours
end to end and Windows can implement it (``msvcrt.locking``). These tests pin
that down. They run the WINDOWS code path on every OS: on native Windows against
the real ``msvcrt``, and on POSIX against an fcntl-backed stand-in that gives
``msvcrt.locking`` semantics (lock N bytes at the handle's current position),
so the Windows branch cannot rot unseen between Windows CI runs.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Windows-backend simulation
# ---------------------------------------------------------------------------


class _FcntlBackedMsvcrt:
    """``msvcrt.locking``-shaped byte-range locking implemented over fcntl.

    Only used on POSIX to drive the Windows branch of ``file_lock``. Like the
    real msvcrt, it locks ``nbytes`` starting at the fd's CURRENT position and
    raises ``OSError`` when a non-blocking request cannot be granted.
    """

    LK_UNLCK = 0
    LK_LOCK = 1
    LK_NBLCK = 2

    def __init__(self, fcntl_module) -> None:
        self._fcntl = fcntl_module

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        f = self._fcntl
        if mode == self.LK_UNLCK:
            op = f.LOCK_UN
        elif mode == self.LK_NBLCK:
            op = f.LOCK_EX | f.LOCK_NB
        else:
            op = f.LOCK_EX
        f.lockf(fd, op, nbytes, 0, os.SEEK_CUR)


def _force_windows_backend() -> None:
    """Put ``hydra.file_lock`` into the configuration it has on native Windows.

    On real Windows this is already the live configuration, so it is a no-op and
    the test exercises the shipped ``msvcrt`` path verbatim.
    """
    import hydra.file_lock as fl

    if fl._HAVE_MSVCRT and not fl._HAVE_FCNTL:
        return  # native Windows: the real backend IS what we want to test
    if not fl._HAVE_FCNTL:
        raise RuntimeError("no lock backend available to simulate the Windows path")
    fl.msvcrt = _FcntlBackedMsvcrt(fl.fcntl)
    fl._HAVE_MSVCRT = True
    fl.fcntl = None
    fl._HAVE_FCNTL = False


# ---------------------------------------------------------------------------
# The operator's surface: concurrent approval appends on the Windows backend
# ---------------------------------------------------------------------------


def _win_backend_approval_writer(queue_path: str, idx: int, barrier_path: str) -> None:
    """Separate OS process: append one approval through the WINDOWS lock path."""
    _force_windows_backend()

    from hydra import workbench_approvals as wa

    bp = Path(barrier_path)
    while not bp.exists():
        time.sleep(0.001)
    rec = wa.create_request(
        request_id=f"req-{idx}",
        run_id=f"run-{idx}",
        tool_name="bash",
        risk_tier="risky",
        summary=f"req {idx}",
        arguments_preview={"i": idx},
    )
    time.sleep(0.02)  # widen the race window
    wa.append_record(Path(queue_path), rec)


def test_windows_backend_loses_no_approval_on_concurrent_append(tmp_path: Path) -> None:
    """The Windows lock backend must serialize concurrent approval appends.

    Before the fix this path was a no-op and approvals vanished.
    """
    from hydra import workbench_approvals as wa

    queue = tmp_path / "approvals.jsonl"
    barrier = tmp_path / "win-approve-go"
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_win_backend_approval_writer, args=(str(queue), i, str(barrier)))
        for i in range(8)
    ]
    for p in procs:
        p.start()
    barrier.write_text("go")
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, f"writer {p.pid} exited {p.exitcode}"

    ids = {r.request_id for r in wa.load_records(queue)}
    assert ids == {f"req-{i}" for i in range(8)}, f"lost an approval: {sorted(ids)}"


# ---------------------------------------------------------------------------
# No silent no-op, ever
# ---------------------------------------------------------------------------


def test_missing_backend_raises_loudly_and_never_yields_unlocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no lock backend, locked_path must FAIL LOUDLY, not silently yield.

    The original bug was precisely this branch yielding an unlocked path.
    """
    import hydra.file_lock as fl

    monkeypatch.setattr(fl, "_HAVE_FCNTL", False)
    monkeypatch.setattr(fl, "fcntl", None)
    monkeypatch.setattr(fl, "_HAVE_MSVCRT", False)
    monkeypatch.setattr(fl, "msvcrt", None)

    target = tmp_path / "data.jsonl"
    with pytest.raises(fl.FileLockUnsupportedError):
        with fl.locked_path(target):
            pytest.fail("locked_path yielded without a lock — silent no-op regression")

    with pytest.raises(fl.FileLockUnsupportedError):
        fl.acquire_singleton_lock(tmp_path / "singleton")


# ---------------------------------------------------------------------------
# The singleton guard must tell the TRUTH (one bot = one poller)
# ---------------------------------------------------------------------------


def _singleton_holder(lock_base: str, ready_path: str, stop_path: str, windows_backend: bool) -> None:
    """Separate OS process: hold the singleton lock until told to stop."""
    if windows_backend:
        _force_windows_backend()

    from hydra.file_lock import acquire_singleton_lock

    acquired, fd, pid = acquire_singleton_lock(Path(lock_base))
    if not acquired or fd is None:
        raise SystemExit(3)  # the FIRST caller must always win
    Path(ready_path).write_text(str(pid))
    stop = Path(stop_path)
    for _ in range(6000):
        if stop.exists():
            break
        time.sleep(0.01)
    fd.close()


def _assert_second_caller_is_refused(tmp_path: Path, *, windows_backend: bool) -> None:
    from hydra.file_lock import acquire_singleton_lock

    lock_base = tmp_path / "listener-bot"
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    ctx = mp.get_context("spawn")
    holder = ctx.Process(
        target=_singleton_holder, args=(str(lock_base), str(ready), str(stop), windows_backend)
    )
    holder.start()
    try:
        for _ in range(3000):
            if ready.exists():
                break
            time.sleep(0.01)
        assert ready.exists(), "holder process never acquired the singleton lock"
        holder_pid = int(ready.read_text().strip())

        if windows_backend:
            _force_windows_backend()

        acquired, fd, reported = acquire_singleton_lock(lock_base)
        assert acquired is False, (
            "singleton lock FAILED OPEN: a second poller was told it owns the lock "
            "while a live holder exists — this is how two Telegram pollers 409 "
            "each other and the operator's approvals never arrive"
        )
        assert fd is None
        assert reported == holder_pid, f"expected holder pid {holder_pid}, got {reported}"
    finally:
        stop.write_text("stop")
        holder.join(timeout=60)


def test_singleton_lock_refuses_second_holder_on_native_backend(tmp_path: Path) -> None:
    """A second poller must be refused and told the live holder's pid."""
    _assert_second_caller_is_refused(tmp_path, windows_backend=False)


def test_singleton_lock_refuses_second_holder_on_windows_backend(tmp_path: Path) -> None:
    """Same truth on the Windows backend — it used to say 'you own it' to all."""
    _assert_second_caller_is_refused(tmp_path, windows_backend=True)


def test_singleton_lock_is_released_when_holder_process_exits(tmp_path: Path) -> None:
    """A dead/orphaned poller must never wedge a fresh start."""
    from hydra.file_lock import acquire_singleton_lock

    lock_base = tmp_path / "listener-bot"
    ready = tmp_path / "ready"
    stop = tmp_path / "stop"
    ctx = mp.get_context("spawn")
    holder = ctx.Process(target=_singleton_holder, args=(str(lock_base), str(ready), str(stop), False))
    holder.start()
    for _ in range(3000):
        if ready.exists():
            break
        time.sleep(0.01)
    assert ready.exists()
    stop.write_text("stop")
    holder.join(timeout=60)
    assert holder.exitcode == 0

    acquired, fd, pid = acquire_singleton_lock(lock_base)
    try:
        assert acquired is True, "a dead holder's lock wedged a fresh poller"
        assert pid == os.getpid()
    finally:
        if fd is not None:
            fd.close()
