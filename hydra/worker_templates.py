"""Hydra-native worker skill templates."""
from __future__ import annotations

from typing import Any

from hydra.worker_jobs import build_worker_job_packet


WORKER_TEMPLATE_SCHEMA = "hydra.worker_template.v1"


class WorkerTemplateError(Exception):
    """Worker template routing or construction failure."""


TEMPLATES: dict[str, dict[str, Any]] = {
    "coding-repair": {
        "id": "coding-repair",
        "schema": WORKER_TEMPLATE_SCHEMA,
        "role": "repair",
        "keywords": ("fix", "bug", "repair", "failing", "regression", "broken"),
        "doctrine": "Inspect the failing surface, make the smallest code change, run focused verification, and record evidence.",
    },
    "debugging": {
        "id": "debugging",
        "schema": WORKER_TEMPLATE_SCHEMA,
        "role": "debug",
        "keywords": ("debug", "diagnose", "trace", "root cause", "why"),
        "doctrine": "Reproduce the symptom, isolate the cause, change only what the diagnosis supports, then verify.",
    },
    "refactor": {
        "id": "refactor",
        "schema": WORKER_TEMPLATE_SCHEMA,
        "role": "refactor",
        "keywords": ("refactor", "extract", "split", "simplify", "clean"),
        "doctrine": "Preserve behavior, move code surgically, and run parity checks that prove no regression.",
    },
    "test-writer": {
        "id": "test-writer",
        "schema": WORKER_TEMPLATE_SCHEMA,
        "role": "test",
        "keywords": ("test", "coverage", "regression", "eval", "prove"),
        "doctrine": "Write the failing proof first, then make or keep implementation green with focused verification.",
    },
    "review": {
        "id": "review",
        "schema": WORKER_TEMPLATE_SCHEMA,
        "role": "review",
        "keywords": ("review", "audit", "diff", "risk", "merge"),
        "doctrine": "Inspect the diff, commands, and evidence; classify concrete risks before summaries.",
    },
}


def route_worker_template(intent: str) -> dict[str, Any]:
    if not isinstance(intent, str) or not intent.strip():
        raise WorkerTemplateError("intent must be a non-empty string")
    lowered = intent.lower()
    scores = []
    for template in TEMPLATES.values():
        score = sum(1 for keyword in template["keywords"] if keyword in lowered)
        scores.append((score, template["id"]))
    scores.sort(key=lambda item: (-item[0], item[1]))
    selected = TEMPLATES[scores[0][1]] if scores and scores[0][0] else TEMPLATES["coding-repair"]
    return dict(selected)


def build_worker_job_from_template(
    *,
    template_id: str,
    job_id: str,
    goal: str,
    actions: list[dict[str, Any]],
    verify_commands: list[str],
) -> dict[str, Any]:
    template = TEMPLATES.get(template_id)
    if not template:
        raise WorkerTemplateError(f"unknown worker template: {template_id}")
    plan = "\n".join(
        [
            f"Hydra worker template: {template['id']}",
            f"Role: {template['role']}",
            f"Goal: {goal}",
            "",
            template["doctrine"],
            "Verification is required before the worker output can be accepted.",
        ]
    )
    packet = build_worker_job_packet(
        job_id=job_id,
        goal=goal,
        plan=plan,
        actions=actions,
        verify_commands=verify_commands,
    )
    packet["skill_template"] = {
        "schema": template["schema"],
        "id": template["id"],
        "role": template["role"],
        "doctrine": template["doctrine"],
    }
    return packet
