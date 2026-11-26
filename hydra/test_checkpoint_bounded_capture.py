"""An oversized pre-image must be refused without an unbounded file read."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from hydra.edit_checkpoints import CheckpointStore


def test_snapshot_capture_respects_its_memory_limit(tmp_path, monkeypatch):
    target = tmp_path / "oversized.bin"
    original = b"too large for the configured checkpoint limit"
    target.write_bytes(original)
    store = CheckpointStore(tmp_path, max_bytes=4)
    original_open = Path.open
    requested_sizes = []

    @contextmanager
    def observed_open(path, *args, **kwargs):
        with original_open(path, *args, **kwargs) as stream:
            if path == target:
                class ObservedReader:
                    def read(self, size=-1):
                        requested_sizes.append(size)
                        return stream.read(size)
                yield ObservedReader()
            else:
                yield stream

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", observed_open)
        with pytest.raises(OSError, match="snapshot capacity"):
            store.write_bytes(target, b"must not publish")

    assert requested_sizes
    assert all(0 <= size <= store.max_bytes + 1 for size in requested_sizes)
    assert target.read_bytes() == original
    assert store.peek_all() == []
