"""Hydra-native mission execution loop.

Orchestrates:
1. Mission record creation
2. Truth memory + skill routing
3. Safe command dispatch (via orchestrate.dispatch)
4. Objective verification
5. Continuation plan generation

Uses orchestrate.dispatch() directly for safe commands (no rubric needed).
Continuation is a separate primitive run after mission completes.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from hydra.autonomy import SAFE_BASH_COMMANDS, classify_tool_call
from hydra.continuation import ContinuationError, run_continuation
from hydra.hooks import run_hooks
from hydra.lessons import remember_lesson
from hydra.memory_kernel import assemble_memory_briefing, assemble_truth_context
from hydra.mission import create_mission
from hydra.mission_objectives import load_objectives
from hydra.orchestrate import SubagentTask, dispatch
from hydra.proc import resolve_bash
from hydra.skill_spine import route_capability_cards, route_skill_records
from hydra.wiki_memory import write_mission_page


SCHEMA = "hydra.mission_loop.v1"


class MissionLoopError(Exception):
    """Operator-facing mission loop failure."""


def run_mission_loop(
    *,
    root: Path,
    operator_prompt: str,
    max_concurrency: int = 2,
    memory_root: str | Path | None = None,
    auto_continue: bool = False,
) -> dict[str, Any]:
    repo_root = Path(root).expanduser().resolve()
    if not repo_root.is_dir():
        raise MissionLoopError(f"mission root is not a directory: {repo_root}")
    mission = create_mission(
        root=repo_root,
        operator_prompt=operator_prompt,
        intent="execute",
        next_action="verify",
    )
    run_dir = Path(mission.evidence["run_dir"])
    steps: list[dict[str, Any]] = [
        _step("mission_created", "created mission record", {"mission_id": mission.mission_id}),
    ]
    started_hooks = run_hooks(repo_root, "mission_started", max_concurrency=max_concurrency)
    if started_hooks["executed_count"]:
        steps.append(_step("hooks", "ran mission_started lifecycle hooks", started_hooks))
    truth_context = _active_truth_memory(
        repo_root=repo_root,
        run_dir=repo_root / run_dir,
        operator_prompt=mission.operator_prompt,
        memory_root=memory_root,
    )
    if truth_context is not None:
        steps.append(_step("truth_memory", "loaded source-cited truth memory for mission", truth_context))
    memory_briefing = _memory_briefing(
        repo_root=repo_root,
        run_dir=repo_root / run_dir,
        operator_prompt=mission.operator_prompt,
        memory_root=memory_root,
    )
    if memory_briefing is not None:
        steps.append(_step("memory_briefing", "recorded operator memory briefing for mission", memory_briefing))
    skill_routes = route_skill_records(mission.operator_prompt)
    capability_routes = route_capability_cards(mission.operator_prompt, repo_root / ".hydraAgent/capabilities")
    steps.append(
        _step(
            "skill_route",
            "routed trusted skills and native capabilities",
            {
                "skills": [record.name for record in skill_routes],
                "capabilities": [card.id for card in capability_routes],
            },
        )
    )
    planned_commands = _safe_command_plan(repo_root)
    steps.append(
        _step(
            "safe_command_plan",
            "selected exact auto-allowed proof/status commands",
            {"commands": [task.command for task in planned_commands]},
        )
    )
    dispatch_report = dispatch(planned_commands, max_concurrency=max_concurrency)
    steps.append(_step("worker_dispatch", "executed bounded safe workers", dispatch_report))
    failures = dispatch_report["failed"] + dispatch_report["timed_out"]
    objective_tasks = [objective.to_task(repo_root) for objective in load_objectives(repo_root)]
    if objective_tasks:
        objective_report = dispatch(objective_tasks, max_concurrency=max_concurrency)
        steps.append(_step("objective_dispatch", "executed repo-declared objective manifest", objective_report))
        failures += objective_report["failed"] + objective_report["timed_out"]
    validation_tasks = _runtime_validation_plan(repo_root)
    validation_report = dispatch(validation_tasks, max_concurrency=min(max_concurrency, len(validation_tasks)))
    steps.append(_step("runtime_validation", "executed required runtime validation harnesses", validation_report))
    failures += validation_report["failed"] + validation_report["timed_out"]
    verdict = "GREEN" if failures == 0 else "RED"
    steps.append(
        _step(
            "verification",
            "computed mission verdict from worker dispatch evidence",
            {"verdict": verdict, "failed": failures},
        )
    )
    verified_hooks = run_hooks(repo_root, "mission_verified", max_concurrency=max_concurrency)
    if verified_hooks["executed_count"]:
        steps.append(_step("hooks", "ran mission_verified lifecycle hooks", verified_hooks))
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "mission": mission.to_dict(),
        "summary": {
            "verdict": verdict,
            "steps_total": len(steps),
            "steps_failed": failures,
            "safe_commands": len(planned_commands),
        },
        "steps": steps,
        "evidence_path": (run_dir / "mission_loop.json").as_posix(),
    }
    evidence_path = repo_root / report["evidence_path"]
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if verdict != "GREEN":
        lesson = remember_lesson(
            (
                "mission objective failure: inspect mission evidence and convert "
                "recurring objective failure into a repair slice"
            ),
            source=report["evidence_path"],
            tags=["mission-loop", "failure", "objective"],
            memory_root=memory_root,
        )
        steps.append(_step("failure_lesson", "wrote sourced durable failure lesson", lesson))
        candidate = _failure_eval_candidate(repo_root=repo_root, report=report)
        steps.append(_step("eval_candidate", "wrote failure regression eval candidate", candidate))
        report["steps"] = steps
    wiki_path = write_mission_page(
        repo_root,
        mission_id=mission.mission_id,
        title=mission.operator_prompt,
        evidence_path=report["evidence_path"],
    )
    report["wiki_path"] = wiki_path.relative_to(repo_root).as_posix()
    report["continuation"] = _continuation_plan(report)
    if auto_continue:
        try:
            continuation_run = run_continuation(
                root=repo_root,
                continuation=_auto_continuation_plan(report["continuation"]),
                max_concurrency=max_concurrency,
            )
        except ContinuationError as e:
            continuation_run = {
                "schema": "hydra.continuation_run.v1",
                "verdict": "RED",
                "error": str(e),
            }
        report["continuation_run"] = continuation_run
        steps.append(_step("continuation_run", "executed mission continuation plan", continuation_run))
        report["steps"] = steps
    evidence_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def render_mission_loop_text(report: dict[str, Any]) -> str:
    summary = report["summary"]
    mission = report["mission"]
    lines = [
        f"Mission execution loop: {mission['mission_id']}",
        f"verdict={summary['verdict']} steps={summary['steps_total']} safe_commands={summary['safe_commands']}",
        f"evidence={report['evidence_path']}",
        f"wiki={report.get('wiki_path', '(none)')}",
    ]
    for step in report["steps"]:
        lines.append(f"- {step['kind']}: {step['summary']}")
    return "\n".join(lines) + "\n"


def _safe_command_plan(root: Path) -> list[SubagentTask]:
    candidates = ["pwd", "git status --short", "git diff --check"]
    tasks: list[SubagentTask] = []
    for command in candidates:
        decision = classify_tool_call("bash", {"command": command}, "ask")
        if decision["decision"] != "auto_allow" or command not in SAFE_BASH_COMMANDS:
            raise MissionLoopError(f"unsafe command escaped mission loop plan: {command}")
        tasks.append(
            SubagentTask(
                id=_command_id(command),
                command=[resolve_bash(), "-lc", command],
                cwd=str(root),
                timeout_seconds=20,
            )
        )
    return tasks


def _runtime_validation_plan(root: Path) -> list[SubagentTask]:
    commands = [
        (
            "integrity",
            "if [ -x build/run_integrity.sh ]; then bash build/run_integrity.sh --check-locked; "
            "else printf '{\"skipped\":\"missing build/run_integrity.sh\"}\\n'; fi",
        ),
        (
            "tools",
            "if [ -x build/run_tools.sh ]; then bash build/run_tools.sh; "
            "else printf '{\"skipped\":\"missing build/run_tools.sh\"}\\n'; fi",
        ),
    ]
    return [
        SubagentTask(
            id=f"validate-{name}",
            command=[resolve_bash(), "-lc", command],
            cwd=str(root),
            timeout_seconds=60,
        )
        for name, command in commands
    ]


def _active_truth_memory(
    *,
    repo_root: Path,
    run_dir: Path,
    operator_prompt: str,
    memory_root: str | Path | None,
) -> dict[str, Any] | None:
    try:
        packet = assemble_truth_context(
            operator_prompt,
            repo_root=repo_root,
            memory_root=Path(memory_root) if memory_root else None,
            budget_chars=4096,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not packet["records"]:
        return None
    packet_path = run_dir / "truth_context.json"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "packet_path": packet_path.relative_to(repo_root).as_posix(),
        "records_selected": len(packet["records"]),
        "proof": packet["proof"],
    }


def _memory_briefing(
    *,
    repo_root: Path,
    run_dir: Path,
    operator_prompt: str,
    memory_root: str | Path | None,
) -> dict[str, Any] | None:
    try:
        packet = assemble_memory_briefing(
            operator_prompt,
            repo_root=repo_root,
            memory_root=Path(memory_root) if memory_root else None,
            budget_chars=4096,
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if packet["selected_count"] <= 0:
        return None
    packet_path = run_dir / "memory_briefing.json"
    packet_path.parent.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "packet_path": packet_path.relative_to(repo_root).as_posix(),
        "selected_count": packet["selected_count"],
        "quality": packet["quality"],
        "gaps": packet["gaps"],
        "proof": packet["proof"],
    }


def _continuation_plan(report: dict[str, Any]) -> dict[str, Any]:
    mission = report["mission"]
    summary = report["summary"]
    evidence_inputs = [report["evidence_path"]]
    if report.get("wiki_path"):
        evidence_inputs.append(report["wiki_path"])
    for step in report["steps"]:
        if step["kind"] in {"truth_memory", "memory_briefing"}:
            packet_path = step["data"].get("packet_path")
            if packet_path:
                evidence_inputs.append(packet_path)
    if summary["verdict"] == "GREEN":
        objective = "promote the next repo build slice from verified mission evidence"
        verification = [
            "python3 -m hydra status",
            "python3 -m hydra task-eval agent-parity --format text",
            "git diff --check",
        ]
    else:
        objective = "repair failed mission objective and rerun mission loop to GREEN"
        verification = [
            "python3 -m hydra status",
            "python3 -m hydra mission run \"repair failed mission objective\"",
            "git diff --check",
        ]
    return {
        "schema": "hydra.continuation_plan.v1",
        "mission_id": mission["mission_id"],
        "verdict": summary["verdict"],
        "next_slices": [
            {
                "id": f"continue-{mission['mission_id']}",
                "objective": objective,
                "evidence_inputs": evidence_inputs,
                "verification_commands": verification,
            }
        ],
    }


def _auto_continuation_plan(continuation: dict[str, Any]) -> dict[str, Any]:
    plan = json.loads(json.dumps(continuation))
    plan["next_slices"][0]["verification_commands"] = [
        "python3 -m hydra status",
        "git diff --check",
    ]
    return plan


def _failure_eval_candidate(*, repo_root: Path, report: dict[str, Any]) -> dict[str, Any]:
    mission = report["mission"]
    mission_id = mission["mission_id"]
    candidate = {
        "schema": "hydra.self_improvement.eval_candidate.v1",
        "suggested_eval_id": f"regression-{mission_id}",
        "source_evidence": report["evidence_path"],
        "operator_prompt": mission["operator_prompt"],
        "failure_summary": {
            "verdict": report["summary"]["verdict"],
            "steps_failed": report["summary"]["steps_failed"],
        },
        "verification_commands": [
            "python3 -m hydra status",
            f"python3 -m hydra mission run {json.dumps(mission['operator_prompt'])}",
        ],
        "promotion_rule": "Create a focused eval that reproduces this failure before changing runtime behavior.",
    }
    path = repo_root / mission["evidence"]["run_dir"] / "eval_candidate.json"
    path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": path.relative_to(repo_root).as_posix(), "suggested_eval_id": candidate["suggested_eval_id"]}


def _step(kind: str, summary: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "data": data,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


def _command_id(command: str) -> str:
    return "cmd-" + command.replace(" ", "-").replace("/", "-").replace(".", "-")
