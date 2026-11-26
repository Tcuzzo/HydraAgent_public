"""hydra.roles — config-driven planner / doer / auditor resolution.

Roles can name configured routing entries (``hydra/model_routing.yaml``) or
explicit provider/model assignments. The operator selects each provider and model.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.config import ConfigError, load
from hydra.config_paths import role_config_path
from hydra.providers import (
    CLI_VENDOR_FAMILIES,
    ProviderError,
    cli_transport_status,
    is_cli_transport,
    is_local_provider,
    require_public_provider,
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
    model_explicit: bool = True


@dataclass(frozen=True)
class RoleSet:
    planner: RoleSpec
    doer: RoleSpec
    auditor: RoleSpec


def _role_from_route(role: str, route: str, selector: str | None = None) -> RoleSpec:
    """Read a role's configured provider and model without remote discovery."""
    from hydra import rung_resolver
    from hydra.model_routing import RoutingConfigError, load_routing

    try:
        routing = load_routing()
    except RoutingConfigError as e:
        raise RoleError(f"role {role!r}: routing config unavailable: {e}") from e
    if route not in routing.roles:
        raise RoleError(f"role {role!r}: unknown routing role {route!r}")
    entry = routing.role_entry(route)
    provider, model = rung_resolver.configured_pair(entry.rung, local=entry.local)
    tag = "local" if entry.local else entry.task or entry.rung
    return RoleSpec(role=role, provider=str(provider), model=str(model),
                    family=ROUTE_FAMILY + tag, selector=selector, model_explicit=False)


def _role_from_config(role: str, data: dict) -> RoleSpec:
    selector = data.get("selector")
    route = data.get("route")
    provider = data.get("provider")
    require_public_provider(provider, RoleError)
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


def _optional_mapping(value: object, section: str) -> dict:
    """Normalize an absent section while rejecting non-mapping configuration."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RoleError(f"{section} must be a mapping")
    return value


def _role_config_sections(config: dict) -> tuple[dict, dict]:
    """Validate the agentic section and its role entries before resolution."""
    if not isinstance(config, dict):
        raise RoleError("config root must be a mapping")
    agentic = _optional_mapping(config.get("agentic"), "agentic")
    roles = _optional_mapping(agentic.get("roles"), "agentic.roles")
    for name, data in roles.items():
        _optional_mapping(data, f"agentic.roles.{name}")
    return agentic, roles


def resolve_roles_from_dict(config: dict) -> RoleSet:
    agentic, roles = _role_config_sections(config)
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
                # Both use the same role label; the configured provider/model may
                # also coincide, so independence is not guaranteed.
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
    config_path: str | Path | None = None,
    schema_path: str | Path | None = None,
    workspace: str | Path | None = None,
) -> RoleSet:
    """Resolve selected operator roles, or the shipped routing defaults.

    A schema remains supported for legacy callers. Normal CLI use validates the
    modern role mapping directly; no private checkout schema is required.
    """
    path = role_config_path(config_path, workspace=workspace)
    if path is None:
        return resolve_roles_from_dict({})
    try:
        config = _load_selected_role_config(path, schema_path)
        return resolve_roles_from_dict(config)
    except (RoleError, OSError, UnicodeError) as e:
        raise RoleError(f"role config invalid: {path}: {e}") from e


def _load_selected_role_config(path: Path, schema_path: str | Path | None) -> dict:
    """Select modern role loading or the caller's legacy schema contract."""
    if schema_path is None:
        return _load_role_config(path)
    try:
        return load(path, schema_path)
    except ConfigError as e:
        if path.name != "hydra.yaml":
            raise RoleError(str(e)) from e
        # Preserve modern hydra.yaml compatibility with legacy schemas.
        return _load_role_config(path)


def _load_role_config(config_path: Path) -> dict:
    """Read modern role YAML without depending on a checkout-only schema."""
    if not config_path.is_file():
        raise RoleError("config file not found")
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RoleError(f"invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise RoleError("config root must be a mapping")
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
