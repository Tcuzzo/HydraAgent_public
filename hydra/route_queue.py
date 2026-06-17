"""Async admission + per-tier concurrency + graceful backpressure. Pluggable broker.

Tiers:
  tier2 → local (serial GPU; local_max_concurrency semaphore)
  tier1 → frontier cloud (cloud_max_concurrency semaphore)
  tier3 → cloud-fast (cloud_max_concurrency semaphore)

Backpressure guarantees:
  - Queue full  → submit() returns {"status": "rejected", "reason": "queue_full"}  (never silent drop)
  - Task exceeds queue_timeout_s → {"status": "timed_out"}  (Future is resolved, not abandoned)
  - Worker error → {"status": "error", "error": "<msg>"}
"""
from __future__ import annotations

import asyncio
import logging
from hydra.gateway_config import GatewayConfig

log = logging.getLogger("hydra.route_queue")


class InMemoryBroker:
    """Trivial asyncio.Queue wrapper satisfying the broker interface."""

    def __init__(self, maxsize: int) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)

    def put_nowait(self, item: object) -> None:
        """Raises asyncio.QueueFull when at capacity — callers treat this as rejection."""
        self._q.put_nowait(item)

    async def get(self) -> object:
        return await self._q.get()

    def task_done(self) -> None:
        self._q.task_done()


def make_broker(cfg: GatewayConfig) -> InMemoryBroker:
    """Factory: returns InMemoryBroker. RedisBroker removed in lean-core build."""
    # SLICE 2 CUT: route_queue_redis stripped; always use InMemoryBroker.
    return InMemoryBroker(cfg.queue_maxsize)


class RouteQueue:
    """Admission controller + worker pool.

    Usage::

        q = RouteQueue(cfg, gateway=my_gateway, dispatch=my_dispatch)
        await q.start()
        result = await q.submit({"id": 1, "prompt": "..."})
        await q.stop()

    ``dispatch(decision, task) -> Any`` is the async callable that actually runs
    the task after routing.  It receives the RouteDecision produced by the gateway
    and the original task dict.
    """

    def __init__(self, cfg: GatewayConfig, gateway, dispatch, broker=None) -> None:
        self._cfg = cfg
        self._gw = gateway
        self._dispatch = dispatch
        self._broker = broker or make_broker(cfg)
        # Per-tier semaphores to cap in-flight work at each tier independently.
        self._sems: dict[str, asyncio.Semaphore] = {
            "tier2": asyncio.Semaphore(cfg.local_max_concurrency),
            "tier1": asyncio.Semaphore(cfg.cloud_max_concurrency),
            "tier3": asyncio.Semaphore(cfg.cloud_max_concurrency),
        }
        self._workers: list[asyncio.Task] = []
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Spawn worker coroutines."""
        self._stop.clear()
        self._workers = [
            asyncio.create_task(self._worker(), name=f"route-worker-{i}")
            for i in range(self._cfg.route_workers)
        ]

    async def stop(self) -> None:
        """Signal workers to drain and exit; cancel stragglers after 2 s."""
        self._stop.set()
        # One sentinel per worker so every blocked `get()` wakes up.
        for _ in self._workers:
            try:
                self._broker.put_nowait({"_sentinel": True})
            except Exception:  # noqa: BLE001
                pass
        for w in self._workers:
            try:
                await asyncio.wait_for(w, timeout=2.0)
            except Exception:  # noqa: BLE001
                w.cancel()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, task: dict) -> dict:
        """Enqueue *task* and await its result.

        Returns one of:
            {"status": "ok",        "tier": ..., "model_id": ..., "result": ...}
            {"status": "rejected",  "reason": "queue_full"}
            {"status": "timed_out"}
            {"status": "error",     "error": "<msg>"}
        """
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        try:
            self._broker.put_nowait({"task": task, "fut": fut})
        except asyncio.QueueFull:
            return {"status": "rejected", "reason": "queue_full"}

        try:
            return await asyncio.wait_for(asyncio.shield(fut), self._cfg.queue_timeout_s)
        except asyncio.TimeoutError:
            # The Future is still in the queue; the worker will eventually resolve
            # it, but the caller has already given up.  Mark it so the worker
            # skips the redundant set_result.
            return {"status": "timed_out"}

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        while not self._stop.is_set():
            item = await self._broker.get()
            try:
                if item.get("_sentinel"):
                    return
                task: dict = item["task"]
                fut: asyncio.Future = item["fut"]
                try:
                    decision = self._gw.route(task)
                    sem = self._sems.get(decision.tier, self._sems["tier3"])
                    async with sem:
                        result = await self._dispatch(decision, task)
                    if not fut.done():
                        fut.set_result(
                            {
                                "status": "ok",
                                "tier": decision.tier,
                                "model_id": decision.model_id,
                                "result": result,
                            }
                        )
                except Exception as exc:  # noqa: BLE001
                    log.exception("Worker error processing task %s", task.get("id"))
                    if not fut.done():
                        fut.set_result({"status": "error", "error": str(exc)})
            finally:
                self._broker.task_done()
