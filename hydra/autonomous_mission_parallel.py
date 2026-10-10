"""Autonomous mission loop V3 with bounded concurrent workers.

Uses ThreadPoolExecutor to run multiple worker batches in parallel.
Cloud plans, parallel workers execute the work.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from hydra.emergency_fallback import classify_provider_error
from hydra.autonomous_mission import carry_mission_context, compose_multiturn_goal, review_issues
from hydra.llm import LlmError
from hydra.worker_jobs import WORKER_BATCH_RESULT_SCHEMA, _normalize_batch, _run_dir, run_worker_batch
from hydra.worker_plans import build_worker_batch_from_plan
from hydra.worker_review import review_worker_run
from hydra.cli.tool_binding import bind_tools
from hydra.loop import Tool as _Tool  # noqa: F401 — type alias used below


def mission_tools(root: Path, approval_policy: str = "ask") -> list[_Tool]:
    """Return the full tool set bound to *root* for use in parallel mission loops.

    approval_policy='ask' reuses Gate 1 — no new gate introduced.
    """
    return bind_tools(root, approval_policy=approval_policy)


AUTONOMOUS_MISSION_SCHEMA = "hydra.autonomous_mission.v3"


class AutonomousMissionError(Exception):
    """Autonomous mission setup or execution failure."""


def run_parallel_worker_batch(
    batch: dict,
    repo_root: Path,
    evidence_root: Path,
    max_workers: int = 4,
) -> dict[str, Any]:
    """Execute the native jobs contract and persist a reviewable aggregate.

    Commands, repairs, patches and overlapping writes retain plan order: their
    file dependencies cannot safely be inferred from the action declarations.
    """
    if not isinstance(max_workers, int) or isinstance(max_workers, bool) or max_workers < 1:
        raise AutonomousMissionError("max_workers must be an integer >= 1")
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise AutonomousMissionError(f"repo_root is not a directory: {root}")
    normalized = _normalize_batch(batch)
    workers = normalized["jobs"]
    run_dir = _run_dir(root, evidence_root, normalized["batch_id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "batch.json").write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    (run_dir / "plan.md").write_text(normalized["goal"] + "\n", encoding="utf-8")
    can_parallelize = _independent_writes(workers, root)
    packets = [
        dict(normalized, batch_id=f"{normalized['batch_id']}-worker-{index}", jobs=[job])
        for index, job in enumerate(workers)
    ] if can_parallelize else [normalized]
    results: list[dict[str, Any] | None] = [None] * len(packets)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                run_worker_batch, packet, repo_root=root,
                evidence_root=run_dir / "workers" / str(index),
            ): index
            for index, packet in enumerate(packets)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as e:
                # Persist the exception as failed batch evidence so the same
                # reviewer can inspect every outcome, including provider loss.
                failed_dir = run_dir / "workers" / str(index) / "failed"
                failed_dir.mkdir(parents=True, exist_ok=True)
                failure = {
                    "schema": WORKER_BATCH_RESULT_SCHEMA,
                    "batch_id": packets[index]["batch_id"],
                    "run_dir": str(failed_dir), "jobs": [],
                    "error": str(e), "failure_reason": str(e), "status": "failed",
                }
                if isinstance(e, LlmError):
                    failure.update(error_class=classify_provider_error(e), provider_fallback=True)
                (failed_dir / "result.json").write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
                results[index] = failure

    all_passed = all(r.get("status") == "passed" for r in results)
    outcome = {
        "schema": WORKER_BATCH_RESULT_SCHEMA,
        "batch_id": normalized["batch_id"],
        "run_dir": str(run_dir),
        "status": "passed" if all_passed else "failed",
        # Native batch review recursively reviews every child batch.
        "jobs": results,
        "parallel_results": results,
        "workers_completed": sum(
            len(packet["jobs"]) if result["status"] == "passed"
            else sum(job.get("status") == "passed" for job in result.get("jobs", []))
            for packet, result in zip(packets, results)
        ),
        "workers_total": len(workers),
        "parallel": can_parallelize and len(packets) > 1 and max_workers > 1,
    }
    if not all_passed:
        outcome["failure_reason"] = "; ".join(
            result.get("failure_reason") or f"worker batch failed: {packet['batch_id']}"
            for packet, result in zip(packets, results) if result["status"] != "passed"
        )
    (run_dir / "result.json").write_text(json.dumps(outcome, indent=2) + "\n", encoding="utf-8")
    return outcome


def _independent_writes(jobs: list[dict[str, Any]], root: Path) -> bool:
    claimed: set[Path] = set()
    for job in jobs:
        if job.get("verify_commands") or job.get("auto_fix") or job.get("resolution_spec"):
            return False
        paths: set[Path] = set()
        for action in job["actions"]:
            if not isinstance(action, dict) or action.get("kind") not in {"write_text", "replace_text"}:
                return False
            raw_path = action.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                return False
            paths.add((root / raw_path).resolve())
        if any(path.is_relative_to(other) or other.is_relative_to(path)
               for path in paths for other in claimed):
            return False
        claimed.update(paths)
    return True


def run_autonomous_mission_v3(
    *,
    mission_id: str,
    prompt: str,
    plan_path: Path,
    repo_root: Path,
    evidence_root: Path | None = None,
    max_cycles: int = 10,
    auto_repair: bool = True,
    parallel_workers: int = 4,
) -> dict[str, Any]:
    """Autonomous mission with PARALLEL execution + aggressive auto-repair.
    
    Loops until verdict=accepted or max_cycles hit. No gates.
    Runs independent workers in parallel when their declared actions permit it.
    """
    if not isinstance(max_cycles, int) or isinstance(max_cycles, bool) or max_cycles < 1:
        raise AutonomousMissionError("max_cycles must be an integer >= 1")
    if not isinstance(parallel_workers, int) or isinstance(parallel_workers, bool) or parallel_workers < 1:
        raise AutonomousMissionError("parallel_workers must be an integer >= 1")
    if not prompt.strip():
        raise AutonomousMissionError("prompt must be a non-empty string")
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise AutonomousMissionError(f"repo_root is not a directory: {root}")
    run_dir = _mission_dir(root, evidence_root, mission_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
    
    # Aggressive parallel loop: execute → review → repair until accepted
    attempts: list[dict[str, Any]] = []
    for cycle in range(1, max_cycles + 1):
        cycle_goal = compose_multiturn_goal(prompt, attempts)
        batch = build_worker_batch_from_plan(
            plan_path, batch_id=f"{mission_id}-batch-c{cycle}",
            goal=cycle_goal,
        )
        carry_mission_context(batch, cycle_goal)
        if attempts:
            batch["context"] = {"prior_attempts": list(attempts), "repair_cycle": cycle}
        
        # Execute independent declared writes concurrently, bounded by workers.
        worker_result = run_parallel_worker_batch(
            batch,
            repo_root=root,
            evidence_root=run_dir / f"worker_c{cycle}",
            max_workers=parallel_workers,
        )
        
        (run_dir / f"worker_batch_c{cycle}.json").write_text(json.dumps(batch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        
        review = review_worker_run(Path(worker_result["run_dir"]))
        verdict = "accepted" if worker_result["status"] == "passed" and review["verdict"] == "accepted" else "rejected"
        attempts.append({
            "cycle": cycle, "verdict": verdict, "issues": review_issues(review),
            "failure_reason": worker_result.get("failure_reason"),
        })
        
        if verdict == "accepted" or not auto_repair:
            break
        
    
    result = {
        "schema": AUTONOMOUS_MISSION_SCHEMA,
        "mission_id": mission_id,
        "prompt": prompt,
        "verdict": verdict,
        "cycles": cycle,
        "parallel": worker_result.get("parallel", False),
        "parallel_workers": parallel_workers,
        "run_dir": str(run_dir),
        "prompt_path": str(run_dir / "prompt.md"),
        "batch_path": str(run_dir / f"worker_batch_c{cycle}.json"),
        "worker_result": worker_result,
        "review": review,
        "trajectory": attempts,
        "next_action": "stop" if verdict == "accepted" else "operator-or-planner-repair",
    }
    (run_dir / "worker_result.json").write_text(json.dumps(worker_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "review.json").write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _mission_dir(root: Path, evidence_root: Path | None, mission_id: str) -> Path:
    """Compute mission run directory."""
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in mission_id).strip(".-")
    if not safe_id:
        raise AutonomousMissionError("mission_id does not contain a safe path segment")
    if evidence_root:
        return evidence_root.expanduser().resolve() / "missions" / safe_id
    return root / "evidence" / "missions" / safe_id
