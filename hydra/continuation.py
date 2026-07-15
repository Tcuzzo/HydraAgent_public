"""Execute Hydra continuation plans as bounded runtime evidence.

Continuation = post-mission follow-through runner.
Takes a continuation plan, executes verification commands (via orchestrate.dispatch),
records evidence. Dumb and narrow by design.

Uses orchestrate.dispatch() directly — no rubric judging needed.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from typing import Callable

from hydra.autonomy import SAFE_BASH_COMMANDS, classify_tool_call
from hydra.emergency_fallback import (
    LIFE_SUPPORT_PROVIDER,
    S6_CHECKPOINT_NAME,
    probe_model,
)
from hydra.orchestrate import SubagentTask, dispatch
from hydra.proc import resolve_bash
from hydra import workbench_ledger as _wl


SCHEMA = "hydra.continuation_run.v1"
LOOP_SCHEMA = "hydra.continuation_loop.v1"
REPO_ROOT = Path(__file__).resolve().parent.parent


class ContinuationError(Exception):
    """Operator-facing continuation failure."""


def resume_blocked_mission(
    *,
    repo_root: str | Path,
    ledger_path: str | Path,
    slice_id: str,
    probe: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """S6 — resume an autonomous mission that life-support paused.

    A mission is resumable when ALL of:
      - its ledger record is ``blocked`` (the running->blocked transition the
        S6 fallback drove), AND
      - it carries an ``s6_fallback_checkpoint`` path that exists on disk, AND
      - the provider is healthy again (``probe()`` returns True; defaults to
        probing the local life-support Ollama via ``probe_model``).

    On success it loads the checkpoint, walks the ledger ``blocked->running``
    (the existing transition map — no new states), and returns the checkpoint
    so the caller can re-enter ``AgentLoop.run`` from where it paused. On a
    failed probe it leaves the mission ``blocked`` and reports ``resumed=False``
    — never a silent give-up.
    """
    root = Path(repo_root).expanduser().resolve()
    ledger = Path(ledger_path)
    records = _wl.load_records(ledger)
    record = next((r for r in records if r.slice_id == slice_id), None)
    if record is None:
        raise ContinuationError(f"slice_id not found in ledger: {slice_id!r}")
    if record.status != "blocked":
        return {"resumed": False, "reason": f"mission is {record.status!r}, not blocked", "slice_id": slice_id}
    if not record.s6_fallback_checkpoint:
        return {"resumed": False, "reason": "no s6 fallback checkpoint recorded", "slice_id": slice_id}

    checkpoint_path = root / record.s6_fallback_checkpoint
    if not checkpoint_path.is_file():
        return {
            "resumed": False,
            "reason": f"checkpoint missing on disk: {record.s6_fallback_checkpoint}",
            "slice_id": slice_id,
        }

    healthy = probe() if probe is not None else probe_model(LIFE_SUPPORT_PROVIDER, "", timeout=5.0)
    if not healthy:
        return {"resumed": False, "reason": "provider still unhealthy", "slice_id": slice_id}

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    _wl.update_record(ledger, slice_id, status="running")
    return {
        "resumed": True,
        "slice_id": slice_id,
        "checkpoint": checkpoint,
        "checkpoint_path": str(checkpoint_path),
    }


def run_continuation(
    *,
    root: str | Path,
    continuation: dict[str, Any],
    max_concurrency: int = 2,
) -> dict[str, Any]:
    repo_root = Path(root).expanduser().resolve()
    if not repo_root.is_dir():
        raise ContinuationError(f"continuation root is not a directory: {repo_root}")
    if continuation.get("schema") != "hydra.continuation_plan.v1":
        raise ContinuationError("unsupported continuation schema")
    slices = continuation.get("next_slices")
    if not isinstance(slices, list) or not slices:
        raise ContinuationError("continuation requires at least one next slice")
    next_slice = slices[0]
    commands = next_slice.get("verification_commands", [])
    if not isinstance(commands, list) or not commands:
        raise ContinuationError("next slice requires verification_commands")
    tasks = [_task_from_command(command, repo_root) for command in commands]
    dispatch_report = dispatch(tasks, max_concurrency=max_concurrency)
    verdict = "GREEN" if dispatch_report["failed"] == 0 and dispatch_report["timed_out"] == 0 else "RED"
    packet = {
        "schema": SCHEMA,
        "slice_id": next_slice.get("id", ""),
        "objective": next_slice.get("objective", ""),
        "mission_id": continuation.get("mission_id", ""),
        "verdict": verdict,
        "evidence_inputs": next_slice.get("evidence_inputs", []),
        "dispatch": dispatch_report,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    evidence_path = _evidence_path(repo_root, packet["slice_id"])
    packet["evidence_path"] = evidence_path.relative_to(repo_root).as_posix()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return packet


def run_continuation_loop(
    *,
    root: str | Path,
    continuation: dict[str, Any],
    cycles: int = 2,
    max_concurrency: int = 2,
) -> dict[str, Any]:
    repo_root = Path(root).expanduser().resolve()
    if cycles < 1:
        raise ContinuationError("continuation loop requires at least one cycle")
    runs = []
    for index in range(cycles):
        cycle_plan = _cycle_plan(continuation, index + 1)
        packet = run_continuation(
            root=repo_root,
            continuation=cycle_plan,
            max_concurrency=max_concurrency,
        )
        runs.append(packet)
        if packet["verdict"] != "GREEN":
            break
    verdict = "GREEN" if len(runs) == cycles and all(run["verdict"] == "GREEN" for run in runs) else "RED"
    report = {
        "schema": LOOP_SCHEMA,
        "mission_id": continuation.get("mission_id", ""),
        "verdict": verdict,
        "cycles_requested": cycles,
        "cycles_completed": len(runs),
        "runs": runs,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    evidence_path = _loop_evidence_path(repo_root, report["mission_id"])
    report["evidence_path"] = evidence_path.relative_to(repo_root).as_posix()
    state_path = _loop_state_path(repo_root, report["mission_id"])
    report["state_path"] = state_path.relative_to(repo_root).as_posix()
    state = _next_continuation_state(report, continuation)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def render_text(packet: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Continuation run: {packet['slice_id']}",
            f"verdict={packet['verdict']} succeeded={packet['dispatch']['succeeded']} failed={packet['dispatch']['failed']}",
            f"evidence={packet['evidence_path']}",
        ]
    ) + "\n"


def render_loop_text(report: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"Continuation loop: {report['mission_id']}",
            f"verdict={report['verdict']} cycles={report['cycles_completed']}/{report['cycles_requested']}",
            f"evidence={report['evidence_path']}",
        ]
    ) + "\n"


def _cycle_plan(continuation: dict[str, Any], cycle: int) -> dict[str, Any]:
    plan = json.loads(json.dumps(continuation))
    if plan.get("schema") != "hydra.continuation_plan.v1":
        raise ContinuationError("unsupported continuation schema")
    slices = plan.get("next_slices")
    if not isinstance(slices, list) or not slices:
        raise ContinuationError("continuation requires at least one next slice")
    base_id = str(slices[0].get("id", "continuation"))
    slices[0]["id"] = f"{base_id}-cycle-{cycle}"
    return plan


def _task_from_command(command: str, root: Path) -> SubagentTask:
    if not isinstance(command, str) or not command.strip():
        raise ContinuationError("verification command must be a non-empty string")
    decision = classify_tool_call("bash", {"command": command}, "ask")
    if decision["decision"] != "auto_allow" or command not in SAFE_BASH_COMMANDS:
        raise ContinuationError(f"continuation command is not auto-allowed: {command}")
    return SubagentTask(
        id="continuation-" + command.replace(" ", "-").replace("/", "-").replace(".", "-"),
        command=[resolve_bash(), "-lc", command],
        cwd=str(REPO_ROOT if command.startswith("python3 -m hydra ") else root),
        env={"PYTHONPATH": str(REPO_ROOT)},
        timeout_seconds=60,
    )


def _evidence_path(root: Path, slice_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in slice_id)[:120] or "continuation"
    return root / "evidence" / "continuation-runs" / safe / "continuation_run.json"


def _loop_evidence_path(root: Path, mission_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in mission_id)[:120] or "continuation-loop"
    return root / "evidence" / "continuation-loops" / safe / "continuation_loop.json"


def _loop_state_path(root: Path, mission_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in mission_id)[:120] or "continuation-loop"
    return root / "evidence" / "continuation-loops" / safe / "continuation_state.json"


def _next_continuation_state(report: dict[str, Any], continuation: dict[str, Any]) -> dict[str, Any]:
    base_slice = continuation["next_slices"][0]
    base_id = str(base_slice.get("id", "continuation"))
    next_cycle = int(report["cycles_completed"]) + 1
    evidence_inputs = list(base_slice.get("evidence_inputs", []))
    evidence_inputs.append(report["evidence_path"])
    return {
        "schema": "hydra.continuation_state.v1",
        "mission_id": report["mission_id"],
        "last_loop_evidence": report["evidence_path"],
        "last_verdict": report["verdict"],
        "next_cycle": next_cycle,
        "next_continuation": {
            "schema": "hydra.continuation_plan.v1",
            "mission_id": report["mission_id"],
            "verdict": report["verdict"],
            "next_slices": [
                {
                    "id": f"{base_id}-next-{next_cycle}",
                    "objective": f"continue after {report['cycles_completed']} verified cycle(s): {base_slice.get('objective', '')}",
                    "evidence_inputs": evidence_inputs,
                    "verification_commands": [
                        "python3 -m hydra status",
                        "git diff --check",
                    ],
                }
            ],
        },
    }
