"""Unified persistent vector memory for HydraAgent (v1, BUILDER pass).

One real SQLite file (`~/.hydra-memory/memory.sqlite`) is the single source of
truth, backed by:

* **nomic-embed-text** 768-d vectors (REUSED from
  :func:`hydra.semantic_recall.embed_nomic` — no new embedder), and
* **sqlite-vec** ``vec0`` cosine KNN (loaded via the vendored ``vec0.so`` /
  ``importlib`` fallback), with
* **FTS5** BM25 keyword retrieval, fused with **reciprocal-rank fusion**.

The schema uses a proven single-file layout so the Go
fabric can later share one file: ``entries`` + ``entries_fts`` (porter) +
``entries_vec`` (vec0 float[768] cosine) + ``entries_history`` + ``edges``.

Design choices (scoped to one local runtime — see slice doctrine):

1. **Episodic+semantic split** — embed ONCE at write time (kills
   semantic_recall.py's re-embed-every-turn flood).
2. **nomic asymmetric prefixes** — store with ``search_document: ``, query
   with ``search_query: `` (model-card fix the bare embedder omits).
3. **Add-only, invalidate-don't-delete** — never overwrite (policy: no hard
   deletes); exact-dup NOOP via ``content_hash`` UNIQUE plus a
   cosine ``>= 0.95`` top-1 NOOP.
4. **Hybrid retrieval + RRF(k=60)** — fuse vec0 KNN rank and FTS5 BM25 rank;
   catches literal IDs/error codes vectors miss; stdlib-only, no numpy.
5. **Scoped rows** — every row carries ``scope`` ('hydra' | 'shared' | ...);
   search filters on it so shared-vs-individual memory is enforced at the
   query boundary.

Graceful degrade: if the vec0 extension cannot load, or the embedder raises
(ollama down), the affected call raises a typed :class:`BackendUnavailable`
that the existing recall fallback already handles — chat never breaks.

DEFERRED (explicitly out of v1, no schema change to add later): the ``edges``
graph tier (stub), LLM fact-extraction on the write path, summary pressure
control, and quiet-hours curation. v1 also does NOT rewire the live chat path
or migrate working_memory's JSON — those are the next increments. It does NOT
touch ``memory/vector.py`` (its TF-IDF contract is pinned by the §10.8 eval).
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import sqlite3
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from hydra.local_memory import DEFAULT_MEMORY_ROOT
from hydra.semantic_recall import _cosine_similarity, embed_nomic

# ── Constants ───────────────────────────────────────────────────────────────

NOMIC_DIM = 768
RRF_K = 60
DEDUP_COSINE_THRESHOLD = 0.95

# ── Schema v2 additive columns (guarded ALTER TABLE — idempotent) ─────────
# Each ALTER is wrapped in a PRAGMA table_info check so re-running _open on
# an already-migrated db is a complete no-op.  Existing rows backfill to their
# DEFAULT automatically by SQLite; no UPDATE needed.
_SCHEMA_V2_COLUMNS: list[tuple[str, str]] = [
    ("memory_type",       "TEXT NOT NULL DEFAULT 'fact'"),
    ("importance",        "REAL NOT NULL DEFAULT 0.5"),
    ("access_count",      "INTEGER NOT NULL DEFAULT 0"),
    ("last_accessed_at",  "TEXT"),
    ("superseded_by",     "INTEGER"),
]
_SCHEMA_V2_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_entries_superseded "
    "ON entries(superseded_by)"
)

# Single source of truth: a sibling file of the existing corpus dir.
DEFAULT_DB_PATH = DEFAULT_MEMORY_ROOT / "memory.sqlite"

# nomic-embed-text asymmetric prefixes (model card).
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "

# Vendored vec0 extension (pinned fallback for PEP-668 environments where the
# `sqlite_vec` wheel cannot be installed system-wide).
_VENDORED_VEC0 = Path(__file__).resolve().parent / "_vendor" / "sqlite_vec" / "vec0.so"

_Embedder = Callable[..., list[float]]


# ── Typed error for graceful degrade ────────────────────────────────────────


class BackendUnavailable(RuntimeError):
    """A required backend (vec0 extension or the embedder) is unavailable.

    Callers (the recall fallback) catch this to degrade to the legacy
    file-dump path instead of crashing chat.
    """


# ── Hit ─────────────────────────────────────────────────────────────────────


@dataclass
class Hit:
    memory_id: int
    body: str
    score: float
    source: str | None
    scope: str


# ── vec0 extension resolution ───────────────────────────────────────────────


def resolve_vec0_extension() -> str | None:
    """Resolve a loadable path to the sqlite-vec ``vec0`` extension.

    Order (REUSE-first, then the verified fallback):

    1. ``HYDRA_VEC0_PATH`` env override (operator/ops escape hatch);
    2. ``importlib`` -> ``sqlite_vec.loadable_path()`` if the wheel is present;
    3. the pinned vendored ``vec0.so`` shipped beside this module.

    Returns the path **without** the platform suffix where SQLite expects it
    (it appends ``.so``/``.dylib`` itself), or ``None`` if nothing resolves.
    """
    override = os.environ.get("HYDRA_VEC0_PATH")
    if override and Path(override).exists():
        return _strip_ext(override)

    try:  # the wheel, if a venv install exists
        sqlite_vec = importlib.import_module("sqlite_vec")
        loadable = sqlite_vec.loadable_path()
        if loadable and Path(loadable).exists():
            return _strip_ext(loadable)
    except Exception:
        pass

    if _VENDORED_VEC0.exists():
        return _strip_ext(str(_VENDORED_VEC0))
    return None


def _strip_ext(path: str) -> str:
    for suffix in (".so", ".dylib", ".dll"):
        if path.endswith(suffix):
            return path[: -len(suffix)]
    return path


# ── Serialization ───────────────────────────────────────────────────────────


def _serialize(vec: list[float]) -> bytes:
    """Pack a float vector into vec0's little-endian float32 wire format."""
    return struct.pack(f"{len(vec)}f", *vec)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _recency_score(created_at: str | None, now_ts: float, half_life_seconds: float) -> float:
    """Exponential recency score in [0, 1]: 1.0 for brand-new, ~0.5 at half-life.

    ``created_at`` is an ISO-8601 string (UTC). Returns 0.0 on parse error.
    ``half_life_seconds`` is the half-life period in seconds (e.g. 30*86400 for 30 days).
    """
    if not created_at:
        return 0.0
    try:
        # Handle both offset-aware (e.g. "+00:00") and offset-naive forms.
        ts_str = created_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_seconds = max(now_ts - dt.timestamp(), 0.0)
    except ValueError:
        return 0.0
    if half_life_seconds <= 0.0:
        return 1.0
    return math.exp(-math.log(2) * age_seconds / half_life_seconds)


def _content_hash(scope: str, kind: str, text: str) -> str:
    h = hashlib.sha256()
    h.update(scope.encode("utf-8"))
    h.update(b"\x00")
    h.update(kind.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.strip().encode("utf-8"))
    return h.hexdigest()


# ── Schema (single-file layout) ─────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  scope       TEXT NOT NULL DEFAULT 'hydra',
  kind        TEXT NOT NULL DEFAULT 'fact',
  body        TEXT NOT NULL,
  source      TEXT,
  tags        TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL,
  valid_at    TEXT,
  invalid_at  TEXT,
  expired_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_scope ON entries(scope);
CREATE INDEX IF NOT EXISTS idx_entries_kind  ON entries(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts
  USING fts5(body, content='entries', content_rowid='id', tokenize='porter');

CREATE TABLE IF NOT EXISTS entries_history (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id INTEGER NOT NULL,
  event     TEXT NOT NULL,
  old       TEXT,
  new       TEXT,
  ts        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_memory ON entries_history(memory_id);

CREATE TABLE IF NOT EXISTS edges (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  from_id    INTEGER NOT NULL,
  to_id      INTEGER NOT NULL,
  relation   TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to   ON edges(to_id);
"""

# entries_vec is a vec0 virtual table, created separately (needs the extension
# and cannot use IF NOT EXISTS reliably across vec0 versions -> guarded check).
_VEC_TABLE = (
    "CREATE VIRTUAL TABLE entries_vec "
    f"USING vec0(emb float[{NOMIC_DIM}] distance_metric=cosine)"
)


# ── UnifiedMemory ────────────────────────────────────────────────────────────


class UnifiedMemory:
    """A persistent, hybrid (vector + keyword) memory store on one SQLite file.

    Parameters
    ----------
    path:
        SQLite file. Defaults to ``~/.hydra-memory/memory.sqlite``.
    embedder:
        768-d embedder. Defaults to the REAL local nomic client
        (:func:`hydra.semantic_recall.embed_nomic`); tests inject a fake.

    Raises
    ------
    BackendUnavailable:
        If the vec0 extension cannot be loaded (the store cannot function).
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        embedder: _Embedder = embed_nomic,
    ) -> None:
        self.path = Path(path).expanduser() if path else DEFAULT_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._embedder = embedder
        # Derive the vector dimension from the embedder so a test-injected fake
        # (e.g. 14-dim bag-of-words) and the production 768-d nomic embedder
        # both work correctly. We probe with a minimal string before opening the
        # DB so any embedder failure raises BackendUnavailable before schema DDL.
        try:
            _probe = embedder("probe")
        except Exception as exc:
            raise BackendUnavailable(f"embedder probe failed: {exc}") from exc
        if not isinstance(_probe, list) or not _probe:
            raise BackendUnavailable(
                f"embedder probe returned unexpected type: {type(_probe)}"
            )
        self._dim: int = len(_probe)
        self._db = self._open()

    # -- connection / schema -------------------------------------------------

    def _open(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path))
        con.row_factory = sqlite3.Row
        ext = resolve_vec0_extension()
        if not ext:
            con.close()
            raise BackendUnavailable(
                "sqlite-vec vec0 extension not found "
                "(set HYDRA_VEC0_PATH, install sqlite-vec, or restore the "
                "vendored hydra/_vendor/sqlite_vec/vec0.so)"
            )
        try:
            con.enable_load_extension(True)
            con.load_extension(ext)
        except (sqlite3.OperationalError, AttributeError) as exc:
            con.close()
            raise BackendUnavailable(f"cannot load vec0 extension {ext!r}: {exc}") from exc
        finally:
            try:
                con.enable_load_extension(False)
            except Exception:
                pass
        con.executescript(_SCHEMA)
        self._ensure_vec_table(con)
        self._migrate_schema_v2(con)
        con.commit()
        return con

    def _ensure_vec_table(self, con: sqlite3.Connection) -> None:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entries_vec'"
        ).fetchone()
        if not exists:
            vec_table_ddl = (
                "CREATE VIRTUAL TABLE entries_vec "
                f"USING vec0(emb float[{self._dim}] distance_metric=cosine)"
            )
            con.execute(vec_table_ddl)

    @staticmethod
    def _migrate_schema_v2(con: sqlite3.Connection) -> None:
        """Idempotent PRAGMA-guarded ALTER TABLE for schema v2 additive columns.

        Each column is only added if PRAGMA table_info shows it absent.
        Re-running on an already-migrated db is a complete no-op (no ALTER,
        no error). Existing rows backfill to the column DEFAULT automatically
        by SQLite — no UPDATE needed and no re-embedding.
        """
        existing = {
            row[1]
            for row in con.execute("PRAGMA table_info(entries)").fetchall()
        }
        for col_name, col_def in _SCHEMA_V2_COLUMNS:
            if col_name not in existing:
                con.execute(
                    f"ALTER TABLE entries ADD COLUMN {col_name} {col_def}"
                )
        con.execute(_SCHEMA_V2_INDEX)

    def close(self) -> None:
        try:
            self._db.commit()
        finally:
            self._db.close()

    def __enter__(self) -> "UnifiedMemory":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- embedding -----------------------------------------------------------

    def _embed(self, text: str) -> list[float]:
        """Embed via the injected embedder, mapping any failure to a typed error."""
        try:
            vec = self._embedder(text)
        except BackendUnavailable:
            raise
        except Exception as exc:  # ollama down / transport / model error
            raise BackendUnavailable(f"embedder failed: {exc}") from exc
        if not isinstance(vec, list) or len(vec) != self._dim:
            raise BackendUnavailable(
                f"embedder returned {len(vec) if isinstance(vec, list) else type(vec)} "
                f"dims, expected {self._dim}"
            )
        return [float(x) for x in vec]

    # -- write path ----------------------------------------------------------

    def add(
        self,
        text: str,
        *,
        scope: str = "hydra",
        kind: str = "fact",
        source: str | None = None,
        tags: Iterable[str] | None = None,
        valid_at: str | None = None,
    ) -> int:
        """Persist a distilled fact and return its memory id.

        Embeds ``search_document: `` + ``text`` once, inserts the row + 768-d
        vector + FTS row, and logs an ADD event. Add-only: an exact duplicate
        (same ``scope``/``kind``/``text`` -> same ``content_hash``) is a NOOP
        returning the existing id; a near-duplicate (cosine >= 0.95 to the
        top-1 nearest in the same scope) is also a NOOP.

        Raises :class:`BackendUnavailable` if the embedder is down (caller
        degrades). The store is never left half-written: the vector is computed
        BEFORE any row is inserted.
        """
        body = (text or "").strip()
        if not body:
            raise ValueError("cannot add empty text")

        chash = _content_hash(scope, kind, body)
        existing = self._db.execute(
            "SELECT id FROM entries WHERE content_hash = ?", (chash,)
        ).fetchone()
        if existing:
            return int(existing["id"])  # exact-dup NOOP, no new history

        # Embed BEFORE writing so a failure leaves the store untouched.
        vec = self._embed(_DOC_PREFIX + body)

        # Near-dup guard: NOOP if the nearest same-scope vector is >= threshold.
        near = self._nearest_in_scope(vec, scope)
        if near is not None:
            near_id, sim = near
            if sim >= DEDUP_COSINE_THRESHOLD:
                return near_id

        now = _now_iso()
        tags_json = json.dumps(sorted({str(t) for t in tags})) if tags else "[]"
        cur = self._db.execute(
            "INSERT INTO entries(scope, kind, body, source, tags, content_hash, "
            "created_at, valid_at) VALUES (?,?,?,?,?,?,?,?)",
            (scope, kind, body, source, tags_json, chash, now, valid_at or now),
        )
        memory_id = int(cur.lastrowid)
        self._db.execute(
            "INSERT INTO entries_fts(rowid, body) VALUES (?, ?)", (memory_id, body)
        )
        self._db.execute(
            "INSERT INTO entries_vec(rowid, emb) VALUES (?, ?)",
            (memory_id, _serialize(vec)),
        )
        self._db.execute(
            "INSERT INTO entries_history(memory_id, event, old, new, ts) "
            "VALUES (?,?,?,?,?)",
            (memory_id, "ADD", None, body, now),
        )
        self._db.commit()
        return memory_id

    def _nearest_in_scope(self, vec: list[float], scope: str) -> tuple[int, float] | None:
        """Return (id, cosine) of the nearest stored vector in ``scope``, or None.

        Uses vec0 KNN to find candidates, then pure-Python cosine (reused from
        semantic_recall) for the exact dedup decision.
        """
        rows = self._db.execute(
            "SELECT v.rowid AS id, v.distance AS dist "
            "FROM entries_vec v "
            "WHERE v.emb MATCH ? AND k = 5 "
            "ORDER BY v.distance",
            (_serialize(vec),),
        ).fetchall()
        for row in rows:
            ent = self._db.execute(
                "SELECT scope FROM entries WHERE id = ?", (row["id"],)
            ).fetchone()
            if ent is None or ent["scope"] != scope:
                continue
            stored = self._load_vector(int(row["id"]))
            if stored is None:
                continue
            return int(row["id"]), _cosine_similarity(vec, stored)
        return None

    def _load_vector(self, memory_id: int) -> list[float] | None:
        row = self._db.execute(
            "SELECT emb FROM entries_vec WHERE rowid = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        raw = row["emb"]
        return list(struct.unpack(f"{len(raw) // 4}f", raw))

    # -- read path -----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        top_k: int = 6,
        min_similarity: float | None = None,
    ) -> list[Hit]:
        """Hybrid semantic+keyword search with composite ranking, scope-filtered.

        Embeds ``search_query: `` + ``query`` once, runs a vec0 cosine KNN and
        an FTS5 BM25 pass, fuses the two rankings with reciprocal-rank fusion
        (k=60) to produce a candidate set, then re-ranks that candidate set with
        a composite score:

            score = w_cos*cosine + w_recency*recency(half-life)
                  + w_access*log1p(access_count) + w_importance*importance

        Weights come from ``hydra.memory_policy`` (loaded lazily; the frozen
        cosine-only DEFAULT is used when the yaml is absent so this degrades to
        today's pure-cosine behavior).

        Additional rules applied before scoring:
        * Rows where ``superseded_by IS NOT NULL`` are skipped.
        * After scoring, ``memory_type='core'`` rows are always included up to
          ``policy.core_max_rows`` (injected at the end of the result list if
          they are not already in the top scored set).
        * ``min_similarity`` (when set) drops any candidate whose TRUE cosine is
          below the floor; this is applied BEFORE composite scoring so
          loosely-related chunks never inflate the composite score.

        On SURFACED (returned) hits: ``access_count`` is incremented by 1 and
        ``last_accessed_at`` is set to now. Rows in the candidate pool but not
        returned are NOT bumped.

        ``Hit.score`` carries the TRUE query↔hit cosine (for callers that
        threshold it), NOT the composite or RRF value.

        Raises :class:`BackendUnavailable` if the embedder is down.
        """
        q = (query or "").strip()
        if not q:
            return []
        if top_k < 1:
            top_k = 1

        # Pull a wider pool than top_k so RRF + scope filter have material.
        pool = max(top_k * 4, 24)

        qvec = self._embed(_QUERY_PREFIX + q)
        vec_ranks = self._vec_search(qvec, pool)
        fts_ranks = self._fts_search(q, pool)

        fused = self._rrf_fuse(vec_ranks, fts_ranks)

        # Load policy (lazy import to avoid circular import at module load time).
        from hydra.memory_policy import load_policy as _load_policy

        policy = _load_policy()
        w = policy.score_weights
        half_life = float(policy.recency_half_life_days) * 86400.0  # days -> seconds
        now_ts = datetime.now(timezone.utc).timestamp()

        # ── Collect + score candidates ────────────────────────────────────────
        # Normalise all RRF scores to [0, 1] relative to the best candidate so
        # FTS-boosted literal-token matches (which RRF floats to rank-1 even
        # when cosine is middling) contribute proportionally to the composite.
        # The effective cosine used in the composite is max(true_cosine, rrf_norm)
        # so a strong FTS match is never down-weighted below its RRF relevance.
        fused_list = list(fused)
        max_rrf = fused_list[0][1] if fused_list else 1.0

        # candidate_rows: list of (composite_score, cosine, row, stored)
        candidate_rows: list[tuple[float, float, sqlite3.Row, list[float] | None]] = []
        core_candidates: list[tuple[float, float, sqlite3.Row, list[float] | None]] = []

        seen_ids: set[int] = set()
        for memory_id, rrf_score in fused_list:
            if memory_id in seen_ids:
                continue
            seen_ids.add(memory_id)
            row = self._db.execute(
                "SELECT id, body, source, scope, invalid_at, expired_at, "
                "memory_type, importance, access_count, created_at, superseded_by "
                "FROM entries WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            # add-only: never surface invalidated/expired rows.
            if row["invalid_at"] is not None or row["expired_at"] is not None:
                continue
            # Skip superseded rows (they are not deleted, just invisible to search).
            if row["superseded_by"] is not None:
                continue
            if scope is not None and row["scope"] != scope:
                continue
            # TRUE cosine: the relevance signal callers can floor and report.
            stored = self._load_vector(int(row["id"]))
            cosine = _cosine_similarity(qvec, stored) if stored is not None else 0.0
            if min_similarity is not None and cosine < min_similarity:
                continue

            # Effective cosine for composite: the higher of the true cosine and
            # the normalized RRF score (so FTS-only keyword wins stay promoted).
            rrf_norm = rrf_score / max_rrf if max_rrf > 0.0 else 0.0
            cosine_eff = max(cosine, rrf_norm)

            # Recency: exponential decay relative to created_at.
            recency = _recency_score(row["created_at"], now_ts, half_life)
            # Access: log1p normalised (we don't normalise to corpus max to keep
            # the score cheap; log1p naturally bounds growth).
            access_score = math.log1p(float(row["access_count"] or 0))
            importance = float(row["importance"] if row["importance"] is not None else 0.5)

            composite = (
                w.cosine * cosine_eff
                + w.recency * recency
                + w.access * access_score
                + w.importance * importance
            )

            if row["memory_type"] == "core":
                core_candidates.append((composite, cosine, row, stored))
            else:
                candidate_rows.append((composite, cosine, row, stored))

        # Sort non-core candidates by composite score descending.
        candidate_rows.sort(key=lambda t: t[0], reverse=True)

        # ── Build result list ─────────────────────────────────────────────────
        hits: list[Hit] = []
        surfaced_ids: list[int] = []

        for composite, cosine, row, _stored in candidate_rows:
            if len(hits) >= top_k:
                break
            hits.append(
                Hit(
                    memory_id=int(row["id"]),
                    body=row["body"],
                    score=round(cosine, 6),
                    source=row["source"],
                    scope=row["scope"],
                )
            )
            surfaced_ids.append(int(row["id"]))

        # Inject core rows (always included, up to core_max_rows).
        if policy.always_include_core and core_candidates:
            core_candidates.sort(key=lambda t: t[0], reverse=True)
            already_in = {h.memory_id for h in hits}
            injected = 0
            for composite, cosine, row, _stored in core_candidates:
                if injected >= policy.core_max_rows:
                    break
                mid = int(row["id"])
                if mid not in already_in:
                    hits.append(
                        Hit(
                            memory_id=mid,
                            body=row["body"],
                            score=round(cosine, 6),
                            source=row["source"],
                            scope=row["scope"],
                        )
                    )
                    surfaced_ids.append(mid)
                    already_in.add(mid)
                    injected += 1

        # ── Bump access on surfaced hits ──────────────────────────────────────
        if surfaced_ids:
            now_iso = _now_iso()
            for mid in surfaced_ids:
                self._db.execute(
                    "UPDATE entries "
                    "SET access_count = access_count + 1, last_accessed_at = ? "
                    "WHERE id = ?",
                    (now_iso, mid),
                )
            self._db.commit()

        return hits

    def _vec_search(self, qvec: list[float], k: int) -> list[int]:
        """Return entry ids ordered by ascending cosine distance (best first).

        Raises :class:`BackendUnavailable` if the vec0 table rejects the query
        vector (e.g. dimension mismatch between the injected embedder and an
        existing table created with a different dimension), so the caller can
        degrade gracefully to the file-corpus fallback path.
        """
        try:
            rows = self._db.execute(
                "SELECT rowid AS id FROM entries_vec "
                "WHERE emb MATCH ? AND k = ? ORDER BY distance",
                (_serialize(qvec), k),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise BackendUnavailable(
                f"vec0 search failed (possible dimension mismatch — "
                f"store dim may differ from embedder dim {self._dim}): {exc}"
            ) from exc
        return [int(r["id"]) for r in rows]

    def _fts_search(self, query: str, k: int) -> list[int]:
        """Return entry ids ordered by BM25 (best first). Tolerant of FTS syntax."""
        match = _fts_query(query)
        if not match:
            return []
        try:
            rows = self._db.execute(
                "SELECT rowid AS id FROM entries_fts "
                "WHERE entries_fts MATCH ? ORDER BY bm25(entries_fts) LIMIT ?",
                (match, k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [int(r["id"]) for r in rows]

    @staticmethod
    def _rrf_fuse(*rankings: list[int]) -> list[tuple[int, float]]:
        """Reciprocal-rank fusion (k=60) over one or more id rankings.

        score(id) = sum over rankings of 1 / (k + rank), rank 1-based.
        Returns (id, score) best-first.
        """
        scores: dict[int, float] = {}
        for ranking in rankings:
            for rank, memory_id in enumerate(ranking, start=1):
                scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (RRF_K + rank)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    # -- audit ---------------------------------------------------------------

    def get_history(self, memory_id: int) -> list[dict[str, Any]]:
        """Return the audit trail for one memory id, oldest first."""
        rows = self._db.execute(
            "SELECT event, old, new, ts FROM entries_history "
            "WHERE memory_id = ? ORDER BY id",
            (memory_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── FTS query sanitization ──────────────────────────────────────────────────

# Conversational filler that carries no retrieval signal. If these tokens reach
# the BM25 OR-join, every memory containing "you"/"how"/"do" matches, and RRF
# then promotes those off-topic chunks above the genuinely-relevant one (the live
# bug: "yo who are you ... model routing" pulled unrelated topics chunks).
# Dropping them (plus tokens of length <= 2) leaves only the content words.
_FTS_STOPWORDS = frozenset(
    {
        "yo", "who", "are", "you", "and", "what", "do", "how", "i", "me", "my",
        "the", "a", "of", "to", "is", "it", "about", "want", "done",
    }
)


# FTS5 MATCH treats many chars as syntax (e.g. '-', ':', '"'). For a free-text
# query we quote each alphanumeric/underscore token so literal IDs like
# E_QUIET_BREACH match verbatim without raising a syntax error. Short tokens
# (len <= 2) and conversational stop-words are dropped first so BM25 ranks on
# content words only — never on filler that matches half the corpus.
def _fts_query(query: str) -> str:
    import re

    tokens = re.findall(r"[A-Za-z0-9_]+", query)
    kept = [
        tok
        for tok in tokens
        if len(tok) > 2 and tok.lower() not in _FTS_STOPWORDS
    ]
    if not kept:
        return ""
    return " OR ".join(f'"{tok}"' for tok in kept)
