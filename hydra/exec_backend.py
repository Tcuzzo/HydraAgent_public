"""hydra.exec_backend — OS-sandboxed execution backend (Slice 8: mashup build).

Public API
----------
run_sandboxed(cmd, *, workspace, network=False, timeout, env=None) -> ExecResult

  Runs ``cmd`` (a list of strings) inside a bwrap(1) sandbox when bwrap is
  available and unprivileged user namespaces are enabled.  Falls back to a
  plain subprocess run on the host with a WARNING log if bwrap is absent or
  userns is disabled.

Sandbox guarantees (Linux + bwrap path)
----------------------------------------
- Writes are confined to ``workspace``  (bind-mounted rw; the rest is ro or absent).
- Network is OFF by default (``--unshare-net``).  Pass ``network=True`` for the
  rare cases that need it (e.g. pip install in an isolated build).
- ``--die-with-parent`` ensures the child is reaped if the Python process exits.
- Capability probe is done once at import time and cached.

Wiring
------
worker_aci._test and worker_jobs._run_verify_commands both call run_sandboxed so
agent-issued test/verify commands run confined BY DEFAULT.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ExecResult:
    returncode: int
    stdout: str
    sandboxed: bool = False


# ---------------------------------------------------------------------------
# Capability probe — done once, cached in module globals
# ---------------------------------------------------------------------------

_PROBE_DONE: bool = False
_BWRAP_CAPABLE: bool = False


def _probe_bwrap() -> bool:
    """Return True iff bwrap is on PATH and unprivileged userns is enabled."""
    if shutil.which("bwrap") is None:
        return False
    # Check unprivileged userns (Linux kernel setting)
    userns_file = Path("/proc/sys/kernel/unprivileged_userns_clone")
    if userns_file.exists():
        try:
            val = userns_file.read_text().strip()
            if val != "1":
                return False
        except OSError:
            return False
    # Quick functional test — a no-op sandbox invocation
    # Use absolute /bin/true so the path is unambiguous inside the sandbox.
    true_cmd = "/bin/true" if Path("/bin/true").exists() else "/usr/bin/true"
    try:
        r = subprocess.run(
            ["bwrap",
             "--ro-bind", "/usr", "/usr",
             "--ro-bind", "/bin", "/bin",
             "--ro-bind-try", "/lib", "/lib",
             "--ro-bind-try", "/lib64", "/lib64",
             "--proc", "/proc", "--dev", "/dev",
             "--die-with-parent", "--new-session",
             true_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


def _bwrap_available() -> bool:
    """Return cached capability result (probed lazily on first call)."""
    global _PROBE_DONE, _BWRAP_CAPABLE
    if not _PROBE_DONE:
        _BWRAP_CAPABLE = _probe_bwrap()
        _PROBE_DONE = True
    return _BWRAP_CAPABLE


def _reset_capability_cache() -> None:
    """Reset the cached probe result (used in tests only)."""
    global _PROBE_DONE, _BWRAP_CAPABLE
    _PROBE_DONE = False
    _BWRAP_CAPABLE = False


# ---------------------------------------------------------------------------
# Core sandboxed runner
# ---------------------------------------------------------------------------

def run_sandboxed(
    cmd: Sequence[str],
    *,
    workspace: Path,
    network: bool = False,
    timeout: float = 120,
    env: dict[str, str] | None = None,
) -> ExecResult:
    """Run *cmd* in an OS sandbox confined to *workspace*.

    Parameters
    ----------
    cmd:
        Command as a list of strings (not a shell string — use ``["sh", "-c", ...]``
        if you need shell expansion).
    workspace:
        Directory that the sandboxed process may READ AND WRITE.  Everything
        else is read-only (system dirs) or absent.
    network:
        ``False`` (default): ``--unshare-net`` — no outbound network.
        ``True``: host network namespace is shared (use only for pip-style installs).
    timeout:
        Wall-clock seconds before the child is killed.
    env:
        Optional environment dict.  Defaults to the current process environment.

    Returns
    -------
    ExecResult
        ``.returncode`` and ``.stdout`` (combined stdout+stderr).
    """
    workspace = Path(workspace).expanduser().resolve()

    if _bwrap_available():
        result = _run_in_bwrap(cmd, workspace=workspace, network=network, timeout=timeout, env=env)
        return ExecResult(returncode=result.returncode, stdout=result.stdout, sandboxed=True)
    else:
        log.warning(
            "hydra.exec_backend: bwrap sandbox unavailable — "
            "running command on the host WITHOUT confinement. "
            "Install bwrap and enable unprivileged_userns_clone=1 for sandboxing."
        )
        result = _run_on_host(cmd, workspace=workspace, timeout=timeout, env=env)
        return ExecResult(returncode=result.returncode, stdout=result.stdout, sandboxed=False)


def _build_bwrap_cmd(
    cmd: Sequence[str],
    *,
    workspace: Path,
    network: bool,
) -> list[str]:
    """Build the full bwrap argv."""
    bwrap = ["bwrap"]

    # --- Read-only system mounts ---
    def ro(src: str, dst: str | None = None) -> None:
        if Path(src).exists():
            bwrap.extend(["--ro-bind", src, dst or src])

    ro("/usr")
    ro("/bin")
    ro("/lib")
    ro("/lib64")
    ro("/lib32")
    ro("/etc")
    ro("/sbin")
    ro("/run/openssl", "/run/openssl")  # some distros put certs here

    # Special filesystems
    bwrap.extend(["--proc", "/proc"])
    bwrap.extend(["--dev", "/dev"])

    # Workspace: bind rw so the command can read/write its own tree
    bwrap.extend(["--bind", str(workspace), str(workspace)])

    # Safety flags
    bwrap.append("--die-with-parent")
    bwrap.append("--new-session")

    # Network
    if not network:
        bwrap.append("--unshare-net")

    # Working directory inside the sandbox
    bwrap.extend(["--chdir", str(workspace)])

    # The actual command
    bwrap.extend(list(cmd))
    return bwrap


def _run_in_bwrap(
    cmd: Sequence[str],
    *,
    workspace: Path,
    network: bool,
    timeout: float,
    env: dict[str, str] | None,
) -> ExecResult:
    bwrap_cmd = _build_bwrap_cmd(cmd, workspace=workspace, network=network)
    proc = subprocess.run(
        bwrap_cmd,
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return ExecResult(returncode=proc.returncode, stdout=proc.stdout or "")


def _run_on_host(
    cmd: Sequence[str],
    *,
    workspace: Path,
    timeout: float,
    env: dict[str, str] | None,
) -> ExecResult:
    proc = subprocess.run(
        list(cmd),
        cwd=str(workspace),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )
    return ExecResult(returncode=proc.returncode, stdout=proc.stdout or "")


# ---------------------------------------------------------------------------
# Shell-string variant used by the worker wiring below
# ---------------------------------------------------------------------------

def run_sandboxed_shell(
    shell_cmd: str,
    *,
    workspace: Path,
    network: bool = False,
    timeout: float = 120,
    env: dict[str, str] | None = None,
) -> ExecResult:
    """Convenience wrapper: run a shell string via ``sh -c``."""
    return run_sandboxed(
        ["sh", "-c", shell_cmd],
        workspace=workspace,
        network=network,
        timeout=timeout,
        env=env,
    )
