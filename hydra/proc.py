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
    * Kill: ``taskkill /F /T /PID`` kills the entire tree while the parent
      still exists; ``proc.terminate()`` is a fallback. ``os.killpg`` /
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
import math
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import Event, Thread
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
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    candidates = [os.environ.get("ComSpec"), str(Path(system_root) / "System32" / "cmd.exe")]
    for value in candidates:
        if value:
            path = Path(value)
            if path.is_absolute() and path.is_file():
                return [str(path), "/d", "/s", "/c"]
    raise ShellUnavailableError("cmd.exe was not found at ComSpec or SystemRoot\\System32")


def _shell_argv(command: str, *, prefer_bash: bool = False) -> str | list[str]:
    """Build shell arguments using the selected shell's command-line rules."""
    if _IS_POSIX:
        prefix = _posix_shell(prefer_bash)
    else:
        prefix = _windows_shell(prefer_bash)
        if prefix[-1] == "/c":
            # Popen serializes a sequence using C runtime escaping, which is
            # not cmd.exe's parser. /s removes only the added outer quotes;
            # preserve every quote and shell operator in the command itself.
            return f'"{prefix[0]}" /d /s /c "{command}"'
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
    args: str | list[str],
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
    # taskkill needs the parent's PID alive to discover descendants. On Windows
    # terminate() kills only that process (it does not send CTRL_BREAK_EVENT),
    # so calling it first can orphan the very descendants we need to stop.
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0:
            return
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    try:
        proc.terminate()
    except OSError:
        pass


def kill_owned_group(proc: subprocess.Popen) -> None:
    """Kill our isolated group even after its leader exits; never use foreign PIDs."""
    if _IS_POSIX and _HAS_KILLPG:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    else:
        _windows_kill_tree(proc)


def windows_process_parents() -> dict[int, int]:
    """One bounded Toolhelp snapshot; avoids querying every process for metadata."""
    if not _IS_WINDOWS:
        return {}
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return {}
    result = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(Entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            result[int(entry.pid)] = int(entry.parent)
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


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
_ACTIVE_CANCELLATION: ContextVar[Event | None] = ContextVar("hydra_process_cancellation", default=None)


@contextmanager
def use_cancellation(event: Event | None):
    """Propagate a turn owner's cancellation to synchronous process tools."""
    token = _ACTIVE_CANCELLATION.set(event)
    try:
        yield
    finally:
        _ACTIVE_CANCELLATION.reset(token)


class ProcessCancelled(subprocess.SubprocessError):
    """The owner revoked a running command."""


class OutputLimitExceeded(subprocess.SubprocessError):
    """A command exceeded an explicitly fatal output budget."""


def run_captured(
    argv: str | list[str], *, cwd=None, env=None, input=None, capture_output=True,
    text=True, timeout=None, cancel_event: Event | None = None,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    stdout_file=None, merge_output=False, fail_on_output_limit=False, popen_factory=None,
) -> subprocess.CompletedProcess:
    """Drain owned process pipes without retaining unbounded progress output.

    Each stream keeps its prefix up to the declared byte ceiling and reports
    truncation. A separate stdin writer keeps cancellation responsive even when
    a native child stops reading a large prompt. Popen uses shell=False;
    callers can explicitly launch their selected shell through _shell_argv.
    """
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("timeout must be a finite positive number")
    if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) or max_output_bytes < 0:
        raise ValueError("max_output_bytes must be a non-negative integer")
    if cancel_event is None:
        cancel_event = _ACTIVE_CANCELLATION.get()
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelled("process cancelled before launch")
    payload = input.encode("utf-8") if isinstance(input, str) else input
    deadline = time.monotonic() + timeout if timeout is not None else None
    proc = (popen_factory or popen_portable)(argv, cwd=cwd, env=env,
                          stdin=subprocess.PIPE if payload is not None else None,
                          stdout=subprocess.PIPE if capture_output else None,
                          stderr=subprocess.STDOUT if merge_output else subprocess.PIPE if capture_output else None)
    buffers = [bytearray(), bytearray()]
    totals = [0, 0]
    threads = []
    limit_reached = Event()
    io_errors = []

    def drain(stream, index):
        try:
            while chunk := stream.read(65536):
                available = max_output_bytes - totals[index]
                totals[index] += len(chunk)
                if available > 0:
                    if index == 0 and stdout_file is not None:
                        stdout_file.write(chunk[:available])
                    else:
                        buffers[index].extend(chunk[:available])
                if totals[index] > max_output_bytes and fail_on_output_limit:
                    limit_reached.set()
        except (OSError, ValueError) as exc:
            io_errors.append(exc)

    def send_input():
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # Child may exit or be cancelled without consuming the prompt.
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    for index, stream in enumerate((proc.stdout, proc.stderr)):
        if stream is not None:
            threads.append(Thread(target=drain, args=(stream, index), daemon=True))
    if payload is not None:
        threads.append(Thread(target=send_input, daemon=True))
    error = None
    try:
        for thread in threads:
            thread.start()
        while proc.poll() is None:
            if io_errors:
                error = io_errors[0]
                break
            if limit_reached.is_set():
                error = OutputLimitExceeded(f"process output exceeds {max_output_bytes} bytes")
                break
            if cancel_event is not None and cancel_event.is_set():
                error = ProcessCancelled("process cancelled")
                break
            remaining = deadline - time.monotonic() if deadline is not None else None
            if remaining is not None and remaining <= 0:
                error = subprocess.TimeoutExpired(argv, timeout)
                break
            try:
                proc.wait(timeout=min(0.05, remaining) if remaining is not None else 0.05)
            except subprocess.TimeoutExpired:
                pass
    finally:
        kill_owned_group(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
    captured = [bytes(buf).decode("utf-8", errors="replace") if text else bytes(buf) for buf in buffers]
    if stdout_file is not None:
        stdout_file.flush()
    if io_errors and error is None:
        error = io_errors[0]
    if limit_reached.is_set() and error is None:
        error = OutputLimitExceeded(f"process output exceeds {max_output_bytes} bytes")
    if error is not None:
        if isinstance(error, subprocess.TimeoutExpired):
            error.output, error.stderr = captured
            error.stdout_truncated = totals[0] > max_output_bytes
            error.stderr_truncated = totals[1] > max_output_bytes
        raise error
    result = subprocess.CompletedProcess(argv, proc.returncode,
        captured[0] if capture_output else None, captured[1] if capture_output else None)
    result.stdout_truncated = totals[0] > max_output_bytes
    result.stderr_truncated = totals[1] > max_output_bytes
    return result


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

    start = time.monotonic()
    timed_out = False
    try:
        proc = run_captured(argv, cwd=cwd_str, env=env, timeout=timeout,
                            max_output_bytes=max_output_bytes, text=False)
        stdout_b, stderr_b = proc.stdout, proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_b, stderr_b = exc.output or b"", exc.stderr or b""
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
