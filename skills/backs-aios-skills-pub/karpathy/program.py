"""karpathy — the Markdown-programmable autoresearch front-end.

Named in tribute to Andrej Karpathy, whose `autoresearch` (github.com/karpathy/
autoresearch) crystallized one primitive we did not have: **you program the
Markdown, not the code.** A coding agent is pointed at a plain-English program
file, and autonomously loops *propose -> run a short experiment -> measure ONE
metric -> keep if better, else discard -> repeat*.

We studied that idea and reimplemented it natively on top of our own, stronger
engine (`evolve.py`) rather than vendoring his `train.py` /
`prepare.py`. Our engine already adds what his bare loop lacks: a lexicographic
hard gate, byte-exact `git` keep/revert, a sandbox guard, and an anti-bloat
penalty. This module is the thin, well-tested adapter that lets the operator
*write the research program in plain English* and have that drive the engine.

The two seams:
  * ``parse_program(md)``  -> ``ResearchProgram``  (Markdown -> a precise spec)
  * ``run_program(prog, ...)`` -> evolve.run(...)  (spec -> the real keep/revert)

Operator laws honored (inherited from the engine + enforced here):
  * sandbox by default (a program omitting ``## Sandbox`` is sandboxed);
  * mutation must PROVE UPLIFT (lexicographic strict dominance) or it reverts;
  * the blind Tester / Proposer are injected — never self-graded;
  * malformed programs fail LOUD (``ProgramError``), never silently mis-route.

stdlib only (re, shlex, dataclasses, pathlib) + the engine.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from evolve import (
    AutoResearchError,
    Metric,
    MetricField,
    run as _evolve_run,
)


class ProgramError(AutoResearchError):
    """Raised when a program.md is malformed (missing/invalid required parts)."""


# --------------------------------------------------------------------------- #
# ResearchProgram
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ResearchProgram:
    """A parsed, plain-English research program (the operator-authored spec).

    Attributes:
        title: the ``# Title`` line — what this loop is for, in plain English.
        goal: the free text under ``## Goal`` (the human intent; surfaced to the
            Proposer, never to the blind Tester).
        metric: the lexicographic ``Metric`` built from ``## Metric`` (first gate
            is the hard bar; remaining axes optimize in declared priority order).
        fair_game: globs under ``## Fair game`` the Proposer may edit — the
            "only train.py is editable" discipline, enforced after each proposal.
        budget_rounds: ``rounds:`` under ``## Budget`` (default 1).
        sandbox: ``required:`` under ``## Sandbox`` (True required).
        eval_cmd: ``cmd:`` under ``## Eval`` split into argv (default None).
        target_dir: optional ``dir:`` under ``## Target`` (overridable at run).
        raw: the original Markdown (for logging / provenance).
    """

    title: str
    goal: str
    metric: Metric
    fair_game: list[str] = field(default_factory=list)
    budget_rounds: int = 1
    sandbox: bool = True
    eval_cmd: Optional[list[str]] = None
    target_dir: Optional[str] = None
    raw: str = ""


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
_KNOWN_SECTIONS = {"goal", "metric", "fair game", "budget", "sandbox", "eval", "target"}


def _split_sections(text: str) -> tuple[str, dict[str, list[str]]]:
    """Return (title, {lower_header: [body lines]}). HTML comments are dropped."""
    title = ""
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("<!--"):  # drop comment lines (may be inline help)
            continue
        if stripped.startswith("## "):
            current = stripped[3:].strip().lower()
            sections.setdefault(current, [])
            continue
        if stripped.startswith("# ") and not title:
            title = stripped[2:].strip()
            current = None
            continue
        if current is not None:
            sections[current].append(line)
    return title, sections


def _body(sections: dict[str, list[str]], name: str) -> str:
    return "\n".join(sections.get(name, [])).strip()


def _bullets(sections: dict[str, list[str]], name: str) -> list[str]:
    out: list[str] = []
    for line in sections.get(name, []):
        s = line.strip()
        if s.startswith("- "):
            out.append(s[2:].strip())
    return out


def _kv(sections: dict[str, list[str]], name: str, key: str) -> Optional[str]:
    """First ``key: value`` line in a section's body (case-insensitive key)."""
    for line in sections.get(name, []):
        s = line.strip()
        if ":" in s:
            k, _, v = s.partition(":")
            if k.strip().lower() == key.lower():
                return v.strip()
    return None


def _parse_metric_line(line: str) -> MetricField:
    """``name | max|min | gate?`` -> MetricField. Gate optional/blank = None."""
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2 or not parts[0]:
        raise ProgramError(
            f"metric line must be 'name | max|min | gate?', got: {line!r}"
        )
    name, direction = parts[0], parts[1].lower()
    gate: Optional[float] = None
    if len(parts) >= 3 and parts[2] != "":
        try:
            gate = float(parts[2])
        except ValueError as exc:
            raise ProgramError(
                f"metric {name!r}: gate must be a number, got {parts[2]!r}"
            ) from exc
    try:
        return MetricField(name, direction, gate)  # validates direction
    except AutoResearchError as exc:
        raise ProgramError(str(exc)) from exc


def parse_program(md: str | Path) -> ResearchProgram:
    """Parse a plain-English program.md into a precise ``ResearchProgram``.

    ``md`` may be the Markdown text itself or a path to a ``.md`` file. Required:
    a ``# Title`` and a ``## Metric`` with at least one ``- name | max|min | gate?``
    line. ``## Goal``/``Fair game``/``Budget``/``Sandbox``/``Eval``/``Target`` are
    optional except Fair game; defaults are rounds=1, sandbox=True, no eval,
    and no target.
    """
    if isinstance(md, Path):
        text = md.read_text(encoding="utf-8")
    elif isinstance(md, str) and "\n" not in md and md.strip().endswith(".md"):
        # a bare path-as-string convenience
        text = Path(md).read_text(encoding="utf-8")
    else:
        text = md

    title, sections = _split_sections(text)
    if not title:
        raise ProgramError("program.md must start with a '# Title' line")

    metric_lines = _bullets(sections, "metric")
    if not metric_lines:
        raise ProgramError("program.md must have a '## Metric' section with ≥1 axis")
    metric = Metric([_parse_metric_line(ln) for ln in metric_lines])

    rounds_raw = _kv(sections, "budget", "rounds")
    if rounds_raw is None:
        budget_rounds = 1
    else:
        try:
            budget_rounds = int(rounds_raw)
        except ValueError as exc:
            raise ProgramError(f"Budget rounds must be an integer, got {rounds_raw!r}") from exc
    if budget_rounds < 1:
        raise ProgramError(f"Budget rounds must be ≥ 1, got {budget_rounds}")

    sandbox_raw = _kv(sections, "sandbox", "required")
    if sandbox_raw is not None and sandbox_raw.strip().lower() not in ("true", "yes", "1"):
        raise ProgramError("Sandbox required must be true; only disposable clones are supported")
    sandbox = True

    fair_game = _bullets(sections, "fair game")
    if not fair_game:
        raise ProgramError("Fair game must list the files the proposer may change")

    eval_raw = _kv(sections, "eval", "cmd")
    eval_cmd = shlex.split(eval_raw) if eval_raw else None

    target_dir = _kv(sections, "target", "dir") or None

    return ResearchProgram(
        title=title,
        goal=_body(sections, "goal"),
        metric=metric,
        fair_game=fair_game,
        budget_rounds=budget_rounds,
        sandbox=sandbox,
        eval_cmd=eval_cmd,
        target_dir=target_dir,
        raw=text,
    )


# --------------------------------------------------------------------------- #
# runner
# --------------------------------------------------------------------------- #
def run_program(
    program: ResearchProgram,
    *,
    propose: Callable[[Path], object],
    test: Callable[[Path], Mapping[str, float]],
    target_dir: str | Path | None = None,
    penalty: Optional[Callable[[Path], float]] = None,
    max_rounds: Optional[int] = None,
    logger_path: str | Path | None = None,
    sandbox_root: str | Path | None = None,
) -> dict:
    """Drive the proven evolve.py engine from a parsed ``ResearchProgram``.

    The Markdown owns the *what* (metric, gate, budget, sandbox, eval); the caller
    injects the *who* (the model-backed ``propose`` Proposer and the blind ``test``
    Tester). This is a thin adapter — all keep/revert, gating, sandbox-guarding and
    logging live in ``evolve.run``.

    Args:
        program: a ``ResearchProgram`` from :func:`parse_program`.
        propose: Proposer callable; mutates the working tree in place.
        test: blind Tester callable; returns the fitness vector. Never sees rationale.
        target_dir: the git working tree to evolve. Falls back to
            ``program.target_dir``; one of the two MUST be set.
        penalty: optional anti-bloat hook (>0 fails an otherwise-winning candidate).
        max_rounds: cap that overrides ``program.budget_rounds`` when given.
        logger_path: JSONL log destination (defaults to the engine's default).
        sandbox_root: explicit writable directory for disposable clones; use
            this when the host's default temp directory is restricted.

    Returns:
        The engine result, including every round result and surfaced errors,
        plus ``sandbox_dir`` and ``source_dir`` for review.
    """
    tgt = target_dir if target_dir is not None else program.target_dir
    if tgt is None:
        raise ProgramError(
            "no target: pass target_dir=... or set a '## Target' dir in the program.md"
        )
    rounds = max_rounds if max_rounds is not None else program.budget_rounds
    from evolve import cleanup_sandbox, prepare_sandbox

    sandbox_dir = prepare_sandbox(tgt, sandbox_root=sandbox_root)
    try:
        result = _evolve_run(
            target_dir=sandbox_dir,
            eval_cmd=program.eval_cmd,
            metric=program.metric,
            propose=propose,
            test=test,
            penalty=penalty,
            max_rounds=rounds,
            sandbox=program.sandbox,
            logger_path=logger_path,
            fair_game=program.fair_game,
        )
    except Exception as exc:
        try:
            cleanup_sandbox(sandbox_dir)
        except Exception as cleanup_error:
            raise ProgramError(
                f"experiment failed and cleanup failed at {sandbox_dir}: {cleanup_error}"
            ) from exc
        raise
    result["sandbox_dir"] = str(sandbox_dir)
    result["source_dir"] = str(Path(tgt).resolve())
    return result
