"""GPU-busy → cloud fallback for the model router.

Operator law (2026-06-14): the single local GPU runs one model at a time, so when
a video job holds the GPU lock, local-GPU reads (qwen2.5-coder on the card) must
reroute to cloud instead of contending. These tests pin that behavior.

Platform note: the GPU-busy seam is a cross-process POSIX advisory-lock
(``fcntl.flock``) protocol, so simulating a GPU job *holding* the lock needs
``fcntl`` itself — which native Windows does not have. Only the tests that need a
lock HOLDER are skipped, on a probed capability (see ``requires_flock``); the
rest run on every platform.

This module must NEVER ``import fcntl`` at module scope: on Windows that raises
ModuleNotFoundError during COLLECTION, which aborts the entire pytest run and
silently reduces the whole Windows suite to zero signal.
"""
import builtins
import logging

import pytest

from hydra.model_router import ModelRouter, _gpu_busy, TaskComplexity

try:  # POSIX
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # native Windows has no fcntl
    fcntl = None  # type: ignore[assignment]
    _HAVE_FCNTL = False


# Conditioned on the ACTUAL probed capability (can this platform take an flock?),
# never on an OS name — an OS check is a blanket excuse, this is a real reason.
requires_flock = pytest.mark.skipif(
    not _HAVE_FCNTL,
    reason=(
        "needs fcntl.flock to simulate a GPU job HOLDING the lock; the GPU-busy "
        "seam is a POSIX advisory-lock protocol with no native-Windows producer "
        "(model_router._gpu_busy warns loudly and reports 'not busy' there)"
    ),
)


def _hold_lock(path: str):
    """Hold the GPU lock exclusively — simulates a running a GPU job video job."""
    fd = open(path, "a+")
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
    return fd


@requires_flock
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


@requires_flock
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


@requires_flock
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


def test_gpu_busy_reports_loudly_when_fcntl_missing(tmp_path, monkeypatch, caplog):
    """No SILENT fallback on a platform without fcntl.

    A GPU lock file existing means something is speaking the GPU-lock protocol.
    If this platform cannot read that lock, answering a bare "not busy" is
    indistinguishable from a genuinely free GPU — so the router must SAY so.
    Runs on every platform: on Windows the import genuinely fails, on POSIX the
    monkeypatch reproduces it.
    """
    lock = tmp_path / "gpu.lock"
    lock.write_text("")  # lock file EXISTS -> the protocol is in use
    monkeypatch.setenv("HYDRA_GPU_LOCK", str(lock))

    real_import = builtins.__import__

    def _no_fcntl(name, *args, **kwargs):
        if name == "fcntl":
            raise ImportError("No module named 'fcntl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_fcntl)
    with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
        assert _gpu_busy() is False  # the only answer this platform can give
    assert "fcntl" in caplog.text, "missing-capability degrade must not be silent"
    assert "CANNOT be detected" in caplog.text
