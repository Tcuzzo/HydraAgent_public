"""hydra.roles — config-driven planner / doer / auditor resolution."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from core.config import ConfigError, load
from hydra.providers import FORBIDDEN_PROVIDER_NAMES, ProviderError, resolve


ROLE_NAMES = ("planner", "doer", "auditor")
SELECTOR_DEFAULTS = {
    "highest_reasoner": ("ollama", "qwen3:14b", "local-ollama-reasoner"),
    "best_coder_tool_caller": ("ollama", "qwen3-coder:8b", "local-ollama-coder"),
    "cloud_qa_evaluator": ("ollama-cloud", "llama-3.3-70b-versatile", "cloud-ollama-llama-70b"),
}
OPENAI_FAMILIES = frozenset({"openai", "openai-codex", "codex"})


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


def _role_from_config(role: str, data: dict) -> RoleSpec:
    selector = data.get("selector")
    provider = data.get("provider")
    model = data.get("model")
    family = data.get("family")
    if selector and (not provider or not model or not family):
        if selector not in SELECTOR_DEFAULTS:
            raise RoleError(f"unknown role selector {selector!r} for {role}")
        provider, model, family = SELECTOR_DEFAULTS[selector]
    if not provider or not model or not family:
        raise RoleError(
            f"role {role!r} requires provider, model, and family "
            f"or a known selector"
        )
    provider_l = str(provider).lower()
    family_l = str(family).lower()
    if provider_l in FORBIDDEN_PROVIDER_NAMES or "claude" in provider_l or "anthropic" in provider_l:
        raise RoleError(f"role {role!r} selected forbidden provider {provider!r}")
    if "claude" in family_l or "anthropic" in family_l:
        raise RoleError(f"role {role!r} selected forbidden family {family!r}")
    if "claude" in str(model).lower() or "anthropic" in str(model).lower():
        raise RoleError(f"role {role!r} selected forbidden model {model!r}")
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
    for name in ("planner", "doer"):
        role = resolved[name]
        if role.family == auditor.family:
            raise RoleError(
                f"PROVIDER_COLLISION: {name} family {role.family!r} "
                f"matches auditor family {auditor.family!r}"
            )
        if role.provider == auditor.provider:
            raise RoleError(
                f"PROVIDER_COLLISION: {name} provider {role.provider!r} "
                f"matches auditor provider {auditor.provider!r}"
            )
        if role.family.lower() in OPENAI_FAMILIES and auditor.family.lower() in OPENAI_FAMILIES:
            raise RoleError(
                f"PROVIDER_COLLISION: {name} family {role.family!r} "
                "is OpenAI/Codex-family and cannot be graded by Codex"
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
    if role.provider == "codex":
        # The codex provider shells the official `codex` CLI ("Sign in with
        # ChatGPT"). It is callable iff the binary resolves on PATH (or via
        # HYDRA_CODEX_BIN). The user must also have run `codex login` once; the
        # CLI itself reports a clear error at call time if not signed in.
        try:
            from hydra.codex_client import resolve_codex_bin

            bin_path = resolve_codex_bin()
        except Exception as e:  # noqa: BLE001
            return False, f"EVALUATOR_UNAVAILABLE: {e}"
        return True, f"available (codex CLI: {bin_path})"
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
    if wanted and wanted not in names:
        return False, f"MODEL_UNAVAILABLE: {wanted!r} not in provider catalog"
    return True, "available"


def should_unload_between_roles(first: RoleSpec, second: RoleSpec) -> bool:
    """Return True when two consecutive roles would keep different local
    Ollama models resident on the same GPU.

    Cloud/API roles do not consume local VRAM, and identical local model
    names can be reused without unloading.
    """
    return (
        first.provider == "ollama"
        and second.provider == "ollama"
        and first.model
        and second.model
        and first.model != second.model
    )
