"""hydra.model_routing — typed loader for the single model-routing source of truth.

The roster + routing policy live in ``hydra/model_routing.yaml`` (schema
``hydra.model_routing.v2``). Roster entries name BACKS rungs only; each rung is
resolved to a live route at call time by ``hydra.rung_resolver`` (the BACKS
Kev/Needle rung resolver). No model, provider or key name is stored in Hydra. This module parses that file into frozen dataclasses
and exposes a small set of helpers that the three historical hardcode sites read
from:

  * ``hydra/cli/cmd_chat.py``      → ``chat_default(profile)`` / ``worker_default()``
  * ``hydra/model_router.py``      → ``role_models()`` / ``cloud_fallback_ladder()``
  * ``hydra/runtime_route.py``     → ``verifier_route()`` / provider helpers

Pointing an entry at another rung changes the route everywhere — no Python edit.
If the YAML is missing or unreadable, ``load_routing()`` raises
``RoutingConfigError``; a rung that can't be resolved raises the retryable
``RungResolutionError`` so callers fall through to the next rung. There is no in-code roster.

The loader is REUSE of the existing declarative pattern (see
``hydra/declarative_runtime.py`` + ``hydra/intake_rules.yaml``): schema + YAML +
typed loader, not a new abstraction.

This file is the SSOT for the LLM/chat roster only. The media roster lives in its
own SSOT: ``hydra/studio/models.yaml``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from hydra import rung_resolver
from hydra.rung_resolver import RungResolutionError  # noqa: F401 - re-export

_LOG = logging.getLogger(__name__)

SCHEMA = "hydra.model_routing.v2"

# Canonical location of the YAML source of truth (next to this module).
DEFAULT_ROUTING_PATH = Path(__file__).resolve().parent / "model_routing.yaml"


# Rung -> (transport, route) resolved this process; never written anywhere.
_RESOLVED: dict[tuple[str, bool], tuple[str, str]] = {}
# Rung -> (when, why) it last failed to resolve. A dead rung is skipped for a short
# while instead of being re-asked on every hop of one turn; it is retried after.
_FAILED: dict[tuple[str, bool], tuple[float, str]] = {}
FAILED_RETRY_SECONDS = 60.0
_RESOLVER: Callable[..., tuple[str, str]] | None = None


def set_resolver(fn: Callable[..., tuple[str, str]] | None) -> None:
    """Swap the rung resolver (tests use an offline one); None restores BACKS."""
    global _RESOLVER
    _RESOLVER = fn
    _RESOLVED.clear()
    _FAILED.clear()


def resolve_rung(rung: str, local: bool = False) -> tuple[str, str]:
    key = (rung, local)
    if key in _RESOLVED:
        return _RESOLVED[key]
    failed = _FAILED.get(key)
    if failed and time.monotonic() - failed[0] < FAILED_RETRY_SECONDS:
        raise RungResolutionError(f"{failed[1]} (skipped; retrying in under {FAILED_RETRY_SECONDS:.0f}s)")
    fn = _RESOLVER or rung_resolver.resolve
    try:
        _RESOLVED[key] = fn(rung, local=local)
    except RungResolutionError as exc:
        _FAILED[key] = (time.monotonic(), str(exc))
        raise
    _FAILED.pop(key, None)
    return _RESOLVED[key]


@dataclass(frozen=True)
class RosterEntry:
    """One BACKS rung the pack can route to. The model is resolved, never stored."""

    id: str
    rung: str
    local: bool = False
    role_tags: tuple[str, ...] = ()

    def as_pair(self) -> tuple[str, str]:
        return resolve_rung(self.rung, self.local)

    @property
    def provider(self) -> str:
        return self.as_pair()[0]

    @property
    def model(self) -> str:
        return self.as_pair()[1]

    @property
    def base_url(self) -> str:
        """Hydra stores no endpoints for rungs; the transport supplies it at call time."""
        return ""


@dataclass(frozen=True)
class ModelRouting:
    """Parsed model_routing.yaml: roster + routing policy."""

    schema: str
    roster: tuple[RosterEntry, ...]
    chat_profiles: dict[str, str]
    complexity: dict[str, str]
    roles: dict[str, str]
    verifier: str
    fallbacks: dict[str, tuple[str, ...]]
    # Hydra transports, not model providers: every cloud rung goes through the BACKS
    # gateway; the local GPU rung is served on this host.
    cloud_fallback_ladder: tuple[str, ...] = (rung_resolver.GATEWAY,)
    local_fallback_provider: str = rung_resolver.LOCAL
    source_path: str = ""

    _by_id: dict[str, RosterEntry] = field(default_factory=dict, compare=False, repr=False)

    @property
    def free_fallback_pairs(self) -> tuple[tuple[str, str], ...]:
        """Emergency tier: (transport, model) of the free-tagged rungs, resolved now.

        A rung BACKS can't resolve is skipped (logged), never fatal."""
        out: list[tuple[str, str]] = []
        for e in self.roster:
            if "free" in e.role_tags and not e.local:
                try:
                    out.append(e.as_pair())
                except RungResolutionError as exc:
                    _LOG.warning("free rung %s unavailable: %s", e.id, exc)
        return tuple(out)

    @property
    def free_fallback_models(self) -> tuple[str, ...]:
        """Model half of free_fallback_pairs (kept for older callers)."""
        return tuple(model for _transport, model in self.free_fallback_pairs)

    def _chain(self, roster_id: str, role: str | None = None) -> list[str]:
        """Roster ids in fall-through order: the entry, the role's own fallbacks, the
        other cloud rungs in roster order, then the local GPU rung."""
        chain: list[str] = [roster_id, *self.fallbacks.get(role or "", ())]
        chain += [e.id for e in self.roster if not e.local]
        chain += [e.id for e in self.roster if e.local]
        return list(dict.fromkeys(chain))

    def chain_pairs(self, role: str, limit: int = 7) -> tuple[tuple[str, str], ...]:
        """Call-time fall-through for one role: every rung after the role's own
        route that resolves now (unresolvable ones are skipped), local GPU last."""
        first = self.role_pair(role)
        out: list[tuple[str, str]] = []
        for rid in self._chain(self.roles[role], role):
            try:
                pair = self.entry(rid).as_pair()
            except RungResolutionError as exc:
                _LOG.warning("rung %s skipped for %s fall-through: %s", rid, role, exc)
                continue
            if pair != first and pair not in out:
                out.append(pair)
        local = [p for p in out if p[0] == rung_resolver.LOCAL]
        cloud = [p for p in out if p[0] != rung_resolver.LOCAL]
        return tuple((cloud[: max(0, limit - len(local[:1]))] + local[:1])[:limit])

    def _pair(self, roster_id: str, role: str | None = None) -> tuple[str, str]:
        """Resolve one roster entry; if its rung is out, fall through the chain.

        Chain: the entry -> the role's own fallbacks -> the other cloud rungs in
        roster order -> the local GPU rung. Mechanical fall-through only; BACKS
        Kev/Needle own the choice of model. Raises only when no rung is reachable.
        """
        chain = self._chain(roster_id, role)
        for attempt in (1, 2):
            errors: list[str] = []
            for rid in chain:
                try:
                    pair = self.entry(rid).as_pair()
                except RungResolutionError as exc:
                    errors.append(f"{rid}: {exc}")
                    continue
                if rid != roster_id:
                    _LOG.warning("rung %s unavailable; falling through to %s", roster_id, rid)
                return pair
            if attempt == 1 and _FAILED:
                # Everything looked out, partly from memory: ask every rung fresh once
                # before giving up, so a rung that came back is never missed.
                _FAILED.clear()
                continue
            break
        raise RungResolutionError("no rung is reachable: " + "; ".join(errors))

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
        return self._pair(roster_id, "chat")

    def worker_default(self) -> tuple[str, str]:
        """(provider, model) for the local worker / forced --local profile."""
        return self._pair(self.chat_profiles["local"])

    def role_pair(self, role: str) -> tuple[str, str]:
        """(provider, model) for a named router role (router/worker/doer/...)."""
        return self._pair(self.roles[role], role)

    def role_entry(self, role: str) -> RosterEntry:
        return self.entry(self.roles[role])

    def complexity_role_pair(self, level: str) -> tuple[str, str]:
        return self._pair(self.complexity[level])

    def verifier_pair(self) -> tuple[str, str]:
        return self._pair(self.verifier, "auditor")

    def verifier_route(self) -> str:
        """'provider/model' string for runtime_route.DEFAULT_RUNTIME_ROUTE."""
        provider, model = self.verifier_pair()
        return f"{provider}/{model}"

    def fallback_pairs(self, role: str) -> tuple[tuple[str, str], ...]:
        """Ordered provider/model pairs owned by one role/category."""
        pairs: list[tuple[str, str]] = []
        for roster_id in self.fallbacks.get(role, ()):
            try:
                pairs.append(self.entry(roster_id).as_pair())
            except RungResolutionError as exc:  # never-fail: skip it, try the next rung
                _LOG.warning("fallback rung %s for %s unavailable: %s", roster_id, role, exc)
        return tuple(pairs)


def _build(data: dict[str, Any], source_path: str) -> ModelRouting:
    roster_raw = data.get("roster") or []
    roster: list[RosterEntry] = []
    for item in roster_raw:
        stored = sorted(k for k in ("model", "provider", "base_url") if k in item)
        if stored:
            raise ValueError(
                f"roster entry {item.get('id')!r} stores {stored}; entries name a BACKS rung only"
            )
        if not item.get("rung"):
            raise ValueError(f"roster entry {item.get('id')!r} names no BACKS rung")
        roster.append(
            RosterEntry(
                id=str(item["id"]),
                rung=str(item["rung"]),
                local=bool(item.get("local", False)),
                role_tags=tuple(str(t) for t in (item.get("role_tags") or ())),
            )
        )
    routing = data.get("routing") or {}
    by_id = {e.id: e for e in roster}
    mr = ModelRouting(
        schema=str(data.get("schema", SCHEMA)),
        roster=tuple(roster),
        chat_profiles=dict(routing.get("chat_profiles") or {}),
        complexity=dict(routing.get("complexity") or {}),
        roles=dict(routing.get("roles") or {}),
        verifier=str(routing.get("verifier", "")),
        fallbacks={
            str(role): tuple(str(roster_id) for roster_id in (roster_ids or ()))
            for role, roster_ids in (routing.get("fallbacks") or {}).items()
        },
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
        + [roster_id for roster_ids in mr.fallbacks.values() for roster_id in roster_ids]
    )
    missing = [rid for rid in required_ids if rid not in by_id]
    if missing:
        raise ValueError(f"model_routing references unknown roster ids: {sorted(set(missing))}")
    if not mr.chat_profiles.get("auto") or not mr.chat_profiles.get("local"):
        raise ValueError("model_routing.chat_profiles must define 'auto' and 'local'")
    if not any(e.local for e in roster):
        raise ValueError("model_routing roster needs a local GPU rung (local: true) as the last resort")
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
            "Hydra names no model itself, so it will not guess one: restore the file "
            "(git checkout -- hydra/model_routing.yaml) or fix the error above."
        ) from exc
    _CACHE[key] = mr
    return mr


def clear_cache() -> None:
    """Drop the cached routing and resolved rungs (tests repointing the YAML call this)."""
    _CACHE.clear()
    _RESOLVED.clear()
    _FAILED.clear()


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
