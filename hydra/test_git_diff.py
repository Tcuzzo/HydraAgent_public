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


def test_timeout_returns_skillerror_not_raw_exception(tmp_path):
    wt = _make_worktree(tmp_path)
    # A 0.001s timeout kills git mid-rev-parse (the very first _run_git call in
    # run()).  The timeout must surface as a SkillError mentioning 124 / timed
    # out — NOT a raw subprocess.TimeoutExpired leaking to the caller.
    with pytest.raises(git_diff.SkillError) as ei:
        git_diff.run(wt, timeout=0.001)
    msg = str(ei.value)
    assert "124" in msg or "timed out" in msg


def test_run_git_timeout_yields_structured_completed_process(tmp_path):
    """_run_git must catch TimeoutExpired and return a CompletedProcess with
    the timeout exit code, never raise."""
    wt = _make_worktree(tmp_path)
    cp = git_diff._run_git(wt, ["status"], timeout=0.001)
    assert isinstance(cp, subprocess.CompletedProcess)
    assert cp.returncode == git_diff.GIT_TIMEOUT_EXIT_CODE