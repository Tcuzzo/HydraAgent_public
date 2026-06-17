from hydra.gateway_config import GatewayConfig
from hydra.routing_gateway import RoutingGateway, RouteDecision
from hydra.complexity_classifier import Classification
from hydra.host_telemetry import AcceptDecision, CapacitySnapshot
from hydra.model_router import TaskComplexity


def _tel(accept):
    snap = CapacitySnapshot(0.0, [], 0.0, 1, 1, False)

    class T:
        def can_accept_local(self, est_vram_mb=None):
            return AcceptDecision(accept, "x", snap)

    return T()


class FixedClassifier:
    def __init__(self, c):
        self.c = c

    def classify(self, prompt, tag):
        return Classification(self.c, 1.0, "tag")


def gw(complexity, local_ok):
    return RoutingGateway(GatewayConfig(), telemetry=_tel(local_ok), classifier=FixedClassifier(complexity))


def test_complex_goes_frontier():
    d = gw(TaskComplexity.COMPLEX, True).route({"prompt": "design"})
    assert d.tier == "tier1" and d.model_id in GatewayConfig().frontier_model_ids


def test_simple_local_when_capacity():
    d = gw(TaskComplexity.SIMPLE, True).route({"prompt": "summarize"})
    assert d.tier == "tier2"


def test_simple_failover_cloud_when_saturated():
    d = gw(TaskComplexity.SIMPLE, False).route({"prompt": "summarize"})
    assert d.tier == "tier3" and d.model_id in GatewayConfig().cloud_fast_model_ids


def test_critical_never_local():
    d = gw(TaskComplexity.CRITICAL, True).route({"prompt": "rotate creds"})
    assert d.tier == "tier1"
