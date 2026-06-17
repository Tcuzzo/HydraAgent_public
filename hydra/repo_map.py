"""hydra.repo_map — Native ranked repo-map for code localization (slice 10).

Aider-style personalized PageRank over a file-reference graph, extracted
purely with stdlib ``ast`` (Python) and regex (everything else).
Zero third-party dependencies — pure stdlib, works offline.

Public API
----------
RepoMap(repo_root)
    .build(query, seed_files=None)         -> RepoMapResult
    .rank_files(query, seed_files=None)    -> list[tuple[str, float]]
    .emit_map(query, *, max_bytes, seed_files=None) -> str
    ._pagerank(personalization=None)       -> dict[str, float]
"""
from __future__ import annotations

import ast
import re
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKIP_DIRS: frozenset[str] = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "dist", "build", ".venv", "venv", ".tox",
})

DAMPING = 0.85
MAX_ITERS = 30
CONVERGENCE_TOL = 1e-6

# Identifier-match multiplier when a file path/name contains the query term
IDENT_MATCH_MULTIPLIER = 3.0
# Boost given to exact-locate hits (filename contains the query)
LOCATE_BOOST = 2.5

# Regex for extracting top-level defs from non-Python text files
_DEF_RE = re.compile(
    r'^\s*(?:function|def|class|func|fn|sub|proc|procedure)\s+([A-Za-z_][A-Za-z0-9_]*)',
    re.MULTILINE,
)
_IMPORT_RE = re.compile(r'^\s*(?:import|require|include|use)\s+["\']?([A-Za-z0-9_./\\-]+)', re.MULTILINE)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileNode:
    """Represents one file in the repo graph."""
    path: str           # repo-relative POSIX path
    defs: list[str] = field(default_factory=list)    # names defined here
    refs: list[str] = field(default_factory=list)    # names/modules referenced
    parse_error: str | None = None


@dataclass
class RepoMapResult:
    """Output of RepoMap.build()."""
    files: dict[str, FileNode]
    edges: dict[str, list[str]]   # file -> list[files it references]
    scores: dict[str, float]      # file -> PageRank score
    query: str
    seed_files: list[str]


# ---------------------------------------------------------------------------
# AST-based Python extractor
# ---------------------------------------------------------------------------

def _extract_python(source: str, path: str) -> tuple[list[str], list[str], str | None]:
    """Return (defs, refs, error_or_None) from Python source via ast."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as exc:
        return [], [], f"SyntaxError: {exc}"

    defs: list[str] = []
    refs: list[str] = []

    for node in ast.walk(tree):
        # Definitions
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defs.append(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defs.append(t.id)
        # References: imports
        elif isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                refs.append(node.module.split(".")[0])
            for alias in node.names:
                refs.append(alias.name)
        # References: attribute access and name uses (calls, etc.)
        elif isinstance(node, ast.Attribute):
            refs.append(node.attr)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs.append(node.id)

    return defs, refs, None


# ---------------------------------------------------------------------------
# Lightweight regex extractor for non-Python files
# ---------------------------------------------------------------------------

def _extract_nonpython(source: str) -> tuple[list[str], list[str]]:
    """Best-effort def/ref extraction for non-Python text files."""
    defs = [m.group(1) for m in _DEF_RE.finditer(source)]
    refs = [m.group(1) for m in _IMPORT_RE.finditer(source)]
    return defs, refs


# ---------------------------------------------------------------------------
# File scanner
# ---------------------------------------------------------------------------

def _is_text(raw: bytes) -> bool:
    """Heuristic: a file is text if it has no null bytes in first 8 KB."""
    return b"\x00" not in raw[:8192]


def _scan_repo(root: Path) -> dict[str, FileNode]:
    """Walk repo, parse each file, return {rel_path: FileNode}."""
    nodes: dict[str, FileNode] = {}

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Prune skip dirs in-place
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        current = Path(dirpath)

        for fname in sorted(filenames):
            fpath = current / fname
            try:
                rel = fpath.relative_to(root).as_posix()
            except ValueError:
                rel = fpath.as_posix()

            try:
                raw = fpath.read_bytes()
            except OSError:
                nodes[rel] = FileNode(path=rel, parse_error="OSError: unreadable")
                continue

            if not _is_text(raw):
                nodes[rel] = FileNode(path=rel, parse_error="binary: skipped")
                continue

            try:
                source = raw.decode("utf-8", errors="replace")
            except Exception as exc:
                nodes[rel] = FileNode(path=rel, parse_error=f"decode error: {exc}")
                continue

            if fname.endswith(".py"):
                defs, refs, err = _extract_python(source, rel)
                nodes[rel] = FileNode(path=rel, defs=defs, refs=refs, parse_error=err)
            else:
                defs, refs = _extract_nonpython(source)
                nodes[rel] = FileNode(path=rel, defs=defs, refs=refs)

    return nodes


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _build_edges(nodes: dict[str, FileNode]) -> dict[str, list[str]]:
    """Build file->file reference edges.

    For each file A: if any ref in A.refs matches a def in file B (or the
    base name / stem of file B), add an edge A -> B.
    Also: if file A's refs include a module name matching file B's stem, add edge.
    """
    # Build lookup: symbol -> list of files defining it
    sym_to_files: dict[str, list[str]] = {}
    # Also index by file stem (for import-by-module-name)
    stem_to_file: dict[str, list[str]] = {}

    for rel, node in nodes.items():
        stem = Path(rel).stem.lower()
        stem_to_file.setdefault(stem, []).append(rel)
        for defname in node.defs:
            sym_to_files.setdefault(defname.lower(), []).append(rel)

    edges: dict[str, list[str]] = {rel: [] for rel in nodes}

    for rel, node in nodes.items():
        if node.parse_error and not node.defs and not node.refs:
            continue
        targets: set[str] = set()
        for ref in node.refs:
            ref_lower = ref.lower()
            # Symbol-level match
            for target in sym_to_files.get(ref_lower, []):
                if target != rel:
                    targets.add(target)
            # Module-level match (import foo → foo.py)
            for target in stem_to_file.get(ref_lower, []):
                if target != rel:
                    targets.add(target)
        edges[rel] = sorted(targets)

    return edges


# ---------------------------------------------------------------------------
# Native PageRank (power iteration)
# ---------------------------------------------------------------------------

def _pagerank(
    nodes: list[str],
    edges: dict[str, list[str]],
    personalization: dict[str, float] | None = None,
    damping: float = DAMPING,
    max_iters: int = MAX_ITERS,
    tol: float = CONVERGENCE_TOL,
) -> dict[str, float]:
    """Personalized PageRank via power iteration.

    personalization: dict mapping node -> weight bias (need not be normalized).
    Returns a dict of {node: score} with values summing to ~1.0.
    """
    n = len(nodes)
    if n == 0:
        return {}

    node_idx: dict[str, int] = {node: i for i, node in enumerate(nodes)}
    # Default personalization = uniform
    if personalization:
        total_p = sum(personalization.values())
        if total_p <= 0:
            p_vec = [1.0 / n] * n
        else:
            p_vec = [personalization.get(node, 0.0) / total_p for node in nodes]
    else:
        p_vec = [1.0 / n] * n

    # Out-degree for each node
    out_degree: list[int] = [len(edges.get(node, [])) for node in nodes]

    # Build adjacency as incoming edges: in_links[j] = list of i that link to j
    in_links: list[list[int]] = [[] for _ in range(n)]
    for node, targets in edges.items():
        i = node_idx.get(node)
        if i is None:
            continue
        for t in targets:
            j = node_idx.get(t)
            if j is not None:
                in_links[j].append(i)

    # Initialize
    rank = [1.0 / n] * n

    for _ in range(max_iters):
        new_rank = [0.0] * n
        # Dangling nodes (out_degree 0) distribute to all
        dangling_sum = sum(rank[i] for i in range(n) if out_degree[i] == 0)

        for j in range(n):
            # Contribution from in-links
            link_sum = sum(rank[i] / out_degree[i] for i in in_links[j] if out_degree[i] > 0)
            new_rank[j] = (
                damping * (link_sum + dangling_sum / n)
                + (1.0 - damping) * p_vec[j]
            )

        # Check convergence (L1 norm)
        delta = sum(abs(new_rank[i] - rank[i]) for i in range(n))
        rank = new_rank
        if delta < tol:
            break

    # Normalize
    total = sum(rank)
    if total > 0:
        rank = [r / total for r in rank]

    return {nodes[i]: rank[i] for i in range(n)}


# ---------------------------------------------------------------------------
# Identifier-match and locate boost scoring
# ---------------------------------------------------------------------------

def _query_terms(query: str) -> list[str]:
    """Split a query into lower-case identifier tokens."""
    tokens = re.findall(r'[a-z0-9_]+', query.lower())
    return [t for t in tokens if len(t) >= 2]


def _identifier_multiplier(rel: str, node: FileNode, terms: list[str]) -> float:
    """Return a >1.0 multiplier if the file/symbols match query terms."""
    if not terms:
        return 1.0
    text = (rel + " " + " ".join(node.defs)).lower()
    hits = sum(1 for t in terms if t in text)
    if hits == 0:
        return 1.0
    return 1.0 + (IDENT_MATCH_MULTIPLIER - 1.0) * min(hits, len(terms)) / len(terms)


def _locate_boost(rel: str, terms: list[str]) -> float:
    """Return LOCATE_BOOST if the file name contains a query term."""
    name_lower = Path(rel).name.lower()
    for t in terms:
        if t in name_lower:
            return LOCATE_BOOST
    return 1.0


# ---------------------------------------------------------------------------
# RepoMap
# ---------------------------------------------------------------------------

class RepoMap:
    """Ranked repo-map using native personalized PageRank + locate boosting."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).expanduser().resolve()
        self._nodes: dict[str, FileNode] = {}
        self._edges: dict[str, list[str]] = {}
        self._built = False

    # ------------------------------------------------------------------
    # Core build
    # ------------------------------------------------------------------

    def build(
        self,
        query: str,
        seed_files: list[str] | None = None,
    ) -> RepoMapResult:
        """Scan repo, build graph, run personalized PageRank."""
        self._nodes = _scan_repo(self.repo_root)
        self._edges = _build_edges(self._nodes)
        self._built = True

        terms = _query_terms(query)
        personalization = self._make_personalization(query, seed_files, terms)
        scores = _pagerank(
            list(self._nodes.keys()),
            self._edges,
            personalization=personalization,
        )
        # Apply identifier + locate multipliers
        final_scores: dict[str, float] = {}
        for rel, base_score in scores.items():
            node = self._nodes[rel]
            mult = _identifier_multiplier(rel, node, terms) * _locate_boost(rel, terms)
            final_scores[rel] = base_score * mult

        # Re-normalize after multipliers
        total = sum(final_scores.values())
        if total > 0:
            final_scores = {k: v / total for k, v in final_scores.items()}

        return RepoMapResult(
            files=self._nodes,
            edges=self._edges,
            scores=final_scores,
            query=query,
            seed_files=list(seed_files or []),
        )

    def _make_personalization(
        self,
        query: str,
        seed_files: list[str] | None,
        terms: list[str],
    ) -> dict[str, float] | None:
        """Build personalization vector from query terms + seed files."""
        if not self._nodes:
            return None

        p: dict[str, float] = {}

        # Seed files get a large bias
        for sf in (seed_files or []):
            # Normalize seed_file path
            sf_norm = sf.replace("\\", "/")
            if sf_norm in self._nodes:
                p[sf_norm] = 5.0
            else:
                # Try case-insensitive
                for rel in self._nodes:
                    if rel.lower() == sf_norm.lower():
                        p[rel] = 5.0
                        break

        # Query-term bias: files whose name/defs contain query terms
        for rel, node in self._nodes.items():
            mult = _identifier_multiplier(rel, node, terms)
            if mult > 1.0:
                p[rel] = p.get(rel, 0.0) + mult

        return p if p else None

    # ------------------------------------------------------------------
    # Internal PageRank access (used by tests)
    # ------------------------------------------------------------------

    def _pagerank(self, personalization: dict[str, float] | None = None) -> dict[str, float]:
        """Run raw PageRank on the current graph (call build() first)."""
        if not self._built:
            self.build(query="")
        return _pagerank(list(self._nodes.keys()), self._edges, personalization=personalization)

    # ------------------------------------------------------------------
    # rank_files
    # ------------------------------------------------------------------

    def rank_files(
        self,
        query: str,
        seed_files: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return sorted list of (rel_path, score) descending.

        Only returns files that are parseable (not pure binary/unreadable).
        """
        result = self.build(query=query, seed_files=seed_files)
        ranked = sorted(result.scores.items(), key=lambda kv: kv[1], reverse=True)
        # Filter out pure-binary / unreadable nodes that have zero useful content
        filtered = [
            (rel, score)
            for rel, score in ranked
            if not (
                self._nodes[rel].parse_error
                and not self._nodes[rel].defs
                and not self._nodes[rel].refs
            )
            or score > 0
        ]
        return filtered

    # ------------------------------------------------------------------
    # emit_map — budget-aware text rendering
    # ------------------------------------------------------------------

    def emit_map(
        self,
        query: str,
        *,
        max_bytes: int,
        seed_files: list[str] | None = None,
    ) -> str:
        """Emit a ranked map string that fits within max_bytes.

        Format per file:
            <rel_path>: <comma-sep top defs>
        Files are included in rank order until the budget is exhausted.
        """
        if max_bytes <= 0:
            return ""

        ranked = self.rank_files(query=query, seed_files=seed_files)
        lines: list[str] = []
        budget_used = 0

        header = f"# Repo map (query: {query})\n"
        header_bytes = len(header.encode("utf-8"))
        if header_bytes >= max_bytes:
            return ""
        budget_used += header_bytes
        lines.append(header)

        for rel, _score in ranked:
            node = self._nodes.get(rel)
            if node is None:
                continue
            # Skip pure binary/unreadable
            if node.parse_error and not node.defs:
                continue
            defs_str = ", ".join(node.defs[:10]) if node.defs else "(no defs)"
            line = f"{rel}: {defs_str}\n"
            line_bytes = len(line.encode("utf-8"))
            if budget_used + line_bytes > max_bytes:
                # Try a shorter truncated version
                short = f"{rel}: ...\n"
                short_bytes = len(short.encode("utf-8"))
                if budget_used + short_bytes <= max_bytes:
                    lines.append(short)
                    budget_used += short_bytes
                break
            lines.append(line)
            budget_used += line_bytes

        return "".join(lines)


# ---------------------------------------------------------------------------
# Convenience function used by context_engine_v2
# ---------------------------------------------------------------------------

def build_repo_map_context(
    repo_root: Path,
    query: str,
    max_bytes: int = 4000,
    seed_files: list[str] | None = None,
) -> str:
    """Build and return a repo-map string for insertion into context.

    Gracefully returns empty string on any error.
    """
    try:
        rm = RepoMap(repo_root)
        return rm.emit_map(query=query, max_bytes=max_bytes, seed_files=seed_files)
    except Exception:
        return ""
