"""hydra.model_routing — typed loader for the single model-routing source of truth.

The model roster + routing policy live in ``hydra/model_routing.yaml`` (schema
``hydra.model_routing.v1``). This module parses that file into frozen dataclasses
and exposes a small set of helpers that the three historical hardcode sites read
from:

  * ``hydra/cli/cmd_chat.py``      → ``chat_default(profile)`` / ``worker_default()``
  * ``hydra/model_router.py``      → ``role_models()`` / ``cloud_fallback_ladder()``
  * ``hydra/runtime_route.py``     → ``verifier_route()`` / provider helpers

Editing a model name in the YAML changes the resolved model everywhere — NO Python
edit. If the YAML is missing or unreadable, ``load_routing()`` returns the frozen
``DEFAULT`` below, whose values are identical to the historical hardcodes, so the
agent never crashes and never silently picks a different model.

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
    # Safe default: ["nemotron-mini", "phi3:mini"] — same as the YAML value.
    # A missing/invalid yaml never crashes and never silently drops the free tier.
    free_fallback_models: tuple[str, ...] = ("nemotron-mini", "phi3:mini")
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
    # free_fallback_models: ordered list of free cloud model names; safe default
    # when the key is absent so a legacy yaml never loses the free tier.
    _free_raw = routing.get("free_fallback_models")
    _free_tier: tuple[str, ...] = (
        tuple(str(m) for m in _free_raw)
        if _free_raw
        else ("nemotron-mini", "phi3:mini")
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
    # model later. A broken file falls back to DEFAULT (caller catches).
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


# ── Frozen DEFAULT: identical to the historical hardcodes. Used only when the
#    YAML is missing/invalid. NEVER a different model than today's code. ──────
def _frozen_default() -> ModelRouting:
    # Frozen default: identical to hydra/model_routing.yaml. Used only when the
    # YAML is missing or unreadable. Never a different model than the YAML.
    roster = [
        RosterEntry("cloud-doer", "ollama-cloud", "qwen2.5:32b",
                    ("chat", "conversation", "doer", "moderate", "intent"),
                    "https://api.ollama.cloud"),
        RosterEntry("cloud-planner", "ollama-cloud", "qwen2.5:72b",
                    ("planner", "complex", "critical", "auditor", "verifier", "work"),
                    "https://api.ollama.cloud"),
        RosterEntry("cloud-free-nemotron", "ollama-cloud", "nemotron-mini",
                    ("free", "cloud", "fallback"),
                    "https://api.ollama.cloud"),
        RosterEntry("cloud-free-phi", "ollama-cloud", "phi3:mini",
                    ("free", "cloud", "fallback"),
                    "https://api.ollama.cloud"),
        RosterEntry("local-worker", "ollama", "qwen2.5-coder:7b",
                    ("worker", "local", "router", "read"), "http://127.0.0.1:11434"),
    ]
    data = {
        "schema": SCHEMA,
        "roster": [
            {"id": e.id, "provider": e.provider, "model": e.model,
             "role_tags": list(e.role_tags), "base_url": e.base_url}
            for e in roster
        ],
        "routing": {
            "chat_profiles": {"auto": "cloud-doer", "cloud": "cloud-planner", "local": "local-worker"},
            "complexity": {"simple": "local-worker", "moderate": "cloud-doer",
                           "complex": "cloud-planner", "critical": "cloud-planner"},
            "roles": {"router": "local-worker", "worker": "local-worker",
                      "doer": "cloud-doer", "planner": "cloud-planner", "auditor": "cloud-planner",
                      "work": "cloud-planner", "intent": "cloud-doer"},
            "verifier": "cloud-planner",
            "cloud_fallback_ladder": ["ollama-cloud"],
            "local_fallback_provider": "ollama",
            "free_fallback_models": ["nemotron-mini", "phi3:mini"],
        },
    }
    return _build(data, source_path="<frozen-default>")


DEFAULT: ModelRouting = _frozen_default()


# ── Module-level cache (one load). Path-keyed so tests can repoint it. ───────
_CACHE: dict[str, ModelRouting] = {}


def load_routing(path: str | Path | None = None, *, refresh: bool = False) -> ModelRouting:
    """Load + cache the model routing config.

    Fail-safe: a missing or invalid file returns the frozen ``DEFAULT`` (same
    values as today's hardcodes), never an exception and never a different model.
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
    except FileNotFoundError:
        mr = DEFAULT
    except Exception as exc:  # malformed YAML, bad ids, etc. — fail safe.
        _LOG.warning("model_routing config %s unreadable (%s); using frozen DEFAULT", key, exc)
        mr = DEFAULT
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
