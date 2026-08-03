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


# Exit code conventionally used to signal a command that exceeded its
# wall-clock timeout (matches coreutils `timeout`). `_run_git` translates a
# subprocess.TimeoutExpired into this code rather than letting the raw
# exception propagate, so callers stay inside the dict/SkillError contract.
GIT_TIMEOUT_EXIT_CODE = 124


def _run_git(
    worktree: Path, args: list[str], timeout: float
) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(worktree)] + args
    try:
        return subprocess.run(
            cmd,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # `check=False` only suppresses non-zero exit codes; it does NOT catch
        # the timeout. Preserve any partial output git produced before the kill
        # and return a structured timed-out result so `run()` can raise a clear
        # SkillError instead of leaking a raw TimeoutExpired to the caller.
        partial_stdout = exc.stdout
        if isinstance(partial_stdout, bytes):
            partial_stdout = partial_stdout.decode("utf-8", errors="replace")
        elif partial_stdout is None:
            partial_stdout = ""
        partial_stderr = exc.stderr
        if isinstance(partial_stderr, bytes):
            partial_stderr = partial_stderr.decode("utf-8", errors="replace")
        elif partial_stderr is None:
            partial_stderr = ""
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=GIT_TIMEOUT_EXIT_CODE,
            stdout=partial_stdout,
            stderr=partial_stderr or f"git timed out after {timeout}s",
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
