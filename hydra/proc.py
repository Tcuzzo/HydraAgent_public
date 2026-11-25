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

``confinement_violation(command, root)``
    Static write-target check for a shell string: the first path the command
    would write outside ``root`` (redirections and write-shaped verbs), or
    ``None``.  Per-OS grammar: bash on POSIX, cmd.exe on Windows.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
import re
import shlex
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


# ── Workspace write-target confinement ────────────────────────────────────────
#
# Per-OS contract.  The check is static: it reads the command string the same
# way the shell that will run it does, and refuses a write whose resolved path
# leaves ``root``.  It is a path rule, not a sandbox; it applies on every OS.
#   POSIX   (bash):    ``>`` ``>>`` ``&>`` redirections; cp/mv/install/ln/touch/
#                      mkdir/tee operands; ``dd of=``; ``-t DIR``; ``cd`` and
#                      ``bash -c`` / ``eval`` are followed.
#   Windows (cmd.exe): ``>`` ``>>`` redirections; copy/xcopy/robocopy/move/md/
#                      mkdir/mklink operands; ``cd`` / ``cmd /c`` are followed.
# Reads are never refused.  A target that cannot be resolved statically (an
# unexpanded ``$VAR``, ``$(...)``, ``%VAR%``) is refused rather than guessed.

_SEPARATORS = frozenset(";&|()")
_PUNCTUATION = _SEPARATORS | frozenset("<>")
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_PASSTHROUGH = frozenset({"sudo", "env", "command", "exec", "nohup", "time", "nice"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_POSIX_SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})
_WINDOWS_SINKS = frozenset({"nul"})
# verb -> which operands are write targets: "all", "last", or an operand index.
_POSIX_WRITE_VERBS = {
    "touch": "all", "mkdir": "all", "tee": "all", "mv": "all",
    "cp": "last", "install": "last", "ln": "last", "dd": "of",
}
_WINDOWS_WRITE_VERBS = {
    "copy": "last", "xcopy": "last", "move": "all", "md": "all", "mkdir": "all",
    "robocopy": 1, "mklink": 0,
}
_TARGET_DIR_FLAGS = frozenset({"-t", "--target-directory"})
_MAX_NESTING = 3


def _tokenize(command: str, windows: bool) -> list[str]:
    """Split ``command`` into words and shell punctuation tokens."""
    text = command.replace("\r", "\n").replace("\n", " ; ")
    try:
        lex = shlex.shlex(text, posix=not windows, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError:  # unbalanced quote: the shell will reject it too; scan bare
        lex = shlex.shlex(re.sub(r"[\"']", " ", text), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    return [t.strip('"') for t in tokens] if windows else tokens


def _is_separator(token: str) -> bool:
    return bool(token) and set(token) <= _SEPARATORS


def _flush(commands: list, words: list[str], redirects: list[str]) -> None:
    if words or redirects:
        commands.append((words, redirects))


def _redirect_target(token: str, nxt: str | None) -> str | None:
    """The file a redirection operator writes to; ``None`` for reads and fd dups."""
    if ">" not in token or nxt is None:
        return None
    if token.endswith("&") and re.fullmatch(r"\d+|-", nxt):
        return None  # ``2>&1`` duplicates a descriptor, it does not name a file
    return nxt


def _split_commands(tokens: list[str]) -> list[tuple[list[str], list[str]]]:
    """Group tokens into simple commands: ``(words, redirection_targets)``."""
    commands: list[tuple[list[str], list[str]]] = []
    words: list[str] = []
    redirects: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_separator(token):
            _flush(commands, words, redirects)
            words, redirects = [], []
        elif set(token) <= _PUNCTUATION:  # a redirection operator, not a quoted word
            target = _redirect_target(token, tokens[i + 1] if i + 1 < len(tokens) else None)
            if target is not None:
                redirects.append(target)
            if words and words[-1].isdigit():
                words.pop()  # the fd number in ``2>file``
            i += 1  # the next token belongs to the redirection
        else:
            words.append(token)
        i += 1
    _flush(commands, words, redirects)
    return commands


def _verb_name(word: str, windows: bool) -> str:
    name = (ntpath if windows else posixpath).basename(word)
    if windows:
        name = name.lower().removesuffix(".exe")
    return name


def _strip_wrappers(words: list[str], windows: bool) -> list[str]:
    """Drop leading ``VAR=x`` assignments and pass-through wrappers (sudo, env...)."""
    words = list(words)
    while words:
        if _ASSIGNMENT.match(words[0]) or (
            not windows and _verb_name(words[0], windows) in _PASSTHROUGH
        ):
            words.pop(0)
            while words and words[0].startswith("-") and not windows:
                words.pop(0)
        else:
            break
    return words


def _is_option(word: str, windows: bool) -> bool:
    if windows:
        return word.startswith("/") and not re.search(r"[\\/]", word[1:])
    return word.startswith("-") and word != "-"


def _operands(args: list[str], windows: bool) -> list[str]:
    """Positional operands: options skipped; after ``--`` everything is positional."""
    out: list[str] = []
    positional = False
    for word in args:
        if not positional and word == "--":
            positional = True
        elif positional or not _is_option(word, windows):
            out.append(word)
    return out


def _target_dir(args: list[str]) -> tuple[str | None, list[str]]:
    """Pull ``-t DIR`` / ``--target-directory[=]DIR`` out of ``args``."""
    for i, word in enumerate(args):
        if word in _TARGET_DIR_FLAGS and i + 1 < len(args):
            return args[i + 1], args[:i] + args[i + 2:]
        if word.startswith("--target-directory="):
            return word.split("=", 1)[1], args[:i] + args[i + 1:]
    return None, args


def _select(operands: list[str], mode: str | int) -> list[str]:
    if mode == "all":
        return operands
    if mode == "last":
        return operands[-1:]
    return operands[mode:mode + 1]


def _write_operands(args: list[str], mode: str | int, windows: bool) -> list[str]:
    if mode == "of":  # dd of=PATH
        return [a[3:] for a in args if a.startswith("of=")]
    directory, args = (None, args) if windows else _target_dir(args)
    targets = _select(_operands(args, windows), mode)
    return targets if directory is None else [*targets, directory]


def _verb_targets(words: list[str], windows: bool) -> list[str]:
    """Write targets named by a write-shaped command's operands."""
    if not words:
        return []
    table = _WINDOWS_WRITE_VERBS if windows else _POSIX_WRITE_VERBS
    mode = table.get(_verb_name(words[0], windows))
    return [] if mode is None else _write_operands(words[1:], mode, windows)


def _windows_nested(verb: str, args: list[str]) -> str | None:
    flags = [i for i, a in enumerate(args) if a.lower() in ("/c", "/k")]
    return " ".join(args[flags[0] + 1:]) if verb == "cmd" and flags else None


def _posix_nested(verb: str, args: list[str]) -> str | None:
    if verb == "eval":
        return " ".join(args)
    if verb not in _POSIX_SHELLS:
        return None
    for i, arg in enumerate(args[:-1]):
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg):
            return args[i + 1]
    return None


def _nested_script(words: list[str], windows: bool) -> str | None:
    """The script text of ``bash -c ...`` / ``eval ...`` / ``cmd /c ...``."""
    if not words:
        return None
    pick = _windows_nested if windows else _posix_nested
    return pick(_verb_name(words[0], windows), words[1:])


def _expand(raw: str, windows: bool) -> str | None:
    """Expand ``~`` and defined variables; ``None`` when still unresolvable."""
    text = os.path.expanduser(os.path.expandvars(raw))
    unresolved = ("%",) if windows else ("$", "`")
    return None if any(ch in text for ch in unresolved) else text


def _resolve(text: str, cwd: str, windows: bool) -> str:
    """Absolute, normalised path; symlinks resolved when the flavour is this OS."""
    mod = ntpath if windows else posixpath
    joined = mod.join(cwd, text)
    if windows == (os.name == "nt"):
        return os.path.realpath(joined)
    return mod.normpath(joined)


def _inside(path: str, root: str, windows: bool) -> bool:
    mod = ntpath if windows else posixpath
    try:
        return mod.commonpath([mod.normcase(path), mod.normcase(root)]) == mod.normcase(root)
    except ValueError:  # different drives
        return False


def _is_sink(text: str, windows: bool) -> bool:
    return text.lower() in _WINDOWS_SINKS if windows else text in _POSIX_SINKS


def _cwd_unknown(text: str, cwd: str | None, windows: bool) -> bool:
    return cwd is None and not (ntpath if windows else posixpath).isabs(text)


def _escape(raw: str, cwd: str | None, root: str, windows: bool) -> str | None:
    """The blocked path text when writing ``raw`` leaves ``root``, else ``None``."""
    text = _expand(raw, windows)
    if text is None:
        return f"{raw} (cannot be resolved before the command runs)"
    if _is_sink(text, windows):
        return None
    if _cwd_unknown(text, cwd, windows):
        return f"{raw} (working directory unknown after cd)"
    path = _resolve(text, cwd or root, windows)
    return None if _inside(path, root, windows) else path


def _next_cwd(words: list[str], cwd: str | None, windows: bool) -> str | None:
    """The working directory after ``cd`` / ``pushd`` / ``chdir``; unchanged otherwise."""
    if not words or _verb_name(words[0], windows) not in ("cd", "chdir", "pushd"):
        return cwd
    ops = _operands(words[1:], windows)
    if not ops:
        return cwd if windows else os.path.expanduser("~")
    text = _expand(ops[0], windows)
    if text is None or text == "-" or cwd is None:
        return None
    return _resolve(text, cwd, windows)


def _nested_violation(
    words: list[str], root: str, cwd: str | None, windows: bool, depth: int
) -> str | None:
    script = _nested_script(words, windows)
    if not script or depth >= _MAX_NESTING:
        return None
    return _scan(script, root, cwd, windows, depth + 1)


def _scan(
    command: str, root: str, cwd: str | None, windows: bool, depth: int
) -> str | None:
    for words, redirects in _split_commands(_tokenize(command, windows)):
        words = _strip_wrappers(words, windows)
        for raw in [*redirects, *_verb_targets(words, windows)]:
            blocked = _escape(raw, cwd, root, windows)
            if blocked:
                return blocked
        blocked = _nested_violation(words, root, cwd, windows, depth)
        if blocked:
            return blocked
        cwd = _next_cwd(words, cwd, windows)
    return None


def confinement_violation(
    command: str, root: str | Path, *, windows: bool | None = None
) -> str | None:
    """First path ``command`` would write outside ``root``, or ``None``.

    ``windows`` selects the grammar (default: this OS).  Never raises on
    odd input: what cannot be parsed is scanned bare, what cannot be resolved
    is reported as blocked.
    """
    windows = (os.name == "nt") if windows is None else windows
    return _scan(command, str(root), str(root), windows, 0)
