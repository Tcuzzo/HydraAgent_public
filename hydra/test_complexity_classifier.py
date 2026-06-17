"""Tests for hydra.complexity_classifier — Task 3 of Telemetry-Driven Routing Gateway."""
from hydra.complexity_classifier import ComplexityClassifier, Classification
from hydra.model_router import TaskComplexity


class StubEmbedder:
    def embed(self, texts, model="nomic-embed-text"):
        out = []
        for t in texts:
            tl = t.lower()
            if "architecture" in tl or "design a distributed" in tl:
                out.append([1, 0, 0, 0])
            elif "summarize" in tl or "list" in tl:
                out.append([0, 0, 0, 1])
            else:
                out.append([0, 1, 0, 0])
        return out


def test_tag_override_wins():
    c = ComplexityClassifier(embedder=StubEmbedder())
    r = c.classify("anything", tag="critical")
    assert r.complexity == TaskComplexity.CRITICAL and r.method == "tag"


def test_semantic_routes_architecture_to_complex():
    c = ComplexityClassifier(embedder=StubEmbedder())
    r = c.classify("Design a distributed architecture for the gateway", tag=None)
    assert r.complexity in (TaskComplexity.COMPLEX, TaskComplexity.CRITICAL) and r.method == "semantic"


def test_semantic_routes_summary_to_simple():
    c = ComplexityClassifier(embedder=StubEmbedder())
    r = c.classify("Summarize this log file", tag=None)
    assert r.complexity in (TaskComplexity.SIMPLE, TaskComplexity.MODERATE) and r.method == "semantic"


def test_embedder_failure_falls_back_to_deterministic():
    class Boom:
        def embed(self, *a, **k):
            raise RuntimeError("down")

    c = ComplexityClassifier(embedder=Boom())
    r = c.classify("Design the architecture", tag=None)
    assert r.method == "deterministic" and r.complexity is not None


def test_empty_prompt_never_raises_even_when_embedder_down():
    class Boom:
        def embed(self, *a, **k): raise RuntimeError("down")
    c = ComplexityClassifier(embedder=Boom())
    for p in ["", "   ", "\n\t"]:
        r = c.classify(p, tag=None)            # must not raise
        assert r.complexity is not None and r.method == "deterministic"
