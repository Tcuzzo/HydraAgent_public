"""Offline runtime identity from standalone provider and role configuration."""
from __future__ import annotations

from hydra.model_routing import load_routing
from hydra.rung_resolver import configured_pair


def offline_pair(entry, *, env_dir=None) -> tuple[str, str]:
    provider, model = configured_pair(entry.rung, local=entry.local, env_dir=env_dir)
    return provider, model or "(not configured)"


def offline_runtime_route(*, env_dir=None) -> dict[str, str]:
    routing = load_routing()
    planner = offline_pair(routing.entry(routing.roles["planner"]), env_dir=env_dir)
    worker = offline_pair(routing.entry(routing.roles["worker"]), env_dir=env_dir)
    verifier = offline_pair(routing.entry(routing.verifier), env_dir=env_dir)
    return {"conversation_provider": planner[0], "planner_provider": planner[0],
            "worker_provider": worker[0], "verifier": "/".join(verifier),
            "local_gpu_policy": "worker-only-unless-explicit"}


def default_runtime_route(*, env_dir=None) -> dict[str, str]:
    return offline_runtime_route(env_dir=env_dir)


def __getattr__(name: str):
    if name == "DEFAULT_RUNTIME_ROUTE":
        return default_runtime_route()
    raise AttributeError(name)


def route_string(route: dict[str, str]) -> str:
    return (f"chat:{route['conversation_provider']} "
            f"planner:{route['planner_provider']} worker:{route['worker_provider']}")
