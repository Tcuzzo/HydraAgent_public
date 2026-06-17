"""Autonomous mission loop V2 backed by worker batches and review gates."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.worker_jobs import run_worker_batch
from hydra.worker_plans import build_worker_batch_from_plan
from hydra.worker_review import review_worker_run


AUTONOMOUS_MISSION_SCHEMA = "hydra.autonomous_mission.v2"


class AutonomousMissionError(Exception):
    """Autonomous mission setup or execution failure."""


# S9: bound how many prior attempts fold into the goal so a long repair loop
# can't grow the prompt without limit (context-window guard, build guide §S9).
MAX_GOAL_ATTEMPTS = 12


def compose_multiturn_goal(
    base_goal: str, attempts: list[dict[str, Any]], *, max_attempts: int = MAX_GOAL_ATTEMPTS
) -> str:
    """Fold the prior-attempt trajectory into the mission goal.

    The native runtime invariant (``the native loop``) is that every
    iteration receives the original mission PLUS prior decisions, evaluations,
    and review findings. The worker batch drops any extra ``context`` key
    during normalization, but the ``goal`` survives and feeds both ``plan.md``
    and ``build_worker_memory_context``. So the goal is the carrier for
    multiturn memory: each cycle's goal is the base mission plus a record of
    prior cycles' verdicts and *all* of their findings (never just the first).

    S9 guard: only the most-recent ``max_attempts`` cycles are folded in; older
    ones are summarized as an elision note so the goal can't explode the context
    window on a long repair loop.
    """
    base = base_goal.strip()
    if not attempts:
        return base
    lines = [
        base,
        "",
        "=== PRIOR ATTEMPTS (multiturn memory — do not repeat these failures) ===",
    ]
    kept = attempts
    if max_attempts and len(attempts) > max_attempts:
        elided = len(attempts) - max_attempts
        kept = attempts[-max_attempts:]
        lines.append(
            f"({elided} earlier attempt(s) elided to bound context; showing the most recent {max_attempts}.)"
        )
    for attempt in kept:
        cycle = attempt.get("cycle")
        verdict = attempt.get("verdict", "unknown")
        lines.append(f"- cycle {cycle}: {verdict}")
        for issue in attempt.get("issues") or []:
            lines.append(f"    • {issue}")
        failure_reason = attempt.get("failure_reason")
        if failure_reason:
            lines.append(f"    • failure: {failure_reason}")
    lines.append("=== END PRIOR ATTEMPTS ===")
    return "\n".join(lines)


def run_autonomous_mission_v2(
    *,
    mission_id: str,
    prompt: str,
    plan_path: Path,
    repo_root: Path,
    evidence_root: Path | None = None,
    max_cycles: int = 10,
    auto_repair: bool = True,
) -> dict[str, Any]:
    """Autonomous mission with aggressive auto-repair loop.
    
    Loops until verdict=accepted or max_cycles hit. No gates.
    """
    if not prompt.strip():
        raise AutonomousMissionError("prompt must be a non-empty string")
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise AutonomousMissionError(f"repo_root is not a directory: {root}")
    run_dir = _mission_dir(root, evidence_root, mission_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
    
    # Multiturn repair loop: execute → review → carry the FULL trajectory
    # forward → repair until accepted. Each cycle's goal accumulates every
    # prior cycle's findings so the worker (and its memory context) sees the
    # whole history, not a single overwritten error string.
    attempts: list[dict[str, Any]] = []
    for cycle in range(1, max_cycles + 1):
        cycle_goal = compose_multiturn_goal(prompt, attempts)
        batch = build_worker_batch_from_plan(plan_path, batch_id=f"{mission_id}-batch-c{cycle}", goal=cycle_goal)
        if attempts:
            # Structured trajectory is also stamped on the batch artifact for
            # review. (Normalization drops it before the worker, which is why
            # the goal above is the real multiturn carrier.)
            batch["context"] = {"prior_attempts": attempts, "repair_cycle": cycle}
        (run_dir / f"worker_batch_c{cycle}.json").write_text(json.dumps(batch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        worker_result = run_worker_batch(batch, repo_root=root, evidence_root=run_dir / f"worker_c{cycle}")
        review = review_worker_run(Path(worker_result["run_dir"]))
        verdict = "accepted" if worker_result["status"] == "passed" and review["verdict"] == "accepted" else "rejected"

        # Record this cycle's full result — ALL issues, not just the first.
        attempts.append(
            {
                "cycle": cycle,
                "verdict": verdict,
                "issues": [str(issue) for issue in (review.get("issues") or [])],
                "failure_reason": worker_result.get("failure_reason"),
            }
        )

        if verdict == "accepted" or not auto_repair:
            break

    result = {
        "schema": AUTONOMOUS_MISSION_SCHEMA,
        "mission_id": mission_id,
        "prompt": prompt,
        "verdict": verdict,
        "cycles": cycle,
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
    (run_dir / "trajectory.json").write_text(json.dumps(attempts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def render_autonomous_mission_text(result: dict[str, Any]) -> str:
    return (
        f"Hydra autonomous mission: {result['mission_id']}\n"
        f"verdict: {result['verdict']}\n"
        f"cycles: {result['cycles']}\n"
        f"run_dir: {result['run_dir']}\n"
        f"next_action: {result['next_action']}\n"
    )


def _mission_dir(root: Path, evidence_root: Path | None, mission_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in mission_id).strip(".-")
    if not safe_id:
        raise AutonomousMissionError("mission_id does not contain a safe path segment")
    base = evidence_root.expanduser().resolve() if evidence_root else root / "evidence" / "autonomous-missions"
    return base / safe_id
