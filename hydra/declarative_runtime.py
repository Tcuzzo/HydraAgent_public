"""Declarative runtime kernel for HydraAgent.

Contracts live in `.hydraAgent`; this module loads them, builds planner briefs,
validates planner decisions, and invokes declared read-only tools.
"""
from __future__ import annotations

import importlib
import datetime as _dt
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from hydra.policy import ApprovalPolicy


class DeclarativeRuntimeError(Exception):
    """Raised when runtime contracts, decisions, or tool execution are invalid."""


@dataclass(frozen=True)
class RuntimeCatalog:
    root: Path
    tools: dict[str, dict[str, Any]]
    ux: dict[str, Any]
    policies: dict[str, Any]
    skills: dict[str, Any]


@dataclass(frozen=True)
class DeclarativeTurnResult:
    handled: bool
    text: str
    decision: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None


REQUIRED_DECISION_KEYS = (
    "schema",
    "intent",
    "selected_skills",
    "selected_tools",
    "execution_mode",
    "requires_approval",
    "plan",
    "verification",
)


def load_runtime_catalog(root: Path) -> RuntimeCatalog:
    """Load declarative runtime contracts from a workspace root."""
    root = root.resolve()
    registry = _read_yaml(root / ".hydraAgent/tools/registry.yaml")
    tools: dict[str, dict[str, Any]] = {}
    for item in registry.get("tools", []):
        contract = _read_yaml(root / str(item))
        for tool_id, tool_contract in _iter_tool_contracts(contract, str(item)):
            tool_contract = dict(tool_contract)
            tool_contract["_contract_path"] = str(item)
            tools[tool_id] = tool_contract

    skills_path = root / ".hydraAgent/skills/index.yaml"
    skills = _read_yaml(skills_path) if skills_path.exists() else {"skills": []}
    skills = _merge_materialized_skills(root, skills, available_tools=set(tools))
    return RuntimeCatalog(
        root=root,
        tools=tools,
        ux=_read_yaml(root / ".hydraAgent/ux/response-contracts.yaml"),
        policies={
            "danger_gates": _read_yaml(root / ".hydraAgent/policies/danger-gates.yaml"),
            "trust_tiers": _read_yaml(root / ".hydraAgent/policies/trust-tiers.yaml"),
        },
        skills=skills,
    )


def _brief_models() -> dict[str, str]:
    """Return the planner/doer/verifier model names sourced from the SSOT."""
    from hydra.model_routing import load_routing  # local import avoids top-level cycle

    routing = load_routing()
    return {
        "planner": routing.role_entry("planner").model,   # cloud-planner model
        "doer": routing.role_entry("doer").model,         # cloud-doer model
        "verifier": routing.verifier_pair()[1],           # llama-3.3-70b-versatile
    }


def build_runtime_brief(operator_input: str, catalog: RuntimeCatalog, *, root: Path) -> dict[str, Any]:
    """Build the compact planner brief from loaded contracts."""
    return {
        "schema": "hydra.runtime_brief.v1",
        "operator_input": operator_input,
        "workspace": {
            "root": str(root.resolve()),
            "git_status_summary": _git_status_summary(root),
        },
        "models": _brief_models(),
        "tools": [
            {
                "tool_id": tool_id,
                "description": str(contract.get("description") or ""),
                "risk": str(contract.get("risk") or ""),
                "input_schema_ref": str(contract.get("_contract_path") or ""),
            }
            for tool_id, contract in sorted(catalog.tools.items())
        ],
        "skills": _brief_skills(catalog.skills, query=operator_input),
        "skills_inventory": {
            "total": len(catalog.skills.get("skills", [])) if isinstance(catalog.skills.get("skills"), list) else 0,
            "materialized": int(catalog.skills.get("materialized_skill_count") or 0),
            "search_tool": "skill_library.search",
        },
        "policies": {
            "danger_gates": catalog.policies.get("danger_gates", {}),
            "approval_required_when": _approval_required_when(catalog.policies),
        },
        "memory": {
            "relevant_episodes": _recent_episodes(catalog.root),
            "known_preferences": [],
        },
        "ux": {
            "voice_contract": catalog.ux.get("voice", "Hydra Agent operator voice"),
            "response_contracts": catalog.ux,
        },
    }


def execute_agent_decision(
    decision: dict[str, Any],
    catalog: RuntimeCatalog,
    *,
    root: Path,
    approval_policy: ApprovalPolicy | None = None,
) -> dict[str, Any]:
    """Validate and execute a planner decision against the declarative catalog."""
    _validate_decision(decision, catalog)
    policy = approval_policy or ApprovalPolicy("ask")  # safe default: gate risky tools (bash/fs_write/fs_edit); pass ApprovalPolicy("allow") to run unattended

    results = []
    for step in decision.get("plan", []):
        tool_id = step.get("tool_id")
        if not tool_id:
            continue
        contract = catalog.tools.get(tool_id)
        if not contract:
            raise DeclarativeRuntimeError(f"unknown declarative tool: {tool_id}")
        arguments = step.get("arguments") or {}
        if _contract_requires_approval(contract):
            policy.require(_policy_tool_name(contract), arguments)
        results.append(_invoke_tool(contract, arguments, root=root))
    validation = _validate_execution_results(decision, catalog, results)
    return {"schema": "hydra.execution_result.v1", "results": results, "validation": validation}


def parse_agent_decision(text: str) -> dict[str, Any]:
    """Parse a planner YAML decision."""
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise DeclarativeRuntimeError("planner decision must be a mapping")
    return data


def doctor_runtime_catalog(catalog: RuntimeCatalog) -> dict[str, Any]:
    """Validate that declarative contracts point at real executors and skills."""
    findings: list[dict[str, str]] = []
    risk_tiers = set((catalog.policies.get("trust_tiers", {}).get("tiers") or {}).keys())
    declared_risks = catalog.policies.get("trust_tiers", {}).get("tool_risks") or {}

    for tool_id, contract in sorted(catalog.tools.items()):
        risk = str(contract.get("risk") or "")
        if risk not in risk_tiers:
            findings.append({"level": "error", "target": tool_id, "message": f"unknown risk tier: {risk}"})
        if declared_risks.get(tool_id) != risk:
            findings.append({"level": "error", "target": tool_id, "message": "tool risk not declared in trust tiers"})
        executor = str(contract.get("executor") or "")
        if ":" not in executor:
            findings.append({"level": "error", "target": tool_id, "message": f"bad executor reference: {executor}"})
            continue
        module_name, fn_name = executor.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            getattr(module, fn_name)
        except (ImportError, AttributeError) as exc:
            findings.append({"level": "error", "target": tool_id, "message": f"executor unavailable: {exc}"})

    skills = catalog.skills.get("skills", [])
    if not isinstance(skills, list):
        findings.append({"level": "error", "target": "skills", "message": "skill index must contain a skills list"})
        skills = []
    for item in skills:
        if not isinstance(item, dict):
            findings.append({"level": "error", "target": "skills", "message": "skill index entry must be a mapping"})
            continue
        skill_id = str(item.get("skill_id") or "")
        path = str(item.get("path") or "")
        if not skill_id:
            findings.append({"level": "error", "target": "skills", "message": "skill entry missing skill_id"})
        if not path:
            findings.append({"level": "error", "target": skill_id or "skills", "message": "skill entry missing path"})
            continue
        skill_path = Path(path).expanduser()
        if not skill_path.is_absolute():
            skill_path = catalog.root / skill_path
        if not skill_path.is_file():
            findings.append({"level": "error", "target": skill_id or path, "message": f"skill path not found: {path}"})
        allowed_tools = item.get("allowed_tools", [])
        if allowed_tools is None:
            allowed_tools = []
        if not isinstance(allowed_tools, list):
            findings.append({"level": "error", "target": skill_id or "skills", "message": "allowed_tools must be a list"})
            continue
        for tool_id in allowed_tools:
            if not isinstance(tool_id, str) or not tool_id.strip():
                findings.append({"level": "error", "target": skill_id or "skills", "message": "allowed_tools entries must be non-empty strings"})
                continue
            if tool_id not in catalog.tools:
                findings.append({"level": "error", "target": skill_id or "skills", "message": f"unknown allowed tool: {tool_id}"})

    status = "OK" if not findings else "WARN"
    return {
        "schema": "hydra.declarative_doctor.v1",
        "status": status,
        "counts": {"tools": len(catalog.tools), "skills": len(skills), "findings": len(findings)},
        "findings": findings,
    }


def run_declarative_turn(
    operator_input: str,
    catalog: RuntimeCatalog,
    *,
    root: Path,
    planner,
    memory_root: Path | None = None,
) -> DeclarativeTurnResult:
    """Run one planner-first declarative turn."""
    brief = build_runtime_brief(operator_input, catalog, root=root)
    decision = planner(brief)
    decision = prepare_runtime_decision(operator_input, decision, catalog)
    execution = execute_agent_decision(decision, catalog, root=root)
    _append_episodic_memory(
        memory_root or catalog.root,
        operator_input=operator_input,
        decision=decision,
        execution=execution,
    )
    text = render_execution_result(execution)
    return DeclarativeTurnResult(True, text, decision=decision, execution=execution)


def prepare_runtime_decision(
    operator_input: str,
    decision: dict[str, Any],
    catalog: RuntimeCatalog,
) -> dict[str, Any]:
    """Add deterministic runtime preflight for broad work before execution.

    Planner-visible tools are not enough for the operator rule. Broad audit,
    code, design, and implementation turns must discover relevant skills and
    fan out read-only subagents automatically unless the planner already did so.
    """
    if not _needs_parallel_preflight(operator_input, decision):
        return decision
    tools = catalog.tools
    selected_tools = list(decision.get("selected_tools") or [])
    selected_tool_ids = {str(item.get("tool_id") or "") for item in selected_tools if isinstance(item, dict)}
    plan = list(decision.get("plan") or [])
    existing_plan_tools = {str(item.get("tool_id") or "") for item in plan if isinstance(item, dict)}
    preflight: list[dict[str, Any]] = []

    if "skill_library.search" in tools and "skill_library.search" not in existing_plan_tools:
        if "skill_library.search" not in selected_tool_ids:
            selected_tools.append(
                {
                    "tool_id": "skill_library.search",
                    "reason": "automatic preflight for broad runtime work",
                }
            )
            selected_tool_ids.add("skill_library.search")
        preflight.append(
            {
                "id": "auto_skill_library_search",
                "action": "search materialized skill library before planning/execution",
                "tool_id": "skill_library.search",
                "arguments": {"query": operator_input, "limit": 8},
                "expected_evidence": "relevant materialized skill hits",
            }
        )

    if "spawn_subagents" in tools and not ({"spawn_subagent", "spawn_subagents"} & existing_plan_tools):
        if "spawn_subagents" not in selected_tool_ids:
            selected_tools.append(
                {
                    "tool_id": "spawn_subagents",
                    "reason": "automatic parallel read/judge/verify fan-out for broad runtime work",
                }
            )
        preflight.append(
            {
                "id": "auto_parallel_runtime_audit",
                "action": "fan out independent read-only runtime audits",
                "tool_id": "spawn_subagents",
                "arguments": {
                    "tasks": _parallel_preflight_tasks(operator_input),
                    "max_workers": 3,
                    "timeout": 300,
                    "runtime_only": True,
                },
                "expected_evidence": "parallel subagent reports",
            }
        )

    if not preflight:
        return decision
    updated = dict(decision)
    updated["selected_tools"] = selected_tools
    updated["plan"] = preflight + plan
    return updated


def render_execution_result(execution: dict[str, Any]) -> str:
    lines = ["# Hydra declarative execution", ""]
    for result in execution.get("results", []):
        if not isinstance(result, dict):
            continue
        run_id = result.get("run_id")
        if run_id:
            lines.append(f"run_id: {run_id}")
        loop_id = result.get("loop_id")
        if loop_id:
            lines.append(f"loop_id: {loop_id}")
        if "cycles_completed" in result:
            lines.append(f"cycles_completed: {result['cycles_completed']}")
        counts = result.get("counts") or {}
        for key in sorted(counts):
            lines.append(f"- {key}: {counts[key]}")
    return "\n".join(lines).rstrip() + "\n"


def _validate_execution_results(
    decision: dict[str, Any],
    catalog: RuntimeCatalog,
    results: list[Any],
) -> dict[str, Any]:
    plan_steps = [step for step in decision.get("plan", []) if isinstance(step, dict) and step.get("tool_id")]
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for index, step in enumerate(plan_steps):
        tool_id = str(step.get("tool_id") or "")
        result = results[index] if index < len(results) else None
        contract = catalog.tools.get(tool_id, {})
        evidence_required = list(((contract.get("evidence") or {}).get("required") or []))
        ok = isinstance(result, dict)
        if ok and result.get("ok") is False:
            ok = False
        if ok and str(result.get("status") or "").lower() in {"failed", "timeout", "blocked"}:
            ok = False
        missing_evidence = [
            key for key in evidence_required
            if not (isinstance(result, dict) and result.get(key) not in {None, ""})
        ]
        if missing_evidence:
            ok = False
        check = {
            "step_id": step.get("id", f"step-{index}"),
            "tool_id": tool_id,
            "result_present": result is not None,
            "result_schema": result.get("schema") if isinstance(result, dict) else "",
            "required_evidence": evidence_required,
            "missing_evidence": missing_evidence,
            "ok": ok,
        }
        checks.append(check)
        if not ok:
            failures.append(str(check["step_id"]))
    return {
        "schema": "hydra.execution_validation.v1",
        "steps_checked": len(checks),
        "passed": not failures,
        "failures": failures,
        "checks": checks,
    }


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DeclarativeRuntimeError(f"contract not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise DeclarativeRuntimeError(f"contract must be a mapping: {path}")
    return data


def _iter_tool_contracts(contract: dict[str, Any], source: str) -> list[tuple[str, dict[str, Any]]]:
    if isinstance(contract.get("tools"), list):
        pairs = []
        for item in contract["tools"]:
            if not isinstance(item, dict):
                raise DeclarativeRuntimeError(f"tool set item must be a mapping: {source}")
            tool_id = str(item.get("tool_id") or "").strip()
            if not tool_id:
                raise DeclarativeRuntimeError(f"tool contract missing tool_id: {source}")
            pairs.append((tool_id, item))
        return pairs
    tool_id = str(contract.get("tool_id") or "").strip()
    if not tool_id:
        raise DeclarativeRuntimeError(f"tool contract missing tool_id: {source}")
    return [(tool_id, contract)]


def _git_status_summary(root: Path) -> str:
    proc = subprocess.run(
        ["git", "status", "--short"],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=10,
    )
    if proc.returncode != 0:
        return "git status unavailable"
    lines = proc.stdout.splitlines()
    return f"{len(lines)} changed path(s)"


def _validate_decision(decision: dict[str, Any], catalog: RuntimeCatalog) -> None:
    for key in REQUIRED_DECISION_KEYS:
        if key not in decision:
            raise DeclarativeRuntimeError(f"missing required decision key: {key}")
    if decision.get("schema") != "hydra.agent_decision.v1":
        raise DeclarativeRuntimeError(f"invalid decision schema: {decision.get('schema')}")
    if not isinstance(decision.get("intent"), dict):
        raise DeclarativeRuntimeError("intent must be a mapping")
    if decision["intent"].get("kind") not in {"chat", "audit", "code_change", "research", "design", "operate", "unknown"}:
        raise DeclarativeRuntimeError(f"invalid intent kind: {decision['intent'].get('kind')}")
    if decision.get("execution_mode") not in {"direct", "plan_then_execute", "ask_one_question", "refuse", "design_only"}:
        raise DeclarativeRuntimeError(f"invalid execution_mode: {decision.get('execution_mode')}")
    if not isinstance(decision.get("selected_tools"), list):
        raise DeclarativeRuntimeError("selected_tools must be a list")
    selected_tool_ids = set()
    for selected in decision["selected_tools"]:
        if not isinstance(selected, dict):
            raise DeclarativeRuntimeError("selected_tools entries must be mappings")
        tool_id = selected.get("tool_id")
        if tool_id not in catalog.tools:
            raise DeclarativeRuntimeError(f"unknown selected tool: {tool_id}")
        selected_tool_ids.add(tool_id)
    if not isinstance(decision.get("plan"), list):
        raise DeclarativeRuntimeError("plan must be a list")
    for step in decision["plan"]:
        if not isinstance(step, dict):
            raise DeclarativeRuntimeError("plan entries must be mappings")
        tool_id = step.get("tool_id")
        if tool_id and tool_id not in selected_tool_ids:
            raise DeclarativeRuntimeError(f"plan tool was not selected: {tool_id}")
    if not isinstance(decision.get("verification"), list):
        raise DeclarativeRuntimeError("verification must be a list")


def _brief_skills(skills: dict[str, Any], *, query: str = "") -> list[dict[str, Any]]:
    raw = skills.get("skills", [])
    if not isinstance(raw, list):
        return []
    tokens = _tokens(query)
    declared = [item for item in raw if isinstance(item, dict) and not item.get("materialized")]
    materialized = [item for item in raw if isinstance(item, dict) and item.get("materialized")]
    if tokens:
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for item in materialized:
            text = _skill_index_text(item)
            score = sum(text.count(token) for token in tokens)
            if score:
                scored.append((score, str(item.get("skill_id") or ""), item))
        materialized = [
            item
            for _score, _skill_id, item in sorted(scored, key=lambda row: (-row[0], row[1]))
        ]
    selected = declared + materialized[:25]
    brief = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        brief.append(
            {
                "skill_id": item.get("skill_id") or item.get("id") or item.get("name", ""),
                "description": item.get("description", ""),
                "trigger_summary": item.get("trigger_summary", item.get("description", "")),
                "allowed_tools": item.get("allowed_tools", []),
            }
        )
    return brief


def _skill_index_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("skill_id", "name", "description", "trigger_summary", "path")
    ).lower().replace("-", " ").replace("_", " ")


def _tokens(query: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", str(query or "").lower())
        if token not in {"the", "and", "for", "with", "this", "that", "skill", "skills"}
    ]


def _merge_materialized_skills(
    root: Path,
    skills: dict[str, Any],
    *,
    available_tools: set[str],
) -> dict[str, Any]:
    raw = skills.get("skills", [])
    declared = list(raw) if isinstance(raw, list) else []
    seen = {
        str(item.get("skill_id") or item.get("id") or item.get("name") or "")
        for item in declared
        if isinstance(item, dict)
    }
    merged = list(declared)
    for path in sorted((root / "hydra" / "schemes").rglob("SKILL.md")):
        item = _materialized_skill_index_item(root, path, available_tools=available_tools)
        skill_id = item["skill_id"]
        if skill_id in seen:
            continue
        seen.add(skill_id)
        merged.append(item)
    updated = dict(skills)
    updated["skills"] = merged
    updated["materialized_skill_count"] = len(merged) - len(declared)
    return updated


def _materialized_skill_index_item(
    root: Path,
    path: Path,
    *,
    available_tools: set[str],
) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _split_frontmatter(text)
    skill_id = str(meta.get("name") or path.parent.name)
    description = str(meta.get("description") or _first_non_heading_line(body))
    allowed = [
        tool
        for tool in ("fs_read", "list_directory", "grep", "skill_library.search", "skill_show")
        if tool in available_tools
    ]
    return {
        "skill_id": skill_id,
        "name": skill_id.replace("-", " ").title(),
        "path": _rel_path(root, path),
        "description": description,
        "trigger_summary": description,
        "allowed_tools": allowed,
        "materialized": True,
    }


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    data = yaml.safe_load(parts[1]) or {}
    return (data if isinstance(data, dict) else {}), parts[2]


def _first_non_heading_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:240]
    return ""


def _rel_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _needs_parallel_preflight(operator_input: str, decision: dict[str, Any]) -> bool:
    kind = str((decision.get("intent") or {}).get("kind") or "")
    if kind not in {"audit", "code_change", "research", "design", "operate"}:
        return False
    text = f"{operator_input} {json.dumps(decision.get('plan', []), sort_keys=True)}".lower()
    broad_terms = (
        "whole repo",
        "end to end",
        "e2e",
        "audit",
        "debug",
        "fix",
        "implement",
        "refactor",
        "runtime",
        "parallel",
        "subagent",
        "loop",
        "all seams",
    )
    return any(term in text for term in broad_terms)


def _parallel_preflight_tasks(operator_input: str) -> list[str]:
    target = operator_input.strip()
    return [
        f"Read-only audit: find relevant skill/library support for this task and cite files only: {target}",
        f"Read-only audit: inspect runtime/model-routing/subagent seams for this task and cite files only: {target}",
        f"Read-only verification audit: identify existing tests or harnesses that prove this task and cite commands only: {target}",
    ]


def _approval_required_when(policies: dict[str, Any]) -> list[str]:
    gates = policies.get("danger_gates", {}).get("gates", [])
    if not isinstance(gates, list):
        return []
    return [str(gate.get("description") or gate.get("gate_id")) for gate in gates if isinstance(gate, dict)]


def _recent_episodes(root: Path, *, limit: int = 5) -> list[str]:
    path = root / ".hydraAgent/memory/episodic.jsonl"
    if not path.is_file():
        return []
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-limit:]


def _append_episodic_memory(
    root: Path,
    *,
    operator_input: str,
    decision: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    memory_dir = root / ".hydraAgent" / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    path = memory_dir / "episodic.jsonl"
    record = {
        "schema": "hydra.memory.episodic.v1",
        "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
        "operator_input": operator_input,
        "decision": decision,
        "execution": execution,
        "goal_achieved": "fully",
        "source": "hydra.declarative_runtime",
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _invoke_tool(contract: dict[str, Any], arguments: dict[str, Any], *, root: Path) -> Any:
    executor = str(contract.get("executor") or "")
    if ":" not in executor:
        raise DeclarativeRuntimeError(f"bad executor reference: {executor}")
    module_name, fn_name = executor.split(":", 1)
    module = importlib.import_module(module_name)
    fn = getattr(module, fn_name)
    merged = _default_arguments(contract)
    merged.update(arguments)
    tool_id = str(contract.get("tool_id") or "")
    if _executor_requires_root(contract):
        merged.setdefault("root", root)
    if tool_id == "git_diff" and merged.get("worktree") in {None, "", "."}:
        merged["worktree"] = root
    if tool_id == "shell":
        merged.pop("cwd", None)
        merged.setdefault("root", root)
    return fn(**merged)


def _executor_requires_root(contract: dict[str, Any]) -> bool:
    return str(contract.get("tool_id") or "") in {
        "fs_read",
        "list_directory",
        "grep",
        "glob",
        "shell",
        "skill_library.search",
        "skill_search",
        "spawn_subagent",
        "spawn_subagents",
    }


def _contract_requires_approval(contract: dict[str, Any]) -> bool:
    approval = contract.get("approval") or {}
    if not isinstance(approval, dict):
        return True
    return bool(approval.get("required", True))


def _policy_tool_name(contract: dict[str, Any]) -> str:
    tool_id = str(contract.get("tool_id") or "")
    return "bash" if tool_id == "shell" else tool_id


def _default_arguments(contract: dict[str, Any]) -> dict[str, Any]:
    args: dict[str, Any] = {}
    props = ((contract.get("input_schema") or {}).get("properties") or {})
    for name, spec in props.items():
        if isinstance(spec, dict) and "default" in spec:
            args[name] = spec["default"]
    return args
