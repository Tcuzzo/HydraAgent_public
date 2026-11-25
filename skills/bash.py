"""skills.bash — bounded shell execution under a scoped root.

Mirror of OpenMono's `BashTool` (`src/OpenMono.Cli/Tools/BashTool.cs`),
adapted to HydraAgent's skill protocol.

Constraints (enforced, not advised):
  - Runs with `cwd=root`, and refuses a command whose WRITE TARGETS
    (redirections; cp/mv/touch/mkdir/tee/dd of=/install/ln operands;
    copy/move/md on Windows) resolve outside `root`
    (`hydra.proc.confinement_violation`). The path rule applies on every
    OS, sandbox or not; reads are never refused. Danger-pattern gating
    beyond write paths stays with the §10.7 hook stack (`worktree_scope`,
    `authority_class_routing`).
  - On Linux the command runs inside the bwrap kernel sandbox BY DEFAULT: the
    workspace root read-write, the rest of the filesystem read-only, so a write
    outside the root fails at the kernel whatever the command text looks like
    (the static scan above is the cheap, named first layer, not the boundary).
    `HYDRA_SHELL_SANDBOX=0` is the loud kill switch (a WARNING line on every
    call); no bwrap -> host execution + the scan + one loud install line.
    macOS and Windows: host execution + the static scan, as before.
  - Wall-clock bounded by `timeout`; the process is killed on
    expiry and the partial output is returned with `timed_out=True`.
  - stdout+stderr captured separately, each truncated at
    `max_output_bytes` (default 64 KiB) — no streaming, no partial
    state past the budget.
  - Returns structured dict, never raw bytes; agent layer (§10.7
    hooks) audits the call.

Returns: `ok`, `exit_code`, `stdout`, `stderr`, `duration_s`,
`timed_out`, `truncated`, `command`, `cwd`.

Maturity: SCAFFOLDED. Promoted by §10.26.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from hydra.exec_backend import confine_shell_argv, shell_sandbox_state
from hydra.proc import confinement_violation, kill_tree, popen_portable, _shell_argv
from skills.fs_read import SkillError

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024


def _truncate(buf: bytes, limit: int) -> tuple[str, bool]:
    if len(buf) <= limit:
        return buf.decode("utf-8", errors="replace"), False
    return buf[:limit].decode("utf-8", errors="replace"), True


def _confinement_refusal(command: str, root: Path) -> dict | None:
    """Structured refusal naming the blocked path, or None when writes stay inside."""
    blocked = confinement_violation(command, root)
    if blocked is None:
        return None
    return {
        "ok": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": (
            f"refused: write target outside the workspace root: {blocked} "
            f"(workspace root: {root})"
        ),
        "duration_s": 0.0,
        "timed_out": False,
        "truncated": False,
        "command": command,
        "cwd": str(root),
        "reason": "write-outside-workspace-root",
        "blocked_path": blocked,
    }


def _validate(command: object, timeout: float, max_output_bytes: int) -> None:
    if not isinstance(command, str) or not command.strip():
        raise SkillError("command must be a non-empty string")
    if timeout <= 0:
        raise SkillError(f"timeout must be positive, got {timeout}")
    if max_output_bytes <= 0:
        raise SkillError(
            f"max_output_bytes must be positive, got {max_output_bytes}"
        )


def _run_in_container(
    command: str, root: Path, timeout: float, max_output_bytes: int
) -> dict:
    """OPT-IN container cage (HYDRA_EXEC_SANDBOX=1); never falls back to the host."""
    from hydra.container_sandbox import detect_sandbox_engine, run_in_sandbox

    if not detect_sandbox_engine():
        # An explicitly requested boundary must never silently become host execution.
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Container sandbox requested, but no podman or docker engine was found.",
            "duration_s": 0.0,
            "timed_out": False,
            "truncated": False,
            "command": command,
            "cwd": str(root),
            "reason": "no-container-engine",
        }
    start = time.monotonic()
    result = run_in_sandbox(
        command=command, cwd_mount=str(root), network=True, timeout=timeout
    )
    duration_s = time.monotonic() - start
    # Apply the same truncation contract as the host path.
    # run_in_sandbox returns str; encode to bytes for _truncate.
    stdout, t1 = _truncate(
        result["stdout"].encode("utf-8", errors="replace"), max_output_bytes
    )
    stderr, t2 = _truncate(
        result["stderr"].encode("utf-8", errors="replace"), max_output_bytes
    )
    exit_code = result["exit_code"] if result["exit_code"] is not None else -1
    timed_out = result["timed_out"]
    return {
        "ok": exit_code == 0 and not timed_out,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_s": duration_s,
        "timed_out": timed_out,
        "truncated": t1 or t2,
        "command": command,
        "cwd": str(root),
    }


def _run_on_host(
    command: str, root: Path, timeout: float, max_output_bytes: int, argv: list[str]
) -> dict:
    start = time.monotonic()
    timed_out = False
    # Route through hydra.proc.popen_portable + kill_tree so this code path
    # works on POSIX (start_new_session + os.killpg) AND Windows
    # (CREATE_NEW_PROCESS_GROUP + taskkill) without any POSIX-only calls here.
    # shell=True is replaced by an explicit argv built by hydra.proc._shell_argv
    # so we can use bash -lc on POSIX (same effective behaviour as before) and
    # cmd.exe /c on Windows.
    proc = popen_portable(
        argv,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    stdout, t1 = _truncate(stdout_b, max_output_bytes)
    stderr, t2 = _truncate(stderr_b, max_output_bytes)

    return {
        "ok": exit_code == 0 and not timed_out,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "duration_s": duration_s,
        "timed_out": timed_out,
        "truncated": t1 or t2,
        "command": command,
        "cwd": str(root),
    }


def _sandbox_notice(state: str) -> str:
    """One loud line when the sandbox is not protecting this call; empty otherwise."""
    if state == "off(kill switch)":
        return (
            "WARNING: shell sandbox is OFF (HYDRA_SHELL_SANDBOX kill switch): this command "
            "ran on the host with only the static path scan. Unset it to turn the sandbox back on."
        )
    if state == "unavailable" and sys.platform.startswith("linux"):
        return (
            "WARNING: shell sandbox unavailable: bwrap (bubblewrap) is missing or user "
            "namespaces are disabled; this command ran on the host with only the static "
            "path scan. Install it: apt install bubblewrap (dnf/pacman: bubblewrap)."
        )
    return ""


def _annotate(result: dict, state: str) -> dict:
    result["sandbox"] = state
    notice = _sandbox_notice(state)
    if notice:
        result["stderr"] = f"{notice}\n{result['stderr']}"
    return result


def _host_argv(command: str, root: Path, state: str) -> list[str]:
    argv = _shell_argv(command)
    return confine_shell_argv(argv, root) if state == "on" else argv


def run(
    command: str,
    root: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Run `command` (shell string) with `cwd=root`. Raise `SkillError`
    on input refusal; otherwise return a structured result dict — even
    when the command itself failed, timed out, or was refused for writing
    outside `root` (`reason="write-outside-workspace-root"`). Every result
    carries `sandbox`: "on" | "off(kill switch)" | "unavailable" | "container"."""
    _validate(command, timeout, max_output_bytes)
    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")
    # The path rule runs first, on every OS, sandbox or not.
    refusal = _confinement_refusal(command, root_resolved)
    if refusal is not None:
        return refusal
    # Default is OFF — the normal host subprocess path is used when unset/empty.
    if os.environ.get("HYDRA_EXEC_SANDBOX"):
        result = _run_in_container(command, root_resolved, timeout, max_output_bytes)
        return _annotate(result, "container")
    state = shell_sandbox_state()
    argv = _host_argv(command, root_resolved, state)
    return _annotate(_run_on_host(command, root_resolved, timeout, max_output_bytes, argv), state)
