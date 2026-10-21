"""One-time, idempotent migration of the .md memory corpus into UnifiedMemory.

INCREMENT 2 (BUILDER pass). The live chat recall
(:func:`hydra.semantic_recall.build_semantic_memory_context`) used to re-embed
the whole ``.md`` memory corpus on EVERY turn. Increment 1 built the real
persistent ``vec0`` + nomic store (:class:`hydra.unified_memory.UnifiedMemory`,
``~/.hydra-memory/memory.sqlite``). This module migrates that SAME ``.md``
corpus into the store ONCE so the live recall can run a single persistent KNN
instead of an N+1 re-embed per turn.

Design (REUSE over rewrite — minimize new code):

* It walks the **exact** candidate set the live recall already reads, via the
  shared :func:`hydra.semantic_recall.resolve_corpus_chunks` (the SAME resolver
  ``build_semantic_memory_context`` resolves its corpus through) — which reuses
  ``local_memory`` candidate discovery (``_candidate_paths`` /
  ``_recent_daily_memory`` / ``_skill_state_files`` /
  ``_claude_project_memory_candidates``), ``_is_sensitive`` secret-skipping,
  ``_read_excerpt`` bounded read + ``_redact_text`` redaction, and
  ``_split_chunks`` chunking. NO new globber, NO new chunker, NO new redaction.
  The migration defaults to the live ``(root, workspace_root)`` so a no-arg
  migration covers the live corpus and the two paths can never drift.
* For each ``(relpath, chunk)`` it calls
  ``mem.add(chunk, scope='hydra', kind='memory', source=relpath)``. There is NO
  new embedder: :class:`UnifiedMemory` defaults to ``embed_nomic`` and embeds
  (``search_document: `` prefix) ONCE at write time.

Idempotent by construction. ``UnifiedMemory.add`` computes
``_content_hash(scope, kind, body)`` and, on a UNIQUE ``content_hash`` hit,
returns the existing id with NO insert and NO history event
(``unified_memory.py``). So an identical corpus -> identical chunks -> identical
hashes -> every ``add()`` short-circuits BEFORE embedding. Re-running on an
unchanged corpus yields ``added == 0``. New source files added since the last
run will produce ``added > 0`` (correct expected behavior — truly new chunks
get added). There is also a cosine ``>= 0.95`` near-dup NOOP for
semantically-identical-but-not-byte-identical chunks. The report counts
``added`` (a NEW id appeared) vs ``noop`` (an already-known id was returned), so
a second run on the same corpus reports ``added == 0``. No deletes, no
truncation — purely additive, dedup-gated (add-only policy: no hard deletes).

Tolerant of a mid-run backend failure: a per-chunk ``add()`` that raises
:class:`BackendUnavailable` (ollama down) aborts cleanly with a PARTIAL report.
Rows already committed by prior ``add()`` calls persist (``add`` embeds BEFORE
inserting, so no half-written row), and the next run resumes (idempotent) and
finishes the remainder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time

from hydra.semantic_recall import resolve_corpus_chunks
from hydra.unified_memory import BackendUnavailable, UnifiedMemory

# Every migrated chunk is written with this scope/kind so the live KNN
# (search(scope='hydra')) surfaces exactly the migrated corpus and provenance
# is uniform. MUST match the scope the live recall searches with.
MIGRATION_SCOPE = "hydra"
MIGRATION_KIND = "memory"


@dataclass
class MigrationReport:
    """Result of one :func:`migrate_md_corpus_to_unified` pass.

    Attributes
    ----------
    added:
        Count of chunks that produced a NEW store row this pass.
    noop:
        Count of chunks whose ``add()`` returned an already-known id (exact
        content_hash dup or cosine >= 0.95 near-dup) — no new row.
    chunks_seen:
        Total ``(relpath, chunk)`` pairs walked from the corpus.
    sources:
        Sorted distinct source relpaths that contributed at least one chunk.
    aborted:
        True if a per-chunk ``add()`` raised BackendUnavailable mid-run; the
        report is then PARTIAL (committed rows persist, resume next run).
    error:
        The abort reason when ``aborted`` is True, else ``None``.
    """

    added: int = 0
    noop: int = 0
    chunks_seen: int = 0
    sources: list[str] = field(default_factory=list)
    aborted: bool = False
    error: str | None = None


def migrate_md_corpus_to_unified(
    *,
    mem: UnifiedMemory | None = None,
    root: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> MigrationReport:
    """Migrate the live-recall ``.md`` corpus into ``mem``, idempotently.

    Walks the SAME candidate set the live recall reads (via the reused
    :func:`hydra.semantic_recall._collect_chunks`) and persists each chunk with
    ``mem.add(chunk, scope='hydra', kind='memory', source=relpath)``.

    Re-running on an **unchanged** corpus is an all-NOOP (``added == 0``):
    identical chunks hash-collide and ``add()`` short-circuits before embedding.
    New source files added since the last run will produce new rows (``added > 0``)
    — this is correct expected behavior, not a bug.

    Parameters
    ----------
    mem:
        The target store. Defaults to a freshly-opened
        :class:`UnifiedMemory` on the default db path with the default real
        ``embed_nomic`` embedder. Tests inject a tmp-path store with a fake
        embedder. An internally opened store is closed on return, including a partial
        failure; an injected store remains open for its owner.
    root:
        Memory tree root. ``None`` -> ``~/.hydra-memory`` (the live default),
        resolved by the shared :func:`resolve_corpus_chunks`.
    workspace_root:
        Repo root used to locate the ``~/.claude/projects/<slug>/memory`` corpus.
        ``None`` -> :data:`hydra.semantic_recall.DEFAULT_WORKSPACE_ROOT` (the
        live repo root cmd_chat/operator pass). This is the drift fix: a no-arg
        migration now walks the SAME 26-source corpus the live recall walks (all
        25 ``.claude`` project/feedback memories + MEMORY.md), instead of the
        single workspace file the old ``workspace_root=None`` default captured.

    Returns
    -------
    MigrationReport
        Counts of added vs noop chunks; ``aborted`` + ``error`` set on a
        mid-run BackendUnavailable (partial, resumable).
    """
    # Route corpus discovery through the SINGLE shared resolver so the migration
    # and the live recall can never resolve different candidate sets. With NO
    # args this resolves root=~/.hydra-memory + workspace_root=repo root == the
    # live corpus (>=26 sources). No new globber/chunker/redaction here.
    resolved, _workspace, _claude, chunks = resolve_corpus_chunks(root, workspace_root)

    from hydra.semantic_recall import _store_path_for_root
    store = mem if mem is not None else UnifiedMemory(path=_store_path_for_root(resolved))

    try:
        report = MigrationReport()
        sources: set[str] = set()

        report.chunks_seen = len(chunks)

        for relpath, chunk in chunks:
            body = (chunk or "").strip()
            if not body:
                continue
            # Snapshot known ids so we can tell a NEW row from a dedup-NOOP id.
            # (add() returns an existing id for both exact-hash and near-dup NOOPs.)
            existing_before = store._db.execute(
                "SELECT 1 FROM entries WHERE content_hash = ?",
                (_chash(body),),
            ).fetchone()
            try:
                memory_id = store.add(
                    body,
                    scope=MIGRATION_SCOPE,
                    kind=MIGRATION_KIND,
                    source=relpath,
                )
            except BackendUnavailable as exc:
                # ollama down mid-run: abort cleanly with a partial report. Already
                # committed rows persist; the next idempotent run resumes.
                report.aborted = True
                report.error = str(exc)
                break

            if existing_before is None and _is_new_row(store, memory_id, body):
                report.added += 1
                sources.add(relpath)
            else:
                report.noop += 1

        report.sources = sorted(sources)
        return report
    finally:
        if mem is None:
            store.close()


def _chash(body: str) -> str:
    """Content hash for the migration scope/kind (matches UnifiedMemory.add)."""
    from hydra.unified_memory import _content_hash

    return _content_hash(MIGRATION_SCOPE, MIGRATION_KIND, body)


def _is_new_row(store: UnifiedMemory, memory_id: int, body: str) -> bool:
    """True if ``memory_id`` is the row this exact ``body`` just created.

    add() returns the existing id on a near-dup (cosine >= 0.95) NOOP, whose
    stored body differs from ``body``. A NEW row has the same content_hash and
    body as what we just added. This distinguishes a genuine insert from a
    dedup-NOOP without relying on row-count races.
    """
    row = store._db.execute(
        "SELECT content_hash, body FROM entries WHERE id = ?",
        (memory_id,),
    ).fetchone()
    if row is None:
        return False
    return row["content_hash"] == _chash(body) and row["body"] == body


def sync_corpus_chunks(store: UnifiedMemory, chunks: list[tuple[str, str]], *,
                       max_new_embeddings: int = 32, time_budget: float = 10.0) -> dict:
    """Incrementally cache current chunks and return their exact retrieval IDs.

    Historical rows are kept for audit; only this current source manifest is
    eligible for retrieval. Changed, deleted and other-workspace chunks cannot
    survive merely because they remain in the cache. Embedding work is bounded
    per turn. A partial sync yields lexical retrieval of the full source corpus
    while the next turn resumes the idempotent indexing work.
    """
    started = time.monotonic()
    ids = []
    sources = {}
    previous = {}
    added = 0
    reused = 0
    incomplete = False
    for source, raw in chunks:
        body = raw.strip()
        if not body:
            continue
        exists = store._db.execute("SELECT id FROM entries WHERE content_hash = ?", (_chash(body),)).fetchone()
        if exists is not None:
            memory_id = int(exists["id"])
            reused += 1
        else:
            if added >= max_new_embeddings or time.monotonic() - started >= time_budget:
                incomplete = True
                continue
            memory_id = store.add(body, scope=MIGRATION_SCOPE, kind=MIGRATION_KIND, source=source)
            added += 1
        ids.append(memory_id)
        sources.setdefault(memory_id, source)
        if source in previous and previous[source] != memory_id:
            store.link(previous[source], memory_id, relation="same_source")
        previous[source] = memory_id
    summary = {"added": added, "reused": reused, "current_chunks": len(ids),
               "source_chunks": len(chunks), "incomplete": incomplete}
    return {"memory_ids": sorted(set(ids)), "sources": sources,
            "incomplete": incomplete, "reason": "incremental embedding budget reached" if incomplete else "",
            "summary": summary}
