"""A failed restore must retain its exact checkpoint for a later retry."""
import pytest

from hydra.cli.tool_binding import bind_tools
from hydra.edit_checkpoints import CheckpointStore, undo


def test_failed_undo_keeps_preimage_and_retry_restores_exact_bytes(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    parent = root / "subdir"
    parent.mkdir(parents=True)
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    target = parent / "operator.txt"
    original = b"operator-only original\r\n"
    target.write_bytes(original)
    writer = next(tool for tool in bind_tools(root, approval_policy="allow", include_mcp=False)
                  if tool.name == "fs_write")
    writer.invoke(path="subdir/operator.txt", content="replacement\n", overwrite=True)
    store = CheckpointStore(root)
    checkpoint = store.peek_all()[-1]
    parent.rename(root / "temporarily-moved")
    with pytest.raises(OSError):
        undo(store)
    assert store.peek_all()[-1] == checkpoint
    assert store._content_path(checkpoint["seq"]).read_bytes() == original
    (root / "temporarily-moved").rename(parent)
    undo(store)
    assert target.read_bytes() == original
    assert store.peek_all() == []
