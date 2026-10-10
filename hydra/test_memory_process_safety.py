"""Concurrent persistence and process cleanup regression contracts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import errno
from threading import Barrier
from types import SimpleNamespace

import pytest


def test_concurrent_working_memory_writers_preserve_every_entry(tmp_path, monkeypatch):
    from hydra import working_memory as wm

    monkeypatch.setattr(wm, "WORKING_MEMORY_DIR", tmp_path)
    wm.create_memory("shared")
    barrier = Barrier(4)

    def add_batch(writer):
        barrier.wait(timeout=10)
        return [wm.add_entry("shared", f"writer {writer} message {i}", tags=["shared"])
                for i in range(8)]

    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(add_batch, range(4)))
    expected = {entry for batch in batches for entry in batch}
    stored = wm._load_memory("shared")
    index = wm._load_index("shared")
    assert len(expected) == 32
    assert set(stored["entries"]) == expected
    assert stored["stats"]["total_entries"] == 32
    assert set(index["tags_index"]["shared"]) == expected


def test_windows_tree_kill_keeps_parent_alive_until_taskkill(tmp_path, monkeypatch):
    from hydra import proc

    events = []
    child = SimpleNamespace(pid=1234, terminate=lambda: events.append("terminate"))

    def taskkill(argv, **kwargs):
        events.append("taskkill")
        assert events == ["taskkill"], "killing the parent first orphans descendants"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(proc.subprocess, "run", taskkill)
    proc._windows_kill_tree(child)
    assert events[0] == "taskkill"


def test_windows_file_lock_does_not_retry_permanent_errors(tmp_path, monkeypatch):
    from hydra import file_lock

    def broken_lock(*args):
        raise OSError(errno.EBADF, "bad file descriptor")

    def unexpected_retry(*args):
        pytest.fail("permanent lock errors must not enter an infinite retry loop")

    monkeypatch.setattr(file_lock, "_HAVE_FCNTL", False)
    monkeypatch.setattr(file_lock, "_HAVE_MSVCRT", True)
    monkeypatch.setattr(file_lock, "msvcrt", SimpleNamespace(LK_NBLCK=2, locking=broken_lock))
    monkeypatch.setattr(file_lock.time, "sleep", unexpected_retry)
    with (tmp_path / "lock").open("w+b") as stream:
        with pytest.raises(OSError, match="bad file descriptor"):
            file_lock._lock_exclusive(stream, blocking=True)


def test_unified_memory_unwritable_parent_is_typed_backend_failure(tmp_path):
    from hydra.unified_memory import BackendUnavailable, UnifiedMemory

    parent = tmp_path / "not-a-directory"
    parent.write_text("file occupies requested memory directory", encoding="utf-8")
    with pytest.raises(BackendUnavailable, match="memory"):
        UnifiedMemory(path=parent / "memory.sqlite", embedder=lambda text: [1.0, 0.0])
