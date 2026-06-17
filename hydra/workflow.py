"""Operator workflow: dispatch + rubric judging primitive.

This is a thin wrapper around orchestrate.dispatch() that adds per-task
rubric judging. Use this when you need:
- Bounded concurrent execution (from orchestrate.py)
- PLUS deterministic pass/fail judging per task (from rubric_judge.py)

Used by:
- CLI ops workflow command
- LLM planner fan-out with success criteria

Not used by:
- mission_loop.py (uses orchestrate.dispatch() directly for safe commands)
- continuation.py (uses orchestrate.dispatch() directly for verification)
"""
from __future__ import annotations

from typing import Any

from hydra.orchestrate import (
    OrchestrateError,
    SubagentTask,
    dispatch,
)
from hydra.rubric_judge import (
    RubricJudgeError,
    judge as rubric_judge,
)


SCHEMA = "hydra.workflow.v1"


class WorkflowError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def run_workflow(
    tasks: list[dict[str, Any]],
    *,
    max_concurrency: int = 4,
    pass_threshold: float = 1.0,
) -> dict[str, Any]:
    """Dispatch tasks and judge each output, returning a unified report.

    Each task dict supports the §10.58 SubagentTask fields (id, command,
    optional cwd/env/timeout_seconds/success_pattern) plus an optional
    ``rubric`` field — a §10.60 list-of-rule-dicts used to judge that
    task's stdout. A task without ``rubric`` is dispatched but not judged.
    """
    if not isinstance(tasks, list) or not tasks:
        raise WorkflowError("tasks must be a non-empty list of task dicts")
    if not (0.0 <= pass_threshold <= 1.0):
        raise WorkflowError("pass_threshold must be in [0.0, 1.0]")

    subagent_tasks: list[SubagentTask] = []
    rubrics: dict[str, list[dict[str, Any]]] = {}
    seen: set[str] = set()
    for raw in tasks:
        if not isinstance(raw, dict):
            raise WorkflowError("each task must be a dict")
        tid = raw.get("id")
        if not isinstance(tid, str) or not tid:
            raise WorkflowError("each task requires a non-empty id")
        if tid in seen:
            raise WorkflowError(f"duplicate task id: {tid!r}")
        seen.add(tid)
        command = raw.get("command")
        if not isinstance(command, list) or not command:
            raise WorkflowError(f"task {tid!r}: command must be a non-empty list")
        timeout_seconds = raw.get("timeout_seconds", 60)
        if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
            raise WorkflowError(f"task {tid!r}: timeout_seconds must be a positive integer")
        rubric = raw.get("rubric")
        if rubric is not None:
            if not isinstance(rubric, list) or not rubric:
                raise WorkflowError(f"task {tid!r}: rubric must be a non-empty list when provided")
            rubrics[tid] = rubric
        subagent_tasks.append(SubagentTask(
            id=tid,
            command=[str(p) for p in command],
            cwd=raw.get("cwd"),
            env=raw.get("env"),
            timeout_seconds=timeout_seconds,
            success_pattern=raw.get("success_pattern"),
        ))

    try:
        dispatch_report = dispatch(subagent_tasks, max_concurrency=max_concurrency)
    except OrchestrateError as e:
        raise WorkflowError(str(e)) from e

    judge_results: list[dict[str, Any]] = []
    for row in dispatch_report["results"]:
        tid = row["id"]
        rubric = rubrics.get(tid)
        if rubric is None:
            continue
        try:
            judge_report = rubric_judge(row["stdout"], rubric, pass_threshold=pass_threshold)
        except RubricJudgeError as e:
            raise WorkflowError(f"task {tid!r}: rubric error: {e}") from e
        judge_results.append({
            "id": tid,
            "verdict": judge_report["verdict"],
            "score": judge_report["score"],
            "violations": judge_report["violations"],
            "rules_total": judge_report["rules_total"],
        })

    judges_pass = sum(1 for j in judge_results if j["verdict"] == "PASS")
    judges_fail = sum(1 for j in judge_results if j["verdict"] != "PASS")
    dispatched_ok = sum(1 for r in dispatch_report["results"] if r["status"] == "ok")
    dispatched_failed = sum(
        1 for r in dispatch_report["results"]
        if r["status"] in {"failed", "error", "timeout"}
    )
    all_pass = dispatched_failed == 0 and judges_fail == 0

    return {
        "schema": SCHEMA,
        "all_pass": all_pass,
        "summary": {
            "tasks": len(subagent_tasks),
            "dispatched_ok": dispatched_ok,
            "dispatched_failed": dispatched_failed,
            "judges_run": len(judge_results),
            "judges_pass": judges_pass,
            "judges_fail": judges_fail,
        },
        "dispatch": dispatch_report,
        "judges": judge_results,
        "pass_threshold": pass_threshold,
        "max_concurrency": max_concurrency,
    }


def render_text(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"Hydra workflow: all_pass={report['all_pass']}",
        f"tasks={s['tasks']}  dispatched_ok={s['dispatched_ok']}  "
        f"dispatched_failed={s['dispatched_failed']}",
        f"judges_run={s['judges_run']}  judges_pass={s['judges_pass']}  "
        f"judges_fail={s['judges_fail']}",
        "tasks:",
    ]
    judge_by_id = {j["id"]: j for j in report["judges"]}
    for row in report["dispatch"]["results"]:
        judge = judge_by_id.get(row["id"])
        judge_part = f" judge={judge['verdict']} score={judge['score']}" if judge else " judge=(none)"
        lines.append(
            f"  - {row['id']} status={row['status']} rc={row['returncode']}{judge_part}"
        )
    return "\n".join(lines) + "\n"
