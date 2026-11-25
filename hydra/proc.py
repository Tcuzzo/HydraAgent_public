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

import itertools
import math
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
#                      mkdir/tee/truncate/rsync operands; ``dd of=``; ``-t DIR``;
#                      ``tar -C``, ``curl -o``, ``wget -O/-P``.  Followed too:
#                      ``cd``, ``bash -c`` / ``eval`` / ``xargs`` / ``sudo``,
#                      ``$(..)`` / backticks / ``<(..)`` / ``>(..)`` bodies,
#                      ``if``/``do``/``then`` prefixes, and shell variables
#                      (``A=x``, ``export A=x``, ``for A in x y``, ``$PWD``).
#   Windows (cmd.exe): ``>`` ``>>`` redirections; copy/xcopy/robocopy/move/md/
#                      mkdir/mklink operands; ``cd`` / ``cmd /c`` are followed.
# This scan is the cheap, named FIRST layer.  The boundary is the kernel: on Linux
# ``skills.bash`` runs the command inside bwrap by default (workspace read-write,
# the rest read-only; ``hydra.exec_backend.confine_shell_argv``), so a write the
# scan cannot see still fails.  macOS and Windows have no such sandbox here: they
# run on the host with this scan only.  Kill switch: HYDRA_SHELL_SANDBOX=0.
# Reads are never refused.  A write target or a command word that cannot be
# resolved statically (an unset ``$VAR``, ``%VAR%``, a substitution) is refused
# and named, never guessed.

_SEPARATORS = frozenset(";&|()")
_PUNCTUATION = _SEPARATORS | frozenset("<>")
_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*=")
_VARIABLE = re.compile(r"\$(?:\{(\w+)\}|(\w+))")
_PASSTHROUGH = frozenset(
    {"sudo", "env", "command", "exec", "nohup", "time", "nice", "xargs"}
)
_KEYWORDS = frozenset({"do", "then", "else", "elif", "if", "while", "until", "!", "{"})
_DECLARATIONS = frozenset({"export", "declare", "local", "readonly", "typeset"})
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_POSIX_SINKS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"})
_WINDOWS_SINKS = frozenset({"nul"})
_TARGET_DIR = ("-t", "--target-directory")
# verb -> (which operands are write targets: "all" / "last" / "none" / "of" / an
# operand index, option flags whose VALUE is a write target).
_POSIX_WRITE_VERBS = {
    "touch": ("all", ()), "mkdir": ("all", ()), "tee": ("all", ()),
    "truncate": ("all", ()), "mv": ("all", _TARGET_DIR),
    "cp": ("last", _TARGET_DIR), "install": ("last", _TARGET_DIR),
    "ln": ("last", _TARGET_DIR), "rsync": ("last", ()), "dd": ("of", ()),
    "tar": ("none", ("-C", "--directory")),
    "curl": ("none", ("-o", "--output")),
    "wget": ("none", ("-O", "-P", "--output-document", "--directory-prefix")),
}
_WINDOWS_WRITE_VERBS = {
    "copy": ("last", ()), "xcopy": ("last", ()), "move": ("all", ()),
    "md": ("all", ()), "mkdir": ("all", ()), "robocopy": (1, ()), "mklink": (0, ()),
    # Git Bash, MSYS and WSL ship the POSIX write verbs on Windows, so a write
    # through one of them must be confined exactly like a cmd.exe one.
    "touch": ("all", ()), "tee": ("all", ()), "truncate": ("all", ()),
    "cp": ("last", _TARGET_DIR), "mv": ("all", _TARGET_DIR),
    "install": ("last", _TARGET_DIR), "ln": ("last", _TARGET_DIR),
    "rsync": ("last", ()), "dd": ("of", ()),
}
# (opener, closer, stand-in).  A process substitution is a pipe, so its stand-in
# is a harmless sink; a command substitution is unknowable, so its stand-in is
# an unresolved word that is refused wherever a path or a command word is needed.
_UNKNOWN = "$@SUBST@"
_SUBSTITUTIONS = (
    ("$(", ")", _UNKNOWN), ("<(", ")", "/dev/null"),
    (">(", ")", "/dev/null"), ("`", "`", _UNKNOWN),
)
_MAX_NESTING = 3
_MAX_VARIANTS = 64


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


# ── Substitution bodies: scanned exactly like a command of their own ──────────


def _quote_state(ch: str, state: str | None) -> str | None:
    if ch == "'" and state != '"':
        return None if state == "'" else "'"
    if ch == '"' and state != "'":
        return None if state == '"' else '"'
    return state


def _opener_at(text: str, i: int) -> tuple[str, str, str] | None:
    for spec in _SUBSTITUTIONS:
        if text.startswith(spec[0], i):
            return spec
    return None


def _closing(text: str, start: int, spec: tuple[str, str, str]) -> int:
    """Index of the closer matching the substitution opened just before ``start``."""
    opener, closer, _ = spec
    nest = opener[-1] if closer != opener else None
    depth, i = 1, start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        elif ch == nest:
            depth += 1
        i += 1
    return len(text)


def _extract_substitutions(text: str) -> tuple[str, list[str]]:
    """Replace ``$(..)``, backticks, ``<(..)``, ``>(..)`` with stand-ins; return bodies.

    Single-quoted text is literal and left alone; double-quoted text still runs
    ``$(..)`` and backticks, so it is scanned.
    """
    out: list[str] = []
    bodies: list[str] = []
    state: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and state != "'":
            out.append(text[i:i + 2])
            i += 2
            continue
        spec = None if state == "'" else _opener_at(text, i)
        if spec is None:
            state = _quote_state(ch, state)
            out.append(ch)
            i += 1
            continue
        end = _closing(text, i + len(spec[0]), spec)
        bodies.append(text[i + len(spec[0]):end])
        out.append(spec[2])
        i = end + 1
    return "".join(out), bodies


# ── Shell variables: known values only, as bash would see them ────────────────


def _lookup(name: str, env: dict, cwd: str | None) -> list[str] | None:
    """Values ``$name`` can take; ``None`` when unknown (it stays as written)."""
    if name in env:
        return env[name]
    if name == "PWD":
        return None if cwd is None else [cwd]
    value = os.environ.get(name)
    return None if value is None else [value]


def _substitute(text: str, name: str, value: str) -> str:
    pattern = r"\$(?:\{%s\}|%s\b)" % (re.escape(name), re.escape(name))
    return re.sub(pattern, lambda _m: value, text)


def _word_candidates(word: str, env: dict, cwd: str | None) -> list[str]:
    """Every value ``word`` can take after ``$VAR`` expansion; unknowns stay as written."""
    names = {m.group(1) or m.group(2) for m in _VARIABLE.finditer(word)}
    out = [word]
    for name in names:
        values = _lookup(name, env, cwd)
        if values is not None:
            out = [_substitute(c, name, v) for c in out for v in values]
    return out


def _variants(words: list[str], expand) -> list[list[str]]:
    """Every concrete command line ``words`` can become; ``words`` itself if too many."""
    if not words:
        return []
    pools = [expand(w) for w in words]
    if math.prod(len(p) for p in pools) > _MAX_VARIANTS:
        return [words]
    return [list(v) for v in itertools.product(*pools)]


def _assignments(words: list[str]) -> list[tuple[str, str]]:
    """``(name, value)`` pairs a command sets in the shell: ``A=1`` or ``export A=1``."""
    pairs: list[tuple[str, str]] = []
    i = 0
    while i < len(words) and _ASSIGNMENT.match(words[i]):
        pairs.append(words[i].partition("=")[::2])
        i += 1
    rest = words[i:]
    if not rest:
        return pairs
    if rest[0] in _DECLARATIONS:
        return pairs + [w.partition("=")[::2] for w in rest[1:] if _ASSIGNMENT.match(w)]
    return []  # ``A=1 cmd`` scopes A to that one command


def _merge(old: list[str] | None, values: list[str]) -> list[str] | None:
    """Union of every value a variable may hold; ``None`` once any is unknowable."""
    if old is None or any("$" in v or "`" in v for v in values):
        return None
    merged = list(dict.fromkeys([*old, *values]))
    return merged if len(merged) <= _MAX_VARIANTS else None


def _verb_name(word: str, windows: bool) -> str:
    name = (ntpath if windows else posixpath).basename(word)
    if windows:
        name = name.lower().removesuffix(".exe")
    return name


def _is_prefix(word: str, windows: bool) -> bool:
    if _ASSIGNMENT.match(word):
        return True
    return not windows and (word in _KEYWORDS or _verb_name(word, windows) in _PASSTHROUGH)


def _strip_wrappers(words: list[str], windows: bool) -> list[str]:
    """Drop leading ``VAR=x``, ``if``/``do``/``then`` and pass-through wrappers."""
    words = list(words)
    while words and _is_prefix(words[0], windows):
        words.pop(0)
        while words and words[0].startswith("-") and not windows:
            words.pop(0)
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


def _inline_value(word: str, flags: tuple[str, ...]) -> str | None:
    """``--flag=VALUE`` or attached short ``-fVALUE``; ``None`` when ``word`` is neither."""
    for flag in flags:
        if flag.startswith("--"):
            if word.startswith(flag + "="):
                return word[len(flag) + 1:]
        elif word.startswith(flag) and len(word) > len(flag):
            return word[len(flag):]
    return None


def _split_flags(args: list[str], flags: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """``(values of the flags in flags, args without them)``."""
    values: list[str] = []
    rest: list[str] = []
    i = 0
    while i < len(args):
        inline = _inline_value(args[i], flags)
        if args[i] in flags and i + 1 < len(args):
            values.append(args[i + 1])
            i += 1
        elif inline is not None:
            values.append(inline)
        else:
            rest.append(args[i])
        i += 1
    return values, rest


def _select(operands: list[str], mode: str | int) -> list[str]:
    if mode == "all":
        return operands
    if mode == "last":
        return operands[-1:]
    if mode == "none":
        return []
    return operands[mode:mode + 1]


def _write_operands(args: list[str], spec: tuple, windows: bool) -> list[str]:
    mode, flags = spec
    if mode == "of":  # dd of=PATH
        return [a[3:] for a in args if a.startswith("of=")]
    values, args = _split_flags(args, flags)
    return [*_select(_operands(args, windows), mode), *values]


def _verb_targets(words: list[str], windows: bool) -> list[str]:
    """Write targets named by a write-shaped command's operands."""
    if not words:
        return []
    table = _WINDOWS_WRITE_VERBS if windows else _POSIX_WRITE_VERBS
    spec = table.get(_verb_name(words[0], windows))
    return [] if spec is None else _write_operands(words[1:], spec, windows)


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
    """Expand ``~`` (and ``%VAR%`` on Windows); ``None`` when still unresolvable."""
    text = os.path.expanduser(os.path.expandvars(raw) if windows else raw)
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


def _drive_relative(text: str) -> bool:
    """A leading-slash path with no drive letter: the drive is implied, not named."""
    return text.startswith(("/", "\\")) and not ntpath.splitdrive(text)[0]


def _escape(raw: str, cwd: str | None, root: str, windows: bool) -> str | None:
    """The blocked path text when writing ``raw`` leaves ``root``, else ``None``."""
    text = _expand(raw, windows)
    if text is None:
        return f"{raw} (cannot be resolved before the command runs)"
    if _is_sink(text, windows):
        return None
    if _cwd_unknown(text, cwd, windows):
        return f"{raw} (working directory unknown after cd)"
    if windows and _drive_relative(text):
        # "/proc/1/root/x" names no Windows drive: the drive is implied, so it is
        # read against the root's own drive. The refusal keeps the path that was
        # asked for, so the caller sees what it wrote, and the place it lands.
        path = _resolve(text, root, windows)
        if _inside(path, root, windows):
            return None
        return f"{raw} (resolved to {path})"
    try:
        path = _resolve(text, cwd or root, windows)
    except OSError:  # e.g. /proc/1/root: unresolvable means outside, fail closed
        return f"{raw} (cannot be resolved: permission denied or unreadable)"
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


class _Scanner:
    """Walks one command string the way the shell will: cwd, variables, substitutions."""

    def __init__(self, root: str, cwd: str | None, windows: bool, depth: int, env: dict):
        self.root, self.cwd, self.windows = root, cwd, windows
        self.depth, self.env = depth, env

    def run(self, command: str) -> str | None:
        text, bodies = (command, []) if self.windows else _extract_substitutions(command)
        for body in bodies:
            blocked = self.child(body)
            if blocked:
                return blocked
        for words, redirects in _split_commands(_tokenize(text, self.windows)):
            blocked = self.command(words, redirects)
            if blocked:
                return blocked
        return None

    def child(self, script: str) -> str | None:
        if self.depth >= _MAX_NESTING:
            return f"{script} (nested too deeply to verify)"
        scanner = _Scanner(self.root, self.cwd, self.windows, self.depth + 1, dict(self.env))
        return scanner.run(script)

    def expand(self, word: str) -> list[str]:
        return [word] if self.windows else _word_candidates(word, self.env, self.cwd)

    def first_escape(self, texts) -> str | None:
        for text in texts:
            blocked = _escape(text, self.cwd, self.root, self.windows)
            if blocked:
                return blocked
        return None

    def bind(self, name: str, values: list[str]) -> None:
        self.env[name] = _merge(self.env.get(name, []), values)

    def record(self, words: list[str]) -> None:
        if self.windows:
            return
        if len(words) >= 4 and words[0] == "for" and words[2] == "in":
            self.bind(words[1], [c for w in words[3:] for c in self.expand(w)])
            return
        for name, value in _assignments(words):
            self.bind(name, self.expand(value))

    def variant(self, words: list[str]) -> str | None:
        if _expand(words[0], self.windows) is None:
            return f"{words[0]} (command word cannot be resolved before the command runs)"
        blocked = self.first_escape(_verb_targets(words, self.windows))
        script = _nested_script(words, self.windows)
        return blocked or (self.child(script) if script else None)

    def command(self, words: list[str], redirects: list[str]) -> str | None:
        self.record(words)
        words = _strip_wrappers(words, self.windows)
        blocked = self.first_escape(c for r in redirects for c in self.expand(r))
        variants = _variants(words, self.expand)
        for variant in variants:
            blocked = blocked or self.variant(variant)
        cwds = {_next_cwd(v, self.cwd, self.windows) for v in variants}
        if cwds:
            self.cwd = cwds.pop() if len(cwds) == 1 else None
        return blocked


def confinement_violation(
    command: str, root: str | Path, *, windows: bool | None = None
) -> str | None:
    """First path ``command`` would write outside ``root``, or ``None``.

    ``windows`` selects the grammar (default: this OS).  Never raises on
    odd input: what cannot be parsed is scanned bare, what cannot be resolved
    is reported as blocked.
    """
    windows = (os.name == "nt") if windows is None else windows
    return _Scanner(str(root), str(root), windows, 0, {}).run(command)
