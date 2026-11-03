"""Resolve a build target against the requested checkout without editing it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def git(repo: Path, *args: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def resolve(repo_arg: str, target_arg: str, expected_arg: str | None,
            allow_non_git: bool = False) -> tuple[dict, int]:
    repo = Path(repo_arg).expanduser().resolve()
    target = Path(target_arg).expanduser()
    target = (repo / target).resolve() if not target.is_absolute() else target.resolve()
    root_text = git(repo, "rev-parse", "--show-toplevel") if repo.is_dir() else None
    root = Path(root_text).resolve() if root_text else repo
    expected = Path(expected_arg).expanduser().resolve() if expected_arg else None
    errors = []
    if not repo.is_dir():
        errors.append("repository directory does not exist")
    if root_text is None and not allow_non_git:
        errors.append("Git revision unavailable; pass --allow-non-git for a named non-Git project")
    if expected is not None and root != expected:
        errors.append("resolved checkout differs from expected root")
    if not target.is_relative_to(root):
        errors.append("target resolves outside the checkout")
    if not target.exists() and not target.parent.is_dir():
        errors.append("target parent does not exist")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=normal") if root.is_dir() else None
    result = {
        "ok": not errors,
        "root": str(root),
        "target": str(target),
        "git": root_text is not None,
        "head": git(root, "rev-parse", "HEAD") if root_text else None,
        "branch": git(root, "branch", "--show-current") if root_text else None,
        "dirty_paths": len(status.splitlines()) if status else 0,
        "errors": errors,
    }
    return result, 0 if not errors else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--expected-root")
    parser.add_argument("--allow-non-git", action="store_true")
    args = parser.parse_args()
    result, code = resolve(args.repo, args.target, args.expected_root, args.allow_non_git)
    print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
