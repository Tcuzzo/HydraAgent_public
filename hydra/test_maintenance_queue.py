import pytest
from hydra.maintenance_queue import MaintenanceQueue


def test_persistent_idempotent_job_runs_once(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    root.mkdir()
    queue = MaintenanceQueue(tmp_path / 'queue.db')
    queue.enqueue('memory_audit', root, 'daily-1')
    queue.enqueue('memory_audit', root, 'daily-1')
    with pytest.raises(ValueError, match='different job'):
        queue.enqueue('index', root, 'daily-1')
    assert MaintenanceQueue(queue.path).tick()['status'] == 'completed'
    assert queue.tick()['status'] == 'idle'
    assert len(queue.snapshot()['jobs']) == 1


def test_failure_is_backed_off_not_hot_retried(tmp_path, monkeypatch):
    queue = MaintenanceQueue(tmp_path / 'queue.db')
    queue.enqueue('memory_audit', tmp_path, 'job')
    def failure(*args, **kwargs):
        raise OSError('private detail not logged')
    monkeypatch.setattr('hydra.memory_audit.audit', failure)
    result = queue.tick()
    assert result['status'] == 'queued'
    assert result['result']['error_type'] == 'OSError'
    assert queue.tick()['status'] == 'idle'
