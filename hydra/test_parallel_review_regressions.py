"""Independent review findings for concurrent mission evidence."""
from hydra import autonomous_mission_parallel as parallel


def test_resolution_baselines_keep_jobs_serial(tmp_path):
    jobs = [{"actions": [], "resolution_spec": {"fail_to_pass": ["test_one"]}}]
    assert not parallel._independent_writes(jobs, tmp_path)


def test_parent_and_child_file_targets_keep_jobs_serial(tmp_path):
    jobs = [{"actions": [{"kind": "write_text", "path": path}]} for path in ("a", "a/b")]
    assert not parallel._independent_writes(jobs, tmp_path)


def test_mission_reports_actual_execution_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(parallel, "build_worker_batch_from_plan", lambda *a, **kw: {"jobs": []})
    monkeypatch.setattr(parallel, "run_parallel_worker_batch", lambda *a, **kw: {
        "status": "passed", "run_dir": str(tmp_path), "parallel": False,
    })
    monkeypatch.setattr(parallel, "review_worker_run", lambda *a: {"verdict": "accepted"})
    result = parallel.run_autonomous_mission_v3(
        mission_id="serial", prompt="task", plan_path=tmp_path / "unused", repo_root=tmp_path,
    )
    assert result["parallel"] is False
