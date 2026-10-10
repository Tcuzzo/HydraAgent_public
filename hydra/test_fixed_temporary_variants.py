"""Predictable temporary siblings must never be consumed by runtime writers."""
from __future__ import annotations

import pytest

from hydra.apply_patch import apply_patch, create_file
from hydra.edit_checkpoints import CheckpointStore, undo
from skills import todo


def test_todo_add_preserves_existing_temporary_sibling(tmp_path):
    sibling = tmp_path / ".hydra_todos.json.hydra-tmp"
    sibling.write_bytes(b"unrelated data")
    result = todo.run("add", tmp_path, text="Verify the fix")
    assert result["ok"]
    assert todo.run("list", tmp_path)["todos"][0]["text"] == "Verify the fix"
    assert sibling.read_bytes() == b"unrelated data"


@pytest.mark.parametrize("operation", ["modify", "create"])
def test_patch_writer_preserves_existing_temporary_sibling(tmp_path, operation):
    target = tmp_path / "note.txt"
    sibling = tmp_path / "note.txt.hydra-tmp"
    sibling.write_bytes(b"unrelated data")
    if operation == "modify":
        target.write_text("before", encoding="utf-8")
        result = apply_patch(file=target, old_block="before", new_block="after", root=tmp_path, checkpoint=False)
    else:
        result = create_file(file=target, content="after", root=tmp_path, checkpoint=False)
    assert result.ok
    assert target.read_text(encoding="utf-8") == "after"
    assert sibling.read_bytes() == b"unrelated data"


@pytest.mark.parametrize("temporary_name", ["snap-00000001.bin.hydra-tmp", "index.json.tmp"])
def test_checkpoint_record_preserves_existing_temporary_sibling(tmp_path, temporary_name):
    store = CheckpointStore(tmp_path, _base_dir=tmp_path / "checkpoints")
    sibling = store.storage_root / temporary_name
    sibling.write_bytes(b"unrelated data")
    store.record(target_path=tmp_path / "note.txt", op="modify", pre_image=b"before")
    assert store.load_pre_image(store.peek_all()[0]) == b"before"
    assert sibling.read_bytes() == b"unrelated data"


def test_undo_preserves_existing_temporary_sibling(tmp_path):
    target = tmp_path / "note.txt"
    target.write_bytes(b"after")
    sibling = tmp_path / "note.txt.hydra-undo-tmp"
    sibling.write_bytes(b"unrelated data")
    store = CheckpointStore(tmp_path, _base_dir=tmp_path / "checkpoints")
    store.record(target_path=target, op="modify", pre_image=b"before")
    undo(store)
    assert target.read_bytes() == b"before"
    assert sibling.read_bytes() == b"unrelated data"


def test_worker_existing_file_write_preserves_newlines_and_checkpoint(tmp_path, monkeypatch):
    from hydra.worker_jobs import _apply_single_action

    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    target = tmp_path / "note.txt"
    target.write_bytes(b"before\r\n")
    _apply_single_action(tmp_path, {"kind": "write_text", "path": "note.txt", "text": "after\r\n"})
    assert target.read_bytes() == b"after\r\n"
    undo(CheckpointStore(tmp_path))
    assert target.read_bytes() == b"before\r\n"
