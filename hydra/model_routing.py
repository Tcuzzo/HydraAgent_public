"""hydra.model_routing — typed loader for the single model-routing source of truth.

The model roster + routing policy live in ``hydra/model_routing.yaml`` (schema
``hydra.model_routing.v1``). This module parses that file into frozen dataclasses
and exposes a small set of helpers that the three historical hardcode sites read
from:

  * ``hydra/cli/cmd_chat.py``      → ``chat_default(profile)`` / ``worker_default()``
  * ``hydra/model_router.py``      → ``role_models()`` / ``cloud_fallback_ladder()``
  * ``hydra/runtime_route.py``     → ``verifier_route()`` / provider helpers

Editing a model name in the YAML changes the resolved model everywhere — NO Python
edit. No model is named in this code: if the YAML is missing or unreadable,
``load_routing()`` raises ``RoutingConfigError`` naming the file and the cause, so a
broken roster fails loudly instead of silently running on a hidden in-code roster.

The loader is REUSE of the existing declarative pattern (see
``hydra/declarative_runtime.py`` + ``hydra/intake_rules.yaml``): schema + YAML +
typed loader, not a new abstraction.

This file is the SSOT for the LLM/chat roster only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOG = logging.getLogger(__name__)

SCHEMA = "hydra.model_routing.v1"

# Canonical location of the YAML source of truth (next to this module).
DEFAULT_ROUTING_PATH = Path(__file__).resolve().parent / "model_routing.yaml"


@dataclass(frozen=True)
class RosterEntry:
    """One model the agent can route to."""

    id: str
    provider: str
    model: str
    role_tags: tuple[str, ...] = ()
    base_url: str | None = None

    def as_pair(self) -> tuple[str, str]:
        return (self.provider, self.model)


@dataclass(frozen=True)
class ModelRouting:
    """Parsed model_routing.yaml: roster + routing policy."""

    schema: str
    roster: tuple[RosterEntry, ...]
    chat_profiles: dict[str, str]
    complexity: dict[str, str]
    roles: dict[str, str]
    verifier: str
    cloud_fallback_ladder: tuple[str, ...]
    local_fallback_provider: str
    # Ordered FREE cloud models tried between paid-cloud failure and local last-resort.
    # Read from the YAML (routing.free_fallback_models, else the roster's free-tagged
    # entries); never named in code.
    free_fallback_models: tuple[str, ...] = ()
    source_path: str = ""

    _by_id: dict[str, RosterEntry] = field(default_factory=dict, compare=False, repr=False)

    def entry(self, roster_id: str) -> RosterEntry:
        try:
            return self._by_id[roster_id]
        except KeyError as exc:  # pragma: no cover - guarded by validation
            raise KeyError(f"roster id {roster_id!r} not found in model_routing") from exc

    # ── policy helpers (the public surface the hardcode sites read) ──────────
    def chat_default(self, profile: str) -> tuple[str, str]:
        """(provider, model) for a chat profile (auto/cloud/local)."""
        roster_id = self.chat_profiles.get(profile)
        if roster_id is None:
            roster_id = self.chat_profiles["auto"]
        return self.entry(roster_id).as_pair()

    def worker_default(self) -> tuple[str, str]:
        """(provider, model) for the local worker / forced --local profile."""
        return self.entry(self.chat_profiles["local"]).as_pair()

    def role_pair(self, role: str) -> tuple[str, str]:
        """(provider, model) for a named router role (router/worker/doer/...)."""
        return self.entry(self.roles[role]).as_pair()

    def role_entry(self, role: str) -> RosterEntry:
        return self.entry(self.roles[role])

    def complexity_role_pair(self, level: str) -> tuple[str, str]:
        return self.entry(self.complexity[level]).as_pair()

    def verifier_pair(self) -> tuple[str, str]:
        return self.entry(self.verifier).as_pair()

    def verifier_route(self) -> str:
        """'provider/model' string for runtime_route.DEFAULT_RUNTIME_ROUTE."""
        provider, model = self.verifier_pair()
        return f"{provider}/{model}"


def _build(data: dict[str, Any], source_path: str) -> ModelRouting:
    roster_raw = data.get("roster") or []
    roster: list[RosterEntry] = []
    for item in roster_raw:
        roster.append(
            RosterEntry(
                id=str(item["id"]),
                provider=str(item["provider"]),
                model=str(item["model"]),
                role_tags=tuple(str(t) for t in (item.get("role_tags") or ())),
                base_url=item.get("base_url"),
            )
        )
    routing = data.get("routing") or {}
    by_id = {e.id: e for e in roster}
    # free_fallback_models: ordered list of free cloud model names. When the key is
    # absent, the tier is the roster's free-tagged entries in roster order.
    _free_raw = routing.get("free_fallback_models")
    _free_tier: tuple[str, ...] = (
        tuple(str(m) for m in _free_raw)
        if _free_raw
        else tuple(e.model for e in roster if "free" in e.role_tags)
    )
    mr = ModelRouting(
        schema=str(data.get("schema", SCHEMA)),
        roster=tuple(roster),
        chat_profiles=dict(routing.get("chat_profiles") or {}),
        complexity=dict(routing.get("complexity") or {}),
        roles=dict(routing.get("roles") or {}),
        verifier=str(routing.get("verifier", "")),
        cloud_fallback_ladder=tuple(
            str(p) for p in (routing.get("cloud_fallback_ladder") or ())
        ),
        local_fallback_provider=str(routing.get("local_fallback_provider", "ollama")),
        free_fallback_models=_free_tier,
        source_path=source_path,
        _by_id=by_id,
    )
    # Validate required policy ids resolve — otherwise we'd silently pick a wrong
    # model later. A broken file fails loudly (load_routing raises RoutingConfigError).
    required_ids = (
        list(mr.chat_profiles.values())
        + list(mr.roles.values())
        + list(mr.complexity.values())
        + ([mr.verifier] if mr.verifier else [])
    )
    missing = [rid for rid in required_ids if rid not in by_id]
    if missing:
        raise ValueError(f"model_routing references unknown roster ids: {sorted(set(missing))}")
    if not mr.chat_profiles.get("auto") or not mr.chat_profiles.get("local"):
        raise ValueError("model_routing.chat_profiles must define 'auto' and 'local'")
    if not mr.cloud_fallback_ladder:
        raise ValueError("model_routing.cloud_fallback_ladder must be non-empty")
    return mr


class RoutingConfigError(RuntimeError):
    """The model routing file is missing or invalid. Hydra keeps no in-code roster."""


# ── Module-level cache (one load). Path-keyed so tests can repoint it. ───────
_CACHE: dict[str, ModelRouting] = {}


def load_routing(path: str | Path | None = None, *, refresh: bool = False) -> ModelRouting:
    """Load + cache the model routing config.

    Fails loudly: a missing or invalid file raises ``RoutingConfigError`` naming the
    file and the cause. Nothing is cached on failure, so a repaired file loads next call.
    """
    resolved = Path(path) if path is not None else DEFAULT_ROUTING_PATH
    key = str(resolved)
    if not refresh and key in _CACHE:
        return _CACHE[key]
    try:
        text = resolved.read_text()
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("model_routing.yaml top-level must be a mapping")
        mr = _build(data, source_path=key)
    except Exception as exc:  # missing file, malformed YAML, bad ids — fail loudly.
        raise RoutingConfigError(
            f"model_routing config {key} can't be used ({type(exc).__name__}: {exc}). "
            "Hydra names no model in code, so it will not guess one: restore the file "
            "(git checkout -- hydra/model_routing.yaml) or fix the error above."
        ) from exc
    _CACHE[key] = mr
    return mr


def clear_cache() -> None:
    """Drop the cached routing (tests repointing the YAML path call this)."""
    _CACHE.clear()


# ── Convenience module-level helpers (default path). ─────────────────────────
def chat_default(profile: str) -> tuple[str, str]:
    return load_routing().chat_default(profile)


def worker_default() -> tuple[str, str]:
    return load_routing().worker_default()


def cloud_fallback_ladder() -> tuple[str, ...]:
    return load_routing().cloud_fallback_ladder


def role_models() -> dict[str, tuple[str, str]]:
    mr = load_routing()
    return {role: mr.role_pair(role) for role in mr.roles}


def verifier_route() -> str:
    return load_routing().verifier_route()
