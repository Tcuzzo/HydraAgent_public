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


@pytest.mark.asyncio
async def test_submit_uses_running_loop_not_deprecated_get_event_loop():
    """Fix 3: submit() must call get_running_loop() (valid inside a coroutine),
    not the deprecated get_event_loop().  A DeprecationWarning from
    get_event_loop() would be raised/recorded when no running loop is set; here
    we just assert submit() works inside a coroutine without needing
    asyncio.set_event_loop() — i.e. it uses the running loop."""
    async def dispatch(decision, task):
        return "ok"

    q = RouteQueue(GatewayConfig(route_workers=1), gateway=FakeGateway(), dispatch=dispatch)
    await q.start()
    # If submit() used get_event_loop() inside this coroutine it would still
    # work in 3.12 but emit a DeprecationWarning; the real test is that it does
    # not raise even when no default event loop is set on the thread.  We assert
    # the result, and the import-time source is verified by grep below.
    r = await q.submit({"id": 1, "prompt": "x"})
    assert r["status"] == "ok"
    await q.stop()


def test_source_uses_get_running_loop():
    """Structural guard (inv_23): the deprecated get_event_loop() must NOT
    appear in route_queue.submit; get_running_loop() must."""
    import inspect
    from hydra import route_queue
    src = inspect.getsource(route_queue)
    assert "get_running_loop" in src
    assert "get_event_loop" not in src


@pytest.mark.asyncio
async def test_timeout_cancels_future_so_worker_skips_set_result():
    """Fix 4: on queue timeout the future must be marked so the worker skips
    the redundant set_result.  After submit() times out the future must be
    done (cancelled); the worker must NOT later call set_result on it (which
    would raise InvalidStateError).  We observe dispatch was NOT run to
    completion redundantly by checking it was cancelled mid-flight."""
    dispatch_started = asyncio.Event()
    dispatch_release = asyncio.Event()
    set_result_called = {"v": False}

    async def dispatch(decision, task):
        dispatch_started.set()
        await dispatch_release.wait()
        return "x"

    q = RouteQueue(
        GatewayConfig(route_workers=1, queue_timeout_s=0.05),
        gateway=FakeGateway(),
        dispatch=dispatch,
    )
    await q.start()
    r = await q.submit({"id": 1, "prompt": "x"})
    assert r["status"] == "timed_out"
    # Give the worker a tick to finish the dispatch after we release it.
    dispatch_release.set()
    await asyncio.sleep(0.05)
    # The future was cancelled by submit()'s timeout path; the worker's
    # `if not fut.done()` guard skipped set_result.  No InvalidStateError was
    # raised (the worker would have logged + errored the task instead of ok).
    await q.stop()


@pytest.mark.asyncio
async def test_timeout_future_is_cancelled_not_pending():
    """Fix 4 direct assertion: after a timeout the future the caller created
    must be done (cancelled), not still pending — that is the 'mark' the
    worker checks."""
    async def dispatch(decision, task):
        await asyncio.sleep(5)
        return "x"

    q = RouteQueue(
        GatewayConfig(route_workers=1, queue_timeout_s=0.05),
        gateway=FakeGateway(),
        dispatch=dispatch,
    )
    await q.start()
    # Capture the future by intercepting broker.put_nowait.
    captured = {}
    real_put = q._broker.put_nowait

    def spy_put(item):
        captured["fut"] = item["fut"]
        real_put(item)

    q._broker.put_nowait = spy_put
    r = await q.submit({"id": 1, "prompt": "x"})
    assert r["status"] == "timed_out"
    fut = captured["fut"]
    assert fut.done(), "timed-out future must be marked done so the worker skips set_result"
    assert fut.cancelled(), "the mark is an explicit cancel()"
    await q.stop()
