"""Complexity x telemetry -> tier+model. Reuses model_routing.yaml ids (env-overridable)."""
from __future__ import annotations

from dataclasses import dataclass

from hydra.gateway_config import GatewayConfig
from hydra.model_router import TaskComplexity

_FRONTIER = {TaskComplexity.COMPLEX, TaskComplexity.CRITICAL}


@dataclass(frozen=True)
class RouteDecision:
    tier: str
    model_id: str
    complexity: str
    reason: str


class RoutingGateway:
    def __init__(self, cfg: GatewayConfig, telemetry, classifier):
        self._cfg = cfg
        self._tel = telemetry
        self._clf = classifier

    def route(self, task: dict) -> RouteDecision:
        prompt = task.get("prompt", "")
        cls = self._clf.classify(prompt, task.get("complexity"))
        c = cls.complexity
        if c in _FRONTIER:
            return RouteDecision(
                "tier1",
                self._cfg.frontier_model_ids[0],
                c.value,
                f"{c.value}->frontier",
            )
        d = self._tel.can_accept_local(task.get("est_vram_mb"))
        if d.accept:
            return RouteDecision(
                "tier2",
                "local",
                c.value,
                f"{c.value}->local ({d.reason})",
            )
        return RouteDecision(
            "tier3",
            self._cfg.cloud_fast_model_ids[0],
            c.value,
            f"{c.value}->cloud-fast (local saturated: {d.reason})",
        )
