"""Bounded parallel subagent dispatch primitive for HydraAgent.

This is THE runtime primitive for concurrent task execution:
- dispatch(): Run tasks concurrently under bounded concurrency
- dispatch_graph(): Run tasks with dependency-aware phasing

Used by:
- workflow.py (adds rubric judging on top)
- mission_loop.py (safe command execution)
- continuation.py (verification command execution)

Doctrine:
- Concurrency is bounded — peak active workers never exceed ``max_concurrency``.
- Each subagent is isolated to its own subprocess and working directory.
- Timeouts are enforced via the process group (SIGKILL on the whole group).
- Outputs are bounded so a chatty subagent cannot blow the orchestrator's memory.
- No silent gates: invalid input raises :class:`OrchestrateError` with named reason.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from threading import Lock
from typing import Any

from hydra.proc import kill_tree, popen_portable


SCHEMA = "hydra.orchestrate.v1"
MAX_OUTPUT_BYTES = 64 * 1024
DEFAULT_MAX_CONCURRENCY = 4
DEFAULT_TIMEOUT_SECONDS = 60


class OrchestrateError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class SubagentTask:
    id: str
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    success_pattern: str | None = None
    depends_on: list[str] = field(default_factory=list)


def dispatch(
    tasks: list[SubagentTask],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> dict[str, Any]:
    """Run ``tasks`` concurrently under a bound and return an aggregate report."""
    _validate(tasks, max_concurrency)

    peak_active = 0
    active = 0
    lock = Lock()
    started_at = time.time()

    def runner(task: SubagentTask) -> dict[str, Any]:
        nonlocal peak_active, active
        with lock:
            active += 1
            if active > peak_active:
                peak_active = active
        try:
            return _run_one(task)
        finally:
            with lock:
                active -= 1

    results: list[dict[str, Any] | None] = [None] * len(tasks)
    index_by_id = {task.id: i for i, task in enumerate(tasks)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = {pool.submit(runner, t): t for t in tasks}
        for fut in concurrent.futures.as_completed(futures):
            task = futures[fut]
            try:
                row = fut.result()
            except Exception as e:  # noqa: BLE001
                row = {
                    "id": task.id,
                    "command": list(task.command),
                    "status": "error",
                    "returncode": None,
                    "stdout": "",
                    "stderr": f"[orchestrator error: {type(e).__name__}: {e}]",
                    "stdout_bytes": 0,
                    "stderr_bytes": 0,
                    "duration_ms": 0,
                    "success_pattern_matched": None,
                    "timeout_seconds": task.timeout_seconds,
                }
            results[index_by_id[task.id]] = row

    finished_at = time.time()
    final_results = [r for r in results if r is not None]
    by_status = _count_by_status(final_results)

    return {
        "schema": SCHEMA,
        "max_concurrency": max_concurrency,
        "peak_active": peak_active,
        "wall_seconds": round(finished_at - started_at, 3),
        "task_count": len(final_results),
        "by_status": by_status,
        "succeeded": sum(1 for r in final_results if r["status"] == "ok"),
        "failed": sum(1 for r in final_results if r["status"] in {"failed", "error"}),
        "timed_out": sum(1 for r in final_results if r["status"] == "timeout"),
        "results": final_results,
        "policy": (
            "bounded concurrency via ThreadPoolExecutor; per-task timeout enforced "
            "by killpg on the subprocess process group; outputs bounded"
        ),
    }


def dispatch_graph(
    tasks: list[SubagentTask],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> dict[str, Any]:
    """Run a dependency-aware subagent graph in phases."""
    _validate(tasks, max_concurrency)
    tasks_by_id = {task.id: task for task in tasks}
    for task in tasks:
        for dependency in task.depends_on:
            if dependency not in tasks_by_id:
                raise OrchestrateError(f"task {task.id!r}: unknown dependency {dependency!r}")
    phases: list[dict[str, Any]] = []
    completed: set[str] = set()
    failed_or_blocked: set[str] = set()
    remaining = set(tasks_by_id)
    started_at = time.time()
    all_results: list[dict[str, Any]] = []
    while remaining:
        ready_ids = sorted(
            task_id
            for task_id in remaining
            if all(dependency in completed for dependency in tasks_by_id[task_id].depends_on)
        )
        if not ready_ids:
            blocked_ids = sorted(remaining)
            blocked_results = [
                _blocked_result(tasks_by_id[task_id], "dependency cycle or failed dependency")
                for task_id in blocked_ids
            ]
            all_results.extend(blocked_results)
            phases.append({"index": len(phases) + 1, "task_ids": blocked_ids, "report": None, "blocked": blocked_results})
            failed_or_blocked.update(blocked_ids)
            break
        ready_tasks = [tasks_by_id[task_id] for task_id in ready_ids]
        phase_report = dispatch(ready_tasks, max_concurrency=max_concurrency)
        phases.append({"index": len(phases) + 1, "task_ids": ready_ids, "report": phase_report})
        all_results.extend(phase_report["results"])
        for result in phase_report["results"]:
            if result["status"] == "ok":
                completed.add(result["id"])
            else:
                failed_or_blocked.add(result["id"])
        remaining.difference_update(ready_ids)
        blocked_by_failed = sorted(
            task_id
            for task_id in remaining
            if any(dependency in failed_or_blocked for dependency in tasks_by_id[task_id].depends_on)
        )
        if blocked_by_failed:
            blocked_results = [
                _blocked_result(tasks_by_id[task_id], "dependency failed")
                for task_id in blocked_by_failed
            ]
            all_results.extend(blocked_results)
            phases.append({"index": len(phases) + 1, "task_ids": blocked_by_failed, "report": None, "blocked": blocked_results})
            failed_or_blocked.update(blocked_by_failed)
            remaining.difference_update(blocked_by_failed)
    by_status = _count_by_status(all_results)
    failed = sum(1 for result in all_results if result["status"] in {"failed", "error", "blocked"})
    timed_out = sum(1 for result in all_results if result["status"] == "timeout")
    return {
        "schema": "hydra.orchestrate.graph.v1",
        "verdict": "GREEN" if failed == 0 and timed_out == 0 else "RED",
        "task_count": len(all_results),
        "phase_count": len(phases),
        "max_concurrency": max_concurrency,
        "wall_seconds": round(time.time() - started_at, 3),
        "succeeded": sum(1 for result in all_results if result["status"] == "ok"),
        "failed": failed,
        "timed_out": timed_out,
        "by_status": by_status,
        "phases": phases,
        "results": all_results,
        "policy": "dependency-aware phased subagent dispatch; dependents block on failed prerequisites",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra subagent dispatch: {report['task_count']} tasks",
        f"max_concurrency: {report['max_concurrency']}  peak_active: {report['peak_active']}",
        f"wall_seconds: {report['wall_seconds']}",
        f"by_status: " + ", ".join(f"{k}={v}" for k, v in sorted(report['by_status'].items())),
        "results:",
    ]
    for row in report["results"]:
        lines.append(
            f"  - [{row['status']}] {row['id']} rc={row['returncode']} "
            f"duration_ms={row['duration_ms']} "
            f"pattern={row['success_pattern_matched']}"
        )
    return "\n".join(lines) + "\n"


def render_graph_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra subagent graph: {report['task_count']} tasks",
        f"verdict: {report['verdict']}  phases: {report['phase_count']}",
        f"max_concurrency: {report['max_concurrency']}",
        f"by_status: " + ", ".join(f"{k}={v}" for k, v in sorted(report['by_status'].items())),
        "phases:",
    ]
    for phase in report["phases"]:
        lines.append(f"  - phase {phase['index']}: {', '.join(phase['task_ids'])}")
    return "\n".join(lines) + "\n"


def _validate(tasks: list[SubagentTask], max_concurrency: int) -> None:
    if not isinstance(tasks, list) or not tasks:
        raise OrchestrateError("tasks must be a non-empty list of SubagentTask")
    if max_concurrency <= 0:
        raise OrchestrateError("max_concurrency must be a positive integer")
    seen_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, SubagentTask):
            raise OrchestrateError("every entry must be a SubagentTask instance")
        if not task.id or not isinstance(task.id, str):
            raise OrchestrateError("subagent task id must be a non-empty string")
        if task.id in seen_ids:
            raise OrchestrateError(f"duplicate subagent task id: {task.id!r}")
        seen_ids.add(task.id)
        if not isinstance(task.command, list) or not task.command:
            raise OrchestrateError(f"task {task.id!r}: command must be a non-empty list")
        if not all(isinstance(part, str) for part in task.command):
            raise OrchestrateError(f"task {task.id!r}: every command part must be a string")
        if not isinstance(task.timeout_seconds, int) or task.timeout_seconds <= 0:
            raise OrchestrateError(
                f"task {task.id!r}: timeout_seconds must be a positive integer"
            )
        if task.success_pattern is not None:
            try:
                re.compile(task.success_pattern)
            except re.error as e:
                raise OrchestrateError(
                    f"task {task.id!r}: invalid success_pattern regex: {e}"
                ) from e
        if not isinstance(task.depends_on, list) or not all(isinstance(item, str) and item.strip() for item in task.depends_on):
            raise OrchestrateError(f"task {task.id!r}: depends_on must be a list of task ids")


def _run_one(task: SubagentTask) -> dict[str, Any]:
    started = time.time()
    popen_env = None
    if task.env is not None:
        popen_env = {**os.environ, **task.env}
    try:
        proc = popen_portable(
            task.command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=task.cwd,
            env=popen_env,
            text=True,
        )
    except FileNotFoundError as e:
        return _result(
            task,
            status="error",
            returncode=None,
            stdout="",
            stderr=f"[command not found: {e.filename}]",
            stdout_bytes=0,
            stderr_bytes=0,
            started_at=started,
            success_pattern_matched=None,
        )
    try:
        stdout, stderr = proc.communicate(timeout=task.timeout_seconds)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        stdout, stderr = proc.communicate()
        stdout_bytes = len((stdout or "").encode("utf-8", errors="replace"))
        stderr_bytes = len((stderr or "").encode("utf-8", errors="replace"))
        return _result(
            task,
            status="timeout",
            returncode=None,
            stdout=_bound(stdout or ""),
            stderr=_bound((stderr or "") + f"\n[killed after {task.timeout_seconds}s timeout]"),
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            started_at=started,
            success_pattern_matched=None,
        )

    stdout_bytes = len((stdout or "").encode("utf-8", errors="replace"))
    stderr_bytes = len((stderr or "").encode("utf-8", errors="replace"))
    pattern_matched: bool | None = None
    if task.success_pattern is not None:
        pattern_matched = bool(re.search(task.success_pattern, stdout or ""))
    if proc.returncode == 0 and (task.success_pattern is None or pattern_matched):
        status = "ok"
    else:
        status = "failed"
    return _result(
        task,
        status=status,
        returncode=proc.returncode,
        stdout=_bound(stdout or ""),
        stderr=_bound(stderr or ""),
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        started_at=started,
        success_pattern_matched=pattern_matched,
    )


def _result(
    task: SubagentTask,
    *,
    status: str,
    returncode: int | None,
    stdout: str,
    stderr: str,
    stdout_bytes: int,
    stderr_bytes: int,
    started_at: float,
    success_pattern_matched: bool | None,
) -> dict[str, Any]:
    return {
        "id": task.id,
        "command": list(task.command),
        "status": status,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "duration_ms": int((time.time() - started_at) * 1000),
        "success_pattern_matched": success_pattern_matched,
        "timeout_seconds": task.timeout_seconds,
    }


def _blocked_result(task: SubagentTask, reason: str) -> dict[str, Any]:
    return {
        "id": task.id,
        "command": list(task.command),
        "status": "blocked",
        "returncode": None,
        "stdout": "",
        "stderr": reason,
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "duration_ms": 0,
        "success_pattern_matched": None,
        "timeout_seconds": task.timeout_seconds,
    }


def _bound(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return text
    return encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n[truncated]"


def _count_by_status(results: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in results:
        out[row["status"]] = out.get(row["status"], 0) + 1
    return dict(sorted(out.items()))


def task_from_dict(data: dict[str, Any]) -> SubagentTask:
    """Construct a :class:`SubagentTask` from a JSON-shaped dict (e.g. from CLI)."""
    if not isinstance(data, dict):
        raise OrchestrateError("task spec must be a JSON object")
    return SubagentTask(
        id=data.get("id", ""),
        command=list(data.get("command", [])),
        cwd=data.get("cwd"),
        env=data.get("env"),
        timeout_seconds=int(data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
        success_pattern=data.get("success_pattern"),
        depends_on=list(data.get("depends_on", [])),
    )
