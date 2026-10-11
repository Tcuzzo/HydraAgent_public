import pytest

from hydra.local_resources import cpu_budget


def test_cpu_affinity_is_bounded_and_truthful(monkeypatch):
    import psutil
    calls = []
    class Process:
        def cpu_affinity(self, cpus=None):
            if cpus is None:
                return list(range(24))
            calls.append(cpus)
    monkeypatch.setattr(psutil, 'Process', Process)
    monkeypatch.setattr(psutil, 'cpu_count', lambda: 24)
    monkeypatch.setenv('HYDRA_LOCAL_CPU_FRACTION', '0.15')
    result = cpu_budget()
    assert calls == [[0, 1, 2]]
    assert result['max_logical_cpu_share'] == 0.125
    assert 'wattage' in result['note']


@pytest.mark.parametrize('value', ['nan', 'inf', '0', '2'])
def test_invalid_cpu_target_is_loud(monkeypatch, value):
    monkeypatch.setenv('HYDRA_LOCAL_CPU_FRACTION', value)
    with pytest.raises(ValueError):
        cpu_budget()
