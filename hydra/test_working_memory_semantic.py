"""Tests for the semantic-recall increment of slice-0010.

These tests prove that hydra/working_memory.py now USES the embedding field
that has been stored-but-ignored since line 33 of working_memory.py.

Pure-stdlib, deterministic embedder (no numpy, no network, no model calls).
Each test uses an isolated temp memory dir so it never touches the operator's
real ~/.hydra-working-memory data.
"""
from __future__ import annotations

import importlib
import uuid

import pytest


@pytest.fixture()
def wm(tmp_path, monkeypatch):
    """Fresh working_memory module pointed at an isolated temp dir."""
    import hydra.working_memory as working_memory

    # Redirect storage to an isolated temp dir for the duration of the test.
    monkeypatch.setattr(working_memory, "WORKING_MEMORY_DIR", tmp_path / "wm")
    importlib.reload  # no-op reference; module state is module-level dir only
    return working_memory


def _new_memory(wm):
    mem_id = f"t_{uuid.uuid4().hex[:12]}"
    wm.create_memory(mem_id, "test memory")
    return mem_id


def test_embedding_autocomputed_on_add(wm):
    """add_entry without an explicit embedding now stores a real vector."""
    mem_id = _new_memory(wm)
    entry_id = wm.add_entry(mem_id, "The operator loves the canine group swarm")

    stored = wm.get_entry(mem_id, entry_id)
    emb = stored["embedding"]

    assert emb is not None, "embedding should be auto-computed, not None"
    assert isinstance(emb, list) and len(emb) > 0, "embedding must be a non-empty list"
    assert all(isinstance(x, float) for x in emb), "embedding must be a list of floats"


def test_semantic_recall_finds_synonym(wm):
    """Semantic mode recalls a synonym match that the keyword path misses.

    The query "dog team collective" shares NO literal token with the
    stored sentence ("canine group swarm"), so the keyword path returns
    nothing. Semantic mode links dog->canine and team/collective->group via
    concept overlap, so it recalls the entry.
    """
    mem_id = _new_memory(wm)
    wm.add_entry(mem_id, "The operator loves the canine group swarm")

    query = "dog team collective"

    # Negative control: today's keyword behavior finds nothing for this query
    # (no shared literal token with the stored sentence).
    keyword_hits = wm.search_entries(mem_id, query=query)
    assert len(keyword_hits) == 0, "keyword path should NOT match the synonym query"

    keyword_hits_explicit = wm.search_entries(mem_id, query=query, semantic=False)
    assert len(keyword_hits_explicit) == 0, "semantic=False must match keyword behavior"

    # Semantic mode recalls it.
    semantic_hits = wm.search_entries(mem_id, query=query, semantic=True)
    assert len(semantic_hits) >= 1, "semantic mode should recall the synonym entry"
    assert "canine group" in semantic_hits[0]["content"]


def test_semantic_ranks_most_relevant_first(wm):
    """The top semantic result is the most related entry."""
    mem_id = _new_memory(wm)
    wm.add_entry(mem_id, "The canine pack hunts together at night under the moon")
    wm.add_entry(mem_id, "I baked a chocolate cake for the birthday party")
    wm.add_entry(mem_id, "Quarterly tax filing deadlines for the business")

    hits = wm.search_entries(
        mem_id, query="dogs hunting in a group", semantic=True, limit=5
    )
    assert len(hits) >= 1
    assert "canine pack" in hits[0]["content"], (
        f"most-related entry should rank first, got: {hits[0]['content']!r}"
    )


def test_min_similarity_floor_excludes_unrelated(wm):
    """A query with no related stored entry returns 0 results (no false positives)."""
    mem_id = _new_memory(wm)
    wm.add_entry(mem_id, "The canine pack hunts together at night")
    wm.add_entry(mem_id, "I baked a chocolate cake for the party")

    hits = wm.search_entries(
        mem_id,
        query="photosynthesis chlorophyll quantum entanglement spectroscopy",
        semantic=True,
    )
    assert hits == [], f"unrelated query must return no results, got {hits!r}"


def test_keyword_mode_unchanged(wm):
    """Existing keyword/tag/type filtering returns identical results (regression guard)."""
    mem_id = _new_memory(wm)
    id_a = wm.add_entry(
        mem_id, "alpha beta gamma delta", entry_type="fact", tags=["greek"]
    )
    id_b = wm.add_entry(
        mem_id, "delta epsilon zeta", entry_type="note", tags=["greek", "other"]
    )
    id_c = wm.add_entry(mem_id, "completely unrelated words here", entry_type="fact")
    # Fail HERE, loudly, if ids ever collide — not at a confusing filter
    # assertion downstream.
    assert len({id_a, id_b, id_c}) == 3, f"entry id collision: {[id_a, id_b, id_c]}"

    # Keyword query (default behavior).
    hits = wm.search_entries(mem_id, query="delta")
    ids = {h["id"] for h in hits}
    assert ids == {id_a, id_b}, "keyword query must match both 'delta' entries"

    # Tag filter.
    tag_hits = wm.search_entries(mem_id, tags=["other"])
    assert {h["id"] for h in tag_hits} == {id_b}

    # Type filter.
    type_hits = wm.search_entries(mem_id, entry_types=["fact"])
    assert id_a in {h["id"] for h in type_hits}
    assert id_b not in {h["id"] for h in type_hits}

    # No-match keyword query.
    assert wm.search_entries(mem_id, query="nonexistentword") == []


def test_embedder_deterministic(wm):
    """Embedding the same text twice yields identical vectors."""
    text = "The operator loves the canine group swarm"
    v1 = wm.embed_text(text)
    v2 = wm.embed_text(text)
    assert v1 == v2, "embedder must be deterministic"
    assert len(v1) > 0 and all(isinstance(x, float) for x in v1)

    # Different text yields a different vector.
    v3 = wm.embed_text("a completely different sentence about taxes")
    assert v3 != v1


def test_entry_ids_unique_when_clock_stalls(wm, monkeypatch):
    """Adds inside a single clock tick must still mint distinct ids.

    Windows ticks at ~15ms, so datetime.now() returns the identical
    microsecond for back-to-back adds — and identical ids silently
    overwrite each other in the entries dict (seen live: CI run
    29887117743 minted entry_20260722_025824_884581 twice).
    """
    frozen = wm.datetime.now(wm.timezone.utc)

    class _StalledClock:
        @staticmethod
        def now(tz=None):
            return frozen

    monkeypatch.setattr(wm, "datetime", _StalledClock)
    ids = {wm._generate_entry_id() for _ in range(10)}
    assert len(ids) == 10, f"a stalled clock must not collapse ids, got {ids!r}"


def test_rapid_adds_never_overwrite(wm):
    """N rapid adds keep N entries, each with its own content."""
    mem_id = _new_memory(wm)
    ids = [wm.add_entry(mem_id, f"entry number {i}") for i in range(50)]
    assert len(set(ids)) == 50, "duplicate entry ids minted under rapid adds"
    for i, entry_id in enumerate(ids):
        stored = wm.get_entry(mem_id, entry_id)
        assert stored is not None, f"entry {i} vanished (silently overwritten)"
        assert stored["content"] == f"entry number {i}"
