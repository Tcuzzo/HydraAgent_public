"""Windows resolves a bare ``bash`` to the System32 WSL launcher.

That launcher is never the shell a mission's declared ``cwd`` belongs to: with no
WSL distro installed it exits 1 ("Windows Subsystem for Linux has no installed
distributions"), and with one installed it is worse -- it runs the command inside
the WSL filesystem namespace, where a Windows ``cwd`` like ``C:\\Users\\...`` does
not exist. Either way the mission loop's proof commands do not run where the
mission says they run.

Note that ``shutil.which("bash")`` does *not* reveal this: it searches PATH, where
Git-Bash usually wins, while ``CreateProcess`` -- what ``subprocess`` actually uses
for a bare ``"bash"`` -- searches System32 *before* PATH and so gets the launcher.
The two disagree, which is why only an absolute path is safe to hand to Popen.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from hydra import proc
from hydra.proc import ShellUnavailableError, resolve_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_BASH_ARGV = re.compile(r'\[\s*"bash"\s*,\s*"-l?c"')


def test_no_module_builds_a_local_shell_argv_from_bare_bash() -> None:
    """Structural guard: every local shell invocation goes through resolve_bash().

    Fails on the next offender, not just today's five.
    """
    offenders: list[str] = []
    for path in sorted(REPO_ROOT.rglob("*.py")):
        if path.name.startswith("test_") or path.name == "proc.py":
            continue
        if ".git" in path.parts or "build" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _LOCAL_BASH_ARGV.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "local shell argv built from the bare literal \"bash\" -- on Windows that is "
        "the System32 WSL launcher, not a shell that can run in the declared cwd. "
        "Use hydra.proc.resolve_bash() instead:\n  " + "\n  ".join(offenders)
    )


def test_resolve_bash_refuses_the_windows_wsl_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """The System32 WSL stub must never be handed back as the mission shell."""
    monkeypatch.setattr(proc, "_IS_WINDOWS", True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    # The only bash on PATH is the WSL launcher, and there is no Git for Windows.
    monkeypatch.setattr(
        proc.shutil,
        "which",
        lambda name: {"bash": r"C:\Windows\System32\bash.exe"}.get(name),
    )

    with pytest.raises(ShellUnavailableError, match="WSL"):
        resolve_bash()


def test_resolve_bash_prefers_git_for_windows_bash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real Git-for-Windows bash wins over the System32 stub on PATH."""
    git_root = tmp_path / "Git"
    git_bash = git_root / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_text("", encoding="utf-8")
    git_exe = git_root / "cmd" / "git.exe"
    git_exe.parent.mkdir(parents=True)
    git_exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(proc, "_IS_WINDOWS", True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(
        proc.shutil,
        "which",
        lambda name: {
            "bash": r"C:\Windows\System32\bash.exe",
            "git": str(git_exe),
        }.get(name),
    )

    assert resolve_bash() == str(git_bash)


def test_resolve_bash_returns_path_bash_when_not_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-Windows branch keeps its behaviour: whatever bash is on PATH.

    Exercises the branch rather than the host, so it runs everywhere instead of
    skipping on Windows -- and cannot be fooled by PATH lookup differing from the
    host's own resolution.
    """
    monkeypatch.setattr(proc, "_IS_WINDOWS", False)
    expected = shutil.which("bash")
    if expected is None:
        pytest.skip("no bash on PATH on this host")
    assert resolve_bash() == expected
