# hydra/test_git_diff.py — RED contract for skills.git_diff timeout seam.
#
# The contract (skills/git_diff.py docstring) promises a hard timeout that
# returns a dict / raises SkillError.  Before the fix, an expired timeout
# propagated a raw subprocess.TimeoutExpired to the caller — a contract
# breach.  These tests exercise the seam against a REAL git invocation (no
# mocks): we point git at a worktree and force a timeout with an unreachable
# ref + tiny timeout, then assert the caller sees SkillError, not a raw
# TimeoutExpired.
import subprocess
from unittest.mock import patch
import pytest
from skills import git_diff


def _make_worktree(tmp_path):
    """Create a real git worktree in tmp_path and return its path."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "file.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"],
        check=True,
        env={"PATH": __import__("os").environ["PATH"], "GIT_AUTHOR_NAME": "t",
             "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return tmp_path


def _force_timeout(*args, **kwargs):
    """Transport-leaf shim: raise TimeoutExpired regardless of wall-clock.

    Mirrors a real subprocess.run(..., timeout=X) outcome. The SUT
    (skills.git_diff.run + _run_git) still executes its real exception
    translation end to end — only the transport is replaced.
    """
    cmd = args[0] if args else kwargs.get("args", ["git"])
    raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0.001))


def test_timeout_returns_skillerror_not_raw_exception(tmp_path):
    """REAL-SUT timeout path. On fast hardware (github runners) git rev-parse
    completes in <1ms, so a wall-clock 0.001s timeout never fires — the test
    was brittle and could pass for the wrong reason (no timeout ever
    triggered). Force the timeout by monkeypatching ONLY the transport leaf
    (subprocess.run) so the real skills.git_diff.run() / _run_git()
    exception-translation code STILL runs.

    Per BACKS testing invariant 2: do not mock the SUT; patching the
    transport leaf preserves the real command-building + exception
    translation + SkillError raising end to end (analogous to mocking the
    HTTP transport layer while traversing the real prompt-building +
    routing + parsing logic).
    """
    wt = _make_worktree(tmp_path)
    with patch("skills.git_diff.subprocess.run", side_effect=_force_timeout):
        with pytest.raises(git_diff.SkillError) as ei:
            git_diff.run(wt, timeout=0.001)
    msg = str(ei.value)
    assert "124" in msg or "timed out" in msg


def test_run_git_timeout_yields_structured_completed_process(tmp_path):
    """REAL-SUT timeout translation. Same transport-leaf patch as above:
    _run_git must catch TimeoutExpired and return a CompletedProcess with
    the timeout exit code (124), never raise."""
    wt = _make_worktree(tmp_path)
    with patch("skills.git_diff.subprocess.run", side_effect=_force_timeout):
        cp = git_diff._run_git(wt, ["status"], timeout=0.001)
    assert isinstance(cp, subprocess.CompletedProcess)
    assert cp.returncode == git_diff.GIT_TIMEOUT_EXIT_CODE


def test_run_git_does_not_raise_timeoutexpired_under_real_shell(tmp_path):
    """Sanity check that runs against the REAL subprocess.run path: a healthy
    15s default timeout against a tiny real worktree must NOT raise — proves
    the exception-translation branch is only taken when TimeoutExpired
    actually occurs, and that the normal-path return contract still holds."""
    wt = _make_worktree(tmp_path)
    out = git_diff.run(wt)  # default 15s timeout, healthy worktree
    assert out["ok"] is True
    # files_changed is the row count from `git diff --stat` (lines containing
    # "|"). A clean worktree with no working-tree diff against HEAD returns 0
    # — that's the correct contract for a healthy run, NOT a count of files
    # committed in the worktree.
    assert out["files_changed"] == 0  # clean worktree, no diff vs HEAD
    assert "stat" in out