"""Scope gates for a repair plan written in waves of balls.

  python ball_scope_check.py <ball-id> --plan PLAN.md [--repo .] [--base main] [--repo-key APP]
  python ball_scope_check.py --disjoint --plan PLAN.md

A ball heading is any markdown heading that starts with "Ball <id>", e.g. `#### Ball 1A · title`.

A ball's scope starts at a `**Scope:**` or `**Scope (KEY):**` field. It may wrap: a following
line continues the field only when it starts (after any indentation) with a backticked path,
a comma, or a list bullet followed by a backticked path. Anything else ends it. A line that
ends the field but still carries path-like backticked tokens (prose naming a file) is refused
by line (exit 2), so a path is never silently dropped; blank lines, headings, bold fields and
checklist items end the field quietly.

Every backticked token outside parentheses is a path. Inside parentheses a token counts only
when it looks like a path (it has a `/` or a file extension); other tokens there are symbols
that qualify the path before them: `api/health.py` (`health` only, see `api/util.py`).
A path must be relative and stay inside the repo: absolute paths, drive letters, `..`
segments and tokens with spaces are refused by name (exit 2), never silently dropped.

A path may carry the content hash it had when the ball was cut (the LEAP ball contract):
`api/health.py@sha256:3f9a1c0d...` (8 to 64 hex digits, a prefix of the SHA-256 of the blob
at the base commit, i.e. `git show <base>:<path> | sha256sum`; `@SHA256:` is accepted too).
Ball mode verifies it and reports drift. Hashed paths must be written relative to the repo
root and cannot be globs; any other `@<algo>:` suffix is refused.

Ball mode: compare the scope with what the branch changed (`git diff --name-only <base>...HEAD`
plus staged, unstaged and untracked files). With --repo-key APP, only untagged Scope fields and
fields tagged (APP) count, and a leading "APP/" is stripped from each path.
Exit 0 = in scope and no drift, 1 = out of scope or drift (files named),
2 = unknown ball, no scope, unparseable scope, or git error.

Repo keys, one rule for both modes: an untagged Scope field belongs to every repo, so its
paths can clash with any key; fields tagged with two different keys never clash. Within a
key, a leading `KEY/` is stripped before comparing, so `APP/api/health.py` and (APP)
`api/health.py` are the same file.

Disjoint mode: balls inside one `## Wave` section must own different files. Uses the same
matcher as ball mode, in both directions. Exit 0 = disjoint, 1 = overlap, 2 = unparseable.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import pathlib
import re
import subprocess
import sys

HEADING_RE = re.compile(r"^(#{2,6})\s+(.*\S)\s*$")
BALL_RE = re.compile(r"^Ball\s+([A-Za-z0-9][\w.-]*)")
WAVE_RE = re.compile(r"^Wave\b", re.I)
SCOPE_RE = re.compile(r"\*\*Scope(?:\s*\(([^)]*)\))?:\*\*(.*)")
CONTINUATION_RE = re.compile(r"^\s*(?:`|,|[-*]\s+`)")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]")
FIELD_START_RE = re.compile(r"^\s*(?:[-*]\s+)?\*\*[^*]+:\*\*")
TOKEN_RE = re.compile(r"`([^`]+)`|([()])")
HASH_RE = re.compile(r"^(.*)@([A-Za-z][A-Za-z0-9-]*):([^/@]*)$")
GLOB_CHARS = set("*?[")
PATH_TOKEN_RE = re.compile(r"`([^`]+)`")
HEX_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")
EXT_RE = re.compile(r"\.[A-Za-z0-9]+$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ScopeError(ValueError):
    """A scope token that is not a relative path inside the repo."""


class BallNotFound(LookupError):
    """The plan has no ball with that id."""


def parse_plan(plan: str) -> list[dict]:
    """Return balls in order: {id, wave, level, lines}."""
    balls: list[dict] = []
    wave = ""
    current: dict | None = None
    for line in plan.splitlines():
        h = HEADING_RE.match(line)
        if h:
            level, title = len(h.group(1)), h.group(2)
            b = BALL_RE.match(title)
            if b:
                current = {"id": b.group(1).rstrip(".·"), "wave": wave, "level": level, "lines": []}
                balls.append(current)
                continue
            if WAVE_RE.match(title):
                wave = title
            if current and level <= current["level"]:
                current = None
            continue
        if current is not None:
            current["lines"].append(line)
    return balls


def _scope_fields(lines: list[str]) -> list[tuple[str, str]]:
    """(tag, text) for each Scope field, with its continuation lines joined."""
    fields: list[list[str]] = []
    open_field: list[str] | None = None
    for line in lines:
        s = SCOPE_RE.search(line)
        if s:
            open_field = [(s.group(1) or "").strip(), s.group(2)]
            fields.append(open_field)
            continue
        if open_field is None:
            continue
        if CONTINUATION_RE.match(line) and not CHECKBOX_RE.match(line):
            open_field[1] += " " + line.strip()
            continue
        quiet_end = (not line.strip() or line.lstrip().startswith("#") or CHECKBOX_RE.match(line)
                     or FIELD_START_RE.match(line))
        if not quiet_end and any(_path_like(tok) for tok in PATH_TOKEN_RE.findall(line)):
            raise ScopeError(f"line after a Scope field names a path but is not a scope continuation: "
                             f"{line.strip()!r} (start it with the backticked path, or end the scope "
                             f"with a blank line first)")
        open_field = None
    return [(tag, text) for tag, text in fields]


def _path_like(token: str) -> bool:
    bare = HASH_RE.sub(r"\1", token.strip())
    return "/" in bare or "\\" in bare or bool(EXT_RE.search(bare))


def check_token(token: str) -> tuple[str, str | None]:
    """Validate one scope token; return (path, hash prefix or None)."""
    raw = token.strip().rstrip(",;")
    digest = None
    m = HASH_RE.match(raw)
    if m:
        raw, algo, digest = m.group(1), m.group(2), m.group(3)
        if algo.lower() != "sha256":
            raise ScopeError(f"scope token `{token}`: only @sha256: content hashes are supported")
        if GLOB_CHARS & set(raw):
            raise ScopeError(f"scope token `{token}`: a glob cannot carry a content hash")
        if not HEX_RE.match(digest):
            raise ScopeError(f"scope token `{token}`: a content hash is 8 to 64 hex digits after @sha256:")
        digest = digest.lower()
    if not raw or any(c.isspace() for c in raw):
        raise ScopeError(f"scope token `{token}` is not a path (contains spaces)")
    if raw.startswith(("/", "\\")) or DRIVE_RE.match(raw):
        raise ScopeError(f"scope token `{token}` is absolute; scope paths are relative to the repo")
    if ".." in re.split(r"[\\/]", raw):
        raise ScopeError(f"scope token `{token}` leaves the repo (`..`)")
    return raw.replace("\\", "/"), digest


def scope_entries(lines: list[str], repo_key: str | None = None) -> list[tuple[str, str, str | None]]:
    """(repo key, path, hash prefix or None) for every path in the ball's Scope fields."""
    out: list[tuple[str, str, str | None]] = []
    for tag, text in _scope_fields(lines):
        if repo_key and tag and tag != repo_key:
            continue
        depth = 0
        for m in TOKEN_RE.finditer(text):
            if m.group(2) == "(":
                depth += 1
                continue
            if m.group(2) == ")":
                depth = max(0, depth - 1)
                continue
            token = m.group(1)
            if depth and not _path_like(token):
                continue  # a symbol qualifying the path before it
            path, digest = check_token(token)
            for key in {tag, repo_key} - {None, ""}:
                path = _strip_key(path, key)
            out.append((tag, path, digest))
    return out


def _strip_key(path: str, key: str | None) -> str:
    return path[len(key) + 1:] if key and path.startswith(key + "/") else path


def scope_paths(lines: list[str], repo_key: str | None = None) -> list[str]:
    return [p for _, p, _ in scope_entries(lines, repo_key)]


def scope_hashes(lines: list[str], repo_key: str | None = None) -> dict[str, str]:
    return {p: d for _, p, d in scope_entries(lines, repo_key) if d}


def find_ball(plan: str, ball: str) -> dict:
    for b in parse_plan(plan):
        if b["id"] == ball:
            return b
    raise BallNotFound(f"no ball {ball!r} in plan")


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, check=True)


def changed_files(repo: str, base: str) -> set[str]:
    def names(*args: str) -> set[str]:
        out = _git(repo, *args).stdout.decode("utf-8", "replace")
        return {ln.strip() for ln in out.splitlines() if ln.strip()}
    files = names("diff", "--name-only", f"{base}...HEAD")
    files |= names("diff", "--name-only")
    files |= names("diff", "--name-only", "--cached")
    files |= names("ls-files", "--others", "--exclude-standard")
    return files


def drift(repo: str, base: str, hashes: dict[str, str]) -> list[str]:
    """Hashed scope paths whose blob at the base commit no longer matches the plan."""
    problems = []
    for path, want in sorted(hashes.items()):
        try:
            blob = _git(repo, "show", f"{base}:{path}").stdout
        except subprocess.CalledProcessError:
            problems.append(f"{path}: not found at {base} (plan expects sha256:{want})")
            continue
        got = hashlib.sha256(blob).hexdigest()
        if not got.startswith(want):
            problems.append(f"{path}: sha256 at {base} is {got[:len(want)]}, the ball was cut at {want}")
    return problems


def matches(path: str, entry: str) -> bool:
    """The one matcher: exact, suffix under a prefix dir, or glob (also under a prefix)."""
    return (path == entry or path.endswith("/" + entry)
            or fnmatch.fnmatch(path, entry) or fnmatch.fnmatch(path, "*/" + entry))


def in_scope(path: str, scope: list[str]) -> bool:
    return any(matches(path, s) for s in scope)


def overlaps(plan: str) -> list[str]:
    """Paths two balls of the same wave both claim in the same repo, by the ball-mode matcher."""
    problems = []
    by_wave: dict[str, list[dict]] = {}
    for b in parse_plan(plan):
        by_wave.setdefault(b["wave"], []).append(b)
    for wave, balls in by_wave.items():
        scopes = [(b["id"], scope_entries(b["lines"])) for b in balls]
        for i, (a_id, a_entries) in enumerate(scopes):
            for b_id, b_entries in scopes[i + 1:]:
                for ka, pa, _ in a_entries:
                    for kb, pb, _ in b_entries:
                        if ka and kb and ka != kb:
                            continue  # two different repos
                        key = ka or kb
                        na, nb = _strip_key(pa, key), _strip_key(pb, key)
                        if matches(na, nb) or matches(nb, na):
                            where = f" ({key})" if key else ""
                            problems.append(f"{wave or '(no wave)'}: balls {a_id} and {b_id} both own {pa}{where}"
                                            + ("" if pa == pb else f" ~ {pb}"))
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ball", nargs="?")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--base", default="main")
    ap.add_argument("--repo-key", help="plan repo key for this checkout, e.g. APP")
    ap.add_argument("--disjoint", action="store_true", help="check that balls in a wave share no file")
    a = ap.parse_args(argv)
    plan = pathlib.Path(a.plan).read_text(encoding="utf-8")

    try:
        if a.disjoint:
            problems = overlaps(plan)
            for p in problems:
                print("OVERLAP", p)
            print(f"{len(parse_plan(plan))} balls, {len(problems)} overlaps")
            return 1 if problems else 0
        if not a.ball:
            ap.error("give a ball id, or --disjoint")
        lines = find_ball(plan, a.ball)["lines"]
        scope = scope_paths(lines, a.repo_key)
        hashes = scope_hashes(lines, a.repo_key)
    except (ScopeError, BallNotFound) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not scope:
        print(f"ball {a.ball} declares no Scope (coordinator ball); nothing to check")
        return 2
    try:
        changed = changed_files(a.repo, a.base)
        drifted = drift(a.repo, a.base, hashes)
    except (subprocess.CalledProcessError, OSError) as exc:
        raw = getattr(exc, "stderr", b"") or str(exc)
        detail = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw).strip().splitlines()
        print(f"error: git could not list changes against {a.base!r} in {a.repo}: "
              f"{detail[0] if detail else exc}", file=sys.stderr)
        return 2
    out = sorted(f for f in changed if not in_scope(f, scope))
    for f in out:
        print("OUT OF SCOPE", f)
    for d in drifted:
        print("DRIFT", d)
    print(f"ball {a.ball}: {len(scope)} scope paths, {len(out)} out-of-scope files, "
          f"{len(hashes)} hashed, {len(drifted)} drifted")
    return 1 if out or drifted else 0


if __name__ == "__main__":
    sys.exit(main())
