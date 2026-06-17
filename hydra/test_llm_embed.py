"""Tests for OllamaClient.embed via /api/embeddings."""
from hydra.llm import OllamaClient


def test_embed_returns_vectors(monkeypatch):
    # Real constructor uses `endpoint=`, not `base_url=`
    c = OllamaClient(endpoint="http://x")
    monkeypatch.setattr(c, "_post_embeddings", lambda model, text: [0.1, 0.2, 0.3])
    out = c.embed(["a", "b"], model="nomic-embed-text")
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
