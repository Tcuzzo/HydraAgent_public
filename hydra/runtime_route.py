"""Shared default cloud/local runtime route policy.

The provider + verifier defaults are sourced from the single model-routing YAML
(hydra/model_routing.yaml) via the typed loader, so editing a model/provider there
changes them here with no Python edit. If the YAML is missing/invalid the loader
returns the frozen DEFAULT (same values as the historical hardcodes), so this is
fail-safe and never silently picks a different provider.
"""
from __future__ import annotations

from hydra.model_routing import load_routing


def _build_default_runtime_route() -> dict[str, str]:
    routing = load_routing()
    # planner role = the cloud reasoning brain; worker role = local GPU. These map
    # to the historical conversation/planner/worker providers (ollama-cloud/ollama).
    planner_provider, _ = routing.role_pair("planner")
    worker_provider, _ = routing.role_pair("worker")
    return {
        "conversation_provider": planner_provider,
        "planner_provider": planner_provider,
        "worker_provider": worker_provider,
        "verifier": routing.verifier_route(),
        "local_gpu_policy": "worker-only-unless-explicit",
    }


DEFAULT_RUNTIME_ROUTE = _build_default_runtime_route()


def default_runtime_route() -> dict[str, str]:
    return dict(DEFAULT_RUNTIME_ROUTE)


def route_string(route: dict[str, str]) -> str:
    return (
        f"chat:{route['conversation_provider']} "
        f"planner:{route['planner_provider']} "
        f"worker:{route['worker_provider']}"
    )
