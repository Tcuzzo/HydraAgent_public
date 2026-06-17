# hydra/test_host_telemetry.py
from hydra.gateway_config import GatewayConfig
from hydra.host_telemetry import CapacitySnapshot, GpuStat, evaluate_local_capacity


def _snap(free_pct, cpu, ram_used_pct, gpu=True):
    gpus = [GpuStat(index=0, total_mb=1000, free_mb=int(10 * free_pct), used_mb=1000 - int(10 * free_pct), util_pct=0.0)] if gpu else []
    return CapacitySnapshot(ts=0.0, gpus=gpus, cpu_pct=cpu, ram_total_mb=1000,
                            ram_free_mb=int(10 * (100 - ram_used_pct)), gpu_present=gpu)


def test_idle_host_accepts_local():
    cfg = GatewayConfig()
    d = evaluate_local_capacity(_snap(free_pct=80, cpu=10, ram_used_pct=20), cfg)
    assert d.accept is True


def test_saturated_vram_rejects():
    cfg = GatewayConfig()
    d = evaluate_local_capacity(_snap(free_pct=5, cpu=10, ram_used_pct=20), cfg)
    assert d.accept is False and "vram" in d.reason.lower()


def test_saturated_cpu_rejects():
    cfg = GatewayConfig()
    d = evaluate_local_capacity(_snap(free_pct=80, cpu=99, ram_used_pct=20), cfg)
    assert d.accept is False and "cpu" in d.reason.lower()


def test_gpu_absent_falls_back_to_cpu_ram():
    cfg = GatewayConfig()
    ok = evaluate_local_capacity(_snap(free_pct=0, cpu=10, ram_used_pct=10, gpu=False), cfg)
    assert ok.accept is True
    bad = evaluate_local_capacity(_snap(free_pct=0, cpu=99, ram_used_pct=10, gpu=False), cfg)
    assert bad.accept is False
