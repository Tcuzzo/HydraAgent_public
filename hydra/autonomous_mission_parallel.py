"""Autonomous mission loop V3 — PARALLEL, AGGRESSIVE, 10x faster.

Uses ThreadPoolExecutor to run multiple worker batches in parallel.
Cloud plans, parallel workers execute the work.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, List

from hydra.emergency_fallback import classify_provider_error
from hydra.llm import LlmError
from hydra.worker_jobs import run_worker_batch
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
    """Run worker batch in parallel with multiple workers."""
    workers = batch.get("workers", [])
    if not workers:
        return run_worker_batch(batch, repo_root=repo_root, evidence_root=evidence_root)
    
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(run_worker_batch, {"workers": [w]}, repo_root=repo_root, evidence_root=evidence_root): w
            for w in workers
        }
        for future in as_completed(futures):
            worker = futures[future]
            try:
                result = future.result()
                results.append(result)
            except LlmError as e:
                # S6 — a provider failure in a parallel worker is classified
                # (auth vs timeout) and surfaced, not collapsed into an opaque
                # "failed". The mission stays alive (no crash).
                results.append({
                    "worker": worker.get("id"),
                    "error": str(e),
                    "error_class": classify_provider_error(e),
                    "provider_fallback": True,
                    "status": "failed",
                })
            except Exception as e:
                results.append({"worker": worker.get("id"), "error": str(e), "status": "failed"})
    
    # Aggregate results
    all_passed = all(r.get("status") == "passed" for r in results)
    return {
        "run_dir": results[0]["run_dir"] if results else str(evidence_root),
        "status": "passed" if all_passed else "failed",
        "parallel_results": results,
        "workers_completed": len([r for r in results if r.get("status") == "passed"]),
        "workers_total": len(workers),
    }


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
    Runs workers in parallel (4x speedup).
    """
    if not prompt.strip():
        raise AutonomousMissionError("prompt must be a non-empty string")
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise AutonomousMissionError(f"repo_root is not a directory: {root}")
    run_dir = _mission_dir(root, evidence_root, mission_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
    
    # Aggressive parallel loop: execute → review → repair until accepted
    last_error = None
    for cycle in range(1, max_cycles + 1):
        batch = build_worker_batch_from_plan(plan_path, batch_id=f"{mission_id}-batch-c{cycle}", goal=prompt)
        if last_error:
            batch["context"] = {"previous_error": last_error, "repair_cycle": cycle}
        
        # PARALLEL EXECUTION — 4x speedup
        worker_result = run_parallel_worker_batch(
            batch,
            repo_root=root,
            evidence_root=run_dir / f"worker_c{cycle}",
            max_workers=parallel_workers,
        )
        
        (run_dir / f"worker_batch_c{cycle}.json").write_text(json.dumps(batch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        
        review = review_worker_run(Path(worker_result["run_dir"]))
        verdict = "accepted" if worker_result["status"] == "passed" and review["verdict"] == "accepted" else "rejected"
        
        if verdict == "accepted" or not auto_repair:
            break
        
        # Auto-repair: capture error and loop
        last_error = review.get("issues", ["Unknown failure"])[0] if review.get("issues") else "Worker failed"
    
    result = {
        "schema": AUTONOMOUS_MISSION_SCHEMA,
        "mission_id": mission_id,
        "prompt": prompt,
        "verdict": verdict,
        "cycles": cycle,
        "parallel": True,
        "parallel_workers": parallel_workers,
        "run_dir": str(run_dir),
        "prompt_path": str(run_dir / "prompt.md"),
        "batch_path": str(run_dir / f"worker_batch_c{cycle}.json"),
        "worker_result": worker_result,
        "review": review,
        "next_action": "stop" if verdict == "accepted" else "operator-or-planner-repair",
    }
    (run_dir / "worker_result.json").write_text(json.dumps(worker_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "review.json").write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def _mission_dir(root: Path, evidence_root: Path | None, mission_id: str) -> Path:
    """Compute mission run directory."""
    if evidence_root:
        return evidence_root.expanduser().resolve() / "missions" / mission_id
    return root / "evidence" / "missions" / mission_id
