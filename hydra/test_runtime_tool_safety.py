"""Runtime boundary regressions found in the public runtime audit."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from hydra.guardrails import ActionTier, GuardrailConfig, Guardrails
from skills import bash, fs_edit, fs_write


@pytest.mark.parametrize("operation", ["write", "edit"])
def test_file_tools_do_not_touch_existing_temporary_sibling(tmp_path, operation):
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    sibling = tmp_path / "note.txt.hydra-tmp"
    sibling.write_text("unrelated user data", encoding="utf-8")
    if operation == "write":
        result = fs_write.run(target.name, "after", tmp_path, overwrite=True)
    else:
        result = fs_edit.run(target.name, "before", "after", tmp_path)
    assert result["ok"]
    assert target.read_text(encoding="utf-8") == "after"
    assert sibling.read_text(encoding="utf-8") == "unrelated user data"


@pytest.mark.parametrize("operation", ["write", "edit"])
def test_file_tools_do_not_write_through_temporary_hardlink(tmp_path, operation):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("protected", encoding="utf-8")
    target = root / "note.txt"
    target.write_text("before", encoding="utf-8")
    try:
        os.link(outside, root / "note.txt.hydra-tmp")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard-link creation unavailable: {exc}")
    if operation == "write":
        fs_write.run(target.name, "after", root, overwrite=True)
    else:
        fs_edit.run(target.name, "before", "after", root)
    assert outside.read_text(encoding="utf-8") == "protected"


def test_requested_container_sandbox_never_falls_back_to_host(tmp_path, monkeypatch):
    from hydra import container_sandbox

    monkeypatch.setenv("HYDRA_EXEC_SANDBOX", "1")
    monkeypatch.setattr(container_sandbox, "detect_sandbox_engine", lambda: None)
    called = []

    def host_spawn(*args, **kwargs):
        called.append(args)
        pytest.fail("sandboxed request reached host process execution")

    monkeypatch.setattr(bash, "popen_portable", host_spawn)
    result = bash.run("echo sandbox-only", tmp_path)
    assert not called
    assert result["ok"] is False
    assert result["reason"] == "no-container-engine"


@pytest.mark.parametrize("action,option", [
    ("read_file", "allow_read_only_auto"),
    ("write_file", "allow_bounded_write_auto"),
])
def test_disabled_auto_approval_requires_approval(tmp_path, action, option):
    guard = Guardrails(GuardrailConfig(**{option: False}), repo_root=tmp_path)
    allowed, reason, approval = guard.check_action_permission(
        action, {"path": str(tmp_path / "note.txt")}
    )
    assert allowed is False
    assert approval and approval["requires_approval"]


def test_relative_guardrail_paths_resolve_against_repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(tmp_path)
    guard = Guardrails(repo_root=repo)
    assert guard.classify_action_tier("write_file", {"path": "note.txt"}) == ActionTier.BOUNDED_WRITE
    assert not guard._is_safe_path("../outside.txt")
