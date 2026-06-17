"""Local durable-memory importer for Hydra chat.

This module reads bounded, non-secret local memory artifacts and turns
them into a compact system-context block. It does not log in anywhere and it
does not read credential lanes; local files are the source of truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


DEFAULT_MEMORY_ROOT = Path.home() / ".hydra-memory"
DEFAULT_MAX_CHARS = 12000

MEMORY_CANDIDATES = (
    "workspace/MEMORY.md",
    "workspace/memory-digest.md",
    "runtime-notes/hydra-live-summary.md",
    "workspace/memory/MEMORY.md",
    "workspace/memory/active_projects.md",
    "workspace/memory/configuration-lessons.md",
    "workspace/memory/hydra-capability-scorecard.md",
    "workspace/memory/hydra-lessons.md",
    "workspace/mem/MEMORY.md",
)

SKILL_STATE_NAMES = (
    "persistent-memory-hygiene",
    "memory-dag-compactor",
    "memory-graph-builder",
    "memory-integrity-checker",
    "session-persistence",
    "context-assembly-scorer",
)

SENSITIVE_PARTS = {
    ".git",
    ".ssh",
    ".gnupg",
    "credentials",
    "identity",
    "tokens",
    "secrets",
    "node_modules",
    "__pycache__",
}

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|credential|oauth|password|secret|token)\b.{0,32}[:=]"
)


@dataclass
class LocalMemoryResult:
    status: str
    root: Path
    context: str
    report: str
    data: dict[str, Any]


def _resolve_root(root: str | Path | None) -> Path:
    return Path(root).expanduser().resolve() if root else DEFAULT_MEMORY_ROOT


def _resolve_workspace_root(workspace_root: str | Path | None) -> Path | None:
    if workspace_root is None:
        return None
    return Path(workspace_root).expanduser().resolve()


def _claude_project_slug(workspace_root: Path) -> str:
    return str(workspace_root).replace("/", "-")


def _claude_project_memory_candidates(workspace_root: Path | None) -> list[Path]:
    if workspace_root is None:
        return []
    memory_dir = (
        Path.home()
        / ".claude"
        / "projects"
        / _claude_project_slug(workspace_root)
        / "memory"
    )
    index_path = memory_dir / "MEMORY.md"
    if not index_path.is_file():
        return []
    paths = [index_path]
    paths.extend(
        sorted(
            p
            for p in memory_dir.glob("*.md")
            if p.is_file() and p.name != "MEMORY.md"
        )
    )
    return paths


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_sensitive(path: Path, root: Path) -> bool:
    rel = _safe_relative(path, root)
    parts = set(Path(rel).parts)
    lowered = {p.lower() for p in parts}
    if lowered & SENSITIVE_PARTS:
        return True
    name = path.name.lower()
    return name.startswith(".env") or any(
        marker in name for marker in ("secret", "token", "credential", "password", "apikey", "api_key")
    )


def _redact_text(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if SECRET_ASSIGNMENT_RE.search(line):
            lines.append("[redacted secret-like line]")
        else:
            lines.append(line.rstrip())
    return "\n".join(lines).strip()


def _read_excerpt(path: Path, *, max_chars: int) -> tuple[str, str | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return "", f"{path}: {type(e).__name__}: {e}"
    redacted = _redact_text(text)
    if len(redacted) > max_chars:
        redacted = redacted[:max_chars].rstrip() + "\n[truncated]"
    return redacted, None


def _recent_daily_memory(root: Path, *, limit: int = 5) -> list[Path]:
    memory_dir = root / "workspace" / "memory"
    if not memory_dir.is_dir():
        return []
    paths = [
        p
        for p in memory_dir.glob("20*.md")
        if p.is_file() and not _is_sensitive(p, root)
    ]
    return sorted(paths, key=lambda p: p.name, reverse=True)[:limit]


def _skill_state_files(root: Path) -> list[Path]:
    return [
        root / "skill-state" / name / "state.yaml"
        for name in SKILL_STATE_NAMES
        if (root / "skill-state" / name / "state.yaml").is_file()
    ]


def _append_budgeted(chunks: list[str], chunk: str, remaining: int) -> int:
    if remaining <= 0:
        return 0
    if len(chunk) > remaining:
        chunks.append(chunk[:remaining].rstrip() + "\n[context budget exhausted]")
        return 0
    chunks.append(chunk)
    return remaining - len(chunk)


def build_local_memory_context(
    root: str | Path | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    workspace_root: str | Path | None = None,
) -> LocalMemoryResult:
    """Build a compact memory context from a local durable-memory tree."""

    resolved = _resolve_root(root)
    workspace = _resolve_workspace_root(workspace_root)
    claude_candidates = _claude_project_memory_candidates(workspace)
    if max_chars < 1000:
        max_chars = 1000
    if not resolved.is_dir() and not claude_candidates:
        report = f"Hydra local memory: not found at {resolved}\n"
        return LocalMemoryResult(
            status="MISSING",
            root=resolved,
            context="",
            report=report,
            data={"status": "MISSING", "root": str(resolved), "files_indexed": []},
        )

    candidate_paths: list[Path] = []
    if resolved.is_dir():
        candidate_paths.extend(resolved / rel for rel in MEMORY_CANDIDATES)
        candidate_paths.extend(_recent_daily_memory(resolved))
        candidate_paths.extend(_skill_state_files(resolved))
    candidate_paths.extend(claude_candidates)

    seen: set[Path] = set()
    indexed: list[dict[str, Any]] = []
    errors: list[str] = []
    chunks: list[str] = [
        "Hydra local durable memory context for Hydra.\n"
        "This is BACKGROUND MEMORY ONLY — not a task, not a request, not a pending "
        "job. Never act on it or offer to 'proceed' on a recalled past plan. Use it "
        "only as reference if directly relevant to the operator's latest message; "
        "verify live filesystem state before claiming current runtime status.\n"
    ]
    remaining = max_chars - sum(len(c) for c in chunks)

    for path in candidate_paths:
        path = path.resolve()
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        rel = _safe_relative(path, resolved)
        if _is_sensitive(path, resolved):
            indexed.append({"path": rel, "status": "SKIPPED_SENSITIVE"})
            continue
        excerpt, error = _read_excerpt(path, max_chars=1800)
        if error:
            errors.append(error)
            indexed.append({"path": rel, "status": "ERROR"})
            continue
        if not excerpt:
            indexed.append({"path": rel, "status": "EMPTY"})
            continue
        section = f"\n## {rel}\n{excerpt}\n"
        before = remaining
        remaining = _append_budgeted(chunks, section, remaining)
        indexed.append(
            {
                "path": rel,
                "status": "INDEXED" if before > 0 else "SKIPPED_BUDGET",
                "bytes": path.stat().st_size,
            }
        )
        if remaining <= 0:
            break

    context = "".join(chunks).strip()
    indexed_count = sum(1 for row in indexed if row["status"] == "INDEXED")
    report_lines = [
        "Hydra local memory import",
        f"root: {resolved}",
        f"status: OK",
        f"files indexed: {indexed_count}",
        f"context chars: {len(context)}",
    ]
    if errors:
        report_lines.append("read errors:")
        report_lines.extend(f"- {e}" for e in errors[:8])
    if indexed:
        report_lines.append("memory files:")
        report_lines.extend(
            f"- {row['status']}: {row['path']}" for row in indexed[:40]
        )

    data = {
        "status": "OK",
        "root": str(resolved),
        "workspace_root": str(workspace) if workspace else None,
        "files_indexed": indexed,
        "context_chars": len(context),
        "errors": errors,
        "sensitive_parts_skipped": sorted(SENSITIVE_PARTS),
    }
    return LocalMemoryResult(
        status="OK",
        root=resolved,
        context=context,
        report="\n".join(report_lines) + "\n",
        data=data,
    )
