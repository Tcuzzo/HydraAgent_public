"""hydra.roles — config-driven planner / doer / auditor resolution.

Roles name BACKS routing roles (``hydra/model_routing.yaml``), never a model, tag or
vendor family. A routed role resolves to its rung's transport and route at call time;
Kev/Needle on BACKS pick the live model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.config import ConfigError, load
from hydra.providers import (
    CLI_VENDOR_FAMILIES,
    ProviderError,
    cli_transport_status,
    is_cli_transport,
    is_local_provider,
    resolve,
)

_LOG = logging.getLogger(__name__)

ROLE_NAMES = ("planner", "doer", "auditor")
#: Legacy selector names -> the routing role (model_routing.yaml ``roles``) they mean.
SELECTOR_DEFAULTS = {
    "highest_reasoner": "planner",
    "best_coder_tool_caller": "doer",
    "cloud_qa_evaluator": "independent_auditor",
}
#: Family prefix for routed roles: ``route:<local|router task|rung>``.
ROUTE_FAMILY = "route:"


class RoleError(Exception):
    """Role config could not be resolved safely."""


@dataclass(frozen=True)
class RoleSpec:
    role: str
    provider: str
    model: str
    family: str
    selector: str | None = None


@dataclass(frozen=True)
class RoleSet:
    planner: RoleSpec
    doer: RoleSpec
    auditor: RoleSpec


def _role_from_route(role: str, route: str, selector: str | None = None) -> RoleSpec:
    """Resolve a routing role to (transport, route) without asking BACKS.

    Cloud entries carry a BACKS router task, so the route is known up front and the
    gateway walks its live chain per request. The local rung leaves the model empty:
    the local transport asks its rung at call time. Only a cloud entry without a task
    needs BACKS now; if no rung answers, that is a RoleError.
    """
    from hydra import rung_resolver
    from hydra.model_routing import RoutingConfigError, load_routing
    from hydra.rung_resolver import RungResolutionError

    try:
        routing = load_routing()
    except RoutingConfigError as e:
        raise RoleError(f"role {role!r}: routing config unavailable: {e}") from e
    if route not in routing.roles:
        raise RoleError(f"role {role!r}: unknown routing role {route!r}")
    entry = routing.role_entry(route)
    if entry.local:
        provider, model, tag = rung_resolver.LOCAL, "", "local"
    elif entry.task:
        provider, model, tag = rung_resolver.GATEWAY, rung_resolver.route_for(entry.task, entry.rung), entry.task
    else:
        try:
            provider, model = routing.role_pair(route)
        except RungResolutionError as e:
            raise RoleError(f"role {role!r}: no rung reachable for {route!r}: {e}") from e
        tag = entry.rung
    return RoleSpec(role=role, provider=str(provider), model=str(model),
                    family=ROUTE_FAMILY + tag, selector=selector)


def _role_from_config(role: str, data: dict) -> RoleSpec:
    selector = data.get("selector")
    route = data.get("route")
    provider = data.get("provider")
    model = data.get("model")
    family = data.get("family")
    if route:
        return _role_from_route(role, str(route), str(selector) if selector else None)
    if selector and (not provider or not model or not family):
        if selector not in SELECTOR_DEFAULTS:
            raise RoleError(f"unknown role selector {selector!r} for {role}")
        return _role_from_route(role, SELECTOR_DEFAULTS[selector], str(selector))
    if not provider or not model or not family:
        raise RoleError(
            f"role {role!r} requires a route, a known selector, "
            f"or provider, model, and family"
        )
    return RoleSpec(
        role=role,
        provider=str(provider),
        model=str(model),
        family=str(family),
        selector=str(selector) if selector else None,
    )


def resolve_roles_from_dict(config: dict) -> RoleSet:
    agentic = config.get("agentic") or {}
    roles = agentic.get("roles") or {}
    if "auditor" not in roles and "verifier" in roles:
        roles = {**roles, "auditor": roles["verifier"]}
    defaults = {
        "planner": {"selector": "highest_reasoner"},
        "doer": {"selector": "best_coder_tool_caller"},
        "auditor": {"selector": "cloud_qa_evaluator"},
    }
    resolved = {
        name: _role_from_config(name, roles.get(name) or defaults[name])
        for name in ROLE_NAMES
    }
    auditor = resolved["auditor"]
    # Independence is a deployment choice; a shared local server can serve
    # different families and a single-model installation remains usable.
    for name in (("planner", "doer") if agentic.get("require_independent_auditor", False) else ()):
        role = resolved[name]
        routed = role.family.startswith(ROUTE_FAMILY) and auditor.family.startswith(ROUTE_FAMILY)
        if role.family.casefold() == auditor.family.casefold():
            if routed:
                # Both ask BACKS for the same rung task; BACKS may still pick
                # different models, but independence is not guaranteed. Never fail.
                _LOG.warning("degraded auditor: %s and auditor share routing %s",
                             name, role.family)
                continue
            raise RoleError(
                f"PROVIDER_COLLISION: {name} family {role.family!r} "
                f"matches auditor family {auditor.family!r}"
            )
        if role.family.lower() in CLI_VENDOR_FAMILIES and auditor.family.lower() in CLI_VENDOR_FAMILIES:
            raise RoleError(
                f"PROVIDER_COLLISION: {name} family {role.family!r} "
                "shares the CLI transport vendor with the auditor"
            )
    return RoleSet(
        planner=resolved["planner"],
        doer=resolved["doer"],
        auditor=auditor,
    )


def resolve_roles(
    *,
    config_path: str | Path,
    schema_path: str | Path,
) -> RoleSet:
    try:
        config = load(config_path, schema_path)
    except ConfigError as e:
        config_path_obj = Path(config_path)
        if config_path_obj.name != "hydra.yaml":
            raise RoleError(f"role config invalid: {e}") from e
        config = _load_hydra_yaml_roles(config_path_obj, e)
    return resolve_roles_from_dict(config)


def _load_hydra_yaml_roles(config_path: Path, original_error: ConfigError) -> dict:
    """Load the modern hydra.yaml role section when the legacy schema is stale."""
    if not config_path.is_file():
        raise RoleError(f"role config invalid: {original_error}") from original_error
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RoleError(f"role config invalid: {config_path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise RoleError(f"role config invalid: {config_path}: config root must be a mapping")
    roles = (data.get("agentic") or {}).get("roles") or {}
    if not isinstance(roles, dict):
        raise RoleError(f"role config invalid: {config_path}: agentic.roles must be a mapping")
    return data


def provider_available(role: RoleSpec, *, env_dir: str | Path | None = None) -> tuple[bool, str]:
    if is_cli_transport(role.provider):
        # The CLI transport is callable iff its binary resolves; the CLI itself
        # reports a clear error at call time if the user has not signed in.
        return cli_transport_status()
    try:
        client_cfg = resolve(role.provider, env_dir=env_dir)
    except ProviderError as e:
        return False, str(e)
    try:
        from hydra.providers import make_client

        client, _cfg = make_client(role.provider, env_dir=env_dir)
        names = client.list_models(timeout=5.0)
    except Exception as e:  # noqa: BLE001
        return True, f"provider available; model catalog unavailable: {e}"
    wanted = role.model or client_cfg.model
    # The catalog is a KNOWN-STALE ALLOWLIST, never a denylist. A model that
    # IS in the live catalog is positively confirmed available; a model that
    # is NOT in the catalog is INCONCLUSIVE (the live query may have returned
    # an incomplete list, or the provider may not list every valid model) —
    # we FAIL OPEN (admit it) and let any real unavailability surface loudly
    # at chat time, rather than falsely gating off valid newer models off a
    # stale/frozen list. This closes the "stale hardcoded catalog used as a
    # denylist" flaw class at its seam.
    if wanted and wanted not in names:
        return True, (
            f"available (admitting {wanted!r}: not in live catalog of "
            f"{len(names)} models — catalog is an allowlist, not a denylist)"
        )
    return True, "available"


def should_unload_between_roles(first: RoleSpec, second: RoleSpec) -> bool:
    """Return True when two consecutive roles would keep different local
    models resident on the same GPU.

    Cloud/API roles do not consume local VRAM, and identical local model
    names can be reused without unloading.
    """
    return bool(
        is_local_provider(first.provider)
        and is_local_provider(second.provider)
        and first.model
        and second.model
        and first.model != second.model
    )
