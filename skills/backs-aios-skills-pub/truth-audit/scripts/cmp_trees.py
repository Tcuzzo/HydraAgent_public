"""Compare two directory trees by content, not by names or commit ids.

  python cmp_trees.py <dir-A> <dir-B> [--labels running github] [--skip build --skip dist] [--limit 45] [--json]

Each file is hashed with SHA-256. Text files are CRLF -> LF normalized first, so a Windows
checkout and a POSIX checkout of the same source compare equal; a file with a NUL byte is
binary and is hashed exactly as stored. Caches and VCS folders are skipped, and the skip
list is printed so the reader knows exactly what was not compared. Symlinks (to files or
directories) are never followed: each is compared by its target text and listed.

To compare a remote ref, export it to a scratch directory first, for example
  git -C <repo> worktree add --detach <scratch>/ref <ref>      or
  git -C <repo> archive <ref> | tar -x -C <scratch>/ref

Exit 0 = identical, 1 = differ, 2 = error (a directory is missing or anything under it is
unreadable, or bad arguments). A failed probe never reads as clean.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

DEFAULT_SKIP = {"__pycache__", ".git", ".hg", ".svn", ".venv", "venv", "node_modules",
                ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox"}
SKIP_SUFFIXES = (".pyc", ".pyo")


class WalkError(OSError):
    """Something under a tree could not be read; the comparison is not trustworthy."""


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def file_digest(path: str) -> str:
    with open(path, "rb") as fh:
        data = fh.read()
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def walk(root: str, skip: set[str]) -> dict[str, str]:
    """Map relative path -> sha256 (or 'link:'/'dirlink:' + target). Raises WalkError on any read failure."""
    out: dict[str, str] = {}
    errors: list[str] = []

    def onerror(exc: OSError) -> None:
        errors.append(f"{getattr(exc, 'filename', None) or root}: {exc.strerror or exc}")

    for d, dirs, files in os.walk(root, onerror=onerror):
        kept = []
        for x in sorted(dirs):
            if x in skip:
                continue
            p = os.path.join(d, x)
            if os.path.islink(p):
                # os.walk does not descend into a directory symlink; say so instead of hiding it.
                out[_rel(p, root) + "/"] = "dirlink:" + os.readlink(p)
                continue
            kept.append(x)
        dirs[:] = kept
        for f in files:
            if f.endswith(SKIP_SUFFIXES):
                continue
            p = os.path.join(d, f)
            if os.path.islink(p):
                out[_rel(p, root)] = "link:" + os.readlink(p)
                continue
            try:
                out[_rel(p, root)] = file_digest(p)
            except OSError as exc:
                onerror(exc)
    if errors:
        raise WalkError("; ".join(errors))
    return out


def compare(a: dict[str, str], b: dict[str, str]) -> dict[str, list[str]]:
    return {
        "only_a": sorted(set(a) - set(b)),
        "only_b": sorted(set(b) - set(a)),
        "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--labels", nargs=2, default=("A", "B"))
    ap.add_argument("--skip", action="append", default=[], help="extra directory name to skip (repeatable)")
    ap.add_argument("--limit", type=int, default=45, help="max paths printed per list")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    for d in (args.a, args.b):
        if not os.path.isdir(d):
            print(f"error: not a directory: {d}", file=sys.stderr)
            return 2
    skip = DEFAULT_SKIP | set(args.skip)
    try:
        a, b = walk(args.a, skip), walk(args.b, skip)
    except OSError as exc:
        # A probe that could not read is an error, never "differ" and never "same".
        print(f"error reading tree: {exc}", file=sys.stderr)
        return 2
    res = compare(a, b)
    links = sorted(f"{label}:{k} -> {v.split(':', 1)[1]}" for label, tree in ((args.labels[0], a), (args.labels[1], b))
                   for k, v in tree.items() if v.startswith(("link:", "dirlink:")))
    la, lb = args.labels
    if args.json:
        print(json.dumps({"labels": [la, lb], "files": [len(a), len(b)], "skipped_dirs": sorted(skip),
                          "symlinks_not_followed": links, **res}, indent=2))
    else:
        print(f"{la} files {len(a)}  {lb} files {len(b)}  only-{la} {len(res['only_a'])}  "
              f"only-{lb} {len(res['only_b'])}  changed {len(res['changed'])}")
        print("skipped dirs:", ", ".join(sorted(skip)), "| text CRLF normalized, binary exact | sha256")
        for name, lst in ((f"ONLY IN {la}", res["only_a"]), (f"ONLY IN {lb}", res["only_b"]),
                          ("CHANGED", res["changed"]), ("SYMLINKS (not followed)", links)):
            print("==", name)
            for x in lst[:args.limit]:
                print("  ", x)
            if len(lst) > args.limit:
                print("   ...", len(lst) - args.limit, "more")
    return 0 if not any(res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
