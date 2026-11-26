"""Public file tools must leave usable undo snapshots for a fresh CLI process."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from hydra.cli.tool_binding import bind_tools
from hydra.edit_checkpoints import CheckpointStore
from hydra.policy import ApprovalDenied
from skills.fs_read import SkillError


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HYDRA_OPERATOR_AUTH_DIR", str(tmp_path / "auth"))
    return root


def tool(root: Path, name: str, policy: str = "allow"):
    return next(t for t in bind_tools(root, approval_policy=policy, include_mcp=False) if t.name == name)


@pytest.mark.parametrize("operation", ["edit", "overwrite", "create"])
def test_public_mutation_is_undoable_after_process_restart(workspace, operation):
    target = workspace / "notes.txt"
    before = b"before\r\nsecond line\r\n"
    if operation != "create":
        target.write_bytes(before)
    if operation == "edit":
        tool(workspace, "fs_edit").invoke(path="notes.txt", old_string="before", new_string="after")
    else:
        tool(workspace, "fs_write").invoke(path="notes.txt", content="after", overwrite=operation == "overwrite")
    assert target.exists()
    assert len(CheckpointStore(workspace).peek_all()) == 1

    result = subprocess.run(
        [sys.executable, "-m", "hydra", "undo", "--root", str(workspace)],
        cwd=workspace, env=os.environ.copy(), capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if operation == "create":
        assert not target.exists()
    else:
        assert target.read_bytes() == before
    assert CheckpointStore(workspace).peek_all() == []


def test_checkpoint_failure_refuses_to_modify_the_file(workspace, monkeypatch):
    target = workspace / "notes.txt"
    target.write_text("before", encoding="utf-8")
    invalid_store = workspace.parent / "store-is-a-file"
    invalid_store.write_text("unwritable store", encoding="utf-8")
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(invalid_store))

    with pytest.raises(SkillError):
        tool(workspace, "fs_edit").invoke(path="notes.txt", old_string="before", new_string="after")
    assert target.read_text(encoding="utf-8") == "before"


def test_invalid_edit_does_not_add_an_undo_entry(workspace):
    (workspace / "notes.txt").write_text("before", encoding="utf-8")
    with pytest.raises(SkillError):
        tool(workspace, "fs_edit").invoke(path="notes.txt", old_string="missing", new_string="after")
    assert CheckpointStore(workspace).peek_all() == []


def test_denied_write_has_no_file_or_checkpoint(workspace):
    with pytest.raises(ApprovalDenied):
        tool(workspace, "fs_write", "deny").invoke(path="denied.txt", content="blocked")
    assert not (workspace / "denied.txt").exists()
    assert CheckpointStore(workspace).peek_all() == []
