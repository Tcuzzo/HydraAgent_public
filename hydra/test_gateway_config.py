import pytest
from hydra.gateway_config import GatewayConfig


def test_defaults_have_no_hardware_constants(monkeypatch):
    for k in ["LOCAL_MIN_FREE_VRAM_PCT", "LOCAL_MAX_CPU_PCT", "LOCAL_MAX_RAM_PCT",
              "TELEMETRY_POLL_MS", "QUEUE_BACKEND", "QUEUE_MAXSIZE", "QUEUE_TIMEOUT_S",
              "ROUTE_WORKERS", "LOCAL_MAX_CONCURRENCY", "CLOUD_MAX_CONCURRENCY"]:
        monkeypatch.delenv(k, raising=False)
    cfg = GatewayConfig.from_env()
    assert 0 < cfg.local_min_free_vram_pct <= 100
    assert 0 < cfg.local_max_cpu_pct <= 100
    assert cfg.queue_backend in ("memory", "redis")
    assert cfg.queue_maxsize > 0
    assert cfg.route_workers >= 1


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    monkeypatch.setenv("LOCAL_MIN_FREE_VRAM_PCT", "40")
    monkeypatch.setenv("CLOUD_FAST_MODEL_IDS", "deepseek-v4-flash,gemini-3-flash")
    cfg = GatewayConfig.from_env()
    assert cfg.queue_backend == "redis"
    assert cfg.local_min_free_vram_pct == 40.0
    assert cfg.cloud_fast_model_ids == ["deepseek-v4-flash", "gemini-3-flash"]
