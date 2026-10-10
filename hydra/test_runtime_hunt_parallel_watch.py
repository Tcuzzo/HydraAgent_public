"""Real batch contracts, mission feedback and watch limits."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Barrier

import pytest

from hydra import autonomous_mission as serial
from hydra import autonomous_mission_parallel as parallel
from hydra.llm import LlmError
from hydra.watch import WatchConfig, WatchError, WatchLoop
from hydra.worker_review import review_worker_run


def batch():
    return {"schema": "hydra.worker_batch.v1", "batch_id": "batch", "goal": "Mission context",
            "jobs": [{"job_id": name, "goal": name, "actions": [], "verify_commands": []}
                     for name in ("first", "second")]}


def child_result(packet, directory, status="passed"):
    directory.mkdir(parents=True, exist_ok=True)
    result = {"schema": "hydra.worker_batch_result.v1", "batch_id": packet["batch_id"],
              "status": status, "run_dir": str(directory), "jobs": []}
    (directory / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return result


def test_parallel_uses_real_job_contract_and_reviews_all_results(tmp_path, monkeypatch):
    barrier = Barrier(2)
    observed = []

    def run(packet, *, repo_root, evidence_root):
        observed.append(packet)
        if len(packet["jobs"]) == 1:
            barrier.wait(timeout=5)
        status = "failed" if packet["jobs"][0]["job_id"] == "second" else "passed"
        return child_result(packet, evidence_root / packet["batch_id"], status)

    monkeypatch.setattr(parallel, "run_worker_batch", run)
    outcome = parallel.run_parallel_worker_batch(batch(), tmp_path, tmp_path / "evidence", max_workers=2)
    assert len(observed) == 2
    assert all(row["goal"] == "Mission context" and len(row["jobs"]) == 1 for row in observed)
    assert outcome["workers_total"] == 2
    assert outcome["workers_completed"] == 1
    assert outcome["status"] == "failed"
    assert review_worker_run(Path(outcome["run_dir"]))["verdict"] == "rejected"


def test_parallel_provider_exception_is_reviewable_failure(tmp_path, monkeypatch):
    def fail(*a, **kw):
        raise LlmError("HTTP 401 unauthorized")

    monkeypatch.setattr(parallel, "run_worker_batch", fail)
    outcome = parallel.run_parallel_worker_batch(batch(), tmp_path, tmp_path / "evidence")
    assert outcome["status"] == "failed"
    assert outcome["workers_completed"] == 0
    assert all(row["provider_fallback"] for row in outcome["parallel_results"])
    assert review_worker_run(Path(outcome["run_dir"]))["verdict"] == "rejected"


def test_parallel_conflicting_writes_keep_plan_order(tmp_path, monkeypatch):
    packet = batch()
    for job in packet["jobs"]:
        job["actions"] = [{"kind": "write_text", "path": "shared.txt", "text": job["job_id"]}]
    seen = []

    def run(row, **kwargs):
        seen.append(row)
        return {"status": "passed", "run_dir": str(tmp_path)}

    monkeypatch.setattr(parallel, "run_worker_batch", run)
    parallel.run_parallel_worker_batch(packet, tmp_path, tmp_path / "evidence")
    assert len(seen) == 1
    assert [job["job_id"] for job in seen[0]["jobs"]] == ["first", "second"]


@pytest.mark.parametrize("module,runner", [
    (serial, serial.run_autonomous_mission_v2),
    (parallel, parallel.run_autonomous_mission_v3),
])
def test_feedback_reaches_real_job_goal(tmp_path, monkeypatch, module, runner):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"goal": "Base", "steps": [
        {"job_id": "a", "goal": "Worker objective", "actions": [], "verify_commands": []}
    ]}), encoding="utf-8")
    seen_goals = []

    def run(packet, **kwargs):
        seen_goals.append(packet["jobs"][0]["goal"])
        return {"status": "failed", "run_dir": str(tmp_path)}

    monkeypatch.setattr(module, "run_worker_batch" if module is serial else "run_parallel_worker_batch", run)
    monkeypatch.setattr(module, "review_worker_run", lambda *a: {
        "verdict": "rejected", "checks": [
            {"name": "commands_green", "passed": False, "detail": "tests/test_bug.py failed"},
        ],
    })
    runner(mission_id="feedback", prompt="Operator mission", plan_path=plan,
           repo_root=tmp_path, max_cycles=2)
    assert "Operator mission" in seen_goals[0]
    assert "Worker objective" in seen_goals[0]
    assert "tests/test_bug.py failed" in seen_goals[1]


@pytest.mark.parametrize("override", [
    {"interval_seconds": 0}, {"interval_seconds": -1}, {"interval_seconds": float("inf")},
    {"poll_seconds": 0}, {"poll_seconds": -1}, {"poll_seconds": float("nan")},
    {"debounce_seconds": -1}, {"max_cycles": -1},
])
def test_invalid_watch_limits_fail_before_side_effects(override):
    params = {"interval_seconds": 1, "max_cycles": 1, **override}
    with pytest.raises(WatchError):
        config = WatchConfig(**params)
        WatchLoop(config, clock=lambda: 0, sleep=lambda _: None, snapshot=lambda _: {},
                  run_cycle=lambda _: None, stop_check=lambda: True).run()


def test_zero_watch_budget_performs_no_cycles():
    calls = []
    ticks = iter([0, 1, 2, 3])
    result = WatchLoop(WatchConfig(interval_seconds=1, max_cycles=0),
                       clock=lambda: next(ticks), sleep=lambda _: None,
                       snapshot=lambda _: {}, run_cycle=calls.append).run()
    assert result == 0
    assert calls == []
