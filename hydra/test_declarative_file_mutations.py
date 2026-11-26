"""Declared file tools must execute in the requested workspace and remain undoable."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from hydra.declarative_runtime import (
    DeclarativeRuntimeError, execute_agent_decision, load_runtime_catalog,
)
from hydra.edit_checkpoints import CheckpointStore
from hydra.policy import ApprovalDenied, ApprovalPolicy
from skills.fs_read import SkillError


BEFORE = "before résumé\r\nsecond line\r\n".encode("utf-8")
AFTER = "after résumé\r\nsecond line\r\n".encode("utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    root = tmp_path / "requested workspace"
    catalog_root = tmp_path / "catalog workspace"
    ambient = tmp_path / "ambient workspace"
    for directory in (root, catalog_root, ambient):
        directory.mkdir()
    monkeypatch.chdir(ambient)
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HYDRA_OPERATOR_AUTH_DIR", str(tmp_path / "auth"))
    outside = ambient / "proof.txt"
    outside.write_bytes(BEFORE)
    return root, load_runtime_catalog(catalog_root), outside


def _execute(world, tool, arguments, *, mode="allow", plan=False):
    root, catalog, _ = world
    decision = {
        "schema": "hydra.agent_decision.v1", "intent": {"kind": "code_change"},
        "selected_skills": [], "execution_mode": "direct", "requires_approval": False,
        "verification": [], "selected_tools": [{"tool_id": tool}],
        "plan": [{"tool_id": tool, "arguments": arguments}],
    }
    policy = ApprovalPolicy(mode, plan_mode=plan, authority_checker=lambda: False)
    adapter_error = None
    try:
        result = execute_agent_decision(decision, catalog, root=root, approval_policy=policy)
    except TypeError as exc:
        adapter_error = exc
    assert adapter_error is None, f"valid {tool} decision failed in the product adapter: {adapter_error}"
    return result["results"][0]


def _arguments(tool, *, overwrite=False):
    if tool == "fs_edit":
        # These are the public catalog's names, not the Python skill's names.
        return {"path": "proof.txt", "old": "before", "new": "after"}
    return {"path": "proof.txt", "content": AFTER.decode("utf-8"), "overwrite": overwrite}


@pytest.mark.parametrize("operation", ["create", "overwrite", "edit"])
def test_declared_file_mutation_reads_exact_bytes_and_undoes_in_fresh_process(world, operation):
    root, catalog, outside = world
    target = root / "proof.txt"
    if operation != "create":
        target.write_bytes(BEFORE)
    tool = "fs_edit" if operation == "edit" else "fs_write"

    result = _execute(world, tool, _arguments(tool, overwrite=operation == "overwrite"))

    assert result["ok"] is True
    assert target.read_bytes() == AFTER
    assert outside.read_bytes() == BEFORE
    assert not (catalog.root / "proof.txt").exists()
    read = _execute(world, "fs_read", {"path": "proof.txt"})
    assert read["ok"] is True
    assert read["content"].encode("utf-8") == AFTER
    entries = CheckpointStore(root).peek_all()
    assert len(entries) == 1
    assert Path(entries[0]["path"]) == target.resolve()
    assert not CheckpointStore(catalog.root).peek_all()

    env = os.environ.copy()
    source_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [source_root, env.get("PYTHONPATH")]))
    undo = subprocess.run(
        [sys.executable, "-m", "hydra", "undo", "--root", str(root)],
        cwd=outside.parent, env=env, capture_output=True, text=True, timeout=15,
    )
    assert undo.returncode == 0, undo.stdout + undo.stderr
    if operation == "create":
        assert not target.exists()
    else:
        assert target.read_bytes() == BEFORE
    assert not CheckpointStore(root).peek_all()
    assert outside.read_bytes() == BEFORE


@pytest.mark.parametrize("tool", ["fs_write", "fs_edit"])
@pytest.mark.parametrize("mode,plan", [("deny", False), ("allow", True)])
def test_declared_mutations_respect_deny_and_plan_without_checkpointing(world, tool, mode, plan):
    root, _, outside = world
    target = root / "proof.txt"
    target.write_bytes(BEFORE)

    with pytest.raises(ApprovalDenied):
        _execute(world, tool, _arguments(tool, overwrite=True), mode=mode, plan=plan)

    assert target.read_bytes() == outside.read_bytes() == BEFORE
    assert not CheckpointStore(root).peek_all()


@pytest.mark.parametrize("tool", ["fs_write", "fs_edit"])
@pytest.mark.parametrize("escape", ["path", "root_argument"])
def test_declared_file_tools_cannot_redirect_writes_outside_execution_root(world, tool, escape):
    root, _, outside = world
    (root / "proof.txt").write_bytes(BEFORE)
    arguments = _arguments(tool, overwrite=True)
    if escape == "path":
        arguments["path"] = os.path.relpath(outside, root)
    else:
        arguments["root"] = str(outside.parent)

    try:
        _execute(world, tool, arguments)
    except (SkillError, DeclarativeRuntimeError):
        pass  # Refusing the extra root or escape is also a valid confinement policy.

    assert outside.read_bytes() == BEFORE, "planner arguments redirected a file write outside the execution root"
    assert not CheckpointStore(outside.parent).peek_all()
    assert all(Path(entry["path"]).is_relative_to(root) for entry in CheckpointStore(root).peek_all())
