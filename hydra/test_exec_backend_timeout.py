# hydra/test_exec_backend_timeout.py — RED contract for the exec_backend
# timeout seam (inv_24: a structured timeout, not a raw TimeoutExpired).
#
# worker_aci._test and worker_jobs._run_verify_commands consume ExecResult
# rows; a raw subprocess.TimeoutExpired would propagate a traceback and the
# commands.tsv row would be lost.  These tests run a REAL subprocess (no
# mocks) that sleeps longer than the timeout and assert the caller gets an
# ExecResult with returncode == EXEC_TIMEOUT_EXIT_CODE and partial stdout.
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

from hydra.exec_backend import (
    EXEC_TIMEOUT_EXIT_CODE,
    ExecResult,
    _reset_capability_cache,
    run_sandboxed,
    run_sandboxed_shell,
)


def test_shell_runner_uses_a_shell_available_on_the_current_platform(tmp_path):
    result = run_sandboxed_shell("echo hydra-shell-portable", workspace=tmp_path, timeout=10)
    assert result.returncode == 0
    assert "hydra-shell-portable" in result.stdout


def test_run_on_host_timeout_returns_structured_execresult(tmp_path):
    """A host-path command that exceeds timeout returns ExecResult(124), not
    a raw TimeoutExpired.

    Layer A — REAL subprocess path.  The child runs ``echo partial-out;
    sleep 60`` and the timeout is 12s, giving spawn + echo roughly an
    order of magnitude more headroom than the worst observed CI runner
    spawn latency.  On every supported runner the echo will have flushed
    before the kill, so the partial-output assertion is deterministic
    without becoming a wall-clock race.  Test duration is bounded by the
    12s timeout.
    """
    _reset_capability_cache()
    # Force the host path by making bwrap unavailable.  Patch the capability
    # probe result directly (the probe is a free in-process organ, not an
    # external leaf — patching the cached flag is the seam test boundary, not
    # mock theater: the actual subprocess.run is REAL).
    import hydra.exec_backend as eb
    eb._PROBE_DONE = True
    eb._BWRAP_CAPABLE = False
    try:
        result = run_sandboxed(
            [sys.executable, "-c", "import time; print('partial-out', flush=True); time.sleep(60)"],
            workspace=Path(tmp_path),
            timeout=12,
        )
    finally:
        _reset_capability_cache()
    assert isinstance(result, ExecResult)
    assert result.returncode == EXEC_TIMEOUT_EXIT_CODE
    assert "partial-out" in result.stdout
    assert result.sandboxed is False


def _force_timeout_with_partial_stdout(*args, **kwargs):
    """Transport-leaf shim: raise TimeoutExpired carrying partial stdout,
    mirroring a real subprocess.run(..., timeout=X) outcome where some
    output flushed before the kill.  The SUT (hydra.exec_backend._run_on_host
    / _run_in_bwrap) still executes its real bytes->str decode, None->""
    fallback, and ExecResult(124) translation end to end — only the
    transport is replaced.

    Same proven pattern as hydra/test_git_diff.py::_force_timeout (commit
    7ab659f on main): mock the wire, never the brain.
    """
    cmd = args[0] if args else kwargs.get("args", ["sh"])
    raise subprocess.TimeoutExpired(
        cmd=cmd,
        timeout=kwargs.get("timeout", 12),
        output=b"partial-out",
    )


def test_run_on_host_timeout_via_transport_shim(tmp_path):
    """Layer B — DETERMINISTIC transport-leaf shim.  Mock the wire
    (subprocess.run) so the timeout ALWAYS fires with partial stdout,
    independent of spawn latency.  The real run_sandboxed bytes->str
    decode, None->"" fallback, and ExecResult(EXEC_TIMEOUT_EXIT_CODE)
    translation must STILL run end to end.  Mock the wire, never the
    brain.

    Same proven pattern as hydra/test_git_diff.py's two transport-shim
    tests (commit 7ab659f on main).
    """
    import hydra.exec_backend as eb
    eb._PROBE_DONE = True
    eb._BWRAP_CAPABLE = False
    try:
        with patch(
            "hydra.exec_backend.subprocess.run",
            side_effect=_force_timeout_with_partial_stdout,
        ):
            result = run_sandboxed(
                [sys.executable, "-c", "import time; print('partial-out', flush=True); time.sleep(60)"],
                workspace=Path(tmp_path),
                timeout=12,
            )
    finally:
        _reset_capability_cache()
    assert isinstance(result, ExecResult)
    assert result.returncode == EXEC_TIMEOUT_EXIT_CODE
    assert "partial-out" in result.stdout
    assert result.sandboxed is False


def test_timeout_does_not_raise(tmp_path):
    """The seam contract: timeout must never raise; it must always return."""
    import hydra.exec_backend as eb
    eb._PROBE_DONE = True
    eb._BWRAP_CAPABLE = False
    try:
        result = run_sandboxed(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            workspace=Path(tmp_path),
            timeout=0.3,
        )
    finally:
        _reset_capability_cache()
    assert result.returncode == EXEC_TIMEOUT_EXIT_CODE


def test_normal_command_still_runs(tmp_path):
    """Regression guard: the timeout try/except must not break the happy path."""
    import hydra.exec_backend as eb
    eb._PROBE_DONE = True
    eb._BWRAP_CAPABLE = False
    try:
        result = run_sandboxed(
            [sys.executable, "-c", "print('hello-exit-7'); raise SystemExit(7)"],
            workspace=Path(tmp_path),
            timeout=10,
        )
    finally:
        _reset_capability_cache()
    assert result.returncode == 7
    assert "hello-exit-7" in result.stdout
