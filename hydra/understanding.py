"""Advisory code-understanding evidence for Hydra.

The deterministic scorer explains how a candidate fits its stated intent.  A
separately routed auditor model can refute claims the heuristics cannot prove.
Nothing in this module approves execution or blocks another Hydra workflow.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

from hydra import providers
from hydra.llm import ChatMessage
from hydra.model_routing import load_routing


AUDITOR_MAX_TOKENS = 2048
_DIMENSIONS = (
    "Spec adherence",
    "Architectural fit",
    "Type safety",
    "Testability",
    "Security",
)


@dataclass(frozen=True)
class DimensionScore:
    dimension: str
    score: int
    evidence: str


@dataclass(frozen=True)
class UnderstandingResult:
    total_score: int
    verdict: str
    dimension_scores: tuple[DimensionScore, ...]
    failures: list[str]
    recovery_actions: list[str]
    confidence: float
    passed: bool
    status: str


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9_]+", text.lower()))


def _parse_candidate(candidate_code: str) -> ast.Module | None:
    try:
        return ast.parse(candidate_code)
    except SyntaxError:
        return None


def _score_dimensions(candidate_code: str, original_intent: str) -> tuple[DimensionScore, ...]:
    tree = _parse_candidate(candidate_code)
    intent_words = {word for word in _words(original_intent) if len(word) > 3}
    overlap = intent_words & _words(candidate_code)
    overlap_ratio = len(overlap) / max(len(intent_words), 1)
    spec_score = 4 if overlap_ratio >= 0.25 else 3 if overlap_ratio >= 0.15 else 1

    if tree is None:
        architecture_score = type_score = testability_score = 0
    else:
        functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        largest_function = max((len(getattr(node, "body", ())) for node in functions), default=0)
        mutable_globals = sum(
            isinstance(node, (ast.List, ast.Dict, ast.Set))
            for statement in tree.body
            if isinstance(statement, (ast.Assign, ast.AnnAssign))
            for node in [statement.value]
        )
        architecture_score = 4 if largest_function <= 20 and mutable_globals == 0 else 2
        annotated = sum(
            bool(node.returns) and all(arg.annotation for arg in node.args.args)
            for node in functions
        )
        type_score = 4 if functions and annotated == len(functions) else 2 if annotated else 0
        testability_score = 4 if functions and largest_function <= 20 else 1

    suspicious = (
        r"\b(eval|exec)\s*\(",
        r"shell\s*=\s*True",
        r"(?i)(api[_-]?key|password|secret)\s*=\s*['\"][^'\"]+['\"]",
        r"(?i)(select|insert|update|delete).*(\+|\.format\(|f['\"])",
    )
    security_score = 0 if any(re.search(pattern, candidate_code) for pattern in suspicious) else 4

    raw = (spec_score, architecture_score, type_score, testability_score, security_score)
    evidence = (
        f"Intent/code keyword overlap is {overlap_ratio:.0%}.",
        "Parsed structure is small and avoids mutable module globals." if architecture_score == 4 else "Structure is invalid, large, or uses mutable module globals.",
        "Every function parameter and return is annotated." if type_score == 4 else "Function annotations are missing or incomplete.",
        "Functions are present and bounded for isolated tests." if testability_score == 4 else "No bounded function boundary was found for isolated tests.",
        "No high-risk dynamic execution, embedded secret, shell, or interpolated SQL pattern was found." if security_score == 4 else "A high-risk security pattern was found.",
    )
    return tuple(
        DimensionScore(dimension=name, score=score, evidence=why)
        for name, score, why in zip(_DIMENSIONS, raw, evidence, strict=True)
    )


def _deterministic_findings(
    scores: tuple[DimensionScore, ...],
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    actions: list[str] = []
    for item in scores:
        if item.score < 4:
            failures.append(f"{item.dimension}: {item.evidence}")
            actions.append(f"Improve {item.dimension.lower()} and rerun check_candidate().")
    return failures, actions


def _parse_auditor_verdict(raw: str) -> tuple[bool, list[str], list[str], float]:
    """Parse exactly one JSON object with the documented verdict schema."""
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "passed",
        "failures",
        "recovery_actions",
        "confidence",
    }:
        raise ValueError("auditor verdict must contain exactly passed, failures, recovery_actions, and confidence")
    if not isinstance(value["passed"], bool):
        raise ValueError("auditor passed must be a boolean")
    if not isinstance(value["failures"], list) or not all(
        isinstance(item, str) and item.strip() for item in value["failures"]
    ):
        raise ValueError("auditor failures must be a list of non-empty strings")
    if not isinstance(value["recovery_actions"], list) or not all(
        isinstance(item, str) and item.strip() for item in value["recovery_actions"]
    ):
        raise ValueError("auditor recovery_actions must be a list of non-empty strings")
    confidence = value["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("auditor confidence must be a number from 0 to 1")
    if value["passed"] and (value["failures"] or value["recovery_actions"]):
        raise ValueError("a passing auditor verdict cannot include failures or recovery actions")
    if not value["passed"] and (not value["failures"] or not value["recovery_actions"]):
        raise ValueError("a refutation requires failures and recovery actions")
    return (
        value["passed"],
        list(value["failures"]),
        list(value["recovery_actions"]),
        float(confidence),
    )


def _auditor_prompt(candidate_code: str, original_intent: str) -> str:
    return (
        "Refute unsupported claims in this code against the stated intent. "
        "Do not merely confirm the candidate. Return only strict JSON with exactly: "
        '{"passed": boolean, "failures": [string], "recovery_actions": [string], '
        '"confidence": number from 0 to 1}. A false claim must set passed=false.\n\n'
        f"INTENT:\n{original_intent[:6000]}\n\nCANDIDATE CODE:\n{candidate_code[:12000]}"
    )


def check_candidate(candidate_code: str, original_intent: str) -> UnderstandingResult:
    """Return advisory deterministic scores plus a grounded auditor refutation."""
    scores = _score_dimensions(candidate_code, original_intent)
    total_score = sum(item.score for item in scores) * 5
    verdict = "APPROVED" if total_score >= 80 else "REVISE" if total_score >= 60 else "REJECT"
    deterministic_confidence = 0.9 if verdict == "APPROVED" else 0.7 if verdict == "REVISE" else 0.6
    deterministic_failures, deterministic_actions = _deterministic_findings(scores)

    try:
        provider, model = load_routing().role_pair("auditor")
        client, _config = providers.make_client(provider)
        response = client.chat(
            [
                ChatMessage(role="system", content="You are Hydra's grounded code-claim auditor."),
                ChatMessage(role="user", content=_auditor_prompt(candidate_code, original_intent)),
            ],
            model=model,
            max_tokens=AUDITOR_MAX_TOKENS,
            temperature=0.0,
            timeout=60.0,
        )
        model_passed, model_failures, model_actions, model_confidence = _parse_auditor_verdict(
            response.content
        )
    except Exception as exc:  # advisory result must expose every routing, call, or parse failure
        return UnderstandingResult(
            total_score=total_score,
            verdict=verdict,
            dimension_scores=scores,
            failures=[f"Auditor model unavailable: {type(exc).__name__}: {exc}"],
            recovery_actions=[
                "Configure the auditor provider/model in Hydra's model routing and provider environment, then rerun check_candidate()."
            ],
            confidence=0.0,
            passed=False,
            status="model_unavailable",
        )

    if not model_passed:
        return UnderstandingResult(
            total_score=total_score,
            verdict=verdict,
            dimension_scores=scores,
            failures=model_failures,
            recovery_actions=model_actions,
            confidence=min(deterministic_confidence, model_confidence),
            passed=False,
            status="refuted",
        )

    passed = verdict == "APPROVED"
    return UnderstandingResult(
        total_score=total_score,
        verdict=verdict,
        dimension_scores=scores,
        failures=deterministic_failures,
        recovery_actions=deterministic_actions,
        confidence=min(deterministic_confidence, model_confidence),
        passed=passed,
        status="approved" if passed else verdict.lower(),
    )
