"""Search Hydra's materialized SKILL.md library for planner routing."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


SCHEMA = "hydra.skill_library_search.v1"


def search_skill_library(
    *,
    query: str,
    root: Path,
    skills_root: str = "hydra/schemes",
    limit: int = 8,
) -> dict[str, Any]:
    """Return compact skill hits from the materialized library ("schemes")."""
    query = str(query or "").strip()
    limit = max(1, min(int(limit), 25))
    base = (root / skills_root).resolve()
    # Safety: if the requested root is missing, fall back to the legacy name so a
    # stale checkout never "loses" the library (renamed hydra/skills -> hydra/schemes).
    if not base.is_dir():
        for alt in ("hydra/schemes", "hydra/skills"):
            cand = (root / alt).resolve()
            if cand.is_dir():
                base = cand
                break
    if not query:
        return _report(query=query, skills_root=base, hits=[], total_scanned=0)
    if not base.is_dir():
        return _report(query=query, skills_root=base, hits=[], total_scanned=0)

    tokens = _tokens(query)
    scored: list[tuple[int, dict[str, Any]]] = []
    total = 0
    for path in sorted(base.rglob("SKILL.md")):
        total += 1
        hit = _skill_hit(root, path)
        haystack = " ".join(
            [
                hit["skill_id"],
                hit["bundle"],
                hit["description"],
                hit["heading"],
                hit["summary"],
            ]
        ).lower()
        score = sum(haystack.count(token) for token in tokens)
        if score:
            scored.append((score, hit))

    hits = [hit for _score, hit in sorted(scored, key=lambda item: (-item[0], item[1]["path"]))[:limit]]
    return _report(query=query, skills_root=base, hits=hits, total_scanned=total)


def _report(*, query: str, skills_root: Path, hits: list[dict[str, Any]], total_scanned: int) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "query": query,
        "skills_root": str(skills_root),
        "total_scanned": total_scanned,
        "returned": len(hits),
        "hits": hits,
    }


def _skill_hit(root: Path, path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_frontmatter(text)
    rel = _rel(root, path)
    return {
        "skill_id": str(meta.get("name") or path.parent.name),
        "bundle": str(meta.get("bundle") or _bundle_name(root, path)),
        "description": str(meta.get("description") or ""),
        "path": rel,
        "heading": _first_heading(body),
        "summary": _summary(body),
        "allowed_tools": meta.get("allowed-tools") or [],
    }


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    data = yaml.safe_load(parts[1]) or {}
    return (data if isinstance(data, dict) else {}), parts[2]


def _tokens(query: str) -> list[str]:
    raw = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", query.lower())
    stop = {"the", "and", "for", "with", "this", "that", "skill", "skills"}
    return [token for token in raw if token not in stop]


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip()
    return ""


def _summary(body: str) -> str:
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
        if len(" ".join(lines)) > 360:
            break
    return " ".join(lines)[:420].strip()


def _bundle_name(root: Path, path: Path) -> str:
    for gen in ("hydra/schemes/generated", "hydra/skills/generated"):  # schemes + legacy
        try:
            parts = path.resolve().relative_to((root / gen).resolve()).parts
            return parts[0] if parts else ""
        except ValueError:
            continue
    return path.parent.parent.name


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
