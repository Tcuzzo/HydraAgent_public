# hydra/test_gateway_e2e.py
import pytest
from hydra.gateway_config import GatewayConfig
from hydra.routing_gateway import RoutingGateway
from hydra.route_queue import RouteQueue
from hydra.complexity_classifier import Classification
from hydra.host_telemetry import AcceptDecision, CapacitySnapshot
from hydra.model_router import TaskComplexity


def _tel(ok):
    s = CapacitySnapshot(0.0, [], 0.0, 1, 1, False)
    return type("T", (), {"can_accept_local": lambda self, est=None: AcceptDecision(ok, "x", s)})()


def _clf(c):
    return type("C", (), {"classify": lambda self, p, tag: Classification(c, 1.0, "tag")})()


@pytest.mark.asyncio
async def test_e2e_tiers_and_env_swap(monkeypatch):
    seen = []

    async def dispatch(decision, task):
        seen.append(decision.tier)
        return "ok"

    # idle host, simple task -> tier2 (local)
    q = RouteQueue(
        GatewayConfig(),
        gateway=RoutingGateway(GatewayConfig(), _tel(True), _clf(TaskComplexity.SIMPLE)),
        dispatch=dispatch,
    )
    await q.start()
    await q.submit({"prompt": "summarize"})
    await q.stop()
    assert seen[-1] == "tier2"

    # saturated host, same task -> tier3 (failover) — no code change
    q = RouteQueue(
        GatewayConfig(),
        gateway=RoutingGateway(GatewayConfig(), _tel(False), _clf(TaskComplexity.SIMPLE)),
        dispatch=dispatch,
    )
    await q.start()
    await q.submit({"prompt": "summarize"})
    await q.stop()
    assert seen[-1] == "tier3"

    # complex task -> tier1 (frontier)
    q = RouteQueue(
        GatewayConfig(),
        gateway=RoutingGateway(GatewayConfig(), _tel(True), _clf(TaskComplexity.COMPLEX)),
        dispatch=dispatch,
    )
    await q.start()
    await q.submit({"prompt": "design"})
    await q.stop()
    assert seen[-1] == "tier1"

    # env swap: backend toggles purely from env, no code change
    monkeypatch.setenv("QUEUE_BACKEND", "redis")
    assert GatewayConfig.from_env().queue_backend == "redis"
