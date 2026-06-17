"""Deterministic audit for Hydra skill-library truth claims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


SCHEMA = "hydra.skill_library_audit.v1"
FRONTEND_DESIGN_TERMS = ("frontend", "front-end", "design", "ux", "ui", "figma")
REQUIRED_SKILL_SECTIONS = ("## Activation", "## Procedure", "## Verification", "## Refusal Boundaries")


def audit_skill_library(
    *,
    repo_root: str | Path,
    catalog_root: str | Path | None = None,
    runtime_root: str | Path | None = None,
    claimed_production_count: int | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    catalog = Path(catalog_root).expanduser().resolve() if catalog_root else root / "hydra" / "schemes"
    runtime = Path(runtime_root).expanduser().resolve() if runtime_root else root / "skills"
    skill_docs = sorted(catalog.rglob("SKILL.md")) if catalog.is_dir() else []
    validated_docs = _validated_skill_docs(skill_docs)
    runtime_modules = sorted(path for path in runtime.glob("*.py") if path.name != "__init__.py") if runtime.is_dir() else []
    index = _load_index(catalog / "index.yaml")
    frontend_design = _frontend_design_audit(root, catalog, skill_docs)
    claims = _claim_report(
        implemented_skill_docs=len(skill_docs),
        production_grade_skill_docs=len(validated_docs),
        claimed_production_count=claimed_production_count,
    )
    findings = _findings(index, skill_docs, claims, frontend_design)

    return {
        "schema": SCHEMA,
        "catalog_root": _rel(root, catalog),
        "runtime_root": _rel(root, runtime),
        "counts": {
            "implemented_skill_docs": len(skill_docs),
            "production_grade_skill_docs": len(validated_docs),
            "runtime_modules": len(runtime_modules),
            "catalog_entries_claimed": int(index.get("catalog_entries_claimed") or 0),
            "catalog_entries_scaffolded": int(index.get("catalog_entries_scaffolded") or 0),
        },
        "claims": claims,
        "frontend_design": frontend_design,
        "findings": findings,
        "evidence_paths": [_rel(root, path) for path in skill_docs[:50]],
        "policy": "production procedural skill claims require concrete SKILL.md files with activation, procedure, verification, refusal boundaries, and evidence paths; scaffolded catalog rows do not count",
    }


def render_text(report: dict[str, Any]) -> str:
    counts = report["counts"]
    claims = report["claims"]
    frontend_design = report["frontend_design"]
    lines = [
        "# Hydra skill library audit",
        "",
        f"catalog_root: {report['catalog_root']}",
        f"runtime_root: {report['runtime_root']}",
        "",
        "## Counts",
        f"- implemented_skill_docs: {counts['implemented_skill_docs']}",
        f"- production_grade_skill_docs: {counts['production_grade_skill_docs']}",
        f"- runtime_modules: {counts['runtime_modules']}",
        f"- catalog_entries_claimed: {counts['catalog_entries_claimed']}",
        f"- catalog_entries_scaffolded: {counts['catalog_entries_scaffolded']}",
        "",
        "## Claims",
        f"- claimed_production_count: {claims['claimed_production_count']}",
        f"- production_claim_proven: {str(claims['production_claim_proven']).lower()}",
        "",
        "## Frontend / Design",
        f"- software_design_bundle_present: {str(frontend_design['software_design_bundle_present']).lower()}",
        f"- frontend_terms_present: {str(frontend_design['frontend_terms_present']).lower()}",
        f"- implemented_skill_docs: {frontend_design['implemented_skill_docs']}",
        f"- generated_software_design_docs: {frontend_design['generated_software_design_docs']}",
        f"- generated_frontend_design_term_hits: {frontend_design['generated_frontend_design_term_hits']}",
        "",
        "## Findings",
    ]
    findings = report.get("findings") or []
    lines.extend(f"- {finding}" for finding in findings) if findings else lines.append("- no blocking findings")
    return "\n".join(lines) + "\n"


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _load_index(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _claim_report(
    *,
    implemented_skill_docs: int,
    production_grade_skill_docs: int,
    claimed_production_count: int | None,
) -> dict[str, Any]:
    proven_capacity = min(implemented_skill_docs, production_grade_skill_docs)
    if claimed_production_count is None:
        claimed_production_count = proven_capacity
    return {
        "claimed_production_count": int(claimed_production_count),
        "production_claim_proven": int(claimed_production_count) <= proven_capacity,
        "proven_capacity": proven_capacity,
    }


def _frontend_design_audit(root: Path, catalog: Path, skill_docs: list[Path]) -> dict[str, Any]:
    design_root = catalog / "bundles" / "software-design"
    design_docs = [path for path in skill_docs if design_root in path.parents or path == design_root / "SKILL.md"]
    generated_design_root = catalog / "generated" / "software-design"
    generated_design_docs = (
        sorted(generated_design_root.rglob("SKILL.md")) if generated_design_root.is_dir() else []
    )
    all_design_docs = design_docs + generated_design_docs
    text = "\n".join(path.read_text(encoding="utf-8", errors="replace").lower() for path in all_design_docs if path.is_file())
    evidence = [_rel(root, path) for path in design_docs]
    return {
        "software_design_bundle_present": (design_root / "SKILL.md").is_file(),
        "frontend_terms_present": any(term in text for term in FRONTEND_DESIGN_TERMS),
        "implemented_skill_docs": len(design_docs),
        "generated_software_design_docs": len(generated_design_docs),
        "generated_frontend_design_term_hits": sum(text.count(term) for term in FRONTEND_DESIGN_TERMS),
        "evidence_paths": evidence,
    }


def _findings(
    index: dict[str, Any],
    skill_docs: list[Path],
    claims: dict[str, Any],
    frontend_design: dict[str, Any],
) -> list[str]:
    findings: list[str] = []
    indexed_docs = index.get("implemented_skill_docs")
    if indexed_docs is not None and int(indexed_docs) != len(skill_docs):
        findings.append("catalog implemented_skill_docs does not match discoverable SKILL.md count")
    if not claims["production_claim_proven"]:
        findings.append("claimed production skill count exceeds validated procedural skill docs")
    if not frontend_design["software_design_bundle_present"]:
        findings.append("software-design bundle missing")
    if not frontend_design["frontend_terms_present"]:
        findings.append("frontend/design terms missing from software-design bundle")
    return findings


def _validated_skill_docs(skill_docs: list[Path]) -> list[Path]:
    validated = []
    for path in skill_docs:
        text = path.read_text(encoding="utf-8", errors="replace")
        if all(section in text for section in REQUIRED_SKILL_SECTIONS):
            validated.append(path)
    return validated


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)
