"""Tests for hydra.unified_memory — the real persistent vector store.

Acceptance coverage (the agent loop slice, BUILDER pass v1):

* A1  sqlite-vec backend smoke (REAL): vec0 extension loads, vec_version
      == 'v0.1.9', and ``CREATE VIRTUAL TABLE ... USING vec0(emb float[768]
      distance_metric=cosine)`` succeeds.
* A2  real nomic embedding smoke (NETWORK): embed_nomic returns a 768-float
      vector; skips with an explicit reason ONLY if ollama is unreachable.
* A3  persistence across connections: add 3 facts, close, reopen a NEW
      instance on the same file, all 3 rows + 3 vectors present.
* A4  relevance (REAL embeddings): "when does the operator sleep" ranks the
      quiet-hours fact #1 and excludes the cake fact.
* A5  hybrid keyword catch: a literal token ("E_QUIET_BREACH") a paraphrase
      query would miss is returned rank-1 via the FTS5/RRF lane.
* A6  add-only dedup + audit: identical add() twice → same id, ONE row, ONE
      ADD history event.
* A7  scope isolation: search(scope='shared') returns only shared rows.
* A8  graceful degrade: an embedder that raises → typed BackendUnavailable;
      the legacy build_semantic_memory_context fallback still returns a
      LocalMemoryResult (chat unbroken).

A1/A2/A4/A5 use the REAL vec0 extension and REAL nomic vectors — no stubs.
The fast unit tests (A3/A6/A7) inject a deterministic fake embedder so the
store/dedup/scope logic is covered without the network, but every relevance
claim (A4/A5) is proven against live nomic + vec0.
"""
from __future__ import annotations

import platform
import sqlite3
import struct
import sys
import urllib.error

import pytest

from hydra import unified_memory as um
from hydra.local_memory import LocalMemoryResult
from hydra.semantic_recall import build_semantic_memory_context, embed_nomic


# ── real vec0 capability probe (NOT an OS-name check) ──────────────────────
#
# Some CPython builds ship sqlite3 compiled WITHOUT extension loading (proven
# on this repo's own macos-latest CI legs), and the vendored vec0.so is Linux
# x86-64 only.  Either way the vec0 lane genuinely cannot run.  We ask the
# PRODUCT's own probe -- the exact check UnifiedMemory._open enforces -- so the
# skip can never drift from what the product actually requires, and so it is
# conditioned on the ACTUAL capability rather than on a platform name.
_VEC0_UNAVAILABLE = um.vec0_unavailable_reason()

requires_vec0 = pytest.mark.skipif(
    _VEC0_UNAVAILABLE is not None,
    reason=f"real vec0 capability absent here: {_VEC0_UNAVAILABLE}",
)


# ── helpers ────────────────────────────────────────────────────────────────


def _ollama_up() -> bool:
    try:
        embed_nomic("ping")
        return True
    except Exception:
        return False


def _fake_embedder(dim: int = 768):
    """Deterministic, network-free embedder for fast unit tests.

    Maps each token to a fixed axis so semantically-overlapping strings get
    overlapping vectors; cosine still discriminates. Not used for A4/A5 (those
    use real nomic), only for store/dedup/scope plumbing.
    """

    def embed(text: str) -> list[float]:
        vec = [0.0] * dim
        # Strip the asymmetric nomic prefix so 'search_document: x' and
        # 'search_query: x' map to the same content vector.
        body = text.split(":", 1)[-1].strip().lower() if ":" in text else text.lower()
        for tok in body.split():
            h = (hash(tok) % dim + dim) % dim
            vec[h] += 1.0
        # Avoid all-zero vectors (cosine undefined).
        if not any(vec):
            vec[0] = 1.0
        return vec

    return embed


# ── A1: sqlite-vec backend smoke (REAL) ─────────────────────────────────────


@requires_vec0
def test_a1_vec0_extension_loads_and_creates_table():
    path = um.resolve_vec0_extension()
    assert path, "no vec0 extension resolved"
    con = sqlite3.connect(":memory:")
    con.enable_load_extension(True)
    con.load_extension(path)
    version = con.execute("select vec_version()").fetchone()[0]
    assert version == "v0.1.9", f"unexpected vec_version: {version}"
    con.execute(
        "CREATE VIRTUAL TABLE t USING vec0(emb float[768] distance_metric=cosine)"
    )
    # round-trip one vector to prove the table is usable
    con.execute(
        "INSERT INTO t(rowid, emb) VALUES (?, ?)",
        (1, struct.pack("768f", *([0.1] * 768))),
    )
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    con.close()


# ── A2: real nomic embedding smoke (NETWORK) ────────────────────────────────


@pytest.mark.network
@pytest.mark.live
def test_a2_real_nomic_embedding_is_768_floats():
    try:
        vec = embed_nomic("the operator respects quiet hours")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        pytest.skip(f"ollama unreachable, cannot run real nomic smoke: {exc}")
    assert len(vec) == 768, f"nomic returned {len(vec)} dims, expected 768"
    assert all(isinstance(x, float) for x in vec)


# ── A3: persistence across connections (fast, fake embedder) ────────────────


@requires_vec0
def test_a3_persistence_across_connections(tmp_path):
    db = tmp_path / "memory.sqlite"
    embed = _fake_embedder()
    mem = um.UnifiedMemory(path=db, embedder=embed)
    mem.add("agentA runs the docker stack", scope="hydra")
    mem.add("agentB handles media and content", scope="hydra")
    mem.add("hydra is the go fabric runtime", scope="hydra")
    mem.close()

    # Reopen a NEW instance on the SAME file.
    reopened = um.UnifiedMemory(path=db, embedder=embed)
    con = reopened._db
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM entries_vec").fetchone()[0] == 3
    reopened.close()


# ── A4: relevance — the core quality test (REAL embeddings) ─────────────────


@pytest.mark.network
@pytest.mark.live
def test_a4_relevance_with_real_nomic(tmp_path):
    if not _ollama_up():
        pytest.skip("ollama unreachable, cannot run real relevance test")
    db = tmp_path / "memory.sqlite"
    try:
        mem = um.UnifiedMemory(path=db, embedder=embed_nomic)
    except um.BackendUnavailable as exc:
        pytest.skip(f"backend unavailable: {exc}")
    f1 = mem.add("operator quiet hours are 1am to 6am, hold pings", scope="hydra")
    mem.add("Hydra runs a Go fabric plus a Python execution layer", scope="hydra")
    f3 = mem.add("chocolate cake needs two eggs and butter", scope="hydra")

    hits = mem.search("when does the operator sleep", top_k=2)
    mem.close()
    assert hits, "no hits returned"
    assert hits[0].memory_id == f1, (
        f"expected quiet-hours fact rank-1, got {hits[0].body!r}"
    )
    assert all(h.memory_id != f3 for h in hits), "cake fact must be excluded"


# ── A5: hybrid keyword catch (REAL embeddings + FTS5/RRF) ───────────────────


@pytest.mark.network
@pytest.mark.live
def test_a5_hybrid_keyword_catch(tmp_path):
    if not _ollama_up():
        pytest.skip("ollama unreachable, cannot run hybrid keyword test")
    db = tmp_path / "memory.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=embed_nomic)
    target = mem.add(
        "the quiet-hours breach raised error E_QUIET_BREACH at 2am", scope="hydra"
    )
    mem.add("the agent respects the operator's rest and sleep", scope="hydra")
    mem.add("Hydra runs a Go fabric plus a Python execution layer", scope="hydra")

    hits = mem.search("E_QUIET_BREACH", top_k=3)
    mem.close()
    assert hits, "no hits returned"
    assert hits[0].memory_id == target, (
        f"literal token must rank-1 via FTS/RRF, got {hits[0].body!r}"
    )


# ── A6: add-only dedup + audit (fast, fake embedder) ────────────────────────


@requires_vec0
def test_a6_dedup_and_history(tmp_path):
    db = tmp_path / "memory.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_embedder())
    text = "the operator is the sole authority over the runtime"
    id1 = mem.add(text, scope="hydra")
    id2 = mem.add(text, scope="hydra")
    assert id1 == id2, "identical add() must return the same id (NOOP)"
    assert mem._db.execute("SELECT count(*) FROM entries").fetchone()[0] == 1
    history = mem.get_history(id1)
    adds = [h for h in history if h["event"] == "ADD"]
    assert len(adds) == 1, f"expected exactly one ADD event, got {len(adds)}"
    mem.close()


# ── A7: scope isolation (fast, fake embedder) ───────────────────────────────


@requires_vec0
def test_a7_scope_isolation(tmp_path):
    db = tmp_path / "memory.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_embedder())
    mem.add("the fabric bridge connects all agents", scope="hydra")
    shared_id = mem.add("the fabric bridge connects all agents", scope="shared")
    hits = mem.search("fabric bridge connects agents", scope="shared", top_k=5)
    mem.close()
    assert hits, "shared search returned nothing"
    assert all(h.scope == "shared" for h in hits), "scope filter leaked non-shared rows"
    assert any(h.memory_id == shared_id for h in hits)


# ── FTS stop-word sanitization (filler must not OR-join into BM25) ───────────


def test_fts_query_drops_stopwords_and_short_tokens():
    """Acceptance #5: the conversational filler in the failing probe no longer
    OR-joins into the BM25 match (that filler matched half the corpus and let RRF
    promote off-topic chunks). Content words survive."""
    match = um._fts_query(
        "yo who are you, and what do you remember about how I want model routing done?"
    )
    # Stop-words / 2-char tokens are gone.
    for bad in ('"yo"', '"who"', '"you"', '"do"', '"i"', '"how"', '"what"', '"and"'):
        assert bad not in match, f"stop-word leaked into FTS match: {bad}"
    # Content words remain.
    assert '"model"' in match
    assert '"routing"' in match


def test_fts_query_keeps_literal_ids():
    """A literal token a paraphrase would miss must still match verbatim (the
    stop-word filter must not eat real content tokens)."""
    match = um._fts_query("error E_QUIET_BREACH at 2am")
    assert '"E_QUIET_BREACH"' in match


# ── Hit.score is the TRUE cosine + min_similarity floor ──────────────────────


@requires_vec0
def test_hit_score_is_true_cosine_not_rrf(tmp_path):
    """Hit.score now carries the real query↔hit cosine (0..1), NOT the tiny
    unusable RRF fusion value (~0.03). A self-query scores near 1.0."""
    db = tmp_path / "memory.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_embedder())
    mem.add("quiet hours run from one am to six am", scope="hydra")
    hits = mem.search("quiet hours run from one am to six am", top_k=1)
    mem.close()
    assert hits
    # RRF for a single ranking at rank-1 is 1/61 ≈ 0.0164; the true cosine of an
    # identical string is ~1.0. The score must be the cosine, not the RRF value.
    assert hits[0].score > 0.9


@requires_vec0
def test_min_similarity_floor_drops_below_threshold(tmp_path):
    """search(min_similarity=...) drops any fused hit whose TRUE cosine is below
    the floor BEFORE it is appended."""
    db = tmp_path / "memory.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_embedder())
    mem.add("alpha beta gamma delta epsilon zeta", scope="hydra")  # on-topic
    mem.add("completely different lorem ipsum dolor sit", scope="hydra")  # off-topic
    no_floor = mem.search("alpha beta gamma delta epsilon zeta", top_k=5)
    floored = mem.search(
        "alpha beta gamma delta epsilon zeta", top_k=5, min_similarity=0.5
    )
    mem.close()
    assert no_floor, "baseline search returned nothing"
    # The on-topic row clears the floor; the off-topic one does not.
    assert all(h.score >= 0.5 for h in floored)
    assert len(floored) <= len(no_floor)


# ── A8: graceful degrade (typed error + fallback survives) ──────────────────


def test_a8_graceful_degrade_typed_error(tmp_path):
    db = tmp_path / "memory.sqlite"

    def boom(text: str) -> list[float]:
        raise RuntimeError("ollama down")

    # An embedder that fails on the probe raises BackendUnavailable at
    # construction time (the probe fires before the DB is opened so the store
    # is never in a half-constructed state).  Either path — constructor raise
    # OR per-method raise — must be BackendUnavailable so the caller can catch
    # it and degrade gracefully.
    with pytest.raises(um.BackendUnavailable):
        mem = um.UnifiedMemory(path=db, embedder=boom)
        mem.add("this will fail to embed", scope="hydra")
        mem.search("anything")
        mem.close()


def test_a8_legacy_fallback_still_returns_result(tmp_path):
    # When the injected embedder raises, build_semantic_memory_context must
    # still return a LocalMemoryResult (the legacy file-dump safety net),
    # proving chat is never broken by a dead backend.
    root = tmp_path / "mem"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace" / "MEMORY.md").write_text(
        "# Memory\nThe operator respects quiet hours 1am-6am.\n"
    )

    def boom(text: str, **kwargs) -> list[float]:
        raise RuntimeError("ollama down")

    result = build_semantic_memory_context("when does the operator sleep", root=root, embedder=boom)
    assert isinstance(result, LocalMemoryResult)
    assert result.status in ("OK", "MISSING")
    assert result.data.get("fallback") is True


# ════════════════════════════════════════════════════════════════════════════
# INCREMENT 2 — migration + live recall backed by the persistent store.
# ════════════════════════════════════════════════════════════════════════════

import hydra.semantic_recall as semantic_recall
from hydra.memory_migration import MigrationReport, migrate_md_corpus_to_unified


def _fake_768(text: str, **kwargs) -> list[float]:
    """Deterministic 768-d bag-of-words embedder (network-free, store-usable).

    Unlike the 14-d ``tests/test_semantic_recall.fake_embedder``, this returns a
    REAL 768-d vector so UnifiedMemory.add/search run end-to-end (vec0 needs
    float[768]). Strips the nomic asymmetric prefix so 'search_document: x' and
    'search_query: x' map to the same content vector, and counts tokens so
    topically-overlapping strings get high cosine.
    """
    vec = [0.0] * um.NOMIC_DIM
    body = text.split(":", 1)[-1].strip().lower() if ":" in text else text.lower()
    for tok in body.split():
        h = (hash(tok) % um.NOMIC_DIM + um.NOMIC_DIM) % um.NOMIC_DIM
        vec[h] += 1.0
    if not any(vec):
        vec[0] = 1.0
    return vec


def _seed_tree(tmp_path):
    """A tiny ~/.hydra-memory-like root + an ISOLATED tmp workspace root.

    Returns (root, workspace_root). ``workspace_root`` is a tmp path whose
    ``~/.claude/projects/<slug>/memory`` corpus does NOT exist, so
    ``_claude_project_memory_candidates`` returns ``[]`` and the fixture stays
    isolated to its two tmp workspace files. (It must NOT be ``None``: the shared
    resolver now defaults a ``None`` workspace_root to the live repo root — the
    drift fix — which would leak the operator's real .claude corpus into the
    fixture. An isolated tmp workspace root reproduces the old, intended
    no-.claude-corpus behavior without weakening any assertion.)
    """
    root = tmp_path / "mem"
    wm = root / "workspace"
    wm.mkdir(parents=True)
    # workspace/MEMORY.md is a MEMORY_CANDIDATE (reused candidate discovery).
    (wm / "MEMORY.md").write_text(
        "# Index\n\n"
        "## quiet hours\n"
        "The operator does not want to be pinged between 1am and 6am; hold "
        "routine notifications until 6am.\n\n"
        "## fabric\n"
        "Hydra runs a Go fabric plus a Python execution layer.\n",
        encoding="utf-8",
    )
    (wm / "memory-digest.md").write_text(
        "# Digest\n\n## cake\nChocolate cake needs two eggs and butter.\n",
        encoding="utf-8",
    )
    # Isolated workspace root: no ~/.claude/projects/<slug>/memory exists for it,
    # so the .claude corpus is empty (the behavior these tests always asserted).
    isolated_ws = tmp_path / "isolated_ws"
    isolated_ws.mkdir()
    return root, isolated_ws


def _expected_chunk_count(root, workspace_root):
    """Count distinct chunks the live-recall collector would walk (no new globber)."""
    from hydra.local_memory import (
        _claude_project_memory_candidates,
        _resolve_root,
        _resolve_workspace_root,
    )

    resolved = _resolve_root(root)
    ws = _resolve_workspace_root(workspace_root)
    claude = _claude_project_memory_candidates(ws)
    chunks = semantic_recall._collect_chunks(resolved, claude)
    return len([c for _, c in chunks if c.strip()])


# ── M1: migration runs, idempotent (fake embedder, tmp store) ───────────────


@requires_vec0
def test_m1_migration_idempotent(tmp_path):
    root, ws = _seed_tree(tmp_path)
    db = tmp_path / "store.sqlite"
    expected = _expected_chunk_count(root, ws)
    assert expected >= 2, "fixture must yield multiple distinct chunks"

    mem = um.UnifiedMemory(path=db, embedder=_fake_768)
    report = migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)
    assert isinstance(report, MigrationReport)
    assert report.added == expected, f"expected {expected} added, got {report.added}"
    assert report.noop == 0
    row_count = mem._db.execute("SELECT count(*) FROM entries").fetchone()[0]
    assert row_count == report.added

    add_events_before = mem._db.execute(
        "SELECT count(*) FROM entries_history WHERE event='ADD'"
    ).fetchone()[0]

    # Re-run on the SAME store: pure NOOP, no new rows, no new history.
    report2 = migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)
    assert report2.added == 0, f"re-run must add nothing, added {report2.added}"
    assert report2.noop == report.added
    row_count2 = mem._db.execute("SELECT count(*) FROM entries").fetchone()[0]
    assert row_count2 == row_count, "re-run must not change entries count"
    add_events_after = mem._db.execute(
        "SELECT count(*) FROM entries_history WHERE event='ADD'"
    ).fetchone()[0]
    assert add_events_after == add_events_before, "re-run must not add ADD history"
    mem.close()


# ── M2: migrated rows carry source + scope ──────────────────────────────────


@requires_vec0
def test_m2_rows_carry_source_and_scope(tmp_path):
    root, ws = _seed_tree(tmp_path)
    db = tmp_path / "store.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_768)
    migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)

    rows = mem._db.execute("SELECT scope, kind, source FROM entries").fetchall()
    assert rows, "migration produced no rows"
    # The .md relpaths the live recall surfaces for this fixture.
    valid_sources = {"workspace/MEMORY.md", "workspace/memory-digest.md"}
    for r in rows:
        assert r["scope"] == "hydra"
        assert r["kind"] == "memory"
        assert r["source"] is not None
        assert r["source"] in valid_sources, f"unexpected source {r['source']!r}"
    mem.close()


# ── M3: REAL recall-from-store smoke (network + live) ───────────────────────


@pytest.mark.network
@pytest.mark.live
def test_m3_real_recall_from_store(tmp_path, monkeypatch):
    if not _ollama_up():
        pytest.skip("ollama unreachable, cannot run real recall-from-store smoke")
    root, ws = _seed_tree(tmp_path)
    db = tmp_path / "store.sqlite"
    # Migrate the corpus into a REAL nomic store.
    mem = um.UnifiedMemory(path=db, embedder=embed_nomic)
    report = migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)
    mem.close()
    assert report.added >= 1, "migration added nothing"

    # Point the LIVE recall at THIS store (default-path seam) and use a SYNONYM
    # query with no literal overlap on 'quiet'/'1am'/'6am'/'ping'.
    monkeypatch.setattr(semantic_recall, "_default_store_path", lambda: db)
    result = build_semantic_memory_context(
        "when should the agent avoid waking the operator at night",
        root=root,
        workspace_root=ws,
    )
    assert result.status == "OK"
    assert result.data["backend"] == "unified", result.data
    assert result.data["fallback"] is False
    # The quiet-hours chunk surfaces via semantic (synonym) recall from the store.
    assert "1am" in result.context and "6am" in result.context, result.context


# ── M4: live path is the UNCHANGED LocalMemoryResult contract ───────────────


@requires_vec0
def test_m4_live_path_unchanged_contract(tmp_path, monkeypatch):
    root, ws = _seed_tree(tmp_path)
    db = tmp_path / "store.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_768)
    migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)
    mem.close()

    monkeypatch.setattr(semantic_recall, "_default_store_path", lambda: db)
    result = build_semantic_memory_context(
        "quiet hours", root=root, workspace_root=ws, embedder=_fake_768
    )
    assert isinstance(result, LocalMemoryResult)
    # Exact attributes operator.py and elite._stream_turn read.
    assert hasattr(result, "status")
    assert hasattr(result, "context")
    assert hasattr(result, "report")
    assert hasattr(result, "data")


# ── M5: BackendUnavailable -> file-corpus fallback, never crash/12KB dump ────


def test_m5_embedder_down_falls_back_bounded(tmp_path):
    root, ws = _seed_tree(tmp_path)

    def boom(text, **kwargs):
        raise RuntimeError("ollama down")

    # No store-path monkeypatch: store opens on default path, search _embed
    # raises BackendUnavailable -> file-corpus -> bounded legacy fallback.
    result = build_semantic_memory_context(
        "when does the operator sleep", root=root, workspace_root=ws, embedder=boom
    )
    assert isinstance(result, LocalMemoryResult)  # never raised
    assert result.data.get("fallback") is True
    assert result.data.get("fallback_reason")
    assert len(result.context) <= 4000, "must be bounded, NOT the 12KB dump"


def test_m5_vec0_missing_falls_back(tmp_path, monkeypatch):
    root, ws = _seed_tree(tmp_path)
    # Force vec0 absent: both the env path and the resolver return nothing, so
    # UnifiedMemory.__init__ raises BackendUnavailable.
    monkeypatch.setenv("HYDRA_VEC0_PATH", "/nonexistent/vec0.so")
    monkeypatch.setattr(um, "resolve_vec0_extension", lambda: None)

    result = build_semantic_memory_context(
        "when does the operator sleep",
        root=root,
        workspace_root=ws,
        embedder=_fake_768,
    )
    assert isinstance(result, LocalMemoryResult)  # graceful, no crash
    assert result.data.get("fallback") is True
    assert result.data.get("fallback_reason")
    assert len(result.context) <= 4000


# ── M6: anti-tautology — the live path TRULY switched to one KNN ────────────


@requires_vec0
def test_m6_store_path_taken_one_query_embed(tmp_path, monkeypatch):
    root, ws = _seed_tree(tmp_path)
    db = tmp_path / "store.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_768)
    migrate_md_corpus_to_unified(mem=mem, root=root, workspace_root=ws)
    mem.close()

    monkeypatch.setattr(semantic_recall, "_default_store_path", lambda: db)

    calls: list[str] = []

    def counting(text, **kwargs):
        calls.append(text)
        return _fake_768(text)

    result = build_semantic_memory_context(
        "quiet hours operator", root=root, workspace_root=ws, embedder=counting
    )
    # Proof the store path was taken (not the file-corpus fallback).
    assert result.data["backend"] == "unified", result.data
    assert result.data["fallback"] is False
    # Store path: the embedder is called TWICE — once at construction (probe to
    # derive the vector dimension) and once for the KNN query.  The file-corpus
    # fallback would call it len(chunks)+1 times (many more).
    assert len(calls) == 2, f"expected 2 embed calls (probe + query), got {len(calls)}: {calls}"
    assert calls[0] == "probe", f"first call must be the dimension probe, got {calls[0]!r}"
    assert calls[1].startswith("search_query: "), (
        f"second call must be the KNN query, got {calls[1]!r}"
    )


# ════════════════════════════════════════════════════════════════════════════
# INCREMENT 3 — default-corpus coverage + drift lock (the bug that under-captured)
#
# Root cause this section locks down: migrate_md_corpus_to_unified defaulted
# workspace_root=None, so _claude_project_memory_candidates(None) returned [] and
# the 25 .claude project/feedback memories + MEMORY.md (26 sources) were NEVER
# walked -> a no-arg/default migration captured only 1 workspace source / 5
# chunks. The LIVE recall is ALWAYS called with root=DEFAULT_MEMORY_ROOT +
# workspace_root=REPO_ROOT, covering all 26 sources -> the persistent store was
# thinner than what chat needs. The fix: the migration default == the live
# defaults, routed through ONE shared resolve_corpus_chunks so they can't drift.
# ════════════════════════════════════════════════════════════════════════════


def _seed_full_corpus(tmp_path, monkeypatch):
    """Seed a tmp ~/.hydra-memory root + a ~/.claude/projects/<slug>/memory corpus.

    Monkeypatches the LIVE DEFAULTS a no-arg migration resolves through:
      * local_memory.DEFAULT_MEMORY_ROOT -> tmp memory root (the ``root`` default),
      * semantic_recall.DEFAULT_WORKSPACE_ROOT -> a tmp repo path (the
        ``workspace_root`` default — the SAME repo root cmd_chat/operator pass),
      * Path.home -> tmp home, so the .claude slug for the tmp repo path resolves
        into the tmp tree (never the operator's real ~/.claude corpus).

    The .claude corpus seeds MEMORY.md + 25 *.md project/feedback memories — the
    EXACT shape of the real corpus this bug under-captured.

    Returns (memory_root, repo_root, claude_relpaths) where claude_relpaths are
    the source relpaths the live recall surfaces for the .claude memories.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("hydra.local_memory.Path.home", lambda: home, raising=False)

    # tmp ~/.hydra-memory root (the migration's ``root`` default).
    memory_root = home / ".hydra-memory"
    wm = memory_root / "workspace"
    wm.mkdir(parents=True)
    (wm / "MEMORY.md").write_text(
        "# Workspace Index\n\n## fabric\nHydra runs a Go fabric plus a Python "
        "execution layer.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("hydra.local_memory.DEFAULT_MEMORY_ROOT", memory_root)

    # tmp repo root (the migration's ``workspace_root`` default — the live one).
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(semantic_recall, "DEFAULT_WORKSPACE_ROOT", repo_root)

    # The .claude project memory corpus the live recall walks for this repo.
    slug = str(repo_root.resolve()).replace("/", "-")
    claude_mem = home / ".claude" / "projects" / slug / "memory"
    claude_mem.mkdir(parents=True)
    (claude_mem / "MEMORY.md").write_text(
        "# Memory Index\n\n- quiet hours: 1am to 6am, hold pings until 6am.\n",
        encoding="utf-8",
    )
    claude_relpaths = {
        str((claude_mem / "MEMORY.md").resolve()),
    }
    # Each lesson gets a UNIQUE topic with NON-overlapping vocabulary so the
    # bag-of-words _fake_768 embedder does not collapse two lessons as cosine
    # near-dups (which would drop a real .claude source from report.sources).
    topics = [
        "database btree index query planner cache optimization",
        "http rest endpoint header status method idempotent",
        "logging structured rotation level handler formatter sink",
        "authentication token refresh session cookie expiry scope",
        "compression gzip chunk buffer throughput ratio dictionary",
        "scheduler cron interval timer dispatch worker backlog",
        "serialization protobuf schema version field wire format",
        "filesystem watch inotify debounce path glob descriptor",
        "graph traversal topological cycle edge adjacency weight",
        "encryption aes nonce cipher block mode padding key",
        "metrics histogram percentile gauge counter export sample",
        "template render context variable escape partial include",
        "vector embedding cosine similarity neighbor recall fusion",
        "parser tokenizer grammar abstract syntax precedence reduce",
        "container image layer registry digest manifest pull build",
        "statemachine transition guard entry exit deterministic halt",
        "websocket frame ping reconnect heartbeat backpressure stream",
        "configuration override environment precedence validation default merge",
        "concurrency thread lock mutex deadlock atomic semaphore barrier",
        "diff hunk apply conflict context unified rename detect",
        "caching eviction lru ttl invalidation warm hit miss",
        "regex compile match capture anchor quantifier greedy lazy",
        "pagination cursor offset limit keyset stable ordering",
        "markdown frontmatter heading link table fence highlight code",
    ]
    for i, topic in enumerate(topics):
        name = f"feedback_lesson_{i:02d}.md"
        p = claude_mem / name
        p.write_text(
            f"# Lesson {i}: {topic}\n\n"
            f"Lesson {i} records that {topic} is a distinct durable fact the "
            f"operator wants the agent to recall verbatim and never confuse.\n",
            encoding="utf-8",
        )
        claude_relpaths.add(str(p.resolve()))
    return memory_root, repo_root, claude_relpaths


@requires_vec0
def test_default_migration_covers_full_corpus_not_one_source(tmp_path, monkeypatch):
    """A NO-ARG migration must cover the FULL corpus (>=25 sources incl. .claude).

    This is the regression that let the bug through: with the OLD defaults
    (workspace_root=None) a no-arg migrate_md_corpus_to_unified() walked only the
    1 workspace source and captured ~5 chunks. With the fix it defaults
    workspace_root to the live repo root, so it walks all 26 sources.

    Asserts >=25 distinct sources AND that the .claude project/feedback memory
    relpaths are present (NOT just the 1 workspace file). MUST fail pre-fix.
    """
    memory_root, repo_root, claude_relpaths = _seed_full_corpus(tmp_path, monkeypatch)
    db = tmp_path / "store.sqlite"
    mem = um.UnifiedMemory(path=db, embedder=_fake_768)

    # NO root/workspace_root args: exercise the DEFAULTS (the live corpus).
    report = migrate_md_corpus_to_unified(mem=mem)

    assert len(report.sources) >= 25, (
        f"no-arg migration must cover the full corpus, got "
        f"{len(report.sources)} sources: {report.sources}"
    )
    # The .claude memory sources are surfaced as absolute paths (outside the
    # ~/.hydra-memory root), so _safe_relative returns the abs path. At least the
    # 25 seeded feedback lessons + MEMORY.md must appear.
    covered = set(report.sources)
    claude_covered = covered & claude_relpaths
    assert len(claude_covered) >= 25, (
        "no-arg migration missed the .claude project/feedback memories; "
        f"only matched {len(claude_covered)} of {len(claude_relpaths)} .claude "
        f"sources. sources={report.sources}"
    )
    mem.close()


def test_migration_default_corpus_equals_live_recall_corpus(tmp_path, monkeypatch):
    """Drift lock: migration default corpus == live recall corpus.

    Both paths resolve the corpus through the SINGLE shared
    semantic_recall.resolve_corpus_chunks. With NO args, the migration must
    resolve the SAME (resolved root, workspace, claude candidates, chunk set)
    that build_semantic_memory_context resolves with the live defaults — so the
    persistent store and the live recall can never diverge.
    """
    import hydra.local_memory as local_memory

    memory_root, repo_root, _claude = _seed_full_corpus(tmp_path, monkeypatch)

    # What the migration's defaults resolve (root=None, workspace_root=None now
    # map to DEFAULT_MEMORY_ROOT + DEFAULT_WORKSPACE_ROOT via the shared resolver).
    mig_resolved, mig_ws, mig_claude, mig_chunks = semantic_recall.resolve_corpus_chunks(
        None, None
    )

    # What the LIVE recall resolves with the SAME live defaults — the explicit
    # values cmd_chat/operator pass: root=DEFAULT_MEMORY_ROOT (the patched live
    # root), workspace_root=DEFAULT_WORKSPACE_ROOT (the patched live repo root).
    live_resolved, live_ws, live_claude, live_chunks = (
        semantic_recall.resolve_corpus_chunks(
            local_memory.DEFAULT_MEMORY_ROOT, semantic_recall.DEFAULT_WORKSPACE_ROOT
        )
    )

    assert mig_resolved == live_resolved
    assert mig_ws == live_ws
    assert mig_claude == live_claude
    assert mig_chunks == live_chunks
    # And the chunk set is the FULL corpus, not 1 source.
    assert len({rel for rel, _ in mig_chunks}) >= 25, (
        "shared resolver must yield the full corpus for the live defaults"
    )


# ── capability contract: loud failure, never a silent degrade ──────────────


def test_capability_probe_reports_missing_sqlite3_extension_support(monkeypatch):
    """A sqlite3 without extension support must be REPORTED, precisely.

    Simulates the macOS CI interpreter on any host: its sqlite3 is built
    without extension loading, so ``sqlite3.Connection`` has no
    ``enable_load_extension`` at all.  That attribute cannot be deleted from
    the immutable C type, so we override the product's own probe seam -- the
    same function the product itself calls.
    """
    monkeypatch.setattr(um, "_sqlite3_supports_extension_loading", lambda: False)
    reason = um.vec0_unavailable_reason()
    assert reason is not None, "probe must report the capability as absent"
    assert "enable_load_extension" in reason, f"must name the capability: {reason}"
    assert "keyword" in reason.lower(), f"must name the real degrade: {reason}"


def test_store_fails_loudly_when_sqlite3_lacks_extension_support(tmp_path, monkeypatch):
    """No silent degrade: the store raises a typed, precise BackendUnavailable."""
    monkeypatch.setattr(um, "_sqlite3_supports_extension_loading", lambda: False)
    with pytest.raises(um.BackendUnavailable) as excinfo:
        um.UnifiedMemory(path=tmp_path / "m.sqlite", embedder=_fake_embedder())
    msg = str(excinfo.value)
    assert "enable_load_extension" in msg, f"must name the capability: {msg}"
    assert "sqlite3" in msg, f"must name the subsystem: {msg}"


def test_vec0_capability_present_on_linux_x86_64():
    """Anti-fake-green guard: the skip must never quietly eat Linux coverage.

    Linux x86-64 ships the vendored vec0.so, so the vec0 lane MUST really run
    there.  If that tier ever loses the capability, this goes RED instead of
    letting ``requires_vec0`` silently turn 11 real tests into skips.
    """
    if not (sys.platform.startswith("linux") and platform.machine() == "x86_64"):
        pytest.skip("guard covers the Linux x86-64 tier that ships the vendored vec0.so")
    assert _VEC0_UNAVAILABLE is None, (
        "Linux x86-64 ships hydra/_vendor/sqlite_vec/vec0.so and MUST exercise the "
        f"real vec0 lane, not skip it: {_VEC0_UNAVAILABLE}"
    )
