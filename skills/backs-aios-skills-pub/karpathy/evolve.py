"""Auto-Research evolution harness — the WOLF PACK's reusable propose->test->keep/revert loop.

A single-file, stdlib-only, model-agnostic harness. Given a target git repo, a
fitness ``Metric`` (lexicographic, with a hard gate), an injected ``propose``
callable (the Proposer) and an injected ``test`` callable (the blind Tester), it:

  1. Snapshots the baseline (the *champion*) fitness via ``test``.
  2. For each round: ``propose`` mutates a prepared disposable clone; ``test``
     scores that candidate **blindly** (it never receives the proposer's
     rationale); an optional anti-bloat ``penalty`` is checked.
  3. KEEPS (git add + commit) the candidate **only** if it clears the hard gate
     AND strictly dominates the champion lexicographically with no regression on
     any higher-or-equal-priority field AND the penalty is clean.
  4. Otherwise REVERTS the clone's index, tracked files, and generated files.

Operator laws honored (CLAUDE.md + memory):
  * experiments mutate only a prepared disposable clone below a declared temp root.
  * mutation MUST PROVE UPLIFT, never revert/regress verified work  -> we only
    commit a candidate that strictly dominates the champion; a non-dominating or
    gate-failing candidate is discarded, never committed. The champion (verified
    baseline) is never overwritten by something worse.
  * never grade your own work  -> the Tester is injected and blind (no rationale).
  * no fake-green  -> keep/revert is enforced against real git state.
  * the lexicographic metric CANNOT be gamed  -> the hard gate is checked FIRST
    and a higher-priority field's regression is dispositive; cost/speed cannot buy
    a gate failure, and a lower-priority improvement cannot rescue a
    higher-priority regression.

ROLES (model-agnostic — any model/agent can be wired via the injected callables):
  * Proposer  = a (typically free, local) model that drafts a candidate diff.
  * Tester    = a blind, fresh-context Auditor that returns a fitness vector and
                NEVER sees the proposer's rationale (no self-grading).
  * Reconciler = the keep/revert decision implemented here (auto-commit on
                proven uplift + no regression, else discard).

Dependencies: stdlib only (subprocess for git, json, dataclasses, pathlib,
shutil, tempfile).
"""

from __future__ import annotations

import json
import hashlib
import math
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence


class AutoResearchError(Exception):
    """Raised on sandbox-guard violations or unrecoverable harness errors."""


def _finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


# --------------------------------------------------------------------------- #
# Metric
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MetricField:
    """One axis of the fitness vector.

    Args:
        name: key into the fitness dict returned by ``test``.
        direction: "max" (bigger is better) or "min" (smaller is better).
        hard_gate: optional threshold. If set, a candidate that fails the gate is
            rejected outright BEFORE any lexicographic comparison. For "max" the
            gate means value >= gate; for "min" it means value <= gate.
    """

    name: str
    direction: str
    hard_gate: Optional[float] = None

    def __post_init__(self) -> None:
        if self.direction not in ("max", "min"):
            raise AutoResearchError(
                f"MetricField {self.name!r}: direction must be 'max' or 'min', "
                f"got {self.direction!r}"
            )
        if not self.name.strip():
            raise AutoResearchError("metric name must not be empty")
        if self.hard_gate is not None and not _finite_number(self.hard_gate):
            raise AutoResearchError(f"MetricField {self.name!r}: gate must be finite")

    def meets_gate(self, value: float) -> bool:
        if self.hard_gate is None:
            return True
        if self.direction == "max":
            return value >= self.hard_gate
        return value <= self.hard_gate

    def better(self, candidate: float, champion: float) -> int:
        """Return +1 if candidate is strictly better, -1 if worse, 0 if equal."""
        if candidate == champion:
            return 0
        if self.direction == "max":
            return 1 if candidate > champion else -1
        return 1 if candidate < champion else -1


class Metric:
    """A lexicographic, gate-aware fitness comparator.

    The decision rule (precise):
      1. GATE  — every field that declares a ``hard_gate`` must be met by the
         candidate, else the candidate FAILS immediately. The gate is checked
         FIRST and cannot be bought with improvements on other (cost/latency)
         fields. (A cheap, fast candidate that misses a success-rate gate still
         loses.) A gated field is a *threshold to satisfy*, NOT a value to push
         past — once it clears the gate it does NOT count as a lexicographic
         optimization axis (so e.g. success 0.95 and 0.96 are EQUIVALENT for
         ranking; you can't "buy" a cost regression by nudging success up).
      2. LEXICOGRAPHIC — walk the NON-gated fields in declared (priority) order.
         At the first such field where candidate and champion differ, that field
         decides: if the candidate is better there, it WINS; if worse, it LOSES
         — even if every lower-priority field improves. Ties on a field fall
         through to the next.
      3. STRICT DOMINANCE — to win, the candidate must be strictly better on at
         least one optimization field with NO regression on any
         higher-or-equal-priority optimization field. A full tie does NOT win
         (mutation must prove uplift, not just match the verified champion).
    """

    def __init__(self, fields: Sequence[MetricField]):
        if not fields:
            raise AutoResearchError("Metric requires at least one MetricField")
        self.fields = list(fields)

    def _validate_vector(self, vec: Mapping[str, float]) -> dict[str, float]:
        validated: dict[str, float] = {}
        for field in self.fields:
            if field.name not in vec:
                raise AutoResearchError(
                    f"fitness vector missing field {field.name!r}: keys={list(vec)}"
                )
            value = vec[field.name]
            if not _finite_number(value):
                raise AutoResearchError(f"fitness field {field.name!r} must be a finite number")
            validated[field.name] = value
        return validated

    # -- gate ------------------------------------------------------------- #
    def gate_failures(self, vec: Mapping[str, float]) -> list[str]:
        """Names of gated fields the vector fails (empty list = all gates pass)."""
        vec = self._validate_vector(vec)
        failed = []
        for f in self.fields:
            if not f.meets_gate(vec[f.name]):
                failed.append(f.name)
        return failed

    def passes_gate(self, vec: Mapping[str, float]) -> bool:
        return not self.gate_failures(vec)

    # -- comparison ------------------------------------------------------- #
    def dominates(
        self, candidate: Mapping[str, float], champion: Mapping[str, float]
    ) -> bool:
        """True iff ``candidate`` should replace ``champion`` (see class docstring)."""
        # (1) gate first — non-negotiable, can't be bought with cost/speed.
        if not self.passes_gate(candidate):
            return False
        candidate = self._validate_vector(candidate)
        champion = self._validate_vector(champion)
        # When a program has only gated fields, they must also rank candidates;
        # otherwise a valid one-metric experiment can never keep an uplift.
        ranking_fields = [f for f in self.fields if f.hard_gate is None]
        if not ranking_fields:
            ranking_fields = self.fields
        for f in ranking_fields:
            cmp = f.better(candidate[f.name], champion[f.name])
            if cmp > 0:
                return True  # strictly better here, all higher were ties -> win
            if cmp < 0:
                return False  # regression on a higher-or-equal axis -> lose
            # cmp == 0: tie, continue to next priority field
        return False  # full tie -> not strictly better -> not kept

    def vec(self, raw: Mapping[str, float]) -> dict:
        """Ordered, validated copy of a fitness mapping (for logging)."""
        return self._validate_vector(raw)


# --------------------------------------------------------------------------- #
# git helpers
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=check,
    )


def _is_git_repo(path: Path) -> bool:
    r = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"


def _head_identity(repo: Path) -> tuple[str, str | None]:
    oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
    symbolic = _git(repo, "symbolic-ref", "-q", "HEAD", check=False)
    return oid, symbolic.stdout.strip() if symbolic.returncode == 0 else None


def _git_control_signature(repo: Path) -> dict[str, str]:
    """Watch metadata that may redirect the engine's Git behavior."""
    git_dir = repo / ".git"
    paths = [git_dir / name for name in
             ("config", "info/exclude", "info/attributes", "info/karpathy-owned")]
    hooks = git_dir / "hooks"
    if hooks.exists():
        paths.extend(path for path in hooks.rglob("*") if path.is_file() or path.is_symlink())
    signature: dict[str, str] = {}
    for path in paths:
        if path.is_symlink():
            raise AutoResearchError("Git control metadata contains a symlink")
        if path.is_file():
            signature[path.relative_to(git_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return signature


def _assert_git_control(repo: Path, expected: dict[str, str], phase: str) -> None:
    if _git_control_signature(repo) != expected:
        raise AutoResearchError(f"{phase} changed Git control metadata outside Fair game")


def _assert_head(repo: Path, expected: tuple[str, str | None], phase: str) -> None:
    if _head_identity(repo) != expected:
        raise AutoResearchError(f"{phase} changed Git HEAD or branch outside Fair game")


def _restore_baseline(repo: Path, expected_head: str) -> None:
    """Reset the disposable clone's index and worktree to its last champion."""
    if not _under_sandbox(repo):
        raise AutoResearchError("rollback refused outside a prepared disposable clone")
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != repo.resolve():
        raise AutoResearchError("rollback refused below the Git top-level")
    _git(repo, "reset", "--hard", expected_head)
    _git(repo, "clean", "-ffdxq")
    _git(repo, "checkout", "--detach", "-q", "-f", expected_head)
    if _changed_paths(repo):
        raise AutoResearchError("rollback incomplete; disposable clone remains dirty")


def cleanup_sandbox(path: str | Path) -> None:
    """Delete only a prepared experiment clone and its dedicated temp parent."""
    repo = Path(path).resolve()
    if not _under_sandbox(repo) or repo.name != "repo":
        raise AutoResearchError("cleanup refused outside a prepared disposable clone")
    parent = repo.parent
    if list(parent.iterdir()) != [repo] and set(parent.iterdir()) != {repo, parent / "evolution_log.jsonl"}:
        raise AutoResearchError("cleanup refused: experiment parent contains unexpected files")

    def _make_writable(func, name, _error):
        os.chmod(name, stat.S_IREAD | stat.S_IWRITE)
        func(name)

    shutil.rmtree(parent, onerror=_make_writable)


def _status_entries(repo: Path) -> list[tuple[str, str]]:
    """Include tracked, untracked and ignored mutations; reject rename syntax."""
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored").stdout
    entries = [entry for entry in raw.split("\0") if entry]
    paths: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        status, path = entry[:2], entry[3:]
        if "R" in status or "C" in status:
            raise AutoResearchError("renames/copies are outside this experiment's file contract")
        paths.append((status, path.replace("\\", "/")))
        index += 1
    return paths


def _changed_paths(repo: Path) -> list[str]:
    return [path for _, path in _status_entries(repo)]


def _discard_generated_scratch(repo: Path, candidate_paths: Sequence[str]) -> list[str]:
    """Remove only new scorer files; tracked edits remain visible and fail checks."""
    if not _under_sandbox(repo):
        raise AutoResearchError("scorer cleanup refused outside a prepared clone")
    candidate = set(candidate_paths)
    scratch_entries = [(path, (repo / path).is_dir()) for status, path in _status_entries(repo)
                       if path not in candidate and status in ("??", "!!")]
    scratch = [path for path, _ in scratch_entries]
    if scratch:
        _git(repo, "clean", "-ffdxq", "--", *scratch)
        for path, was_dir in scratch_entries:
            cursor = (repo / path) if was_dir else (repo / path).parent
            while cursor != repo and cursor.is_relative_to(repo):
                try:
                    cursor.rmdir()
                except OSError:
                    break
                cursor = cursor.parent
    return scratch


def _allowed_paths(repo: Path, patterns: Sequence[str]) -> set[str]:
    allowed: set[str] = set()
    for pattern in patterns:
        p = Path(pattern)
        if p.is_absolute() or ".." in p.parts or not pattern.strip() or pattern.startswith(".git"):
            raise AutoResearchError(f"unsafe fair-game pattern: {pattern!r}")
        for path in repo.glob(pattern):
            rel = path.relative_to(repo)
            if rel.parts and rel.parts[0] == ".git":
                raise AutoResearchError(f"fair-game pattern includes Git metadata: {pattern!r}")
            if path.is_symlink():
                raise AutoResearchError(f"fair-game pattern includes a symlink: {pattern!r}")
            if path.is_file():
                allowed.add(rel.as_posix())
        if not any(char in pattern for char in "*?["):
            allowed.add(p.as_posix())  # exact path may be newly created or deleted
    return allowed


def _candidate_signature(repo: Path, paths: Sequence[str]) -> dict[str, tuple[str | None, str, str]]:
    signature: dict[str, tuple[str | None, str, str]] = {}
    for rel in paths:
        raw_path = repo / rel
        path = raw_path.resolve()
        if not path.is_relative_to(repo) or raw_path.is_symlink():
            raise AutoResearchError(f"candidate path escapes disposable clone: {rel}")
        contents = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        index_entry = _git(repo, "ls-files", "--stage", "--", rel).stdout
        index_flags = _git(repo, "ls-files", "-v", "--", rel).stdout
        signature[rel] = (contents, index_entry, index_flags)
    return signature


def prepare_sandbox(source_dir: str | Path, sandbox_root: str | Path | None = None) -> Path:
    """Clone committed source into a dedicated temporary checkout for experiments."""
    source = Path(source_dir).resolve()
    top = _git(source, "rev-parse", "--show-toplevel", check=False)
    if top.returncode or Path(top.stdout.strip()).resolve() != source:
        raise AutoResearchError("source must be the exact Git top-level directory")
    if _git(source, "status", "--porcelain", "--untracked-files=all").stdout.strip():
        raise AutoResearchError("source has uncommitted changes; snapshot or commit first")
    root = Path(sandbox_root or os.getenv("KARPATHY_SANDBOX_ROOT") or tempfile.gettempdir()).resolve()
    if root.is_relative_to(source):
        raise AutoResearchError("sandbox root must be outside the source checkout")
    root.mkdir(parents=True, exist_ok=True)
    parent = Path(tempfile.mkdtemp(prefix="karpathy-", dir=root))
    repo = parent / "repo"
    bundle = parent / "snapshot.bundle"
    try:
        snapshot = subprocess.run(
            ["git", "-C", str(source), "bundle", "create", str(bundle), "HEAD"],
            capture_output=True, text=True,
        )
        if snapshot.returncode:
            raise AutoResearchError(f"failed to snapshot source commit: {snapshot.stderr.strip()}")
        clone = subprocess.run(
            ["git", "clone", "--quiet", str(bundle), str(repo)],
            capture_output=True, text=True,
        )
        if clone.returncode:
            raise AutoResearchError(f"failed to prepare disposable clone: {clone.stderr.strip()}")
        bundle.unlink()
        _git(repo, "remote", "remove", "origin")
        _git(repo, "checkout", "--detach", "-q", "HEAD")
        for key in ("user.name", "user.email"):
            value = _git(source, "config", "--get", key, check=False)
            if value.returncode == 0 and value.stdout.strip():
                _git(repo, "config", key, value.stdout.strip())
        (repo / ".git" / "info" / "karpathy-owned").write_text(
            json.dumps({"source": str(source), "root": str(root)}), encoding="utf-8"
        )
    except Exception:
        if parent.resolve().parent == root and parent.name.startswith("karpathy-"):
            def _make_writable(func, name, _error):
                os.chmod(name, stat.S_IREAD | stat.S_IWRITE)
                func(name)
            shutil.rmtree(parent, onerror=_make_writable)
        raise
    return repo


# --------------------------------------------------------------------------- #
# sandbox guard
# --------------------------------------------------------------------------- #
def _under_sandbox(path: Path) -> bool:
    """Only a marked clone below its declared temporary root may be mutated."""
    resolved = path.resolve()
    try:
        marker = resolved / ".git" / "info" / "karpathy-owned"
        owner = json.loads(marker.read_text(encoding="utf-8"))
        root = Path(owner["root"]).resolve()
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (resolved.name == "repo" and resolved.parent.name.startswith("karpathy-") and
            resolved.parent.parent == root)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def run(
    *,
    target_dir,
    eval_cmd: Optional[Sequence[str]] = None,
    metric: Metric,
    propose: Callable[[Path], object],
    test: Callable[[Path], Mapping[str, float]],
    penalty: Optional[Callable[[Path], float]] = None,
    max_rounds: int = 1,
    sandbox: bool = True,
    logger_path=None,
    fair_game: Optional[Sequence[str]] = None,
) -> dict:
    """Run the propose->test->keep/revert evolution loop on ``target_dir``.

    Args:
        target_dir: a prepared disposable clone from ``prepare_sandbox``.
        eval_cmd: optional command run before ``test`` each round (e.g. the
            project's test/eval suite). Its output is not parsed here; ``test``
            owns turning state into a fitness vector. A non-zero exit causes the
            round to revert (the candidate is treated as failing the gate).
        metric: the lexicographic ``Metric`` comparator.
        propose: Proposer callable; mutates the working tree in place to draft a
            candidate. Model-agnostic (wire any local/cloud model behind it).
        test: blind Tester callable; returns the fitness vector for the current
            tree. Receives only ``target_dir`` — never the proposer's rationale.
        penalty: optional anti-bloat hook; returns >0 to FAIL an otherwise-winning
            candidate (so the loop can't bloat LOC/deps to a better score).
        max_rounds: number of propose->test rounds.
        sandbox: must be True; only a clone made by prepare_sandbox is accepted.
        logger_path: JSONL log destination; defaults to
        ``<target_dir>/../evolution_log.jsonl`` (outside the clone).

    Returns:
        {"rounds": n, "kept": k, "final_champion": vec, "log": str(path),
         "round_results": [...], "errors": [...]}
    """
    repo = Path(target_dir).resolve()

    # -- sandbox guard (operator law: tests/mutation always in a sandbox) -- #
    if not sandbox or not _under_sandbox(repo):
        raise AutoResearchError(
            "refusing to mutate a path that is not a prepared disposable clone; "
            "call prepare_sandbox(source_dir) first"
        )
    if not repo.exists():
        raise AutoResearchError(f"target_dir does not exist: {repo}")
    if not _is_git_repo(repo):
        raise AutoResearchError(
            f"target_dir is not a git repo (revert relies on git): {repo}"
        )
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if Path(top).resolve() != repo:
        raise AutoResearchError("target_dir must be the exact Git top-level directory")
    if not fair_game:
        raise AutoResearchError("fair_game must list the only files the proposer may edit")
    _allowed_paths(repo, fair_game)  # validate patterns before any mutation
    if max_rounds < 1:
        raise AutoResearchError("max_rounds must be at least one")
    # working tree must start clean so revert is well-defined.
    dirty = _changed_paths(repo)
    if dirty:
        raise AutoResearchError(
            f"target_dir has uncommitted changes; commit/stash first:\n{dirty}"
        )

    log_path = Path(logger_path).resolve() if logger_path else repo.parent / "evolution_log.jsonl"
    if not log_path.is_relative_to(repo.parent) or log_path.is_relative_to(repo):
        raise AutoResearchError("log must be outside the clone inside its disposable parent")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    def _log(entry: dict) -> None:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    # -- baseline (champion) ---------------------------------------------- #
    champion_identity = _head_identity(repo)
    control_signature = _git_control_signature(repo)
    champion = metric.vec(test(repo))
    _assert_head(repo, champion_identity, "baseline tester")
    _assert_git_control(repo, control_signature, "baseline tester")
    if _changed_paths(repo):
        _restore_baseline(repo, champion_identity[0])
        raise AutoResearchError("baseline tester changed the disposable checkout")
    if not metric.passes_gate(champion):
        # The verified baseline itself fails its gate; we still evolve toward it,
        # but record that the starting champion is below gate.
        pass

    kept = 0
    round_results: list[dict] = []
    for rnd in range(1, max_rounds + 1):
        champion_identity = _head_identity(repo)
        reason = ""
        action = "reverted"
        candidate_vec: Optional[dict] = None
        scratch_removed: list[str] = []
        try:
            propose(repo)
            _assert_head(repo, champion_identity, "proposer")
            _assert_git_control(repo, control_signature, "proposer")
            changed = _changed_paths(repo)
            allowed = _allowed_paths(repo, fair_game)
            unexpected = sorted(set(changed) - allowed)
            if unexpected:
                raise AutoResearchError(f"proposer changed files outside Fair game: {unexpected}")
            candidate_signature = _candidate_signature(repo, changed)
            candidate_paths = list(changed)

            gate_blocked = False
            if eval_cmd is not None:
                proc = subprocess.run(
                    list(eval_cmd),
                    cwd=str(repo),
                    capture_output=True,
                    text=True,
                )
                if proc.returncode != 0:
                    gate_blocked = True
                    reason = f"eval_cmd failed (exit {proc.returncode})"
                _assert_head(repo, champion_identity, "evaluation")
                _assert_git_control(repo, control_signature, "evaluation")
                scratch_removed.extend(_discard_generated_scratch(repo, candidate_paths))
                changed = _changed_paths(repo)
                unexpected = sorted(set(changed) - _allowed_paths(repo, fair_game))
                if unexpected:
                    raise AutoResearchError(f"evaluation changed files outside Fair game: {unexpected}")
                if _candidate_signature(repo, changed) != candidate_signature:
                    raise AutoResearchError("evaluation modified candidate files")

            if not gate_blocked:
                candidate_vec = metric.vec(test(repo))
                _assert_head(repo, champion_identity, "tester")
                _assert_git_control(repo, control_signature, "tester")
                scratch_removed.extend(_discard_generated_scratch(repo, candidate_paths))
                changed = _changed_paths(repo)
                unexpected = sorted(set(changed) - _allowed_paths(repo, fair_game))
                if unexpected:
                    raise AutoResearchError(f"tester changed files outside Fair game: {unexpected}")
                if _candidate_signature(repo, changed) != candidate_signature:
                    raise AutoResearchError("tester modified candidate files")

                penalty_value = penalty(repo) if penalty is not None else 0
                _assert_head(repo, champion_identity, "penalty hook")
                _assert_git_control(repo, control_signature, "penalty hook")
                scratch_removed.extend(_discard_generated_scratch(repo, candidate_paths))
                changed = _changed_paths(repo)
                unexpected = sorted(set(changed) - _allowed_paths(repo, fair_game))
                if unexpected or _candidate_signature(repo, changed) != candidate_signature:
                    raise AutoResearchError("penalty hook modified candidate files or Fair game")

                gate_fail = metric.gate_failures(candidate_vec)
                if gate_fail:
                    reason = f"gate failed: {', '.join(gate_fail)}"
                elif penalty_value > 0:
                    reason = f"anti-bloat penalty ({penalty_value}) > 0"
                elif metric.dominates(candidate_vec, champion):
                    # KEEP — proven uplift, no regression: commit it.
                    if not changed:
                        raise AutoResearchError("score improved without a candidate file change")
                    _git(repo, "add", "-A", "--", *changed)
                    _git(
                        repo,
                        "commit",
                        "-q",
                        "-m",
                        f"auto-research: kept round {rnd} "
                        f"({json.dumps(candidate_vec)})",
                    )
                    if _git(repo, "rev-parse", "HEAD^").stdout.strip() != champion_identity[0]:
                        raise AutoResearchError("engine commit has an unexpected parent")
                    committed = set(filter(None, _git(
                        repo, "diff", "--name-only", "-z", champion_identity[0], "HEAD"
                    ).stdout.split("\0")))
                    if committed != set(changed):
                        raise AutoResearchError(
                            f"engine commit changed files outside candidate: {sorted(committed ^ set(changed))}"
                        )
                    _assert_git_control(repo, control_signature, "engine commit")
                    if _changed_paths(repo):
                        raise AutoResearchError("engine commit left a dirty disposable clone")
                    champion = candidate_vec
                    kept += 1
                    action = "kept"
                    reason = "dominates champion (gate ok, no regression)"
                else:
                    reason = "does not dominate champion (no proven uplift)"
        except Exception as exc:  # proposer/tester/penalty blew up -> safe revert
            _assert_git_control(repo, control_signature, "experiment")
            reason = f"error: {exc!r}"
            action = "reverted"

        if action != "kept":
            _restore_baseline(repo, champion_identity[0])

        round_result = {
            "round": rnd,
            "action": action,
            "reason": reason,
            "champion_vec": champion,
            "candidate_vec": candidate_vec,
            "generated_scratch_removed": scratch_removed,
        }
        round_results.append(round_result)
        _log(round_result)

    return {
        "rounds": max_rounds,
        "kept": kept,
        "final_champion": champion,
        "log": str(log_path),
        "round_results": round_results,
        "errors": [item["reason"] for item in round_results if item["reason"].startswith("error:")],
    }
