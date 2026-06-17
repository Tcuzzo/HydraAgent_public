"""Deterministic quality checks for planner-produced worker packets."""
from __future__ import annotations

import re
from typing import Any

from hydra.worker_jobs import WORKER_BATCH_SCHEMA, WORKER_JOB_SCHEMA


PLANNER_QUALITY_SCHEMA = "hydra.planner_quality.v1"

_MULTI_FILE_HINT = re.compile(
    r"\b(?:multi[- ]file|across\s+files|refactor|together|implementation\s+and\s+tests?|tests?\s+and\s+implementation)\b",
    re.IGNORECASE,
)
_PATH_HINT = re.compile(r"\b[\w./-]+\.(?:py|ts|tsx|js|jsx|rs|go|md|json|yaml|yml|toml)\b")


def evaluate_planner_packet_quality(prompt: str, packet: dict[str, Any]) -> dict[str, Any]:
    """Return an accept/reject packet for planner output before worker execution."""
    missing: list[str] = []
    warnings: list[str] = []
    prompt_scope = _prompt_scope(prompt)
    schema = packet.get("schema") if isinstance(packet, dict) else None
    jobs = _packet_jobs(packet)

    if schema not in {WORKER_JOB_SCHEMA, WORKER_BATCH_SCHEMA}:
        missing.append("supported-worker-schema-required")
    if prompt_scope["multi_file"] and schema != WORKER_BATCH_SCHEMA:
        missing.append("batch-required-for-multi-file-prompt")
    if not jobs:
        missing.append("at-least-one-worker-job-required")

    action_paths: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            missing.append("worker-jobs-must-be-objects")
            continue
        actions = job.get("actions")
        verify_commands = job.get("verify_commands")
        if not isinstance(actions, list) or not actions:
            missing.append("actions-required")
        else:
            action_paths.update(_action_paths(actions))
        if not isinstance(verify_commands, list) or not any(isinstance(command, str) and command.strip() for command in verify_commands):
            missing.append("verify-commands-required")

    if prompt_scope["mentioned_files"]:
        missing_files = sorted(set(prompt_scope["mentioned_files"]) - action_paths)
        if missing_files:
            missing.append("mentioned-files-must-be-covered")
            warnings.append("uncovered mentioned files: " + ", ".join(missing_files))

    unique_missing = sorted(set(missing))
    return {
        "schema": PLANNER_QUALITY_SCHEMA,
        "verdict": "accepted" if not unique_missing else "rejected",
        "missing": unique_missing,
        "warnings": warnings,
        "prompt_scope": prompt_scope,
        "jobs_count": len(jobs),
        "policy": "deterministic packet quality gate before worker execution",
    }


def _prompt_scope(prompt: str) -> dict[str, Any]:
    files = sorted(set(_PATH_HINT.findall(prompt or "")))
    multi_file = len(files) >= 2 or bool(_MULTI_FILE_HINT.search(prompt or ""))
    return {
        "multi_file": multi_file,
        "mentioned_files": files,
    }


def _packet_jobs(packet: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(packet, dict):
        return []
    if packet.get("schema") == WORKER_BATCH_SCHEMA and isinstance(packet.get("jobs"), list):
        return packet["jobs"]
    if packet.get("schema") == WORKER_JOB_SCHEMA:
        return [packet]
    return []


def _action_paths(actions: list[Any]) -> set[str]:
    paths: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        path = action.get("path")
        if isinstance(path, str) and path.strip():
            paths.add(path.strip())
    return paths
