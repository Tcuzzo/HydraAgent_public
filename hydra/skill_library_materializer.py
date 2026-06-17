"""Materialize Hydra skill catalog entries into concrete SKILL.md files."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "hydra.skill_library_materializer.v1"
ENTRY_RE = re.compile(r"^\s*\d+\.\s+\*\*(?P<name>[^*]+)\*\*\s*-\s*(?P<summary>.+?)\s*$")


@dataclass(frozen=True)
class CatalogEntry:
    bundle: str
    name: str
    slug: str
    summary: str
    source_path: Path


def discover_catalog_entries(bundles_root: str | Path) -> list[CatalogEntry]:
    root = Path(bundles_root).expanduser().resolve()
    entries: list[CatalogEntry] = []
    for path in sorted(root.glob("*/SKILL.md")):
        bundle = path.parent.name
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = ENTRY_RE.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            entries.append(
                CatalogEntry(
                    bundle=bundle,
                    name=name,
                    slug=_slugify(name),
                    summary=match.group("summary").strip(),
                    source_path=path,
                )
            )
    return entries


def materialize_skill_library(
    entries: list[CatalogEntry],
    *,
    output_root: str | Path,
    min_count: int = 0,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(output_root).expanduser().resolve()
    skills: list[dict[str, str]] = []
    written = 0
    for entry in entries:
        path = root / entry.bundle / entry.slug / "SKILL.md"
        skills.append(
            {
                "bundle": entry.bundle,
                "name": entry.name,
                "slug": entry.slug,
                "path": str(path),
                "source_path": str(entry.source_path),
            }
        )
        if dry_run:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _render_skill(entry)
        if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != content:
            path.write_text(content, encoding="utf-8")
        written += 1
    return {
        "schema": SCHEMA,
        "entries_total": len(entries),
        "written": written,
        "dry_run": dry_run,
        "min_count": min_count,
        "meets_min_count": len(entries) >= min_count,
        "output_root": str(root),
        "skills": skills,
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def render_text(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Hydra skill library materializer",
            "",
            f"entries_total: {report['entries_total']}",
            f"written: {report['written']}",
            f"dry_run: {str(report['dry_run']).lower()}",
            f"min_count: {report['min_count']}",
            f"meets_min_count: {str(report['meets_min_count']).lower()}",
            f"output_root: {report['output_root']}",
        ]
    ) + "\n"


def _render_skill(entry: CatalogEntry) -> str:
    title = entry.name.replace("-", " ").title()
    return f"""---
name: {entry.slug}
description: {entry.summary}
bundle: {entry.bundle}
source_catalog: {entry.source_path.as_posix()}
status: materialized
license: MIT
allowed-tools:
  - fs_read
  - grep
  - glob
  - list_directory
  - bash
---

# {title}

{entry.summary}

## Activation

Use this skill when the operator asks for `{entry.name}` work or a closely matching task in the `{entry.bundle}` domain.

## Inputs

- Objective or problem statement.
- Relevant repository paths, documents, URLs, logs, screenshots, or command output.
- Constraints, refusal boundaries, and success criteria.

## Procedure

1. Restate the objective and identify the evidence needed.
2. Gather the smallest relevant context with read-only tools first.
3. Choose the simplest workflow that satisfies the objective.
4. Execute only bounded, reversible steps unless the operator explicitly approves riskier action.
5. Produce a concise result with evidence paths, commands run, and unresolved gaps.

## Verification

- Cite the files, commands, outputs, or evidence packets used.
- Run the narrowest available test, build, lint, audit, or smoke command for the task.
- Mark claims as unproven when no evidence path exists.

## Refusal Boundaries

- Do not invent facts, citations, tool results, or production readiness.
- Do not read secrets or credential material.
- Do not perform destructive, production, or network-write actions without explicit approval.
- Do not claim this materialized skill is runtime-proven until a focused eval or evidence packet promotes it.
"""


def _slugify(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "-", text.strip().lower())
    value = re.sub(r"-+", "-", value).strip("-")
    return value or "skill"
