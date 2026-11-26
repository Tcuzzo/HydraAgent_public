"""Failed checkpoint publication must not destroy an existing recovery path."""
from pathlib import Path

import pytest

from hydra.edit_checkpoints import CheckpointStore, undo


@pytest.mark.parametrize("limits", [{"max_entries": 1}, {"max_bytes": 6}])
def test_failed_publication_at_retention_capacity_keeps_prior_history(tmp_path, limits):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = CheckpointStore(tmp_path, **limits)
    store.write_bytes(first, b"changed first")
    before = store.peek_all()

    with pytest.raises(FileExistsError):
        store.write_bytes(second, b"never publish", overwrite=False)

    assert store.peek_all() == before
    assert store.load_pre_image(before[0]) == b"first"
    assert second.read_bytes() == b"second"
    undo(store)
    assert first.read_bytes() == b"first"


def test_failed_snapshot_index_update_preserves_prior_content(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = CheckpointStore(tmp_path, max_entries=1)
    store.write_bytes(first, b"changed first")
    before = store.peek_all()

    with monkeypatch.context() as patch:
        def fail_index(_entries):
            raise OSError("index volume unavailable")
        patch.setattr(store, "_write_index", fail_index)
        with pytest.raises(OSError, match="index volume unavailable"):
            store.write_bytes(second, b"never publish")

    assert store.peek_all() == before
    assert store.load_pre_image(before[0]) == b"first"
    assert second.read_bytes() == b"second"
    undo(store)
    assert first.read_bytes() == b"first"


@pytest.mark.parametrize("limits", [{"max_entries": 1}, {"max_bytes": 6}])
def test_successful_publication_still_enforces_retention(tmp_path, limits):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = CheckpointStore(tmp_path, **limits)
    store.write_bytes(first, b"changed first")
    store.write_bytes(second, b"changed second")

    entries = store.peek_all()
    assert [Path(entry["path"]).name for entry in entries] == ["second.txt"]
    assert len(list(store.storage_root.glob("snap-*.bin"))) == 1
    undo(store)
    assert second.read_bytes() == b"second"


def test_zero_entry_capacity_refuses_an_unrecoverable_write(tmp_path):
    target = tmp_path / "existing.txt"
    target.write_bytes(b"original")
    store = CheckpointStore(tmp_path, max_entries=0)

    with pytest.raises(OSError, match="snapshot capacity"):
        store.write_bytes(target, b"must not publish")

    assert target.read_bytes() == b"original"
    assert store.peek_all() == []
