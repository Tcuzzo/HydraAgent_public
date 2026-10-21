"""Needle's native threadpool must not inherit a restrictive implicit affinity."""
import io
import json

import pytest


@pytest.mark.parametrize("configured, expected", [(None, list(range(9))), ("0.15", [0])])
def test_needle_default_uses_available_cpus_but_honors_explicit_budget(monkeypatch, capsys, configured, expected):
    import psutil
    from hydra import local_model_worker

    applied = []

    class Process:
        def cpu_affinity(self, cpus=None):
            if cpus is None:
                return list(range(9))
            applied.append(cpus)

    monkeypatch.setattr(psutil, "Process", Process)
    monkeypatch.setattr(psutil, "cpu_count", lambda: 9)
    monkeypatch.setattr(local_model_worker.sys, "stdin", io.StringIO('{"backend":"needle"}'))
    monkeypatch.setattr(local_model_worker, "_needle", lambda request: {"success": True})
    if configured is None:
        monkeypatch.delenv("HYDRA_LOCAL_CPU_FRACTION", raising=False)
    else:
        monkeypatch.setenv("HYDRA_LOCAL_CPU_FRACTION", configured)
    assert local_model_worker.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert applied == [expected]
    assert receipt["resource_budget"]["logical_cpus"] == expected
