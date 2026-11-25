"""Ball 1C contract: the ``bash`` tool confines WRITE TARGETS to the workspace root.

``cwd=root`` is not confinement: the command may still write anywhere the OS user
can. These tests run real subprocesses through ``skills.bash.run`` and read the real
filesystem back. No mocking of processes, files or sockets.

Per-OS contract (the path rule applies on every OS, sandbox or not):
  * POSIX  -> ``bash -lc``: redirections plus cp/mv/touch/mkdir/tee/dd of=/install/ln.
  * Windows -> ``cmd.exe /c``: redirections plus copy/move/md/mkdir.
Each platform's cases run only on that platform; the other platform's cases are
reported as skipped, never silently dropped.
"""
from __future__ import annotations

import os
import sys

import pytest

from skills import bash

IS_WINDOWS = sys.platform == "win32"
posix_only = pytest.mark.skipif(IS_WINDOWS, reason="POSIX shell syntax")
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="cmd.exe syntax")

# (id, command template, what must exist when the write is allowed)
# {src} is a readable file that always lives OUTSIDE the root (reads are free);
# {dst} is the write target.
POSIX_WRITES = [
    ("redirect", "echo hello > {dst}", "file"),
    ("append", "echo hello >> {dst}", "file"),
    ("cp", "cp {src} {dst}", "file"),
    ("mv", "cp {src} {dst}.seed && mv {dst}.seed {dst}", "file"),
    ("touch", "touch {dst}", "exists"),
    ("mkdir", "mkdir {dst}", "exists"),
    ("tee", "echo hello | tee {dst}", "file"),
    ("dd", "dd if={src} of={dst} status=none", "file"),
    ("install", "install {src} {dst}", "file"),
    ("ln_s", "ln -s {src} {dst}", "exists"),
]
WINDOWS_WRITES = [
    ("redirect", "echo hello > {dst}", "file"),
    ("append", "echo hello >> {dst}", "file"),
    ("copy", "copy {src} {dst}", "file"),
    ("md", "md {dst}", "exists"),
]


def _case_params(cases, marker):
    return [pytest.param(tmpl, kind, id=cid, marks=marker) for cid, tmpl, kind in cases]


ALL_WRITES = _case_params(POSIX_WRITES, posix_only) + _case_params(
    WINDOWS_WRITES, windows_only
)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "elsewhere"
    root.mkdir()
    outside.mkdir()
    src = outside / "source.txt"
    src.write_text("seed-content\n", encoding="utf-8")
    return root, outside, src


def _command(template, src, dst):
    return template.format(src=src, dst=dst)


def _exists_as(path, kind):
    if kind == "file":
        return path.is_file() and path.stat().st_size > 0
    return os.path.lexists(path)


@pytest.mark.parametrize("template,kind", ALL_WRITES)
def test_bash_write_outside_root_is_denied(world, template, kind):
    root, outside, src = world
    target = outside / "escaped.out"
    result = bash.run(_command(template, src, target), root)
    assert result["ok"] is False, result
    assert str(target) in result["stderr"], "the refusal must name the blocked path"
    assert not os.path.lexists(target), "the write must not reach the disk"
    assert src.read_text(encoding="utf-8") == "seed-content\n"


@pytest.mark.parametrize("template,kind", ALL_WRITES)
def test_bash_write_inside_root_is_allowed(world, template, kind):
    root, outside, src = world
    target = root / "inside.out"
    result = bash.run(_command(template, src, target), root)
    assert result["ok"] is True, result
    assert _exists_as(target, kind), f"{target} missing or empty after {template!r}"


@posix_only
@pytest.mark.parametrize(
    "how",
    ["absolute", "dotdot", "symlink", "cd_then_relative", "nested_shell"],
)
def test_bash_cwd_is_not_the_only_confinement(world, how):
    """cwd IS the root in every case; the write target still has to be refused."""
    root, outside, _src = world
    target = outside / "via.out"
    if how == "absolute":
        command = f"echo hello > {target}"
    elif how == "dotdot":
        command = f"echo hello > ../{outside.name}/via.out"
    elif how == "symlink":
        (root / "door").symlink_to(outside, target_is_directory=True)
        command = "echo hello > door/via.out"
    elif how == "cd_then_relative":
        command = f"cd {outside} && touch via.out"
    else:
        command = f"bash -c 'echo hello > {target}'"
    result = bash.run(command, root)
    assert result["cwd"] == str(root.resolve())
    assert result["ok"] is False, result
    assert not os.path.lexists(target)
    assert str(outside.resolve()) in result["stderr"] or str(target) in result["stderr"]


@windows_only
@pytest.mark.parametrize("how", ["absolute", "dotdot"])
def test_bash_cwd_is_not_the_only_confinement_windows(world, how):
    root, outside, _src = world
    target = outside / "via.out"
    command = (
        f"echo hello > {target}"
        if how == "absolute"
        else f"echo hello > ..\\{outside.name}\\via.out"
    )
    result = bash.run(command, root)
    assert result["ok"] is False, result
    assert not os.path.lexists(target)


@posix_only
def test_reads_and_harmless_sinks_are_unaffected(world):
    root, outside, src = world
    result = bash.run(f"cat {src} && echo quiet > /dev/null 2>&1 && ls {outside}", root)
    assert result["ok"] is True, result
    assert "seed-content" in result["stdout"]
    copied = root / "copy.txt"
    result = bash.run(f"cp {src} {copied}", root)
    assert result["ok"] is True, result
    assert copied.read_text(encoding="utf-8") == "seed-content\n"


@windows_only
def test_reads_and_harmless_sinks_are_unaffected_windows(world):
    root, _outside, src = world
    result = bash.run(f"type {src} > nul", root)
    assert result["ok"] is True, result


@posix_only
def test_path_rule_applies_when_the_sandbox_is_requested(world, monkeypatch):
    """The sandbox stays opt-in, but the path rule runs first, sandbox or not."""
    from hydra import container_sandbox

    monkeypatch.setenv("HYDRA_EXEC_SANDBOX", "1")
    monkeypatch.setattr(container_sandbox, "detect_sandbox_engine", lambda: None)
    root, outside, _src = world
    target = outside / "sandboxed.out"
    result = bash.run(f"echo hello > {target}", root)
    assert result["ok"] is False
    assert result.get("reason") == "write-outside-workspace-root", result
    assert str(target) in result["stderr"]
    assert not os.path.lexists(target)
