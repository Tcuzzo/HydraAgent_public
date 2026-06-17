"""skills.git_diff — shell out to `git diff --stat` inside a worktree.

Constraints:
  - `worktree` must be a directory that is the root of a git work tree
    (we ask git, we don't guess).
  - Returns the stat output as a string, plus the number of files reported
    as changed.
  - Hard timeout (default 15s).

Returns a dict, raises `SkillError` on refusal or git error. No fallbacks
to alternative diff tools — if git is unavailable, the caller must know.

Maturity: SCAFFOLDED. Promoted by §10.6-git-diff eval.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class SkillError(Exception):
    """A skill refused the request or could not complete it."""


DEFAULT_TIMEOUT_SECONDS = 15


def _run_git(
    worktree: Path, args: list[str], timeout: float
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(worktree)] + args
    return subprocess.run(
        cmd,
        timeout=timeout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def run(
    worktree: str | Path,
    ref: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    if shutil.which("git") is None:
        raise SkillError("git binary not on PATH")
    worktree = Path(worktree).resolve()
    if not worktree.is_dir():
        raise SkillError(f"worktree is not a directory: {worktree}")
    check = _run_git(worktree, ["rev-parse", "--is-inside-work-tree"], timeout)
    if check.returncode != 0 or check.stdout.strip() != "true":
        raise SkillError(
            f"{worktree} is not a git worktree: {check.stderr.strip()}"
        )

    args = ["diff", "--stat"]
    if ref is not None:
        args.append(ref)
    proc = _run_git(worktree, args, timeout)
    if proc.returncode != 0:
        raise SkillError(
            f"git diff failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )

    stat = proc.stdout
    # Count file rows. `git diff --stat` ends with a summary like
    # ` 3 files changed, 12 insertions(+), 4 deletions(-)`. Files-changed
    # lines look like ` path/to/file | N ++--`. We count lines that contain
    # a vertical bar; the summary line never does.
    files_changed = sum(1 for line in stat.splitlines() if "|" in line)

    return {
        "ok": True,
        "worktree": str(worktree),
        "ref": ref,
        "files_changed": files_changed,
        "stat": stat,
    }
