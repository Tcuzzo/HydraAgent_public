# hydra/test_exec_backend_timeout.py — RED contract for the exec_backend
# timeout seam (inv_24: a structured timeout, not a raw TimeoutExpired).
#
# worker_aci._test and worker_jobs._run_verify_commands consume ExecResult
# rows; a raw subprocess.TimeoutExpired would propagate a traceback and the
# commands.tsv row would be lost.  These tests run a REAL subprocess (no
# mocks) that sleeps longer than the timeout and assert the caller gets an
# ExecResult with returncode == EXEC_TIMEOUT_EXIT_CODE and partial stdout.
import time
from pathlib import Path

from hydra.exec_backend import (
    EXEC_TIMEOUT_EXIT_CODE,
    ExecResult,
    _reset_capability_cache,
    run_sandboxed,
)


def test_run_on_host_timeout_returns_structured_execresult(tmp_path):
    """A host-path command that exceeds timeout returns ExecResult(124), not
    a raw TimeoutExpired."""
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
            ["sh", "-c", "echo partial-out; sleep 10"],
            workspace=Path(tmp_path),
            timeout=0.5,
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
            ["sh", "-c", "sleep 30"],
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
            ["sh", "-c", "echo hello-exit-7; exit 7"],
            workspace=Path(tmp_path),
            timeout=10,
        )
    finally:
        _reset_capability_cache()
    assert result.returncode == 7
    assert "hello-exit-7" in result.stdout