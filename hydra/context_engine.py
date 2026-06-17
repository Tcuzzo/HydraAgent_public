"""Bounded context bundle assembly for HydraAgent planner / doer turns.

Picks up the highest-signal context — recent durable lessons, top failure
clusters, and the most-recent slice promotions — assembles them in priority
order under a hard byte budget, and emits both a structured manifest and a
ready-to-paste rendered block.

Why this exists: per the operator build plan, "context engineering is now
central" — strong agents win by feeding the model only the most relevant
context, not by stuffing everything in. This module is the runtime primitive
behind that discipline.

Deterministic, read-only. Higher-priority sources are selected first; once the
budget is exhausted, lower-priority sources are dropped and ``truncated`` is
set. Never writes to source files.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hydra.failure_clusters import FailureClusterError, cluster_failures
from hydra.local_memory import DEFAULT_MEMORY_ROOT
from hydra.lessons import LESSON_RELATIVE_PATH


SCHEMA = "hydra.context_engine.v1"
DEFAULT_BUDGET_BYTES = 8_192
DEFAULT_MAX_LESSONS = 10
DEFAULT_MAX_CLUSTERS = 10
DEFAULT_MAX_PROMOTIONS = 12
PREVIEW_BYTES_PER_SOURCE = 768

_LESSON_ENTRY_PATTERN = re.compile(r"^## (?P<ts>20\d{2}-.+?)$", re.MULTILINE)


class ContextEngineError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def assemble_context(
    *,
    repo_root: str | Path,
    memory_root: str | Path | None = None,
    evidence_root: str | Path | None = None,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
    max_lessons: int = DEFAULT_MAX_LESSONS,
    max_clusters: int = DEFAULT_MAX_CLUSTERS,
    max_promotions: int = DEFAULT_MAX_PROMOTIONS,
    query: str | None = None,
) -> dict[str, Any]:
    """Assemble a budget-bounded context bundle.

    Priority order: durable lessons → failure clusters → recent promotions.
    Items are added in that order until the budget is exhausted; later items
    are dropped (and the ``truncated`` flag is set).

    When ``query`` is provided, candidates within each priority bucket are
    re-ranked by relevance to the query (token-occurrence count, descending)
    before the budget is applied — so a long-tail-relevant lesson can beat
    a most-recent lesson when the query points at it.
    """
    if budget_bytes <= 0:
        raise ContextEngineError("budget_bytes must be a positive integer")
    if max_lessons < 0 or max_clusters < 0 or max_promotions < 0:
        raise ContextEngineError("max_* parameters must be non-negative")
    if query is not None and not isinstance(query, str):
        raise ContextEngineError("query must be a string or None")

    repo_root_path = Path(repo_root).expanduser().resolve()
    if not repo_root_path.is_dir():
        raise ContextEngineError(f"repo_root is not a directory: {repo_root_path}")
    memory_root_path = (
        Path(memory_root).expanduser().resolve() if memory_root else DEFAULT_MEMORY_ROOT
    )
    evidence_root_path = (
        Path(evidence_root).expanduser().resolve()
        if evidence_root
        else repo_root_path / "evidence"
    )

    query_tokens = _query_tokens(query) if query else []

    lesson_cands = _lesson_candidates(memory_root_path, max_lessons)
    cluster_cands = _cluster_candidates(evidence_root_path, max_clusters)
    promotion_cands = _promotion_candidates(repo_root_path, max_promotions)
    if query_tokens:
        lesson_cands = _rerank_by_query(lesson_cands, query_tokens)
        cluster_cands = _rerank_by_query(cluster_cands, query_tokens)
        promotion_cands = _rerank_by_query(promotion_cands, query_tokens)

    candidates: list[dict[str, Any]] = []
    candidates.extend(lesson_cands)
    candidates.extend(cluster_cands)
    candidates.extend(promotion_cands)

    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    selected_bytes = 0
    for cand in candidates:
        item_bytes = cand["bytes"]
        if selected_bytes + item_bytes > budget_bytes:
            dropped.append(cand)
            continue
        selected.append(cand)
        selected_bytes += item_bytes

    rendered = _render_bundle(selected)
    proof = [
        f"repo_root={repo_root_path}",
        f"memory_root={memory_root_path}",
        f"evidence_root={evidence_root_path}",
        f"budget_bytes={budget_bytes}",
        f"selected_bytes={selected_bytes}",
        f"selected={len(selected)} dropped={len(dropped)} candidates={len(candidates)}",
        f"query_tokens={len(query_tokens)}",
    ]
    return {
        "schema": SCHEMA,
        "budget_bytes": budget_bytes,
        "selected_bytes": selected_bytes,
        "truncated": bool(dropped),
        "sources": selected,
        "dropped": dropped,
        "rendered": rendered,
        "query": query,
        "query_tokens": list(query_tokens),
        "proof": proof,
        "policy": "priority: lessons → clusters → promotions; query-rerank within each bucket if provided; hard byte budget; read-only",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra context bundle",
        f"budget_bytes={report['budget_bytes']}  "
        f"selected_bytes={report['selected_bytes']}  "
        f"truncated={report['truncated']}",
        f"sources ({len(report['sources'])} selected, {len(report['dropped'])} dropped):",
    ]
    for src in report["sources"]:
        lines.append(f"  - [{src['kind']}] {src['title']} ({src['bytes']} bytes)")
    if report["dropped"]:
        lines.append("dropped (budget exceeded):")
        for src in report["dropped"][:5]:
            lines.append(f"  - [{src['kind']}] {src['title']} ({src['bytes']} bytes)")
        if len(report["dropped"]) > 5:
            lines.append(f"  - ... +{len(report['dropped']) - 5} more")
    lines.append("--- rendered context ---")
    lines.append(report["rendered"].rstrip("\n"))
    lines.append("--- end rendered ---")
    return "\n".join(lines) + "\n"


_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_§\.\-]{2,}")


def _query_tokens(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _QUERY_TOKEN_RE.finditer(query.lower()):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _rerank_by_query(
    candidates: list[dict[str, Any]], tokens: list[str]
) -> list[dict[str, Any]]:
    if not tokens or not candidates:
        return candidates
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for idx, cand in enumerate(candidates):
        haystack = (cand.get("title", "") + "\n" + cand.get("preview", "")).lower()
        score = sum(haystack.count(t) for t in tokens)
        # Original ordering preserved as tiebreak (insertion idx)
        scored.append((-score, idx, cand))
    scored.sort()
    return [item[2] for item in scored]


def _lesson_candidates(memory_root: Path, max_lessons: int) -> list[dict[str, Any]]:
    if max_lessons == 0:
        return []
    path = memory_root / LESSON_RELATIVE_PATH
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries = _parse_lesson_entries(text)
    # Most-recent first
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    entries = entries[:max_lessons]
    out: list[dict[str, Any]] = []
    for entry in entries:
        body = _bound_preview(entry["body"])
        out.append({
            "kind": "lesson",
            "title": entry["timestamp"],
            "bytes": len(body.encode("utf-8", errors="replace")),
            "preview": body,
        })
    return out


def _parse_lesson_entries(text: str) -> list[dict[str, str]]:
    matches = list(_LESSON_ENTRY_PATTERN.finditer(text))
    entries: list[dict[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        entries.append({
            "timestamp": m.group("ts").strip(),
            "body": text[start:end].strip(),
        })
    return entries


def _cluster_candidates(evidence_root: Path, max_clusters: int) -> list[dict[str, Any]]:
    if max_clusters == 0 or not evidence_root.is_dir():
        return []
    try:
        report = cluster_failures(evidence_root)
    except FailureClusterError:
        return []
    out: list[dict[str, Any]] = []
    for cluster in report["clusters"][:max_clusters]:
        title = f"{cluster['id']} (x{cluster['count']}, {cluster['severity']})"
        body = _bound_preview(
            f"{cluster['repair_target']}\nsources: {', '.join(cluster.get('sources', [])[:3])}"
        )
        out.append({
            "kind": "cluster",
            "title": title,
            "bytes": len(body.encode("utf-8", errors="replace")),
            "preview": body,
        })
    return out


def _promotion_candidates(repo_root: Path, max_promotions: int) -> list[dict[str, Any]]:
    if max_promotions == 0:
        return []
    status_path = repo_root / "STATUS.md"
    if not status_path.is_file():
        return []
    try:
        text = status_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    in_log = False
    rows: list[str] = []
    for line in text.splitlines():
        if line.startswith("## Promotions"):
            in_log = True
            continue
        if in_log and line.startswith("- `"):
            rows.append(line)
    # Newest first
    rows = list(reversed(rows))[:max_promotions]
    out: list[dict[str, Any]] = []
    for row in rows:
        body = _bound_preview(row)
        out.append({
            "kind": "promotion",
            "title": _promotion_title(row),
            "bytes": len(body.encode("utf-8", errors="replace")),
            "preview": body,
        })
    return out


def _promotion_title(row: str) -> str:
    m = re.search(r"§(\d+\.\d+-[^`]+)", row)
    return f"§{m.group(1)}" if m else row[:60]


def _bound_preview(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= PREVIEW_BYTES_PER_SOURCE:
        return text
    return encoded[:PREVIEW_BYTES_PER_SOURCE].decode("utf-8", errors="replace") + "\n[truncated]"


def _render_bundle(selected: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for src in selected:
        lines.append(f"### {src['kind']}: {src['title']}")
        lines.append(src["preview"])
        lines.append("")
    return "\n".join(lines)
