"""Deterministic rubric judge for HydraAgent draft → critic → revise loops.

A rubric is a list of typed rules. Each rule fires a violation when the draft
does not satisfy it. The judge returns a structured report with a weighted
score, named violations, and a pass/fail verdict against a configurable
threshold.

This is the deterministic backbone of the verification loop. LLM-as-judge
wrappers can be layered on top of the same ``JudgeReport`` shape later, but
the rubric judge stays useful on its own — fast, free, and reproducible.

Supported rule kinds
--------------------

* ``must_contain``   — draft must contain ``pattern`` (literal substring).
* ``must_not_contain`` — draft must NOT contain ``pattern`` (literal substring).
* ``regex_required`` — draft must match ``pattern`` (regex).
* ``regex_forbidden`` — draft must NOT match ``pattern`` (regex).
* ``max_length``      — draft byte length must be ≤ ``value``.
* ``min_length``      — draft byte length must be ≥ ``value``.
* ``must_cite_source`` — draft must contain at least one explicit source-cite
  (e.g. ``http(s)://``, ``§``, ``evidence/``, or ``source:``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable


SCHEMA = "hydra.rubric_judge.v1"
DEFAULT_PASS_THRESHOLD = 1.0  # default: every rule must pass

RULE_KINDS = frozenset({
    "must_contain",
    "must_not_contain",
    "regex_required",
    "regex_forbidden",
    "max_length",
    "min_length",
    "must_cite_source",
})

_SOURCE_CITE_RE = re.compile(
    r"https?://\S+|§\d+\.\d+|evidence/\S+|^source\s*:\s*\S+",
    re.IGNORECASE | re.MULTILINE,
)


class RubricJudgeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    pattern: str | None = None
    value: int | None = None
    weight: float = 1.0
    description: str = ""


def judge(
    draft: str,
    rules: Iterable[Rule | dict[str, Any]],
    *,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
) -> dict[str, Any]:
    """Score ``draft`` against ``rules`` and return a :data:`JudgeReport`."""
    if not isinstance(draft, str):
        raise RubricJudgeError("draft must be a string")
    if not (0.0 <= pass_threshold <= 1.0):
        raise RubricJudgeError("pass_threshold must be in [0.0, 1.0]")

    rule_objs = [_coerce_rule(r) for r in rules]
    if not rule_objs:
        raise RubricJudgeError("rubric must contain at least one rule")
    _validate_unique_ids(rule_objs)
    total_weight = sum(r.weight for r in rule_objs)
    if total_weight <= 0:
        raise RubricJudgeError("sum of rule weights must be positive")

    draft_bytes = len(draft.encode("utf-8", errors="replace"))
    earned = 0.0
    violations: list[dict[str, Any]] = []
    passed_rules: list[dict[str, Any]] = []
    for rule in rule_objs:
        ok, detail = _evaluate(rule, draft, draft_bytes)
        if ok:
            earned += rule.weight
            passed_rules.append({"id": rule.id, "kind": rule.kind})
        else:
            violations.append({
                "id": rule.id,
                "kind": rule.kind,
                "pattern": rule.pattern,
                "value": rule.value,
                "weight": rule.weight,
                "detail": detail,
            })

    score = round(earned / total_weight, 4)
    verdict = "PASS" if score >= pass_threshold and not violations else "FAIL"
    if pass_threshold < 1.0:
        # When threshold is below 1.0 we allow some violations as long as score meets bar
        verdict = "PASS" if score >= pass_threshold else "FAIL"
    proof = [
        f"draft_bytes={draft_bytes}",
        f"rules={len(rule_objs)}",
        f"passed={len(passed_rules)}",
        f"violations={len(violations)}",
        f"score={score}",
        f"pass_threshold={pass_threshold}",
        f"total_weight={round(total_weight, 4)}",
    ]
    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "score": score,
        "pass_threshold": pass_threshold,
        "draft_bytes": draft_bytes,
        "rules_total": len(rule_objs),
        "rules_passed": len(passed_rules),
        "violations": violations,
        "passed_rules": passed_rules,
        "proof": proof,
        "policy": "deterministic rubric judge; same draft + rubric → same verdict",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra rubric judge: {report['verdict']} score={report['score']}",
        f"draft_bytes={report['draft_bytes']}  "
        f"passed={report['rules_passed']}/{report['rules_total']}  "
        f"threshold={report['pass_threshold']}",
    ]
    if report["violations"]:
        lines.append("violations:")
        for v in report["violations"]:
            lines.append(f"  - [{v['kind']}] {v['id']} (weight={v['weight']}): {v['detail']}")
    else:
        lines.append("violations: none")
    return "\n".join(lines) + "\n"


def _coerce_rule(value: Rule | dict[str, Any]) -> Rule:
    if isinstance(value, Rule):
        rule = value
    elif isinstance(value, dict):
        rule = Rule(
            id=value.get("id", ""),
            kind=value.get("kind", ""),
            pattern=value.get("pattern"),
            value=value.get("value"),
            weight=float(value.get("weight", 1.0)),
            description=value.get("description", ""),
        )
    else:
        raise RubricJudgeError(f"rule must be a Rule or dict; got {type(value).__name__}")
    if not rule.id or not isinstance(rule.id, str):
        raise RubricJudgeError("rule id must be a non-empty string")
    if rule.kind not in RULE_KINDS:
        raise RubricJudgeError(
            f"rule {rule.id!r}: unknown kind {rule.kind!r}; valid: {sorted(RULE_KINDS)}"
        )
    if rule.kind in {"must_contain", "must_not_contain"}:
        if not isinstance(rule.pattern, str) or not rule.pattern:
            raise RubricJudgeError(f"rule {rule.id!r}: {rule.kind} requires non-empty pattern")
    if rule.kind in {"regex_required", "regex_forbidden"}:
        if not isinstance(rule.pattern, str) or not rule.pattern:
            raise RubricJudgeError(f"rule {rule.id!r}: {rule.kind} requires non-empty pattern")
        try:
            re.compile(rule.pattern)
        except re.error as e:
            raise RubricJudgeError(f"rule {rule.id!r}: invalid regex: {e}") from e
    if rule.kind in {"max_length", "min_length"}:
        if not isinstance(rule.value, int) or rule.value < 0:
            raise RubricJudgeError(
                f"rule {rule.id!r}: {rule.kind} requires non-negative integer value"
            )
    if rule.weight < 0:
        raise RubricJudgeError(f"rule {rule.id!r}: weight must be non-negative")
    return rule


def _validate_unique_ids(rules: list[Rule]) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule.id in seen:
            raise RubricJudgeError(f"duplicate rule id: {rule.id!r}")
        seen.add(rule.id)


def _evaluate(rule: Rule, draft: str, draft_bytes: int) -> tuple[bool, str]:
    if rule.kind == "must_contain":
        return (rule.pattern in draft, "" if rule.pattern in draft else f"missing literal {rule.pattern!r}")
    if rule.kind == "must_not_contain":
        forbidden = rule.pattern in draft
        return (not forbidden, "" if not forbidden else f"contains forbidden literal {rule.pattern!r}")
    if rule.kind == "regex_required":
        matched = bool(re.search(rule.pattern, draft)) if rule.pattern else False
        return (matched, "" if matched else f"regex {rule.pattern!r} did not match")
    if rule.kind == "regex_forbidden":
        matched = bool(re.search(rule.pattern, draft)) if rule.pattern else False
        return (not matched, "" if not matched else f"forbidden regex {rule.pattern!r} matched")
    if rule.kind == "max_length":
        ok = draft_bytes <= (rule.value or 0)
        return (ok, "" if ok else f"draft {draft_bytes} bytes exceeds max_length={rule.value}")
    if rule.kind == "min_length":
        ok = draft_bytes >= (rule.value or 0)
        return (ok, "" if ok else f"draft {draft_bytes} bytes below min_length={rule.value}")
    if rule.kind == "must_cite_source":
        matched = bool(_SOURCE_CITE_RE.search(draft))
        return (matched, "" if matched else "no source citation found (http(s)://, §slice, evidence/, or 'source:')")
    raise RubricJudgeError(f"rule {rule.id!r}: unimplemented kind {rule.kind!r}")
