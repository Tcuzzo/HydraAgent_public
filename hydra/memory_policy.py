"""hydra.memory_policy — typed loader for the memory scoring policy.

The scoring policy lives in ``hydra/memory_policy.yaml`` (schema
``hydra.memory_policy.v1``). This module parses it into frozen dataclasses and
provides a module-level ``load_policy()`` helper.

Mirrors the ``hydra.model_routing`` frozen-fallback loader pattern exactly:
- typed frozen dataclasses
- a code-frozen DEFAULT that degrades to cosine-only (today's behavior)
- ``load_policy()`` fails safe: missing or corrupt yaml returns DEFAULT, never an
  exception, never a silent behavior change
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_LOG = logging.getLogger(__name__)

SCHEMA = "hydra.memory_policy.v1"

DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "memory_policy.yaml"


@dataclass(frozen=True)
class ScoreWeights:
    """Composite ranking weights.  Must sum to 1.0 (validated at load)."""

    cosine: float
    recency: float
    access: float
    importance: float


@dataclass(frozen=True)
class MemoryPolicy:
    """Parsed memory_policy.yaml: scoring weights + policy knobs."""

    schema: str
    score_weights: ScoreWeights
    recency_half_life_days: int
    always_include_core: bool
    core_max_rows: int
    min_similarity_floor: float
    distill: dict[str, Any]
    source_path: str = ""


# ── Frozen DEFAULT: cosine-only (today's behavior; used when yaml is missing) ─


def _frozen_default() -> MemoryPolicy:
    return MemoryPolicy(
        schema=SCHEMA,
        score_weights=ScoreWeights(cosine=1.0, recency=0.0, access=0.0, importance=0.0),
        recency_half_life_days=30,
        always_include_core=False,
        core_max_rows=8,
        min_similarity_floor=0.62,
        distill={"enabled": False, "extractor": ""},
        source_path="<frozen-default>",
    )


DEFAULT: MemoryPolicy = _frozen_default()


# ── Module-level cache ────────────────────────────────────────────────────────

_CACHE: dict[str, MemoryPolicy] = {}


def load_policy(
    path: str | Path | None = None,
    *,
    refresh: bool = False,
) -> MemoryPolicy:
    """Load + cache the memory policy config.

    Fail-safe: a missing or invalid file returns the frozen ``DEFAULT``
    (cosine-only, today's behavior), never an exception.
    """
    resolved = Path(path) if path is not None else DEFAULT_POLICY_PATH
    key = str(resolved)
    if not refresh and key in _CACHE:
        return _CACHE[key]
    try:
        text = resolved.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("memory_policy.yaml top-level must be a mapping")
        policy = _build(data, source_path=key)
    except FileNotFoundError:
        policy = DEFAULT
    except Exception as exc:
        _LOG.warning(
            "memory_policy config %s unreadable (%s); using frozen DEFAULT", key, exc
        )
        policy = DEFAULT
    _CACHE[key] = policy
    return policy


def clear_cache() -> None:
    """Drop the cached policy (tests with custom paths call this)."""
    _CACHE.clear()


def _build(data: dict[str, Any], source_path: str) -> MemoryPolicy:
    schema = str(data.get("schema", ""))
    if schema != SCHEMA:
        raise ValueError(f"memory_policy schema mismatch: expected {SCHEMA!r}, got {schema!r}")

    raw_w = data.get("score_weights") or {}
    w = ScoreWeights(
        cosine=float(raw_w.get("cosine", 1.0)),
        recency=float(raw_w.get("recency", 0.0)),
        access=float(raw_w.get("access", 0.0)),
        importance=float(raw_w.get("importance", 0.0)),
    )
    total = w.cosine + w.recency + w.access + w.importance
    if abs(total - 1.0) > 1e-4:
        raise ValueError(f"score_weights must sum to 1.0, got {total}")

    return MemoryPolicy(
        schema=schema,
        score_weights=w,
        recency_half_life_days=int(data.get("recency_half_life_days", 30)),
        always_include_core=bool(data.get("always_include_core", False)),
        core_max_rows=int(data.get("core_max_rows", 8)),
        min_similarity_floor=float(data.get("min_similarity_floor", 0.62)),
        distill=dict(data.get("distill") or {}),
        source_path=source_path,
    )
