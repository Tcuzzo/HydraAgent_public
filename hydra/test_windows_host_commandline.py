"""The host execution adapter shares cmd.exe command serialization."""
import subprocess
from types import SimpleNamespace

import pytest

from hydra import exec_backend, proc


@pytest.mark.parametrize("windows", [True, False])
def test_host_adapter_preserves_shell_grammar_and_normal_argv(monkeypatch, tmp_path, windows):
    launches = []

    def capture(args, **kwargs):
        launches.append(args)
        return SimpleNamespace(returncode=0, stdout="completed")

    monkeypatch.setattr(proc, "_IS_WINDOWS", windows)
    monkeypatch.setattr(exec_backend.subprocess, "run", capture)
    command = r'"C:\Program Files\Python\python.exe" "C:\work area\plant & check.py" && echo completed'
    argv = ["cmd.exe", "/c", command] if windows else ["/bin/sh", "-c", 'printf "%s" "a & b"']
    result = exec_backend._run_on_host(argv, workspace=tmp_path, timeout=5, env=None)
    assert result.returncode == 0
    if windows:
        actual = launches[0]
        actual = actual if isinstance(actual, str) else subprocess.list2cmdline(actual)
        assert actual == f'cmd.exe /s /c "{command}"'
    else:
        assert launches[0] == argv
