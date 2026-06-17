"""hydra.branding — visual identity and capability counts.

Lives outside any TUI so multiple surfaces (chat REPL, capability truth
report, Telegram, web UI) share one source of truth for what Hydra calls
itself.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def hydra_mark() -> str:
    """Short visual mark for headers."""
    return "🐲 H1 H2 H3 H4 H5"


def hydra_title() -> str:
    """Full title string for cockpit displays."""
    return f"HYDRA AGENT | {hydra_mark()}"


def hydra_counts(repo_root: Path | None = None) -> dict[str, int]:
    """Live capability tallies — read from disk on each call."""
    root = repo_root or REPO_ROOT
    return {
        "tools": _count_tools(root),
        "skill_docs": _count_skill_docs(root / "hydra" / "schemes"),
        "generated_skill_docs": _count_skill_docs(root / "hydra" / "schemes" / "generated"),
    }


def hydra_signal(repo_root: Path | None = None) -> str:
    """One-line capability signal for status bars."""
    counts = hydra_counts(repo_root)
    return (
        f"{counts['tools']} tools | "
        f"{counts['skill_docs']} skills | {counts['generated_skill_docs']} gen | local GPU"
    )


def _count_skill_docs(root: Path) -> int:
    if not root.is_dir():
        return 0
    return sum(1 for _ in root.rglob("SKILL.md"))


def _count_tools(repo_root: Path) -> int:
    try:
        from hydra.cli.tool_binding import bind_tools

        return len(bind_tools(repo_root))
    except Exception:
        runtime_root = repo_root / "skills"
        if not runtime_root.is_dir():
            return 0
        return sum(1 for path in runtime_root.glob("*.py") if path.name != "__init__.py")
