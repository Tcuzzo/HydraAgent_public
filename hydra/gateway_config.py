# hydra/gateway_config.py
"""Env-driven config for the telemetry routing gateway. No hardware constants —
all hardware limits are runtime-measured; these are ratios/knobs only."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try: return float(os.getenv(name, "").strip() or default)
    except ValueError: return default

def _i(name: str, default: int) -> int:
    try: return int(os.getenv(name, "").strip() or default)
    except ValueError: return default

def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default)


@dataclass(frozen=True)
class GatewayConfig:
    local_min_free_vram_pct: float = 25.0
    local_max_cpu_pct: float = 85.0
    local_max_ram_pct: float = 90.0
    telemetry_poll_ms: int = 1500
    queue_backend: str = "memory"
    redis_url: str = "redis://127.0.0.1:6379/0"
    queue_maxsize: int = 1000
    queue_timeout_s: float = 120.0
    route_workers: int = 4
    local_max_concurrency: int = 1
    cloud_max_concurrency: int = 8
    frontier_model_ids: list[str] = field(default_factory=lambda: ["codex-build", "cloud-planner"])
    cloud_fast_model_ids: list[str] = field(default_factory=lambda: ["free-cloud-1", "free-cloud-2"])

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        backend = (os.getenv("QUEUE_BACKEND", "memory").strip().lower() or "memory")
        if backend not in ("memory", "redis"): backend = "memory"
        return cls(
            local_min_free_vram_pct=_f("LOCAL_MIN_FREE_VRAM_PCT", 25.0),
            local_max_cpu_pct=_f("LOCAL_MAX_CPU_PCT", 85.0),
            local_max_ram_pct=_f("LOCAL_MAX_RAM_PCT", 90.0),
            telemetry_poll_ms=_i("TELEMETRY_POLL_MS", 1500),
            queue_backend=backend,
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            queue_maxsize=_i("QUEUE_MAXSIZE", 1000),
            queue_timeout_s=_f("QUEUE_TIMEOUT_S", 120.0),
            route_workers=max(1, _i("ROUTE_WORKERS", 4)),
            local_max_concurrency=max(1, _i("LOCAL_MAX_CONCURRENCY", 1)),
            cloud_max_concurrency=max(1, _i("CLOUD_MAX_CONCURRENCY", 8)),
            frontier_model_ids=_list("FRONTIER_MODEL_IDS", ["codex-build", "cloud-planner"]),
            cloud_fast_model_ids=_list("CLOUD_FAST_MODEL_IDS", ["free-cloud-1", "free-cloud-2"]),
        )
