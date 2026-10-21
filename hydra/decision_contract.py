"""Validate the shared local choice/score/noul contract before decisions escape.

The shape follows the public Kev/TypeSafe and Laya interfaces. This module
validates data, not model accuracy; a valid decision never grants permission.
"""
from __future__ import annotations

import math

from hydra.llm import LlmError


def _number(value, *, low: float, high: float, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise LlmError(f"{label} must be a finite number in [{low}, {high}]")
    return value


def validate_questions(questions: dict) -> None:
    if not isinstance(questions, dict) or not 1 <= len(questions) <= 64:
        raise LlmError("typed decisions require a non-empty questions object with at most 64 questions")
    for name, question in questions.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(question, dict):
            raise LlmError("typed questions require non-empty names and object definitions")
        kind = question.get("type")
        if kind not in {"choice", "score", "noul"}:
            raise LlmError(f"question {name!r} must have type choice, score, or noul")
        criteria = question.get("criteria")
        if kind == "choice":
            if (not isinstance(criteria, dict) or not 1 <= len(criteria) <= 255
                    or any(not isinstance(k, str) or not k.strip() for k in criteria)):
                raise LlmError(f"question {name!r} has missing or invalid choice criteria (1..255 named options)")
        elif kind == "score":
            if not isinstance(criteria, list) or not 1 <= len(criteria) <= 255:
                raise LlmError(f"question {name!r} has missing or invalid score criteria (1..255 ordered levels)")
        elif criteria is not None and (not isinstance(criteria, dict) or set(criteria) - {"false", "true"}):
            raise LlmError(f"question {name!r} noul criteria may describe only false and true")


def validate_decision(result: dict, questions: dict) -> dict:
    if not isinstance(result, dict):
        raise LlmError("typed decision returned a non-object response")
    answers = result.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(questions):
        raise LlmError("typed decision returned missing or unexpected answers")
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        raise LlmError("typed decision returned invalid usage metadata")
    if (result.get("truncated") or usage.get("truncated")
            or usage.get("state_tokens_dropped") or usage.get("truncated_questions")):
        raise LlmError("typed decision truncated its source state; shorten the state and retry explicitly")
    for name, question in questions.items():
        kind, answer = question["type"], answers[name]
        if not isinstance(answer, dict) or answer.get("type", kind) != kind:
            raise LlmError(f"answer {name!r} does not match question type {kind}")
        if kind == "choice":
            expected = set(question["criteria"])
            if not isinstance(answer.get("choice"), str) or answer["choice"] not in expected:
                raise LlmError(f"answer {name!r} chose an option outside the supplied criteria")
        elif kind == "score":
            expected = {str(i) for i in range(len(question["criteria"]))}
            _number(answer.get("score"), low=0, high=len(expected) - 1, label=f"answer {name!r} score")
        else:
            expected = {"false", "true"}
            _number(answer.get("noul"), low=0, high=1, label=f"answer {name!r} noul")
        if "confidence" in answer:
            _number(answer["confidence"], low=0, high=1, label=f"answer {name!r} confidence")
        if "probabilities" in answer:
            probabilities = answer["probabilities"]
            if not isinstance(probabilities, dict) or set(probabilities) != expected:
                raise LlmError(f"answer {name!r} probability keys do not match the supplied criteria")
            values = [_number(v, low=0, high=1, label=f"answer {name!r} probability") for v in probabilities.values()]
            # Official Kev rounds to four decimals, allowing up to 255 options.
            if abs(sum(values) - 1) >= 0.02:
                raise LlmError(f"answer {name!r} probabilities do not sum to one")
    return result
