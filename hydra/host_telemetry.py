# hydra/host_telemetry.py
"""Live host capacity telemetry. No hardcoded hardware limits — totals are read
at runtime; decisions use env ratios. GPU is optional (CPU-only boxes work)."""
from __future__ import annotations
import asyncio, time, logging
from dataclasses import dataclass
from hydra.gateway_config import GatewayConfig

log = logging.getLogger("hydra.host_telemetry")


@dataclass(frozen=True)
class GpuStat:
    index: int; total_mb: int; free_mb: int; used_mb: int; util_pct: float
    @property
    def free_pct(self) -> float:
        return (self.free_mb / self.total_mb * 100.0) if self.total_mb else 0.0


@dataclass(frozen=True)
class CapacitySnapshot:
    ts: float; gpus: list[GpuStat]; cpu_pct: float
    ram_total_mb: int; ram_free_mb: int; gpu_present: bool
    @property
    def ram_used_pct(self) -> float:
        return ((self.ram_total_mb - self.ram_free_mb) / self.ram_total_mb * 100.0) if self.ram_total_mb else 0.0
    @property
    def best_gpu_free_pct(self) -> float:
        return max((g.free_pct for g in self.gpus), default=0.0)


@dataclass(frozen=True)
class AcceptDecision:
    accept: bool; reason: str; snapshot: CapacitySnapshot


def evaluate_local_capacity(snap: CapacitySnapshot, cfg: GatewayConfig, est_vram_mb: int | None = None) -> AcceptDecision:
    if snap.cpu_pct > cfg.local_max_cpu_pct:
        return AcceptDecision(False, f"cpu {snap.cpu_pct:.0f}% > {cfg.local_max_cpu_pct:.0f}%", snap)
    if snap.ram_used_pct > cfg.local_max_ram_pct:
        return AcceptDecision(False, f"ram {snap.ram_used_pct:.0f}% > {cfg.local_max_ram_pct:.0f}%", snap)
    if snap.gpu_present:
        if snap.best_gpu_free_pct < cfg.local_min_free_vram_pct:
            return AcceptDecision(False, f"vram free {snap.best_gpu_free_pct:.0f}% < {cfg.local_min_free_vram_pct:.0f}%", snap)
        if est_vram_mb is not None and max((g.free_mb for g in snap.gpus), default=0) < est_vram_mb:
            return AcceptDecision(False, f"vram free < est {est_vram_mb}MB", snap)
    return AcceptDecision(True, "ok", snap)


def read_snapshot() -> CapacitySnapshot:
    import psutil
    vm = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    gpus: list[GpuStat] = []
    present = False
    try:
        import pynvml
        pynvml.nvmlInit()
        present = True
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            m = pynvml.nvmlDeviceGetMemoryInfo(h)
            u = pynvml.nvmlDeviceGetUtilizationRates(h)
            gpus.append(GpuStat(i, m.total // 1048576, m.free // 1048576, m.used // 1048576, float(u.gpu)))
        pynvml.nvmlShutdown()
    except Exception as e:  # noqa: BLE001 — GPU optional / portable
        log.debug("no GPU telemetry: %s", e); present = False
    return CapacitySnapshot(time.time(), gpus, float(cpu), vm.total // 1048576, vm.available // 1048576, present)


class HostTelemetry:
    def __init__(self, cfg: GatewayConfig, reader=read_snapshot):
        self._cfg = cfg; self._reader = reader
        self._snap = reader(); self._task: asyncio.Task | None = None; self._stop = asyncio.Event()
    def snapshot(self) -> CapacitySnapshot: return self._snap
    def can_accept_local(self, est_vram_mb: int | None = None) -> AcceptDecision:
        return evaluate_local_capacity(self._snap, self._cfg, est_vram_mb)
    async def start(self):
        self._stop.clear()
        async def loop():
            while not self._stop.is_set():
                try: self._snap = await asyncio.to_thread(self._reader)
                except Exception as e: log.warning("telemetry poll error (keeping last): %s", e)  # noqa: BLE001
                try: await asyncio.wait_for(self._stop.wait(), self._cfg.telemetry_poll_ms / 1000.0)
                except asyncio.TimeoutError: pass
        self._task = asyncio.create_task(loop())
    async def stop(self):
        self._stop.set()
        if self._task: await self._task
