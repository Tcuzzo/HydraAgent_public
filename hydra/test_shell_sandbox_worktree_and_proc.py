"""Ball 1C round 3: two findings from the independent regrade of round 2.

A. A linked git worktree keeps its private gitdir (``<repo>/.git/worktrees/<n>``)
   OUTSIDE the workspace root, so under the default kernel sandbox ``git add`` /
   ``git commit`` died with EROFS on ``index.lock``.  Committing from a linked
   worktree must work, and a write to a sibling directory must still fail.
B. ``touch /proc/1/root/x`` made the static scan raise ``PermissionError`` out of
   ``bash.run``.  An unresolvable path is outside the root: a structured refusal.

Real ``skills.bash.run``, real bubblewrap, a real repo and a real linked worktree,
real commit read back with git.  Skipped only when bwrap is absent.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from hydra import exec_backend
from skills import bash

HAS_SANDBOX = sys.platform.startswith("linux") and exec_backend._bwrap_available()
needs_sandbox = pytest.mark.skipif(
    not HAS_SANDBOX, reason="needs Linux with a working bwrap (bubblewrap)"
)

_IDENT = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **_IDENT},
    ).stdout.strip()


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    monkeypatch.delenv("HYDRA_SHELL_SANDBOX", raising=False)
    monkeypatch.delenv("HYDRA_EXEC_SANDBOX", raising=False)
    for key, value in _IDENT.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def linked(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "trunk")
    (main / "seed").write_text("s\n", encoding="utf-8")
    _git(main, "add", "seed")
    _git(main, "commit", "-q", "-m", "seed")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", "-b", "feature", str(wt))
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    return main, wt, sibling


@needs_sandbox
def test_a_linked_worktree_can_commit_inside_the_sandbox(linked):
    main, wt, sibling = linked
    assert (wt / ".git").is_file(), "fixture must be a linked worktree"
    result = bash.run("echo hi > f && git add f && git commit -q -m x", wt)
    assert result["sandbox"] == "on", result
    assert result["ok"] is True, result
    assert _git(wt, "log", "-1", "--format=%s") == "x"
    assert "f" in _git(wt, "show", "--name-only", "--format=", "HEAD").split()


@needs_sandbox
def test_a_linked_worktree_still_cannot_write_to_a_sibling_or_the_main_tree(linked):
    main, wt, sibling = linked
    result = bash.run(f"timeout 5 touch {sibling}/x", wt)
    assert result["ok"] is False, result
    assert "Read-only file system" in result["stderr"]
    assert not (sibling / "x").exists()
    result = bash.run(f"timeout 5 touch {main}/leak", wt)
    assert result["ok"] is False, result
    assert not (main / "leak").exists()
    result = bash.run(f"timeout 5 touch {main}/.git/hooks/leak", wt)
    assert result["ok"] is False, result
    assert not (main / ".git" / "hooks" / "leak").exists()


def test_a_proc_path_is_refused_cleanly_not_raised(tmp_path):
    result = bash.run("touch /proc/1/root/x", tmp_path)
    assert result["ok"] is False, result
    assert result["reason"] == "write-outside-workspace-root"
    assert "/proc/1/root/x" in result["blocked_path"]
