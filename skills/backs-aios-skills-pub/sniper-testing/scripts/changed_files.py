#!/usr/bin/env python3
"""Read the complete candidate scope, including committed changes, as JSON."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "git could not read the requested scope")
    return result.stdout


def revision(repo, ref):
    try:
        return git(repo, "rev-parse", "--verify", "--end-of-options",
                   ref + "^{commit}").decode("ascii").strip()
    except ValueError as error:
        raise ValueError(f"invalid or unavailable revision {ref!r}: {error}") from error


def paths(repo, *args):
    return sorted({os.fsdecode(name) for name in git(repo, *args).split(b"\0") if name})


def scope(repo, base, candidate, include_worktree=False):
    base_sha = revision(repo, base)
    candidate_sha = revision(repo, candidate)
    changes = {
        "committed": paths(repo, "diff", "--name-only", "--no-renames", "-z",
                           base_sha, candidate_sha, "--"),
        "staged": [], "unstaged": [], "untracked": [],
    }
    if include_worktree:
        if candidate_sha != revision(repo, "HEAD"):
            raise ValueError("candidate must match HEAD when including the worktree")
        changes["staged"] = paths(repo, "diff", "--cached", "--name-only",
                                  "--no-renames", "-z", candidate_sha, "--")
        changes["unstaged"] = paths(repo, "diff", "--name-only", "--no-renames", "-z", "--")
        changes["untracked"] = paths(repo, "ls-files", "--others", "--exclude-standard", "-z")
    return {
        "base_sha": base_sha, "candidate_sha": candidate_sha,
        "files": sorted({name for group in changes.values() for name in group}),
        "changes": changes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--base", required=True, help="verified pre-task commit")
    parser.add_argument("--candidate", default="HEAD")
    parser.add_argument("--include-worktree", action="store_true")
    args = parser.parse_args()
    try:
        result = scope(args.repo, args.base, args.candidate, args.include_worktree)
    except (ValueError, OSError) as error:
        print(f"changed_files: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
