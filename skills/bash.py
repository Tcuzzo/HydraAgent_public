"""skills.bash — bounded shell execution under a scoped root.

Mirror of OpenMono's `BashTool` (`src/OpenMono.Cli/Tools/BashTool.cs`),
adapted to HydraAgent's skill protocol.

Constraints (enforced, not advised):
  - Runs with `cwd=root`; the command itself is the operator's
    responsibility (the §10.7 hook stack — `worktree_scope`,
    `authority_class_routing` — is where danger-pattern gating
    belongs). This skill is the boundary, not the policy.
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
import time
from pathlib import Path

from hydra.proc import ProcessCancelled, popen_portable, run_captured, _shell_argv
from skills.fs_read import SkillError

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024


def _truncate(buf: bytes, limit: int) -> tuple[str, bool]:
    if len(buf) <= limit:
        return buf.decode("utf-8", errors="replace"), False
    return buf[:limit].decode("utf-8", errors="replace"), True


def run(
    command: str,
    root: str | Path,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> dict:
    """Run `command` (shell string) with `cwd=root`. Raise `SkillError`
    on input refusal; otherwise return a structured result dict — even
    when the command itself failed or timed out."""
    if not isinstance(command, str) or not command.strip():
        raise SkillError("command must be a non-empty string")
    if timeout <= 0:
        raise SkillError(f"timeout must be positive, got {timeout}")
    if max_output_bytes <= 0:
        raise SkillError(
            f"max_output_bytes must be positive, got {max_output_bytes}"
        )

    root_resolved = Path(root).resolve()
    if not root_resolved.is_dir():
        raise SkillError(f"root is not a directory: {root_resolved}")

    # --- OPT-IN container cage (HYDRA_EXEC_SANDBOX=1) ---
    # Default is OFF — normal host subprocess path is used when unset/empty.
    if os.environ.get("HYDRA_EXEC_SANDBOX"):
        from hydra.container_sandbox import detect_sandbox_engine, run_in_sandbox
        if detect_sandbox_engine():
            start = time.monotonic()
            result = run_in_sandbox(
                command=command,
                cwd_mount=str(root_resolved),
                network=True,
                timeout=timeout,
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
                "cwd": str(root_resolved),
            }
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
            "cwd": str(root_resolved),
            "reason": "no-container-engine",
        }
    # --- end container cage ---

    start = time.monotonic()
    timed_out = False
    # Shared capture owns output limits, cancellation, and process-group cleanup
    # on POSIX and Windows. Keep the spawn seam available to runtime callers.
    argv = _shell_argv(command)
    truncated = False
    cancelled = False
    try:
        proc = run_captured(argv, cwd=root_resolved, timeout=timeout,
                            max_output_bytes=max_output_bytes, text=False,
                            popen_factory=popen_portable)
        stdout_b, stderr_b = proc.stdout, proc.stderr
        truncated = proc.stdout_truncated or proc.stderr_truncated
        exit_code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_b, stderr_b = exc.output or b"", exc.stderr or b""
        truncated = bool(getattr(exc, "stdout_truncated", False) or getattr(exc, "stderr_truncated", False))
        exit_code = -1
        timed_out = True
    except ProcessCancelled:
        stdout_b, stderr_b = b"", b"Operator cancelled the running command."
        exit_code = -1
        cancelled = True

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
        "truncated": truncated or t1 or t2,
        "cancelled": cancelled,
        "command": command,
        "cwd": str(root_resolved),
    }
