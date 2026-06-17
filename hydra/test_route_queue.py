# hydra/test_route_queue.py
import asyncio
import pytest
from hydra.gateway_config import GatewayConfig
from hydra.route_queue import RouteQueue
from hydra.routing_gateway import RouteDecision


class FakeGateway:
    def route(self, task):
        return RouteDecision("tier2", "local", "simple", "x")


@pytest.mark.asyncio
async def test_processes_and_returns_result():
    async def dispatch(decision, task):
        return f"done:{task['id']}"

    q = RouteQueue(GatewayConfig(route_workers=2), gateway=FakeGateway(), dispatch=dispatch)
    await q.start()
    r = await q.submit({"id": 1, "prompt": "x"})
    assert r["status"] == "ok" and r["result"] == "done:1"
    await q.stop()


@pytest.mark.asyncio
async def test_overflow_rejects_not_drops():
    started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(decision, task):
        started.set()
        await release.wait()
        return "x"

    q = RouteQueue(
        GatewayConfig(route_workers=1, queue_maxsize=1),
        gateway=FakeGateway(),
        dispatch=dispatch,
    )
    await q.start()
    a = asyncio.create_task(q.submit({"id": 1, "prompt": "x"}))  # occupies the single worker
    await started.wait()
    b = asyncio.create_task(q.submit({"id": 2, "prompt": "x"}))  # enqueues, fills maxsize=1
    await asyncio.sleep(0.05)  # let task 2 reach the broker (no timeout wait)
    r3 = await q.submit({"id": 3, "prompt": "x"})  # queue full -> rejected immediately
    assert r3["status"] == "rejected"
    release.set()
    await a
    b.cancel()
    await q.stop()


@pytest.mark.asyncio
async def test_timeout_returns_status():
    async def dispatch(decision, task):
        await asyncio.sleep(5)
        return "x"

    q = RouteQueue(
        GatewayConfig(route_workers=1, queue_timeout_s=0.05),
        gateway=FakeGateway(),
        dispatch=dispatch,
    )
    await q.start()
    r = await q.submit({"id": 1, "prompt": "x"})
    assert r["status"] == "timed_out"
    await q.stop()
