"""Ball 1C round 4: shared repo state stays read-only under the default sandbox.

Round 3 bound a linked worktree's gitdir and the common objects/refs/logs read-write
so ``git commit`` could work.  The blind grade showed that is a sandbox escape: a
hostile workspace can forge a ``.git`` file (or ``commondir``) to choose which host
directories get bound, git writes into objects/refs by renaming, and a writable
``config.worktree`` can run code on the host.  The decision: nothing outside the
workspace root is writable.  A commit from a linked worktree therefore FAILS, and
fails LOUDLY with the way out.

A. In a linked worktree ``git commit`` fails, nothing lands in the repo, and stderr
   carries one clear line naming the way out.
B. Sibling, main-tree and hooks writes still fail.
C. ``touch /proc/1/root/x`` is a structured refusal, not a raised PermissionError.
D. One regression case per grader probe: forged ``.git`` in the workspace and in a
   parent, a forged ``commondir``, a gitdir pointing at a sibling worktree, an
   arbitrary dir holding objects/refs/logs.  The write fails and the target is
   byte-unchanged (hashed before and after).

Real ``skills.bash.run``, real bubblewrap, real repos.  Skipped only when bwrap is absent.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

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


def _tree_hash(path: Path) -> str:
    """Digest of every path and byte under ``path`` (symlinks by target)."""
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        digest.update(str(item.relative_to(path)).encode())
        if item.is_symlink():
            digest.update(os.readlink(item).encode())
        elif item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


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
def test_a_commit_in_a_linked_worktree_fails_loudly_and_changes_nothing(linked):
    main, wt, _ = linked
    assert (wt / ".git").is_file(), "fixture must be a linked worktree"
    before = _tree_hash(main / ".git")
    result = bash.run("echo hi > f && git add f && git commit -q -m x", wt)
    assert result["sandbox"] == "on", result
    assert result["ok"] is False, result
    assert "Read-only file system" in result["stderr"], result
    assert "HYDRA_SHELL_SANDBOX=0" in result["stderr"], result
    assert "read-only" in result["stderr"].lower() and "host" in result["stderr"], result
    assert _git(wt, "log", "-1", "--format=%s") == "seed"
    assert _tree_hash(main / ".git") == before, "nothing may land in the repo"


@needs_sandbox
def test_a_plain_failure_gets_no_git_hint(tmp_path):
    result = bash.run("ls /nonexistent-dir-for-hint-test", tmp_path)
    assert result["ok"] is False
    assert "HYDRA_SHELL_SANDBOX=0" not in result["stderr"]


@needs_sandbox
def test_b_sibling_main_tree_and_hooks_writes_still_fail(linked):
    main, wt, sibling = linked
    for target in (sibling / "x", main / "leak", main / ".git" / "hooks" / "leak"):
        result = bash.run(f"timeout 5 touch {target}", wt)
        assert result["ok"] is False, result
        assert "Read-only file system" in result["stderr"]
        assert not target.exists()


def test_c_a_proc_path_is_refused_cleanly_not_raised(tmp_path):
    target = "/proc/1/root/x"
    result = bash.run(f"touch {target}", tmp_path)
    assert result["ok"] is False, result
    assert result["reason"] == "write-outside-workspace-root"
    assert result["exit_code"] == -1, result
    # Same-user containers can resolve /proc/1/root to /. Other hosts keep the
    # original path with a refusal explanation when the link is unreadable.
    assert target in result["blocked_path"] or result["blocked_path"] == os.path.realpath(target), result


# --- D: one regression case per grader probe -------------------------------------

_WRITER = (
    "python3 -c \"import os,shutil,sys\n"
    "for n in ('objects/pwn', 'refs/pwn', 'logs/pwn', 'HEAD', 'commondir', 'config.worktree'):\n"
    "    try: open(os.path.join(sys.argv[1], n), 'w').write('pwn')\n"
    "    except OSError as e: print(e, file=sys.stderr)\n"
    "for n in ('objects', 'refs', 'logs', 'HEAD'):\n"
    "    p = os.path.join(sys.argv[1], n)\n"
    "    try: shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)\n"
    "    except OSError as e: print(e, file=sys.stderr)\n"
    "sys.exit(1)\" {victim}"
)


def _victim_repo(tmp_path):
    """A real repo; its .git dir is the target the forged pointers aim at."""
    repo = tmp_path / "victim_repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "trunk")
    (repo / "seed").write_text("s\n", encoding="utf-8")
    _git(repo, "add", "seed")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo / ".git"


def _bare_dir(tmp_path):
    """An arbitrary directory that merely has objects/refs/logs."""
    arbitrary = tmp_path / "arbitrary"
    for name in ("objects", "refs", "logs"):
        (arbitrary / name).mkdir(parents=True)
        (arbitrary / name / "keep").write_text("keep", encoding="utf-8")
    return arbitrary


def _forge_in_workspace(ws, victim):
    (ws / "fake").mkdir()
    (ws / "fake" / "commondir").write_text(f"{victim}\n", encoding="utf-8")
    (ws / ".git").write_text("gitdir: ./fake\n", encoding="utf-8")


def _forge_in_parent(ws, victim):
    (ws.parent / "fake").mkdir()
    (ws.parent / "fake" / "commondir").write_text(f"{victim}\n", encoding="utf-8")
    (ws.parent / ".git").write_text("gitdir: ./fake\n", encoding="utf-8")


def _forge_commondir_to_real_repo(ws, victim):
    (ws / "gd").mkdir()
    (ws / "gd" / "commondir").write_text(f"{victim}\n", encoding="utf-8")
    (ws / ".git").write_text(f"gitdir: {ws / 'gd'}\n", encoding="utf-8")


def _forge_gitdir_is_victim(ws, victim):
    (ws / ".git").write_text(f"gitdir: {victim}\n", encoding="utf-8")


FORGERIES = [
    pytest.param(_forge_in_workspace, "repo", id="forged_dot_git_file_in_workspace"),
    pytest.param(_forge_in_parent, "repo", id="forged_dot_git_file_in_parent"),
    pytest.param(_forge_commondir_to_real_repo, "repo", id="forged_commondir_to_real_repo"),
    pytest.param(_forge_gitdir_is_victim, "sibling_worktree", id="gitdir_points_at_sibling_worktree"),
    pytest.param(_forge_in_workspace, "arbitrary", id="arbitrary_dir_with_objects_refs_logs"),
    pytest.param(_forge_gitdir_is_victim, "arbitrary", id="gitdir_is_an_arbitrary_dir"),
]


@needs_sandbox
@pytest.mark.parametrize("forge, kind", FORGERIES)
def test_d_a_forged_git_pointer_cannot_make_a_host_dir_writable(tmp_path, forge, kind):
    ws = tmp_path / "parent" / "ws"
    ws.mkdir(parents=True)
    if kind == "repo":
        victim = _victim_repo(tmp_path)
    elif kind == "sibling_worktree":
        main = _victim_repo(tmp_path).parent
        _git(main, "worktree", "add", "-q", "-b", "other", str(tmp_path / "other"))
        victim = main / ".git" / "worktrees" / "other"
    else:
        victim = _bare_dir(tmp_path)
    forge(ws, victim)
    before = _tree_hash(victim)
    result = bash.run(_WRITER.format(victim=victim), ws)
    assert result["ok"] is False, result
    assert "Read-only file system" in result["stderr"], result
    assert _tree_hash(victim) == before, "the target must be byte-unchanged"
