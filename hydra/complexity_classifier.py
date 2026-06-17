"""Prompt -> TaskComplexity. tag override -> semantic centroid -> deterministic fallback.

Embedder runs the light local nomic-embed-text; routing never blocks on it.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass

from hydra.model_router import TaskComplexity
from hydra import router_classifier

log = logging.getLogger("hydra.complexity_classifier")

_EXEMPLARS = {
    TaskComplexity.SIMPLE: [
        "summarize this text",
        "list the files",
        "extract the dates",
        "classify this sentence",
    ],
    TaskComplexity.MODERATE: [
        "write a function to parse csv",
        "research this api and report",
        "refactor this module",
    ],
    TaskComplexity.COMPLEX: [
        "design a distributed architecture",
        "plan a multi-step migration",
        "debug this race condition across files",
    ],
    TaskComplexity.CRITICAL: [
        "irreversibly delete production data",
        "move money between accounts",
        "rotate the security credentials",
    ],
}

# Maps tag strings (lowercased) to TaskComplexity members.
_TAGS = {c.value: c for c in TaskComplexity}

# Maps router_classifier role constants to TaskComplexity members.
_ROLE_TO_COMPLEXITY = {
    router_classifier.ROLE_PLANNER: TaskComplexity.COMPLEX,
    router_classifier.ROLE_AUDITOR: TaskComplexity.MODERATE,
    router_classifier.ROLE_DOER: TaskComplexity.SIMPLE,
}


@dataclass(frozen=True)
class Classification:
    complexity: TaskComplexity
    score: float
    method: str  # 'tag' | 'semantic' | 'deterministic'


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return (num / (da * db)) if da and db else 0.0


def _centroid(vecs: list[list[float]]) -> list[float]:
    n = len(vecs)
    dim = len(vecs[0])
    return [sum(v[i] for v in vecs) / n for i in range(dim)]


class ComplexityClassifier:
    """Classify prompt complexity using tag override, semantic embeddings, or deterministic fallback."""

    def __init__(self, embedder, model: str = "nomic-embed-text") -> None:
        self._embedder = embedder
        self._model = model
        self._centroids: dict[TaskComplexity, list[float]] | None = None

    def _ensure_centroids(self) -> None:
        if self._centroids is not None:
            return
        self._centroids = {}
        for cls, exemplars in _EXEMPLARS.items():
            vecs = self._embedder.embed(exemplars, model=self._model)
            self._centroids[cls] = _centroid(vecs)

    def classify(self, prompt: str, tag: str | None) -> Classification:
        """Classify prompt to TaskComplexity.

        Never raises — falls back to deterministic on embedder error or empty/whitespace prompt.
        Empty/whitespace prompts immediately return MODERATE via deterministic (no regex needed).
        """
        # 1. Explicit tag override — highest priority.
        if tag and tag.strip().lower() in _TAGS:
            return Classification(_TAGS[tag.strip().lower()], 1.0, "tag")

        # 1b. Empty / whitespace prompt — skip embedding AND regex (both require content).
        if not prompt or not prompt.strip():
            return Classification(TaskComplexity.MODERATE, 0.0, "deterministic")

        # 2. Semantic via embedder + cosine to per-class centroids.
        try:
            self._ensure_centroids()
            vec = self._embedder.embed([prompt], model=self._model)[0]
            best_cls = TaskComplexity.MODERATE
            best_score = -2.0
            for cls, centroid in self._centroids.items():  # type: ignore[union-attr]
                s = _cosine(vec, centroid)
                if s > best_score:
                    best_cls, best_score = cls, s
            return Classification(best_cls, float(best_score), "semantic")
        except Exception as exc:  # noqa: BLE001 — never block routing
            log.warning("semantic classify failed, using deterministic fallback: %s", exc)

        # 3. Deterministic fallback via router_classifier (no LLM, pure regex).
        #    Guard against RouterClassifierError or any other exception so this
        #    path truly never raises.
        try:
            result = router_classifier.classify(prompt)
            role = result.get("role", router_classifier.ROLE_DOER)
            complexity = _ROLE_TO_COMPLEXITY.get(role, TaskComplexity.MODERATE)
            return Classification(complexity, 0.0, "deterministic")
        except Exception as exc:  # noqa: BLE001 — never block routing
            log.warning("deterministic fallback raised, returning MODERATE: %s", exc)
            return Classification(TaskComplexity.MODERATE, 0.0, "deterministic")
