"""Platform path representations preserve execution and memory membership."""
from pathlib import Path, PureWindowsPath
import sys

import pytest

from hydra.exec_backend import _run_on_host
from hydra.local_memory import _claude_project_slug, build_local_memory_context
from hydra.proc import _shell_argv


@pytest.mark.parametrize("workspace", [PureWindowsPath(r"C:\work\repo"), PureWindowsPath(r"\\server\share\repo")])
def test_windows_project_slug_stays_under_its_memory_parent(workspace):
    parent = PureWindowsPath(r"D:\operator\.claude\projects")
    slug = _claude_project_slug(workspace)
    result = parent / slug / "memory"
    assert result.is_relative_to(parent), (slug, result)
    assert len(PureWindowsPath(slug).parts) == 1
    assert ":" not in slug


def test_project_memory_is_loaded_from_the_home_project_directory(tmp_path, monkeypatch):
    operator_home = tmp_path / "home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = workspace.resolve().as_posix().replace(":", "-").replace("/", "-")
    memory = operator_home / ".claude" / "projects" / slug / "memory"
    memory.mkdir(parents=True)
    (memory / "MEMORY.md").write_text("project_memory_membership_signal", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: operator_home)
    result = build_local_memory_context(tmp_path / "local-memory", workspace_root=workspace)
    assert result.status == "OK", result.data
    assert "project_memory_membership_signal" in result.context


def test_explicit_host_transport_preserves_platform_shell_command(tmp_path):
    (tmp_path / "check.py").write_bytes(b"print('host-shell-executed')\n")
    command = _shell_argv(f'"{sys.executable}" -B check.py')
    result = _run_on_host(command, workspace=tmp_path, timeout=10, env=None)
    assert result.returncode == 0, result
    assert "host-shell-executed" in result.stdout
