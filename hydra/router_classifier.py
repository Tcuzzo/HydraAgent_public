"""Deterministic prompt → role classifier for §10.34 model routing.

`classify(prompt)` inspects a user prompt and picks the best §10.34 role
(``planner``, ``doer``, or ``auditor``) using a curated keyword + length
heuristic. No LLM call — pure regex + length.

This is the Phase 4 routing primitive. The wiring into ask/chat is opt-in
behind a `--auto-route` flag so default behavior is unchanged. Operators
who enable auto-routing get: cheap doer model by default, planner role
for design/architecture prompts, auditor role for review/verify prompts.
"""
from __future__ import annotations

import re
from typing import Any


SCHEMA = "hydra.router_classifier.v1"
ROLE_PLANNER = "planner"
ROLE_DOER = "doer"
ROLE_AUDITOR = "auditor"
LONG_PROMPT_BYTES = 500

_PLANNER_PATTERNS = [
    (re.compile(r"\b(?:plan|design|architect(?:ure)?|propose|outline|breakdown)\b", re.IGNORECASE), "planning-verb"),
    (re.compile(r"\b(?:multi[- ]step|across\s+files|refactor\s+across|migration\s+strategy)\b", re.IGNORECASE), "multi-step-scope"),
    (re.compile(r"\b(?:how\s+would\s+i|how\s+should\s+i|what's\s+the\s+best\s+approach)\b", re.IGNORECASE), "approach-question"),
]

_AUDITOR_PATTERNS = [
    (re.compile(r"\b(?:audit|review|verify|validate|check|lint|inspect|critique|sanity[- ]check)\b", re.IGNORECASE), "review-verb"),
    (re.compile(r"\b(?:is\s+this\s+correct|did\s+i\s+miss|find\s+(?:bugs|issues|problems))\b", re.IGNORECASE), "verification-question"),
    (re.compile(r"\b(?:judge|grade|score|evaluate)\b", re.IGNORECASE), "judge-verb"),
]

_DOER_PATTERNS = [
    (re.compile(r"\b(?:write|edit|fix|implement|add|create|build|run|delete|rename|update)\b", re.IGNORECASE), "action-verb"),
    (re.compile(r"\b(?:test|debug|patch|hotfix|commit|push|deploy)\b", re.IGNORECASE), "engineering-verb"),
]


class RouterClassifierError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def classify(prompt: str) -> dict[str, Any]:
    """Pick the best role for ``prompt``.

    Returns ``{role, score, matched, prompt_bytes, policy}`` where
    ``matched`` is the list of pattern_id strings that fired across all
    categories (useful for trace / debugging).
    """
    if not isinstance(prompt, str):
        raise RouterClassifierError("prompt must be a string")
    if not prompt.strip():
        raise RouterClassifierError("prompt must be non-empty")

    prompt_bytes = len(prompt.encode("utf-8", errors="replace"))
    planner_matches = _match_all(prompt, _PLANNER_PATTERNS)
    auditor_matches = _match_all(prompt, _AUDITOR_PATTERNS)
    doer_matches = _match_all(prompt, _DOER_PATTERNS)

    # Long prompts bias toward planner (more context to reason about).
    planner_bonus = 1 if prompt_bytes >= LONG_PROMPT_BYTES else 0

    scores = {
        ROLE_PLANNER: len(planner_matches) + planner_bonus,
        ROLE_AUDITOR: len(auditor_matches),
        ROLE_DOER: len(doer_matches),
    }

    # Tie-break order: auditor > planner > doer (auditor is the safest
    # role to escalate to on ambiguous "check this" prompts).
    max_score = max(scores.values())
    if max_score == 0:
        role = ROLE_DOER
        matched: list[str] = []
    elif scores[ROLE_AUDITOR] == max_score:
        role = ROLE_AUDITOR
        matched = auditor_matches
    elif scores[ROLE_PLANNER] == max_score:
        role = ROLE_PLANNER
        matched = planner_matches + (["long_prompt_bonus"] if planner_bonus else [])
    else:
        role = ROLE_DOER
        matched = doer_matches

    return {
        "schema": SCHEMA,
        "role": role,
        "score": scores[role],
        "scores": scores,
        "matched": matched,
        "prompt_bytes": prompt_bytes,
        "long_prompt_bonus_applied": bool(planner_bonus and role == ROLE_PLANNER),
        "policy": "keyword + length heuristic; tie-break auditor > planner > doer; no LLM",
    }


def _match_all(prompt: str, patterns: list[tuple[re.Pattern[str], str]]) -> list[str]:
    out: list[str] = []
    for regex, label in patterns:
        if regex.search(prompt):
            out.append(label)
    return out
