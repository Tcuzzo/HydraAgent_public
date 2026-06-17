"""Trusted local skill index and compact engineering doctrine for HydraAgent."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hydra.inter_agent import INTER_AGENT_PROTOCOL_PROMPT


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_ROOT = Path(os.path.expanduser("~/.codex/superpowers/skills"))
DEFAULT_REPO_SKILLS_ROOT = REPO_ROOT / "skills"
# The skill library lives under "schemes" (formerly "skills"). Fall back to the old
# name if a checkout still has hydra/skills, so existing skills are never lost.
DEFAULT_HYDRA_SKILLS_ROOT = (
    REPO_ROOT / "hydra" / "schemes"
    if (REPO_ROOT / "hydra" / "schemes").is_dir()
    else REPO_ROOT / "hydra" / "skills"
)
DEFAULT_CAPABILITIES_ROOT = REPO_ROOT / ".hydraAgent/capabilities"
CORE_SKILL_NAMES = (
    "brainstorming",
    "task_planner",
    "systematic-debugging",
    "test-driven-development",
    "verification-before-completion",
)
SKILL_ROUTE_KEYWORDS = {
    "brainstorming": (
        "build",
        "create",
        "design",
        "feature",
        "improve",
        "iterate",
        "product",
        "ux",
        "build",
        "upgrade",
    ),
    "task_planner": (
        "capability forge",
        "task planner",
        "delegate slices",
        "skill node",
        "working memory bundle",
    ),
    "systematic-debugging": (
        "bug",
        "broken",
        "debug",
        "diagnose",
        "error",
        "fail",
        "failing",
        "fix",
        "repair",
        "regression",
    ),
    "test-driven-development": (
        "bug",
        "broken",
        "feature",
        "fix",
        "implement",
        "repair",
        "test",
        "tdd",
    ),
    "verification-before-completion": (
        "commit",
        "done",
        "finish",
        "prove",
        "push",
        "release",
        "safe",
        "ship",
        "verify",
    ),
}


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    path: Path
    heading: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "path": str(self.path),
            "heading": self.heading,
            "summary": self.summary,
        }


def skills_root(root: str | Path | None = None) -> Path:
    if root is not None:
        return Path(root).expanduser().resolve()
    return Path(os.environ.get("HYDRA_SKILLS_ROOT", str(DEFAULT_SKILLS_ROOT))).expanduser().resolve()


def skill_roots(root: str | Path | None = None) -> list[Path]:
    if root is not None or os.environ.get("HYDRA_SKILLS_ROOT"):
        return [skills_root(root)]
    roots: list[Path] = []
    for candidate in (DEFAULT_SKILLS_ROOT, DEFAULT_REPO_SKILLS_ROOT, DEFAULT_HYDRA_SKILLS_ROOT):
        resolved = candidate.expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def list_skill_records(root: str | Path | None = None) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    for base in skill_roots(root):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("SKILL.md")):
            if path.parent == base or any(part.startswith(".") for part in path.relative_to(base).parts):
                continue
            records.append(_parse_skill(path))
    return records


def find_skill(name: str, root: str | Path | None = None) -> SkillRecord:
    wanted = name.strip()
    for record in list_skill_records(root):
        if record.name == wanted or record.path.parent.name == wanted:
            return record
    raise KeyError(f"trusted skill not found: {wanted}")


def render_skill(record: SkillRecord) -> str:
    return "\n".join(
        [
            f"name: {record.name}",
            f"source: {record.path}",
            f"description: {record.description}",
            f"heading: {record.heading}",
            "summary:",
            record.summary,
        ]
    )


def render_skill_list(records: list[SkillRecord]) -> str:
    lines = ["Trusted Hydra skills:"]
    for record in records:
        lines.append(f"- {record.name}: {record.description}")
    if not records:
        lines.append("- none found")
    return "\n".join(lines)


def build_skill_doctrine(root: str | Path | None = None) -> str:
    records = _dedupe_skill_records(list_skill_records(root))
    available = [name for name in CORE_SKILL_NAMES if name in records]
    source_lines = [f"- {name}: {records[name].description}" for name in available]
    if not source_lines:
        source_lines = ["- no trusted local superpower skills were found; keep behavior conservative"]
    return "\n".join(
        [
            "Hydra skill spine doctrine",
            "",
            "Trusted local skill sources:",
            *source_lines,
            "",
            "Default engineering behavior:",
            "- Use brainstorming before creative/product/behavior changes; clarify intent and scope before edits.",
            "- Use systematic-debugging before bug fixes or unexpected behavior; find root cause before fixes.",
            "- Use TDD for features and bug fixes; write or identify a failing proof before implementation.",
            "- Keep edits surgical and repo-patterned; take the best local skill guidance and leave unrelated ceremony.",
            "- Evidence before claims: run fresh verification before saying work is fixed, complete, or safe.",
            "- No blind remote skill cloning. Remote skills require explicit operator approval and review before use.",
            "- Tools still obey approval policy; skills are guidance, not authority escalation.",
        ]
    )


def build_evolution_doctrine() -> str:
    """Return the runtime method Hydra should use for agent improvement work."""
    return "\n".join(
        [
            "Hydra evolution doctrine",
            "",
            "AlphaEvolve runtime template:",
            "- Treat every nontrivial task as an optimization problem with a clear objective and evaluator.",
            "- Start from the current parent context plus inspirations: prior failures, useful patterns, tests, logs, and operator preferences.",
            "- Propose the smallest candidate diff or action plan that could improve the measured outcome.",
            "- Run the evaluator: targeted tests, smoke checks, static checks, or a human-readable proof when automation is unavailable.",
            "- Archive the result: what changed, which evidence passed or failed, and what should be tried next.",
            "",
            "Darwin Godel Machine discipline:",
            "- Treat any self-improvement, prompt change, tool change, or workflow change as a candidate variant until empirical validation passes.",
            "- Prefer diverse small experiments over one grand rewrite; keep useful failures as stepping stones.",
            "- Do not promote a variant just because it sounds better. Promote only after verification evidence beats or preserves the parent behavior.",
            "- Keep safety constraints active: sandbox or scope risky execution, obey the approval policy, and keep human oversight for destructive actions.",
            "",
            "Turn protocol:",
            "- Understand the operator goal, success metric, constraints, and current repo state before editing.",
            "- Use tools to inspect reality; do not invent file contents, runtime state, test status, or capabilities.",
            "- When coding, make a focused candidate change, verify it, then report the result plainly with evidence.",
            "- When chatting, answer naturally but still route actionable work into inspect, plan, execute, evaluate, and archive.",
        ]
    )


def build_agent_system_prompt(base_prompt: str, root: str | Path | None = None) -> str:
    doctrine = (
        build_skill_doctrine(root)
        + "\n\n"
        + build_evolution_doctrine()
        + "\n\n"
        + INTER_AGENT_PROTOCOL_PROMPT
    )
    if base_prompt.strip():
        return base_prompt.rstrip() + "\n\n" + doctrine
    return doctrine


def route_skill_names(prompt: str) -> list[str]:
    text = prompt.lower()
    routed: list[str] = []
    for name in CORE_SKILL_NAMES:
        keywords = SKILL_ROUTE_KEYWORDS.get(name, ())
        if any(_contains_keyword(text, keyword) for keyword in keywords):
            routed.append(name)
    return routed


def route_skill_records(prompt: str, root: str | Path | None = None) -> list[SkillRecord]:
    available = _dedupe_skill_records(list_skill_records(root))
    return [available[name] for name in route_skill_names(prompt) if name in available]


def build_routed_skill_context(prompt: str, root: str | Path | None = None) -> str:
    records = route_skill_records(prompt, root)
    capability_cards = route_capability_cards(prompt)
    if not records and not capability_cards:
        return ""
    lines = [
        "Hydra routed skill context",
        (
            f"Prompt matched {len(records)} trusted skill(s) and "
            f"{len(capability_cards)} native capability card(s). Apply these playbooks before answering."
        ),
    ]
    for record in records:
        lines.extend(
            [
                "",
                f"skill: {record.name}",
                f"description: {record.description}",
                f"summary: {record.summary}",
            ]
        )
    for card in capability_cards:
        lines.extend(
            [
                "",
                f"capability: {card.card_id}",
                f"name: {card.name}",
                f"mission_phases: {', '.join(card.mission_phases)}",
                f"evidence_required: {', '.join(card.evidence_required)}",
            ]
        )
    return "\n".join(lines)


def render_route_json(prompt: str, root: str | Path | None = None) -> str:
    records = route_skill_records(prompt, root)
    capability_cards = route_capability_cards(prompt)
    payload: dict[str, Any] = {
        "schema": "hydra.skills.route.v1",
        "prompt": prompt,
        "records": [record.to_dict() for record in records],
        "capabilities": [card.to_dict() for card in capability_cards],
        "context": build_routed_skill_context(prompt, root),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def route_capability_cards(prompt: str, root: str | Path | None = None) -> list[Any]:
    base = Path(root) if root is not None else DEFAULT_CAPABILITIES_ROOT
    try:
        from hydra.capability_cards import load_cards, route_cards

        return route_cards(prompt, load_cards(base))
    except Exception:
        return []


def skill_doctor_report(root: str | Path | None = None) -> dict[str, Any]:
    roots = skill_roots(root)
    records = list_skill_records(root)
    names = {record.name for record in records}
    missing = [name for name in CORE_SKILL_NAMES if name not in names]
    trusted_root_exists = any(path.is_dir() for path in roots)
    required_core_present = "task_planner" in names
    report = {
        "schema": "hydra.skills.doctor.v1",
        "status": "OK" if trusted_root_exists and required_core_present else "WARN",
        "skills_root": str(roots[0]) if roots else "",
        "skills_roots": [str(path) for path in roots],
        "trusted_root_exists": trusted_root_exists,
        "remote_import_enabled": False,
        "core_skills": list(CORE_SKILL_NAMES),
        "core_skills_present": [name for name in CORE_SKILL_NAMES if name in names],
        "missing_core_skills": missing,
        "total_skills": len(records),
        "policy": "local trusted skills only; no blind remote skill cloning",
    }
    return report


def render_skill_doctor(root: str | Path | None = None) -> str:
    report = skill_doctor_report(root)
    missing = report["missing_core_skills"]
    missing_text = ", ".join(missing) if missing else "none"
    return "\n".join(
        [
            f"Hydra skill spine doctor: {report['status']}",
            f"skills_root: {report['skills_root']}",
            "skills_roots: " + ", ".join(report.get("skills_roots") or [report["skills_root"]]),
            f"trusted_root_exists: {str(report['trusted_root_exists']).lower()}",
            f"remote_import_enabled: {str(report['remote_import_enabled']).lower()}",
            f"total_skills: {report['total_skills']}",
            f"missing_core_skills: {missing_text}",
            f"policy: {report['policy']}",
        ]
    )


def render_doctrine_json(root: str | Path | None = None) -> str:
    records = [record.to_dict() for record in list_skill_records(root)]
    payload: dict[str, Any] = {
        "schema": "hydra.skill_spine.v1",
        "skills_root": str(skills_root(root)),
        "skills_roots": [str(path) for path in skill_roots(root)],
        "core_skills": list(CORE_SKILL_NAMES),
        "records": records,
        "doctrine": build_skill_doctrine(root),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _parse_skill(path: Path) -> SkillRecord:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_frontmatter(text)
    name = meta.get("name") or path.parent.name
    description = meta.get("description") or ""
    heading = ""
    summary_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not heading and stripped.startswith("# "):
            heading = stripped.removeprefix("# ").strip()
            continue
        if stripped and not stripped.startswith("---"):
            summary_lines.append(stripped)
        if len(" ".join(summary_lines)) > 420:
            break
    summary = " ".join(summary_lines)[:500].strip()
    return SkillRecord(name=name, description=description, path=path, heading=heading, summary=summary)


def _contains_keyword(text: str, keyword: str) -> bool:
    escaped = re.escape(keyword.lower()).replace(r"\ ", r"[\s_-]+")
    return re.search(rf"(?<![a-z0-9_\-]){escaped}(?![a-z0-9_\-])", text) is not None


def _dedupe_skill_records(records: list[SkillRecord]) -> dict[str, SkillRecord]:
    available: dict[str, SkillRecord] = {}
    for record in records:
        existing = available.get(record.name)
        if existing is None or len(record.path.parts) < len(existing.path.parts):
            available[record.name] = record
    return available


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    _, _, rest = text.partition("---\n")
    meta_text, marker, body = rest.partition("\n---\n")
    if not marker:
        return {}, text
    meta: dict[str, str] = {}
    for line in meta_text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body
