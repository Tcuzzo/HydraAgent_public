"""Post-publication retention maintenance cannot turn a committed edit into failure."""
import logging

import pytest

from hydra.edit_checkpoints import CheckpointStore, undo


@pytest.mark.parametrize("limits", [{"max_entries": 1}, {"max_bytes": 6}])
def test_retention_failure_reports_committed_change_and_preserves_undo(tmp_path, monkeypatch, caplog, limits):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    store = CheckpointStore(tmp_path, **limits)
    store.write_bytes(first, b"changed first")
    original_write = store._write_index
    calls = 0

    def fail_maintenance_only(entries):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("retention index unavailable")
        original_write(entries)

    monkeypatch.setattr(store, "_write_index", fail_maintenance_only)
    with caplog.at_level(logging.WARNING):
        store.write_bytes(second, b"changed second")

    assert second.read_bytes() == b"changed second"
    assert len(store.peek_all()) == 2
    assert "committed" in caplog.text.lower()
    assert "retention" in caplog.text.lower()
    undo(store, n=2)
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
