"""Aggregate proof report for visible Hydra capability claims."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from hydra.autonomy import SAFE_BASH_COMMANDS
from hydra.declarative_runtime import TRUST_TIERS_SCHEMA, doctor_runtime_catalog, load_runtime_catalog
from hydra.policy import CONTRACT_RUNTIME_NAME, RISKY_TOOLS, TIER_ORDER, TOOL_RISK, runtime_tool_name
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


# --- One risk authority: generated policy files + the capability risk report ---------
# hydra.policy.TOOL_RISK is the only risk table. The two policy YAMLs below are
# RENDERINGS of it (regenerate with `hydra capabilities risk --write`), never a
# second hand-kept copy; the doctor flags any contract that drifts from it.

_GENERATED = "# GENERATED from hydra.policy.TOOL_RISK by `hydra capabilities risk --write`. Do not edit by hand.\n"
_TIER_TEXT = {
    "read_only": ("Observes workspace or runtime state without modifying it.", False),
    "guarded_write": ("May modify local workspace state. Approval is decided per tool (see tool_approval), not by the tier.", False),
    "remote_write": ("May modify remote systems, repositories, services, or APIs. Approval is decided per tool (see tool_approval).", False),
    "destructive": ("May delete data, discard history, or irreversibly change state.", True),
}
_DANGER_EXAMPLES = ("rm", "sudo", "git reset --hard", "git push --force", "curl -X POST", "systemctl restart")
_AUTHORITIES = ("hydra.policy.ApprovalPolicy", "hydra.autonomy", "hydra.channel_trust")


def _contract_ids() -> list[str]:
    """Tool contract ids the bundled catalog defines (the contracts stay hand-written)."""
    return sorted(load_runtime_catalog(Path(__file__).resolve().parent.parent).tools)


def render_trust_tiers_yaml() -> str:
    tool_risks, tool_approval = {}, {}
    for tool_id in _contract_ids():
        entry = TOOL_RISK[runtime_tool_name(tool_id)]
        tool_risks[tool_id] = entry.tier
        tool_approval[tool_id] = "required" if entry.gated else "none"
    data = {
        "schema": TRUST_TIERS_SCHEMA,
        "tiers": {t: {"description": _TIER_TEXT[t][0], "default_requires_approval": _TIER_TEXT[t][1]} for t in TIER_ORDER},
        "runtime_tool_names": dict(sorted(CONTRACT_RUNTIME_NAME.items())),
        "tool_risks": tool_risks,
        "tool_approval": tool_approval,
    }
    return _GENERATED + yaml.safe_dump(data, sort_keys=False)


def render_danger_gates_yaml() -> str:
    gate = {
        "gate_id": "destructive_action",
        "description": (
            "Destructive or authority-class actions require operator approval. "
            "This file renders hydra.policy.TOOL_RISK and the enforced safe-command "
            "table; it is not a separate gate."
        ),
        "requires_approval": True,
        "examples": list(_DANGER_EXAMPLES),
        "gated_tools": sorted(RISKY_TOOLS),
        "safe_bash_commands": sorted(SAFE_BASH_COMMANDS),
        "enforced_by": list(_AUTHORITIES),
    }
    data = {"schema": "hydra.policies.danger_gates.v1", "gates": [gate]}
    return _GENERATED + yaml.safe_dump(data, sort_keys=False)


def write_policy_files(repo_root: str | Path) -> list[Path]:
    policies = Path(repo_root) / ".hydraAgent" / "policies"
    written = []
    for name, text in (("trust-tiers.yaml", render_trust_tiers_yaml()), ("danger-gates.yaml", render_danger_gates_yaml())):
        path = policies / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written


def capability_risk_report(repo_root: str | Path) -> dict[str, Any]:
    """Per-tool report: declared, bound, enabled, surface-exposed, approval class,
    runtime-tested, unsupported-with-reason - plus every contract/table disagreement."""
    root = Path(repo_root).expanduser().resolve()
    catalog = load_runtime_catalog(root)
    doctor = doctor_runtime_catalog(catalog)
    declared = set((catalog.policies.get("trust_tiers", {}).get("tool_risks") or {}))
    broken = {f["target"] for f in doctor["findings"] if f["message"].startswith(("executor", "bad executor"))}
    surface_text = _surface_text(root)
    test_text = _test_text(root)
    runtime_to_contract = {v: k for k, v in CONTRACT_RUNTIME_NAME.items()}
    rows = [
        _risk_row(name, runtime_to_contract.get(name, name), catalog, declared, broken, surface_text, test_text)
        for name in sorted(TOOL_RISK)
    ]
    disagreements = [f for f in doctor["findings"] if f["message"].startswith(("contract", "orphan", "stale", "tool has no"))]
    return {"schema": "hydra.capability_risk.v1", "tools": rows, "disagreements": disagreements, "counts": {"tools": len(rows), "disagreements": len(disagreements)}}


def _risk_row(name, contract_id, catalog, declared, broken, surface_text, test_text) -> dict[str, Any]:
    entry = TOOL_RISK[name]
    has_contract = contract_id in catalog.tools
    surface = has_contract or _quoted(name, surface_text)
    return {
        "tool": name,
        "contract_id": contract_id if has_contract else None,
        "tier": entry.tier,
        "approval": "required" if entry.gated else "none",
        "mutating": entry.mutating,
        "declared": contract_id in declared,
        "bound": has_contract and contract_id not in broken,
        "enabled": has_contract,
        "surface_exposed": surface,
        "runtime_tested": _quoted(name, test_text),
        "unsupported_reason": _unsupported_reason(entry.note, has_contract, surface),
    }


def _unsupported_reason(note: str, has_contract: bool, surface: bool) -> str:
    parts = [
        "" if has_contract else "runtime-bound only: no declarative tool contract",
        "" if surface else "not exposed on any planner or CLI surface",
        note,
    ]
    return "; ".join(p for p in parts if p)


def _quoted(name: str, text: str) -> bool:
    return re.search(r"""["']""" + re.escape(name) + r"""["']""", text) is not None


def _surface_text(root: Path) -> str:
    files = [root / "hydra/cli/tool_binding.py", root / "hydra/mcp_bridge.py", root / "hydra/tool_bridge.py"]
    return "\n".join(f.read_text(encoding="utf-8") for f in files if f.is_file())


def _test_text(root: Path) -> str:
    skip = {"test_capability_risk_ssot.py"}
    tests = (p for p in sorted((root / "hydra").glob("test_*.py")) if p.name not in skip)
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in tests)


def render_risk_text(report: dict[str, Any]) -> str:
    lines = ["Hydra capability risk (one table: hydra.policy.TOOL_RISK)", f"tools: {report['counts']['tools']}  disagreements: {report['counts']['disagreements']}", ""]
    header = f"{'tool':<22}{'tier':<15}{'approval':<10}{'declared':<10}{'bound':<7}{'enabled':<9}{'surface':<9}{'tested':<8}reason"
    lines.append(header)
    for row in report["tools"]:
        flags = [row[k] for k in ("declared", "bound", "enabled", "surface_exposed", "runtime_tested")]
        cells = "".join(f"{'yes' if f else 'no':<{w}}" for f, w in zip(flags, (10, 7, 9, 9, 8)))
        lines.append(f"{row['tool']:<22}{row['tier']:<15}{row['approval']:<10}{cells}{row['unsupported_reason']}")
    for finding in report["disagreements"]:
        lines.append(f"DISAGREE {finding['target']}: {finding['message']}")
    return "\n".join(lines) + "\n"
