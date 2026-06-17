"""Worker plan parsers that produce Hydra worker batch packets."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hydra.worker_jobs import WORKER_BATCH_SCHEMA, build_worker_job_packet


class WorkerPlanError(Exception):
    """Worker plan parse or validation failure."""


def build_worker_batch_from_plan(
    plan_path: Path,
    *,
    batch_id: str,
    goal: str | None = None,
) -> dict[str, Any]:
    path = plan_path.expanduser().resolve()
    if not path.is_file():
        raise WorkerPlanError(f"plan file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        parsed = _parse_json_plan(text)
    else:
        parsed = _parse_markdown_plan(text)
    batch_goal = goal or parsed["goal"]
    if not batch_goal.strip():
        raise WorkerPlanError("batch goal must be non-empty")
    return {
        "schema": WORKER_BATCH_SCHEMA,
        "batch_id": batch_id,
        "goal": batch_goal.strip(),
        "jobs": [
            build_worker_job_packet(
                job_id=step["job_id"],
                goal=step["goal"],
                plan=step.get("plan"),
                actions=step.get("actions", []),
                verify_commands=step.get("verify_commands", []),
            )
            for step in parsed["steps"]
        ],
    }


def _parse_json_plan(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise WorkerPlanError(f"invalid JSON plan: {e}") from e
    if not isinstance(data, dict):
        raise WorkerPlanError("JSON plan must be an object")
    if data.get("schema") == WORKER_BATCH_SCHEMA and isinstance(data.get("jobs"), list):
        return {"goal": _required_str(data, "goal"), "steps": data["jobs"]}
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise WorkerPlanError("JSON plan requires non-empty steps")
    return {"goal": _required_str(data, "goal"), "steps": steps}


def _parse_markdown_plan(text: str) -> dict[str, Any]:
    blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if not blocks:
        raise WorkerPlanError("Markdown plan requires at least one fenced json block")
    steps = []
    for block in blocks:
        try:
            step = json.loads(block)
        except json.JSONDecodeError as e:
            raise WorkerPlanError(f"invalid fenced json worker step: {e}") from e
        if not isinstance(step, dict):
            raise WorkerPlanError("fenced json worker step must be an object")
        steps.append(step)
    goal = _markdown_h1(text) or "Hydra worker batch"
    return {"goal": goal, "steps": steps}


def _markdown_h1(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and stripped[2:].strip():
            return stripped[2:].strip()
    return None


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkerPlanError(f"{key} must be a non-empty string")
    return value.strip()
