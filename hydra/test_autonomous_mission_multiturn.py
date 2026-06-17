"""Multiturn build behavior for the autonomous mission loop.

The native runtime invariant (the native loop RuntimePrompt) requires
that *every* iteration receives the original mission PLUS prior decisions,
evaluations, and review findings. Before this, the repair loop discarded all
cross-cycle context except the single first issue string, and even that was
dropped by worker-batch normalization. These tests pin the multiturn carry.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydra import autonomous_mission as am
from hydra.autonomous_mission import compose_multiturn_goal, run_autonomous_mission_v2


def test_compose_multiturn_goal_no_attempts_returns_base_goal():
    base = "Build §10.99-example"
    assert compose_multiturn_goal(base, []) == base


def test_compose_multiturn_goal_preserves_original_mission_text():
    base = "Fix the agent multiturn loop"
    composed = compose_multiturn_goal(
        base,
        [{"cycle": 1, "verdict": "rejected", "issues": ["tests failed"]}],
    )
    assert composed.startswith(base)


def test_compose_multiturn_goal_carries_every_issue_from_every_cycle():
    base = "Build mission"
    attempts = [
        {"cycle": 1, "verdict": "rejected", "issues": ["import error", "lint failed"]},
        {"cycle": 2, "verdict": "rejected", "issues": ["assertion mismatch"]},
    ]
    composed = compose_multiturn_goal(base, attempts)
    # Multiturn memory means NONE of the prior findings are dropped — not just issues[0].
    for needle in ("import error", "lint failed", "assertion mismatch"):
        assert needle in composed, f"prior finding {needle!r} was dropped from multiturn goal"
    assert "cycle 1" in composed.lower()
    assert "cycle 2" in composed.lower()


def test_run_autonomous_mission_threads_full_trajectory_into_each_cycle(tmp_path, monkeypatch):
    """Cycle N's worker batch goal must contain the findings of cycles 1..N-1."""
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps({"goal": "Build X", "steps": ["fix"]}), encoding="utf-8")

    seen_goals: list[str] = []

    def fake_build_batch(plan, *, batch_id, goal=None):
        seen_goals.append(goal or "")
        return {"batch_id": batch_id, "goal": goal or "Build X", "jobs": [{"job_id": "j"}]}

    # Reject for the first two cycles (distinct findings), accept on the third.
    cycle_box = {"n": 0}

    def fake_run_batch(batch, *, repo_root, evidence_root):
        cycle_box["n"] += 1
        Path(evidence_root).mkdir(parents=True, exist_ok=True)
        status = "passed" if cycle_box["n"] >= 3 else "failed"
        return {"status": status, "run_dir": str(evidence_root)}

    def fake_review(run_dir):
        n = cycle_box["n"]
        if n >= 3:
            return {"verdict": "accepted", "issues": []}
        return {"verdict": "rejected", "issues": [f"failure-from-cycle-{n}"]}

    monkeypatch.setattr(am, "build_worker_batch_from_plan", fake_build_batch)
    monkeypatch.setattr(am, "run_worker_batch", fake_run_batch)
    monkeypatch.setattr(am, "review_worker_run", fake_review)

    result = run_autonomous_mission_v2(
        mission_id="m-multiturn",
        prompt="Build X",
        plan_path=plan_path,
        repo_root=tmp_path,
        evidence_root=tmp_path / "evidence",
        max_cycles=5,
        auto_repair=True,
    )

    assert result["verdict"] == "accepted"
    assert result["cycles"] == 3
    # Cycle 1 goal: just the base mission.
    assert "failure-from-cycle" not in seen_goals[0]
    # Cycle 2 goal: carries cycle-1 finding.
    assert "failure-from-cycle-1" in seen_goals[1]
    # Cycle 3 goal: carries BOTH prior findings (true multiturn memory).
    assert "failure-from-cycle-1" in seen_goals[2]
    assert "failure-from-cycle-2" in seen_goals[2]
