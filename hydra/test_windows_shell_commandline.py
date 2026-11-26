"""Preserve cmd.exe's raw command grammar at the Windows process boundary.

The Popen leaf is captured so Linux CI also checks the exact command line that
Windows would pass to CreateProcess. Real Windows CI separately runs the forged
operator-state attack from a script whose filename contains a space.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from hydra import proc


@pytest.fixture
def windows_launch(monkeypatch):
    launches = []

    def capture(args, **kwargs):
        launches.append((args, kwargs))
        return SimpleNamespace(returncode=0, communicate=lambda **kw: (b"", b""))

    monkeypatch.setattr(proc, "_IS_POSIX", False)
    monkeypatch.setattr(proc, "_IS_WINDOWS", True)
    monkeypatch.setattr(proc.subprocess, "Popen", capture)
    return launches


@pytest.mark.parametrize("entrypoint", ["portable", "run_shell"])
@pytest.mark.parametrize("command", [
    r'C:\Python\python.exe "C:\workspace\plant state.py"',
    r'"C:\Program Files\Python\python.exe" "C:\work area\plant state.py"',
    r'"C:\Program Files\Python\python.exe" "C:\work area\plant & check.py" && echo completed',
    r'echo "literal & pipe | comparison > text" & echo done',
])
def test_cmd_shell_preserves_the_raw_quoted_command(windows_launch, tmp_path, entrypoint, command):
    if entrypoint == "portable":
        proc.popen_portable(proc._shell_argv(command), cwd=tmp_path)
    else:
        assert proc.run_shell(command, cwd=tmp_path).ok

    args, kwargs = windows_launch[-1]
    # Popen serializes a sequence using CRT rules on Windows. cmd.exe does not
    # use those rules: its /s switch removes just the enclosing command quotes.
    actual = args if isinstance(args, str) else subprocess.list2cmdline(args)
    assert actual == f'cmd.exe /s /c "{command}"'
    assert kwargs["creationflags"] == 0x00000200


@pytest.mark.parametrize("argv", [
    [r"C:\Program Files\Python\python.exe", "spaced argument", 'literal"quote', "&"],
    [r"C:\Program Files\Git\bin\bash.exe", "-lc", 'printf "%s" "a & b"'],
])
def test_non_cmd_argv_keeps_normal_subprocess_serialization(windows_launch, argv):
    proc.popen_portable(argv)
    actual, kwargs = windows_launch[-1]
    assert actual == argv
    assert kwargs["creationflags"] == 0x00000200
