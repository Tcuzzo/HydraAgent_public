"""Ad-hoc keyword recall over HydraAgent's own memory.

Searches three sources in parallel:

* Durable lessons (`<memory_root>/workspace/memory/hydra-lessons.md`)
* Failure clusters (`<evidence_root>` via §10.56)
* Recent slice promotions (`<repo_root>/STATUS.md` ## Promotions log)

Returns a ranked combined hit list plus per-source breakdowns. Ranking is
keyword-count based (no external deps) — simple, deterministic, and good
enough for the typical "did we already learn this?" lookup. Higher-budget
retrieval can layer on top of the same surface later.

Read-only. Refuses empty queries and non-existent repo/memory/evidence
roots with named errors.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hydra.failure_clusters import FailureClusterError, cluster_failures
from hydra.lessons import LESSON_RELATIVE_PATH
from hydra.local_memory import DEFAULT_MEMORY_ROOT


SCHEMA = "hydra.recall.v1"
DEFAULT_TOP_K = 5
SNIPPET_BYTES = 280
_TOKEN_RE = re.compile(r"[A-Za-z0-9_§\.\-]{2,}")


class RecallError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def recall(
    query: str,
    *,
    repo_root: str | Path,
    memory_root: str | Path | None = None,
    evidence_root: str | Path | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise RecallError("query must be a non-empty string")
    if top_k <= 0:
        raise RecallError("top_k must be a positive integer")

    repo_root_path = Path(repo_root).expanduser().resolve()
    if not repo_root_path.is_dir():
        raise RecallError(f"repo_root is not a directory: {repo_root_path}")
    memory_root_path = (
        Path(memory_root).expanduser().resolve() if memory_root else DEFAULT_MEMORY_ROOT
    )
    evidence_root_path = (
        Path(evidence_root).expanduser().resolve()
        if evidence_root
        else repo_root_path / "evidence"
    )

    tokens = _tokenize(query)
    if not tokens:
        raise RecallError("query did not yield any searchable tokens")

    hits: list[dict[str, Any]] = []
    hits.extend(_search_lessons(memory_root_path, tokens))
    hits.extend(_search_clusters(evidence_root_path, tokens))
    hits.extend(_search_promotions(repo_root_path, tokens))

    # Score = sum of token-occurrences. Ranking is stable: score desc, then source, then key.
    hits.sort(key=lambda h: (-h["score"], h["source"], h["title"]))
    combined = hits[:top_k]

    by_source: dict[str, list[dict[str, Any]]] = {"lesson": [], "cluster": [], "promotion": []}
    for hit in hits:
        bucket = by_source.setdefault(hit["source"], [])
        if len(bucket) < top_k:
            bucket.append(hit)

    return {
        "schema": SCHEMA,
        "query": query,
        "tokens": tokens,
        "top_k": top_k,
        "totals": {
            "all_hits": len(hits),
            "by_source": {k: sum(1 for h in hits if h["source"] == k) for k in ("lesson", "cluster", "promotion")},
        },
        "combined_top": combined,
        "per_source_top": by_source,
        "proof": [
            f"query_tokens={len(tokens)}",
            f"lessons_searched={(memory_root_path / LESSON_RELATIVE_PATH).is_file()}",
            f"evidence_searched={evidence_root_path.is_dir()}",
            f"status_md_searched={(repo_root_path / 'STATUS.md').is_file()}",
            f"all_hits={len(hits)}",
            f"top_k={top_k}",
        ],
        "policy": "read-only keyword recall; sources: lessons, clusters, promotions",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra recall: {report['query']!r}",
        f"tokens={report['tokens']}",
        f"hits={report['totals']['all_hits']} "
        + "(" + ", ".join(f"{k}={v}" for k, v in report["totals"]["by_source"].items()) + ")",
        f"top {report['top_k']}:",
    ]
    if not report["combined_top"]:
        lines.append("  - no hits")
    for hit in report["combined_top"]:
        lines.append(f"  - [{hit['source']}] score={hit['score']} {hit['title']}")
        for line in hit["snippet"].splitlines()[:3]:
            lines.append(f"      {line}")
    return "\n".join(lines) + "\n"


def _tokenize(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _score(haystack: str, tokens: list[str]) -> int:
    haystack_lower = haystack.lower()
    return sum(haystack_lower.count(t) for t in tokens)


def _snippet(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= SNIPPET_BYTES:
        return text.strip()
    return encoded[:SNIPPET_BYTES].decode("utf-8", errors="replace").strip() + " …"


def _search_lessons(memory_root: Path, tokens: list[str]) -> list[dict[str, Any]]:
    path = memory_root / LESSON_RELATIVE_PATH
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Split on lesson headers (## timestamp)
    entries = re.split(r"\n(?=## 20\d{2}-)", text)
    out: list[dict[str, Any]] = []
    for entry in entries:
        entry = entry.strip()
        if not entry or not entry.startswith("## "):
            continue
        score = _score(entry, tokens)
        if score == 0:
            continue
        header_line = entry.splitlines()[0]
        title = header_line.lstrip("# ").strip()
        out.append({
            "source": "lesson",
            "title": title,
            "score": score,
            "snippet": _snippet(entry),
        })
    return out


def _search_clusters(evidence_root: Path, tokens: list[str]) -> list[dict[str, Any]]:
    if not evidence_root.is_dir():
        return []
    try:
        report = cluster_failures(evidence_root)
    except FailureClusterError:
        return []
    out: list[dict[str, Any]] = []
    for cluster in report["clusters"]:
        body = (
            f"{cluster['id']} x{cluster['count']} severity={cluster['severity']}\n"
            f"{cluster['repair_target']}"
        )
        score = _score(body, tokens)
        if score == 0:
            continue
        out.append({
            "source": "cluster",
            "title": cluster["id"],
            "score": score,
            "snippet": _snippet(body),
        })
    return out


def _search_promotions(repo_root: Path, tokens: list[str]) -> list[dict[str, Any]]:
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
    out: list[dict[str, Any]] = []
    for row in rows:
        score = _score(row, tokens)
        if score == 0:
            continue
        # Extract slice name for the title
        m = re.search(r"§(\d+\.\d+-[^`]+)", row)
        title = f"§{m.group(1)}" if m else row[:80]
        out.append({
            "source": "promotion",
            "title": title,
            "score": score,
            "snippet": _snippet(row),
        })
    return out
