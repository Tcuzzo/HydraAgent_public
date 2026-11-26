"""Snapshot publication failures must preserve files and the prior undo stack."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from hydra.edit_checkpoints import CheckpointStore, undo


def test_exclusive_publication_failure_does_not_leave_a_false_snapshot(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_bytes(b"original")
    store = CheckpointStore(tmp_path)

    with pytest.raises(FileExistsError):
        store.write_bytes(target, b"replacement", overwrite=False)

    assert target.read_bytes() == b"original"
    assert store.peek_all() == []
    assert list(store.storage_root.glob("snap-*.bin")) == []


def test_snapshot_capacity_failure_preserves_original(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_bytes(b"larger than snapshot capacity")
    store = CheckpointStore(tmp_path, max_bytes=4)

    with pytest.raises(OSError, match="snapshot capacity"):
        store.write_bytes(target, b"new")
    assert target.read_bytes() == b"larger than snapshot capacity"
    assert store.peek_all() == []


def test_checkpoint_writer_rejects_an_external_target(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    with pytest.raises(ValueError, match="inside the workspace"):
        CheckpointStore(workspace).write_bytes(outside, b"escape")
    assert not outside.exists()


def test_parallel_writers_keep_every_snapshot(tmp_path):
    targets = [tmp_path / f"file-{i}.txt" for i in range(12)]
    for target in targets:
        target.write_bytes(b"original")

    def write(target):
        CheckpointStore(tmp_path).write_bytes(target, b"changed")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, targets))
    store = CheckpointStore(tmp_path)
    assert len(store.peek_all()) == len(targets)
    assert {entry["path"] for entry in store.peek_all()} == {str(target) for target in targets}
    undo(store, n=len(targets))
    assert all(target.read_bytes() == b"original" for target in targets)
