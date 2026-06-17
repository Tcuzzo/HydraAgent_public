"""GPU-busy → cloud fallback for the model router.

Operator law (2026-06-14): the single local GPU runs one model at a time, so when
a video job holds the GPU lock, local-GPU reads (qwen2.5-coder on the card) must
reroute to cloud instead of contending. These tests pin that behavior.
"""
import fcntl

import pytest

from hydra.model_router import ModelRouter, _gpu_busy, TaskComplexity


def _hold_lock(path: str):
    """Hold the GPU lock exclusively — simulates a running a GPU job video job."""
    fd = open(path, "a+")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
    return fd


def test_gpu_busy_detects_held_lock(tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    monkeypatch.setenv("HYDRA_GPU_LOCK", str(lock))
    assert _gpu_busy() is False  # no lock file yet -> not busy
    fd = _hold_lock(str(lock))
    try:
        assert _gpu_busy() is True  # held -> busy
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
    assert _gpu_busy() is False  # released -> free again


def test_simple_read_local_when_gpu_free(tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    monkeypatch.setenv("HYDRA_GPU_LOCK", str(lock))
    r = ModelRouter()
    # GPU free: a SIMPLE read uses the local 'worker' (free, fast on the GPU).
    assert r._select_model(TaskComplexity.SIMPLE) == "worker"


def test_simple_read_reroutes_to_cloud_when_gpu_busy(tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    monkeypatch.setenv("HYDRA_GPU_LOCK", str(lock))
    r = ModelRouter()
    if r._role_is_local("doer"):
        pytest.skip("config has no cloud 'doer' to reroute to")
    fd = _hold_lock(str(lock))
    try:
        role = r._select_model(TaskComplexity.SIMPLE)
        assert role != "worker", "local read must reroute off the GPU when busy"
        assert not r._role_is_local(role), "reroute target must be a cloud role"
        assert r.last_substitution.get("note") == "gpu_busy_cloud_fallback"
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()


def test_classify_task_heuristic_when_gpu_busy(tmp_path, monkeypatch):
    lock = tmp_path / "gpu.lock"
    monkeypatch.setenv("HYDRA_GPU_LOCK", str(lock))
    r = ModelRouter()
    fd = _hold_lock(str(lock))
    try:
        decision = r.classify_task("read this file and summarize it")
        # GPU busy -> heuristic classify (no local router call) + cloud read.
        assert "GPU busy" in decision.reasoning
        assert not r._role_is_local(decision.recommended_model)
    finally:
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
