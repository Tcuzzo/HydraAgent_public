"""Worker memory context packets for Hydra job execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hydra.memory_kernel import assemble_memory_briefing


WORKER_MEMORY_CONTEXT_SCHEMA = "hydra.worker_memory_context.v1"
VERIFICATION_NORMS = (
    "Run focused verification commands before claiming worker output is usable.",
    "Record evidence paths for job packet, diff, command log, result, review, and failures.",
    "Treat memory as sourced prior context; verify live repo state before claiming current facts.",
)


def build_worker_memory_context(
    *,
    query: str,
    repo_root: Path,
    memory_root: Path | None = None,
    budget_chars: int = 6000,
) -> dict[str, Any]:
    repo = repo_root.expanduser().resolve()
    briefing = assemble_memory_briefing(
        query,
        repo_root=repo,
        memory_root=memory_root,
        budget_chars=budget_chars,
    )
    return {
        "schema": WORKER_MEMORY_CONTEXT_SCHEMA,
        "query": query,
        "repo_root": str(repo),
        "briefing": briefing,
        "repo_conventions": _repo_conventions(repo),
        "verification_norms": list(VERIFICATION_NORMS),
        "proof": [
            f"selected_records={briefing['selected_count']}",
            f"quality_verdict={briefing['quality']['verdict']}",
            "source=hydra.memory_kernel",
        ],
    }


def _repo_conventions(repo: Path) -> list[dict[str, str]]:
    conventions = []
    for rel in (".hydraAgent/PRINCIPLES.md", ".hydraAgent/PHILOSOPHY.md", "docs/HYDRA_BUILD_LEDGER.md"):
        path = repo / rel
        if path.is_file():
            conventions.append({"path": rel, "summary": _first_meaningful_line(path)})
    return conventions


def _first_meaningful_line(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip("# ").strip()
        if stripped:
            return stripped[:240]
    return path.name
