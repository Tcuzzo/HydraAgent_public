"""Deterministic Markdown scaffold for Hydra wiki memory."""
from __future__ import annotations

import re
from pathlib import Path


WIKI_ROOT = Path(".hydraAgent/wiki")
SCHEMA_INDEX = "hydra.wiki.index.v1"
SCHEMA_SECTION = "hydra.wiki.section.v1"
SCHEMA_MISSION = "hydra.wiki.mission.v1"
WIKI_DIRECTORIES = (
    "missions",
    "systems",
    "sources",
    "lessons",
    "skills",
    "decisions",
    "failures",
    "evals",
    "operator",
)


class WikiMemoryError(Exception):
    """Operator-facing wiki memory scaffold failure."""


def scaffold_wiki(root: Path) -> Path:
    wiki_root = root / WIKI_ROOT
    wiki_root.mkdir(parents=True, exist_ok=True)
    for name in WIKI_DIRECTORIES:
        directory = wiki_root / name
        directory.mkdir(parents=True, exist_ok=True)
        _write_section_index(directory / "index.md", name)
    (wiki_root / "index.md").write_text(render_wiki_index(root), encoding="utf-8")
    return wiki_root


def write_mission_page(root: Path, *, mission_id: str, title: str, evidence_path: str) -> Path:
    _validate_slug("mission_id", mission_id)
    clean_title = _single_line(title)
    clean_evidence_path = _single_line(evidence_path)
    if not clean_title:
        raise WikiMemoryError("title must be a non-empty string")
    if not clean_evidence_path:
        raise WikiMemoryError("evidence_path must be a non-empty string")
    scaffold_wiki(root)
    path = root / WIKI_ROOT / "missions" / f"{mission_id}.md"
    path.write_text(
        "\n".join(
            [
                "---",
                f"schema: {SCHEMA_MISSION}",
                f"mission_id: {mission_id}",
                f"title: {_yaml_quote(clean_title)}",
                f"evidence_path: {_yaml_quote(clean_evidence_path)}",
                "---",
                f"# {clean_title}",
                "",
                f"- mission_id: `{mission_id}`",
                f"- evidence: `{clean_evidence_path}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / WIKI_ROOT / "index.md").write_text(render_wiki_index(root), encoding="utf-8")
    return path


def render_wiki_index(root: Path) -> str:
    wiki_root = root / WIKI_ROOT
    mission_links = _mission_links(wiki_root / "missions")
    lines = [
        "---",
        f"schema: {SCHEMA_INDEX}",
        "generated_by: hydra.wiki_memory",
        "---",
        "# Hydra Wiki",
        "",
        "## Sections",
    ]
    lines.extend(f"- [[{name}/index]]" for name in WIKI_DIRECTORIES)
    lines.extend(["", "## Missions"])
    if mission_links:
        lines.extend(mission_links)
    else:
        lines.append("- No mission pages yet.")
    lines.append("")
    return "\n".join(lines)


def _write_section_index(path: Path, name: str) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f"schema: {SCHEMA_SECTION}",
                f"section: {name}",
                "---",
                f"# {name.title()}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _mission_links(missions_dir: Path) -> list[str]:
    if not missions_dir.is_dir():
        return []
    links = []
    for path in sorted(missions_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        title = _read_frontmatter_value(path, "title") or path.stem
        links.append(f"- [[missions/{path.stem}]] {title}")
    return links


def _read_frontmatter_value(path: Path, key: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0] != "---":
        return None
    prefix = f"{key}: "
    for line in lines[1:]:
        if line == "---":
            return None
        if line.startswith(prefix):
            return _yaml_unquote(line[len(prefix) :])
    return None


def _validate_slug(field: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise WikiMemoryError(f"{field} must be a simple path segment")
    if ".." in value:
        raise WikiMemoryError(f"{field} must be a simple path segment")


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value
