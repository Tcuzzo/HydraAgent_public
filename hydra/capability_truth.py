"""Aggregate proof report for visible Hydra capability claims."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.declarative_runtime import doctor_runtime_catalog, load_runtime_catalog
from hydra.skill_library_audit import audit_skill_library


SCHEMA = "hydra.capability_truth.v1"


def capability_truth_report(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve()
    skill_report = audit_skill_library(
        repo_root=root,
        catalog_root=root / "hydra" / "schemes",
        runtime_root=root / "skills",
    )
    catalog = load_runtime_catalog(root)
    declarative = doctor_runtime_catalog(catalog)
    known_tools = set(catalog.tools)
    capabilities = [
        _capability(
            "curated_skill_library",
            "curated procedural SKILL.md library is present and auditable",
            skill_report["counts"]["implemented_skill_docs"] >= 1,
            [
                f"implemented_skill_docs={skill_report['counts']['implemented_skill_docs']}",
                f"catalog_root={skill_report['catalog_root']}",
            ],
        ),
        _capability(
            "software_design_bundle_present",
            "software-design bundle is present with frontend/design coverage",
            bool(
                skill_report["frontend_design"]["software_design_bundle_present"]
                and skill_report["frontend_design"]["frontend_terms_present"]
                and skill_report["frontend_design"]["implemented_skill_docs"] >= 1
            ),
            [
                f"software_design_bundle_present={skill_report['frontend_design']['software_design_bundle_present']}",
                f"frontend_terms_present={skill_report['frontend_design']['frontend_terms_present']}",
                f"implemented_skill_docs={skill_report['frontend_design']['implemented_skill_docs']}",
            ],
        ),
        _capability(
            "planner_skill_library_search",
            "planner can search materialized SKILL.md library on demand",
            _tool_present(declarative, known_tools, "skill_library.search"),
            ["tool_id=skill_library.search", f"declarative_doctor={declarative['status']}"],
        ),
        _capability(
            "declarative_runtime_wired",
            "declarative runtime catalog loads and doctor reports OK",
            declarative.get("status") == "OK" and not declarative.get("findings"),
            [f"declarative_doctor={declarative['status']}", f"tools={declarative['counts']['tools']}"],
        ),
    ]
    blocked = [item for item in capabilities if not item["proven"]]
    return {
        "schema": SCHEMA,
        "status": "PROVEN" if not blocked else "GAPS",
        "repo_root": str(root),
        "capabilities": capabilities,
        "counts": {
            "capabilities": len(capabilities),
            "proven": len(capabilities) - len(blocked),
            "blocked": len(blocked),
        },
        "evidence": {
            "skill_library_audit": {
                "schema": skill_report["schema"],
                "counts": skill_report["counts"],
                "claims": skill_report["claims"],
                "frontend_design": skill_report["frontend_design"],
                "findings": skill_report["findings"],
            },
            "declarative_doctor": declarative,
        },
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra capability truth: {report['status']}",
        f"repo_root: {report['repo_root']}",
        f"proven: {report['counts']['proven']}/{report['counts']['capabilities']}",
        "",
        "Capabilities",
    ]
    for item in report["capabilities"]:
        status = "PROVEN" if item["proven"] else "GAP"
        lines.append(f"- {status} {item['capability_id']}: {item['claim']}")
        for evidence in item["evidence"]:
            lines.append(f"  evidence: {evidence}")
    return "\n".join(lines) + "\n"


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True)


def _capability(capability_id: str, claim: str, proven: bool, evidence: list[str]) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "claim": claim,
        "proven": bool(proven),
        "evidence": evidence,
    }


def _tool_present(report: dict[str, Any], known_tools: set[str], tool_id: str) -> bool:
    if report.get("status") != "OK":
        return False
    findings = report.get("findings") or []
    if findings:
        return False
    return tool_id in known_tools
