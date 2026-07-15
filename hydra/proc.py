"""hydra.proc — cross-platform subprocess helper.

Single source of truth for shell execution and process-group kill in HydraAgent.
All other modules that need "run a shell command with a process-tree kill on
timeout" should call ``run_shell`` or use the ``popen_portable`` / ``kill_tree``
building blocks here, rather than sprinkling POSIX-only calls throughout the
codebase.

OS strategy
-----------
POSIX (Linux, macOS)
    * Shell: ``bash -lc`` when bash is on PATH (login shell so ~/.bashrc /.bash_profile
      are sourced); falls back to ``sh -c`` when bash is absent.
    * Process isolation: ``start_new_session=True`` puts the shell and every
      child it spawns into a new session / process group.
    * Kill: ``os.killpg(os.getpgid(pid), signal.SIGKILL)`` — nukes the whole
      tree, not just the shell.

Windows
    * Shell: ``cmd.exe /c`` normally.  ``run_shell(..., prefer_bash=True)`` uses
      ``bash -lc`` when a *real* bash (Git for Windows / Cygwin) exists.  The
      System32 WSL launcher is never used -- see ``resolve_bash``.
    * Process isolation: ``CREATE_NEW_PROCESS_GROUP`` flag (equivalent to POSIX
      new-session from the scheduling perspective).
    * Kill: ``proc.terminate()`` sends CTRL_BREAK_EVENT to the group, then
      ``taskkill /F /T /PID`` kills the entire tree.  ``os.killpg`` /
      ``os.getpgid`` / ``signal.SIGKILL`` are **never referenced** on Windows.

This module contains ALL the ``os.killpg`` / ``os.getpgid`` / ``signal.SIGKILL``
/ ``os.setsid`` references in the codebase; every call is guarded behind an
``os.name == 'posix'`` branch so the module is import-safe on Windows.

Public API
----------
``run_shell(command, *, cwd, env, timeout, max_output_bytes, prefer_bash)``
    Run a shell string and return a :class:`ShellResult` (ok, exit_code,
    stdout, stderr, timed_out, duration_s).

``resolve_bash()``
    Absolute path to a real bash for *this* host, or ``ShellUnavailableError``.
    Every local ``bash`` invocation in the codebase resolves through this.

``popen_portable(args, *, cwd, env, text, **kwargs)``
    subprocess.Popen wrapper that applies the correct process-isolation flag
    for the current OS.  Returns the Popen object.

``kill_tree(proc)``
    Kill ``proc`` and its entire process subtree.  No-op if the process has
    already exited.  Safe to call from a timeout handler.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Platform detection ────────────────────────────────────────────────────────

_IS_POSIX = os.name == "posix"
_IS_WINDOWS = sys.platform == "win32"

# ── Lazy POSIX-only imports ───────────────────────────────────────────────────
# Never import at module level unconditionally; these don't exist on Windows.
if _IS_POSIX:
    import signal as _signal  # noqa: F811 — already imported, just for clarity
    # os.killpg / os.getpgid are stdlib on POSIX; verify at import time.
    _HAS_KILLPG = hasattr(os, "killpg") and hasattr(os, "getpgid")
else:
    _HAS_KILLPG = False


# ── Shell resolution ──────────────────────────────────────────────────────────


class ShellUnavailableError(RuntimeError):
    """Raised when this host has no bash that can run a command in a given cwd."""


def _is_wsl_launcher(path: Path) -> bool:
    """True if ``path`` is Windows' System32 WSL stub rather than a real bash.

    Windows ships ``C:\\Windows\\System32\\bash.exe``, a launcher for the Windows
    Subsystem for Linux, and it sits first on PATH -- so a bare ``bash`` (and
    therefore ``shutil.which("bash")``) silently resolves to it. With no WSL
    distro installed it just fails; with one installed it is *worse*, because it
    runs the command inside the WSL filesystem namespace, where a Windows cwd
    like ``C:\\Users\\...`` does not exist. It is never the shell that a
    Windows working directory belongs to.
    """
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    parent = str(path.parent).rstrip("\\/").lower()
    return any(
        parent == str(Path(system_root) / sub).rstrip("\\/").lower()
        for sub in ("System32", "Sysnative", "SysWOW64")
    )


def _windows_bash_candidates() -> list[Path]:
    """Real Windows bash executables, best first. Git for Windows ships one."""
    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        # ...\Git\cmd\git.exe -> ...\Git\bin\bash.exe
        git_root = Path(git).parent.parent
        candidates.append(git_root / "bin" / "bash.exe")
        candidates.append(git_root / "usr" / "bin" / "bash.exe")
    for var in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / "Git" / "bin" / "bash.exe")
            candidates.append(Path(base) / "Git" / "usr" / "bin" / "bash.exe")
    on_path = shutil.which("bash")
    if on_path and not _is_wsl_launcher(Path(on_path)):
        candidates.append(Path(on_path))
    return candidates


def resolve_bash() -> str:
    """Absolute path to a bash that can run a command in this host's filesystem.

    Callers must use this instead of the bare literal ``"bash"``: on Windows that
    literal resolves to the WSL launcher (see :func:`_is_wsl_launcher`). Raises
    :class:`ShellUnavailableError` rather than falling back to a shell that would
    run the command somewhere other than the caller's declared cwd.
    """
    if _IS_WINDOWS:
        for candidate in _windows_bash_candidates():
            if candidate.is_file():
                return str(candidate)
        raise ShellUnavailableError(
            "no usable bash on this Windows host: the only 'bash' on PATH is the "
            "System32 WSL launcher, which cannot run a command in a Windows "
            "working directory. Install Git for Windows (it ships bash.exe)."
        )
    bash = shutil.which("bash")
    if not bash:
        raise ShellUnavailableError("bash not found on PATH")
    return bash


def _posix_shell(prefer_bash: bool) -> list[str]:
    """Return the argv prefix for the POSIX shell invocation.

    POSIX always prefers bash, so ``prefer_bash`` is accepted only for signature
    symmetry with :func:`_windows_shell`; ``sh`` is used when bash is genuinely
    absent from the host.
    """
    del prefer_bash
    try:
        return [resolve_bash(), "-lc"]  # login shell: sources profile
    except ShellUnavailableError:
        sh = shutil.which("sh") or "sh"
        return [sh, "-c"]


def _windows_shell(prefer_bash: bool) -> list[str]:
    """Return the argv prefix for the Windows shell invocation."""
    if prefer_bash:
        try:
            return [resolve_bash(), "-lc"]
        except ShellUnavailableError:
            # Documented `prefer_bash` semantics: cmd.exe is the Windows default
            # and remains correct here -- unlike the WSL launcher, it runs in the
            # caller's cwd.
            pass
    return ["cmd.exe", "/c"]


def _shell_argv(command: str, *, prefer_bash: bool = False) -> list[str]:
    """Build [shell, flag, command] for the current OS."""
    if _IS_POSIX:
        prefix = _posix_shell(prefer_bash)
    else:
        prefix = _windows_shell(prefer_bash)
    return [*prefix, command]


# ── Process-group spawn ───────────────────────────────────────────────────────


def _popen_kwargs_isolation() -> dict[str, Any]:
    """Extra kwargs for subprocess.Popen to isolate the child's process group."""
    if _IS_POSIX:
        return {"start_new_session": True}
    if _IS_WINDOWS:
        # CREATE_NEW_PROCESS_GROUP (0x00000200): gives the child its own
        # console/process group so we can CTRL_BREAK it and taskkill /T it.
        return {"creationflags": 0x00000200}
    return {}


def popen_portable(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    text: bool = False,
    **kwargs: Any,
) -> subprocess.Popen:
    """subprocess.Popen with the correct process-isolation flags for this OS.

    Callers that just need a fire-and-forget detached child (e.g. the Telegram
    listener launcher) call this instead of raw Popen so they don't need to
    scatter ``start_new_session=True`` throughout the codebase.
    """
    isolation = _popen_kwargs_isolation()
    # Let callers override isolation by passing explicit creationflags/start_new_session,
    # but default to isolation.
    merged = {**isolation, **kwargs}
    return subprocess.Popen(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=text,
        **merged,
    )


# ── Process-tree kill ─────────────────────────────────────────────────────────


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and every child it spawned, on both POSIX and Windows.

    Safe to call after the process has already exited (catches all expected
    exceptions from reaping a dead process).
    """
    if _IS_POSIX and _HAS_KILLPG:
        # POSIX: kill the entire process group with SIGKILL.
        try:
            pgid = os.getpgid(proc.pid)  # raises ProcessLookupError if dead
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            # Process already dead or pgid query failed — best-effort fallback.
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
    else:
        # Windows: use taskkill /F /T /PID to kill the tree.
        _windows_kill_tree(proc)


def _windows_kill_tree(proc: subprocess.Popen) -> None:
    """Windows-specific process tree kill via taskkill."""
    # First try proc.terminate() which sends CTRL_BREAK_EVENT to the group.
    try:
        proc.terminate()
    except (OSError, PermissionError):
        pass
    # Then use taskkill /F /T to forcibly kill all descendants.
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class ShellResult:
    """Structured result from ``run_shell``."""

    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    command: str
    cwd: str

    def __repr__(self) -> str:
        return (
            f"ShellResult(ok={self.ok}, exit_code={self.exit_code}, "
            f"timed_out={self.timed_out}, stdout={self.stdout!r:.60})"
        )


def _truncate(buf: bytes, limit: int) -> tuple[str, bool]:
    if len(buf) <= limit:
        return buf.decode("utf-8", errors="replace"), False
    return buf[:limit].decode("utf-8", errors="replace"), True


# ── Main public API ───────────────────────────────────────────────────────────

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024


def run_shell(
    command: str,
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    prefer_bash: bool = False,
) -> ShellResult:
    """Run ``command`` in a subprocess shell.

    Parameters
    ----------
    command:
        Shell string to execute.
    cwd:
        Working directory.  Defaults to the current directory.
    env:
        Full environment mapping for the child.  ``None`` inherits ``os.environ``.
    timeout:
        Wall-clock budget in seconds.  On expiry the whole process tree is
        killed and ``ShellResult.timed_out`` is ``True``.
    max_output_bytes:
        Per-stream (stdout/stderr) truncation budget.
    prefer_bash:
        Windows only: if ``True`` and bash is on PATH, use ``bash -lc`` instead
        of ``cmd.exe /c``.  Ignored on POSIX (bash is always preferred there).

    Returns
    -------
    :class:`ShellResult`
        Never raises for command-level failures; raises only for bad input.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if timeout <= 0:
        raise ValueError(f"timeout must be positive, got {timeout}")

    cwd_str = str(Path(cwd).resolve()) if cwd is not None else str(Path.cwd())
    argv = _shell_argv(command, prefer_bash=prefer_bash)

    # Build Popen kwargs — process isolation is OS-specific (see module docstring).
    isolation = _popen_kwargs_isolation()

    start = time.monotonic()
    timed_out = False
    proc = subprocess.Popen(
        argv,
        cwd=cwd_str,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **isolation,
    )
    try:
        stdout_b, stderr_b = proc.communicate(timeout=timeout)
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        try:
            stdout_b, stderr_b = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout_b, stderr_b = b"", b""
        exit_code = -1
        timed_out = True

    duration_s = time.monotonic() - start
    stdout, _ = _truncate(stdout_b, max_output_bytes)
    stderr, _ = _truncate(stderr_b, max_output_bytes)

    return ShellResult(
        ok=exit_code == 0 and not timed_out,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_s=duration_s,
        command=command,
        cwd=cwd_str,
    )
