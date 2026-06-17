"""Hybrid critic: §10.60 deterministic rubric + optional LLM-as-judge rules.

Extends the §10.60 rubric judge with one new rule kind, ``llm_semantic``,
where ``pattern`` carries a yes/no question to ask an LLM about the draft.
The LLM's answer is parsed as PASS / FAIL / UNKNOWN; UNKNOWN never changes
the verdict by itself — it is recorded for trace, and the deterministic rules
still control the bar.

Why this exists: the user build plan calls for a critic that can do semantic
checks beyond literal substrings ("did the answer actually address the
question?"). Layering LLM rules ON TOP OF the deterministic ones means the
critic stays useful (and reproducible) even when no provider is wired in.

The ``llm_callable`` protocol is intentionally tiny: ``(question, draft) ->
{"verdict": "pass"|"fail"|"unknown", "reason": str}``. Real callers wrap an
existing :class:`hydra.llm.OllamaClient` or any provider; tests inject a stub
with no network round-trip.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Protocol, runtime_checkable

from hydra.rubric_judge import (
    RULE_KINDS as BASE_RULE_KINDS,
    RubricJudgeError,
    Rule,
    judge as deterministic_judge,
)


SCHEMA = "hydra.llm_judge.v1"
LLM_RULE_KIND = "llm_semantic"
EXTENDED_RULE_KINDS = BASE_RULE_KINDS | {LLM_RULE_KIND}
ALLOWED_LLM_VERDICTS = frozenset({"pass", "fail", "unknown"})

_VERDICT_PATTERN = re.compile(r"\b(pass|fail|unknown|yes|no)\b", re.IGNORECASE)


class LlmJudgeError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


@runtime_checkable
class LlmJudgeCallable(Protocol):
    def __call__(self, question: str, draft: str) -> dict[str, str]:
        ...


def judge(
    draft: str,
    rules: list[Rule | dict[str, Any]],
    *,
    pass_threshold: float = 1.0,
    llm_callable: LlmJudgeCallable | None = None,
    treat_unknown_as: str = "advisory",
) -> dict[str, Any]:
    """Hybrid judge.

    ``llm_semantic`` rules whose pattern is the yes/no question for the LLM:
        ``{"id": "addresses_user", "kind": "llm_semantic",
           "pattern": "Does the draft actually address the user's question?"}``

    When ``llm_callable`` is None, every ``llm_semantic`` rule is recorded as
    ``status=unknown`` and treated according to ``treat_unknown_as``:

    * ``"advisory"`` (default) — neither pass nor fail; weight is excluded
      from the score (denominator shrinks). The rule appears in
      ``advisory_rules`` for trace, not in violations.
    * ``"fail"`` — counted as a violation (strict mode).
    * ``"pass"`` — counted as passed (permissive mode).
    """
    if treat_unknown_as not in {"advisory", "fail", "pass"}:
        raise LlmJudgeError(
            "treat_unknown_as must be one of: 'advisory', 'fail', 'pass'"
        )
    if llm_callable is not None and not isinstance(llm_callable, LlmJudgeCallable):
        raise LlmJudgeError("llm_callable must be callable (question, draft) -> dict")

    deterministic_rules: list[dict[str, Any]] = []
    llm_rules: list[dict[str, Any]] = []
    for raw in rules:
        rule_dict = _to_dict(raw)
        kind = rule_dict.get("kind")
        if kind == LLM_RULE_KIND:
            _validate_llm_rule(rule_dict)
            llm_rules.append(rule_dict)
        else:
            deterministic_rules.append(rule_dict)

    if not deterministic_rules and not llm_rules:
        raise LlmJudgeError("rubric must contain at least one rule")

    # Run deterministic critic; an empty deterministic list would crash it, so
    # synthesise a no-op rule that always passes when only llm_semantic rules
    # were supplied — preserves the §10.60 contract.
    if deterministic_rules:
        base_report = deterministic_judge(draft, deterministic_rules, pass_threshold=0.0)
    else:
        base_report = {
            "schema": "hydra.rubric_judge.v1",
            "verdict": "PASS",
            "score": 1.0,
            "pass_threshold": 0.0,
            "draft_bytes": len(draft.encode("utf-8", errors="replace")),
            "rules_total": 0,
            "rules_passed": 0,
            "violations": [],
            "passed_rules": [],
            "proof": ["deterministic_rules=0"],
            "policy": "deterministic rubric judge; same draft + rubric → same verdict",
        }

    llm_results: list[dict[str, Any]] = []
    advisory_rules: list[dict[str, Any]] = []
    extra_violations: list[dict[str, Any]] = []
    extra_passes: list[dict[str, Any]] = []
    deterministic_passed_weight = sum(
        next(r for r in deterministic_rules if r["id"] == p["id"]).get("weight", 1.0)
        for p in base_report["passed_rules"]
    )
    deterministic_total_weight = sum(r.get("weight", 1.0) for r in deterministic_rules)
    earned_extra = 0.0
    total_extra = 0.0

    for rule in llm_rules:
        weight = float(rule.get("weight", 1.0))
        question = rule["pattern"]
        if llm_callable is None:
            status = "unknown"
            reason = "no llm_callable provided"
        else:
            try:
                raw = llm_callable(question, draft)
            except Exception as e:  # noqa: BLE001
                status = "unknown"
                reason = f"llm_callable raised {type(e).__name__}: {e}"
            else:
                status, reason = _parse_llm_response(raw)
        llm_results.append({
            "id": rule["id"],
            "kind": LLM_RULE_KIND,
            "question": question,
            "weight": weight,
            "status": status,
            "reason": reason,
        })
        effective_status = status
        if status == "unknown":
            if treat_unknown_as == "fail":
                effective_status = "fail"
            elif treat_unknown_as == "pass":
                effective_status = "pass"
        if effective_status == "pass":
            total_extra += weight
            earned_extra += weight
            extra_passes.append({"id": rule["id"], "kind": LLM_RULE_KIND})
        elif effective_status == "fail":
            total_extra += weight
            extra_violations.append({
                "id": rule["id"],
                "kind": LLM_RULE_KIND,
                "question": question,
                "weight": weight,
                "status": status,
                "reason": reason,
            })
        else:  # advisory
            advisory_rules.append({
                "id": rule["id"],
                "weight": weight,
                "reason": reason,
            })

    total_weight = deterministic_total_weight + total_extra
    if total_weight <= 0:
        score = 0.0
    else:
        score = round((deterministic_passed_weight + earned_extra) / total_weight, 4)

    combined_violations = list(base_report["violations"]) + extra_violations
    combined_passed = list(base_report["passed_rules"]) + extra_passes

    verdict = "PASS" if (score >= pass_threshold and not combined_violations) else "FAIL"
    if pass_threshold < 1.0:
        verdict = "PASS" if score >= pass_threshold else "FAIL"

    proof = [
        f"draft_bytes={base_report['draft_bytes']}",
        f"deterministic_rules={len(deterministic_rules)}",
        f"llm_rules={len(llm_rules)}",
        f"llm_callable={'set' if llm_callable is not None else 'absent'}",
        f"advisory_rules={len(advisory_rules)}",
        f"violations={len(combined_violations)}",
        f"score={score}",
        f"pass_threshold={pass_threshold}",
        f"total_weight={round(total_weight, 4)}",
    ]

    return {
        "schema": SCHEMA,
        "verdict": verdict,
        "score": score,
        "pass_threshold": pass_threshold,
        "treat_unknown_as": treat_unknown_as,
        "draft_bytes": base_report["draft_bytes"],
        "rules_total": len(deterministic_rules) + len(llm_rules),
        "rules_passed": len(combined_passed),
        "violations": combined_violations,
        "passed_rules": combined_passed,
        "advisory_rules": advisory_rules,
        "llm_results": llm_results,
        "deterministic_report": base_report,
        "proof": proof,
        "policy": (
            "hybrid critic: §10.60 deterministic rules + optional llm_semantic; "
            "unknown rules are advisory by default (excluded from score)"
        ),
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra hybrid judge: {report['verdict']} score={report['score']}",
        f"deterministic_rules + llm_rules = {report['rules_total']}, "
        f"passed={report['rules_passed']}, advisory={len(report['advisory_rules'])}",
        f"treat_unknown_as={report['treat_unknown_as']} threshold={report['pass_threshold']}",
    ]
    if report["violations"]:
        lines.append("violations:")
        for v in report["violations"]:
            lines.append(f"  - [{v['kind']}] {v['id']}: {v.get('reason') or v.get('detail') or v.get('question')}")
    if report["advisory_rules"]:
        lines.append("advisory (unknown):")
        for a in report["advisory_rules"]:
            lines.append(f"  - {a['id']} (weight={a['weight']}): {a['reason']}")
    return "\n".join(lines) + "\n"


def _to_dict(rule: Rule | dict[str, Any]) -> dict[str, Any]:
    if isinstance(rule, dict):
        return dict(rule)
    if isinstance(rule, Rule):
        return {
            "id": rule.id,
            "kind": rule.kind,
            "pattern": rule.pattern,
            "value": rule.value,
            "weight": rule.weight,
            "description": rule.description,
        }
    raise LlmJudgeError(f"rule must be a Rule or dict; got {type(rule).__name__}")


def _validate_llm_rule(rule: dict[str, Any]) -> None:
    if not isinstance(rule.get("id"), str) or not rule["id"]:
        raise LlmJudgeError("llm_semantic rule requires non-empty id")
    pattern = rule.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        raise LlmJudgeError(f"llm_semantic rule {rule['id']!r} requires non-empty pattern (question)")
    weight = rule.get("weight", 1.0)
    if not isinstance(weight, (int, float)) or weight < 0:
        raise LlmJudgeError(f"llm_semantic rule {rule['id']!r}: weight must be non-negative")


def _parse_llm_response(raw: Any) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "unknown", f"llm response was not a dict: got {type(raw).__name__}"
    verdict_raw = str(raw.get("verdict", "")).strip().lower()
    reason = str(raw.get("reason", "")).strip()
    if verdict_raw in ALLOWED_LLM_VERDICTS:
        return verdict_raw, reason
    # Lenient fallback: scan the free-text reason for yes/no/pass/fail
    if reason:
        match = _VERDICT_PATTERN.search(reason)
        if match:
            token = match.group(1).lower()
            if token in {"yes", "pass"}:
                return "pass", reason
            if token in {"no", "fail"}:
                return "fail", reason
    return "unknown", reason or f"unparseable llm verdict: {raw}"
