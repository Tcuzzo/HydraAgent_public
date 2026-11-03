"""Shared default cloud/local runtime route policy.

Sourced from hydra/model_routing.yaml, which names BACKS rungs only. Transports come
straight from the rung kind (cloud rung -> BACKS gateway, local rung -> this host's GPU),
so nothing here touches the network at import. The verifier is resolved through BACKS
when asked for; if no rung answers it reports the rung name instead of failing.
"""
from __future__ import annotations

from hydra.model_routing import RungResolutionError, load_routing
from hydra.rung_resolver import GATEWAY, LOCAL


def _transport(roster_id: str) -> str:
    return LOCAL if load_routing().entry(roster_id).local else GATEWAY


def _verifier() -> str:
    routing = load_routing()
    try:
        return routing.verifier_route()
    except RungResolutionError:
        return f"{_transport(routing.verifier)}/rung:{routing.entry(routing.verifier).rung}"


def _build_default_runtime_route() -> dict[str, str]:
    routing = load_routing()
    # planner role = the cloud reasoning brain; worker role = the local GPU rung.
    planner_provider = _transport(routing.roles["planner"])
    worker_provider = _transport(routing.roles["worker"])
    return {
        "conversation_provider": planner_provider,
        "planner_provider": planner_provider,
        "worker_provider": worker_provider,
        "verifier": _verifier(),
        "local_gpu_policy": "worker-only-unless-explicit",
    }


def offline_pair(entry) -> tuple[str, str]:
    """Transport and route for one roster entry, without contacting a service.

    A cloud entry with a router task is ``router/<task>/<rung>``. Anything else
    is labeled with its rung name. The live model stays on the service.
    """
    from hydra.rung_resolver import route_for

    if entry.local:
        return LOCAL, f"rung:{entry.rung}"
    if getattr(entry, "task", ""):
        return GATEWAY, route_for(entry.task, entry.rung)
    return GATEWAY, f"rung:{entry.rung}"


def offline_runtime_route() -> dict[str, str]:
    """The same route map as ``default_runtime_route``, read from the file only."""
    routing = load_routing()
    planner = routing.entry(routing.roles["planner"])
    worker = routing.entry(routing.roles["worker"])
    verifier = routing.entry(routing.verifier)
    planner_transport = offline_pair(planner)[0]
    return {
        "conversation_provider": planner_transport,
        "planner_provider": planner_transport,
        "worker_provider": LOCAL if worker.local else GATEWAY,
        "verifier": "/".join(offline_pair(verifier)),
        "local_gpu_policy": "worker-only-unless-explicit",
    }


def default_runtime_route() -> dict[str, str]:
    return _build_default_runtime_route()


def __getattr__(name: str):
    # Back-compat: DEFAULT_RUNTIME_ROUTE is built on first use, not at import.
    if name == "DEFAULT_RUNTIME_ROUTE":
        return _build_default_runtime_route()
    raise AttributeError(name)


def route_string(route: dict[str, str]) -> str:
    return (
        f"chat:{route['conversation_provider']} "
        f"planner:{route['planner_provider']} "
        f"worker:{route['worker_provider']}"
    )
