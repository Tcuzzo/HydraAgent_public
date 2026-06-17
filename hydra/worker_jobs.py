"""Bounded worker job runtime for Hydra implementation tasks.

Job/slice contract extension — optional resolution_spec field
-------------------------------------------------------------
A job may carry an optional ``resolution_spec`` to enforce the two-list
resolution gate (SWE-bench model):

    {
      "resolution_spec": {
        "fail_to_pass": ["tests/test_fix.py::test_it"],
        "pass_to_pass": ["tests/test_existing.py::test_stable"]
      }
    }

When present, a job is marked 'passed' ONLY if:
  - all verify_commands exit 0  (necessary but NOT sufficient)
  - ResolutionGate.evaluate() returns resolved=True:
      - every fail_to_pass test PASSES after the change
      - every pass_to_pass test PASSES after the change
      - every fail_to_pass test was FAILING at baseline (anti-hollow-green)
        WARNING: when a baseline_runner cannot be wired, the gate logs a warning
        and enforces only the after-state check (hollow-green risk).

Jobs WITHOUT a resolution_spec behave exactly as before (back-compat).

See hydra/resolution_gate.py for full contract and slice schema docs.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from hydra.apply_patch import apply_patch, create_file, PatchFailure
from hydra.worker_memory import build_worker_memory_context
from hydra.resolution_gate import gate_from_job, make_subprocess_test_runner, subprocess_test_runner
from hydra.exec_backend import run_sandboxed_shell
from hydra.event_log import EventLog
# auto_fix_loop is imported lazily inside run_worker_job to avoid circular imports:
#   worker_jobs → auto_fix_loop → swarm_orchestrator → autonomous_mission → worker_jobs

_log = logging.getLogger(__name__)


WORKER_JOB_SCHEMA = "hydra.worker_job.v1"
WORKER_JOB_RESULT_SCHEMA = "hydra.worker_job_result.v1"
WORKER_BATCH_SCHEMA = "hydra.worker_batch.v1"
WORKER_BATCH_RESULT_SCHEMA = "hydra.worker_batch_result.v1"


class WorkerJobError(Exception):
    """Worker job packet or execution failure."""


def build_worker_job_packet(
    *,
    job_id: str,
    goal: str,
    actions: list[dict[str, Any]],
    verify_commands: list[str],
    plan: str | None = None,
) -> dict[str, Any]:
    return _normalize_job(
        {
            "schema": WORKER_JOB_SCHEMA,
            "job_id": job_id,
            "goal": goal,
            "plan": plan or goal,
            "actions": actions,
            "verify_commands": verify_commands,
        }
    )


def _build_default_repair_fn(
    model_client: Any | None,
    job: dict[str, Any],
) -> Callable[[str, int, dict[str, Any]], list[dict[str, Any]]]:
    """Build the DEFAULT model-backed repair_fn for a job.

    When no repair_fn is injected, the worker builds one that:
    1. Calls the MOST CAPABLE model (resolved via model_routing: 'complex' role)
       with a prompt containing the real stderr + the failing file context.
    2. Parses the model's response as a JSON list of worker-job action dicts
       (write_text / replace_text / apply_patch).
    3. Returns those actions so the loop can apply them to disk.

    The model client is injectable for tests (pass ``repair_model_client`` to
    ``run_worker_job``). In production, the client is built from the MOST CAPABLE
    model resolved via model_routing (roles.planner → cloud-planner, the highest
    complexity slot — code generation routes to the most capable available model).

    If the model is unavailable or returns unparseable output the function logs a
    warning and returns [] (no-op) so the loop continues rather than crashing.
    """
    # Lazy import to avoid circular dependencies.
    _client: Any | None = model_client

    def _repair(stderr: str, attempt: int, ctx: dict[str, Any]) -> list[dict[str, Any]]:
        nonlocal _client
        if _client is None:
            try:
                from hydra.model_routing import load_routing           # noqa: PLC0415
                from hydra.providers import make_client, ProviderError  # noqa: PLC0415
                mr = load_routing()
                # 'planner' = cloud-planner (most capable / highest complexity slot).
                entry = mr.role_entry("planner")
                _client, _ = make_client(entry.provider)
            except Exception as exc:  # noqa: BLE001
                _log.warning("repair_fn: cannot build model client: %s", exc)
                return []

        from hydra.llm import ChatMessage  # noqa: PLC0415

        goal = job.get("goal", "")
        plan = job.get("plan", "")
        prompt = (
            "You are an expert repair agent.  A verification command has failed.\n"
            f"JOB GOAL: {goal}\n"
            f"JOB PLAN: {plan}\n\n"
            f"STDERR / FAILURE OUTPUT (attempt {attempt}):\n{stderr}\n\n"
            "Return ONLY a JSON array of worker-job action dicts to fix the failure. "
            "Each action must be one of:\n"
            '  {"kind": "write_text", "path": "relative/path", "text": "...full new content..."}\n'
            '  {"kind": "replace_text", "path": "relative/path", "old": "...", "new": "..."}\n'
            '  {"kind": "apply_patch", "patch": "...unified diff..."}\n'
            "If you cannot determine a fix, return an empty array []. "
            "Do NOT wrap in markdown. Return raw JSON only."
        )

        try:
            from hydra.model_routing import load_routing  # noqa: PLC0415
            mr = load_routing()
            entry = mr.role_entry("planner")
            model_name = entry.model
        except Exception:  # noqa: BLE001
            model_name = "qwen2.5:72b"  # fallback to cloud planner default

        try:
            resp = _client.chat(
                [ChatMessage(role="user", content=prompt)],
                model=model_name,
            )
            content = resp.content.strip()
            # Strip markdown fences if the model wrapped the JSON
            if content.startswith("```"):
                lines = content.splitlines()
                # drop first (``` or ```json) and last (```) lines
                inner = "\n".join(lines[1:-1]) if len(lines) > 2 else ""
                content = inner.strip()
            actions = json.loads(content)
            if not isinstance(actions, list):
                _log.warning("repair_fn: model returned non-list JSON: %r", content[:200])
                return []
            return actions
        except Exception as exc:  # noqa: BLE001
            _log.warning("repair_fn: model call or parse failed (attempt %d): %s", attempt, exc)
            return []

    return _repair


def run_worker_job(
    job: dict[str, Any],
    *,
    repo_root: Path,
    evidence_root: Path | None = None,
    repair_fn: Callable[[str, int, dict[str, Any]], list[dict[str, Any]]] | None = None,
    repair_model_client: Any | None = None,
) -> dict[str, Any]:
    """Run a worker job.

    Parameters
    ----------
    job:
        The job dict (must conform to the worker_job.v1 schema).
    repo_root:
        The directory the job actions and verify commands run in.
    evidence_root:
        Optional override for where run artifacts are written.
    repair_fn:
        PYTHON SEAM — a callable that receives (stderr, attempt, context) and
        returns a list of worker-job action dicts to apply before the next
        verify attempt.  When ``auto_fix.enabled=True`` and a verify command
        fails, this function is called on each repair cycle.

        If None (default) and ``auto_fix.enabled=True``, a DEFAULT repair_fn
        is built automatically from the MOST CAPABLE model (resolved via
        model_routing 'planner' role — cloud-planner = highest complexity).
        Pass ``repair_model_client`` to inject a fake client for tests so no
        live model is needed.

        The OLD JSON ``auto_fix._repair_fn`` path is REMOVED: placing a callable
        in the job dict was never JSON-serializable and always crashed.  The
        Python param is the only real repair seam.
    repair_model_client:
        Injectable model client for the DEFAULT repair_fn.  When provided, the
        default repair_fn calls this client instead of building one from the
        model_routing config.  Tests inject a fake client here; production leaves
        this None so the real most-capable client is resolved at first use.
    """
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise WorkerJobError(f"repo_root is not a directory: {root}")
    normalized = _normalize_job(job)
    run_dir = _run_dir(root, evidence_root, normalized["job_id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "job.json").write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "plan.md").write_text(normalized["plan"].rstrip() + "\n", encoding="utf-8")
    memory_context = build_worker_memory_context(
        query=f"{normalized['goal']}\n\n{normalized['plan']}",
        repo_root=root,
    )
    (run_dir / "memory_context.json").write_text(json.dumps(memory_context, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # --- Typed event log: emitted BY DEFAULT on the real worker path ----------
    event_log_path = run_dir / "typed_events.jsonl"
    elog = EventLog(event_log_path)
    elog.emit("job_started", {
        "job_id": normalized["job_id"],
        "goal": normalized["goal"],
        "actions_count": len(normalized["actions"]),
        "verify_commands_count": len(normalized["verify_commands"]),
    })

    command_rows: list[dict[str, Any]] = []
    status = "passed"
    failure_reason: str | None = None
    try:
        # --- Step 1: capture baseline BEFORE applying the fix -----------------
        # The baseline must be read from the PRE-CHANGE state.  If we run the
        # baseline runner after _apply_actions the repo already contains the fix
        # and every fail_to_pass test looks "already green" — a false hollow-green.
        #
        # We build a cwd-correct runner for this repo_root and run all
        # fail_to_pass tests NOW (before any writes).  The results are stored in
        # a plain dict and wrapped in a closure so gate.evaluate() can call them
        # as if they were a live runner — but they're actually pre-captured.
        baseline_results: dict[str, bool] = {}
        spec_raw = normalized.get("resolution_spec")
        if spec_raw and isinstance(spec_raw, dict):
            fail_to_pass_ids: list[str] = list(spec_raw.get("fail_to_pass", []))
            if fail_to_pass_ids:
                runner = make_subprocess_test_runner(root)
                for _test_id in fail_to_pass_ids:
                    baseline_results[_test_id] = runner(_test_id)

        def _baseline_runner_from_cache(test_id: str) -> bool:
            """Return the PRE-CHANGE pass/fail for this test (already captured)."""
            # Fall back to False (was failing) for any test not captured;
            # this is conservative — it lets the gate through rather than
            # false-blocking.  In practice every fail_to_pass test is captured
            # above before apply.
            return baseline_results.get(test_id, False)

        # --- Step 2: apply the fix --------------------------------------------
        for action in normalized["actions"]:
            _apply_single_action(root, action)
            elog.emit("action_applied", {
                "kind": action.get("kind"),
                "path": action.get("path"),
                "ok": True,
            })
        _write_diff(root, run_dir)

        # --- Step 3: run verify commands on the AFTER state -------------------
        _job_network_enabled: bool = bool(normalized.get("network_enabled", False))
        command_rows = _run_verify_commands(
            root, normalized["verify_commands"], network_enabled=_job_network_enabled
        )
        for row in command_rows:
            elog.emit("verify_ran", {
                "command": row["command"],
                "returncode": row["exit_code"],
                "duration_ms": row["duration_ms"],
            })

        failed = [row for row in command_rows if row["exit_code"] != 0]
        if failed:
            status = "failed"
            first_fail = failed[0]
            _fail_output = first_fail.get("output", "") or ""
            # Enrich failure_reason when the output looks like a network / DNS error
            # AND the sandbox had network disabled — makes the cause immediately obvious.
            if not _job_network_enabled and any(
                pat in _fail_output for pat in _NETWORK_ERROR_PATTERNS
            ):
                failure_reason = (
                    f"verification command failed (network was disabled in the sandbox; "
                    f"set network_enabled:true in the job to allow it): {first_fail['command']}"
                )
            else:
                failure_reason = f"verification command failed: {first_fail['command']}"
            # --- Auto-fix loop: if job opts in, feed real stderr back into repair ---
            # When auto_fix.enabled=True, the bounded stderr-feedback repair loop
            # takes over from here. It re-runs the failing verify command up to
            # MAX_REPAIR_ATTEMPTS times, feeding the real stderr into the repair
            # function after each failure, escalating once at ESCALATE_AFTER.
            auto_fix_spec = normalized.get("auto_fix") or {}
            if isinstance(auto_fix_spec, dict) and auto_fix_spec.get("enabled"):
                # Lazy import to avoid the circular dependency chain.
                from hydra.auto_fix_loop import run_repair_loop, AutoFixResult  # noqa: PLC0415

                # Collect the first failing command's stderr as the seed input.
                first_failed_row = failed[0]
                first_stderr = first_failed_row.get("output", "") or failure_reason

                # Resolve the repair function to use:
                #   1. Python param (repair_fn) — highest priority; injected by caller.
                #   2. Default model-backed repair_fn built from model_routing.
                # The OLD JSON auto_fix_spec['_repair_fn'] path is REMOVED: a callable
                # in a JSON dict was never serializable and always crashed.
                _effective_repair_fn = repair_fn
                if _effective_repair_fn is None:
                    # Build the model-backed default.  repair_model_client is None in
                    # production (resolved lazily at first use); tests inject a fake.
                    _effective_repair_fn = _build_default_repair_fn(
                        repair_model_client, normalized
                    )

                # Thread resolution_spec into the loop context so the in-loop
                # two-list gate is LIVE (not dead).  Without this, the loop's
                # resolution gate block is unreachable even when a spec is present.
                _loop_context: dict[str, Any] = {
                    "job": normalized,
                    "run_dir": str(run_dir),
                    # Seed repair_fn's first call with the already-captured stderr
                    # so the pre-loop failure context is not thrown away.
                    "seed_stderr": first_stderr,
                }
                if normalized.get("resolution_spec"):
                    _loop_context["resolution_spec"] = normalized["resolution_spec"]

                afl_result: AutoFixResult = run_repair_loop(
                    verify_cmd=first_failed_row["command"],
                    workspace=root,
                    repair_fn=_effective_repair_fn,
                    event_log_path=run_dir / "auto_fix_events.jsonl",
                    mission_id=normalized["job_id"],
                    context=_loop_context,
                )
                elog.emit("role_verdict", {
                    "verdict": f"auto_fix_{afl_result.status}",
                    "attempts": afl_result.attempts,
                    "escalated": afl_result.escalated,
                    "last_stderr_snippet": afl_result.last_stderr[:300],
                })
                if afl_result.status == "success":
                    status = "passed"
                    failure_reason = None
                else:
                    # Still failed after the full repair loop.
                    failure_reason = (
                        f"auto_fix loop exhausted ({afl_result.attempts} attempts); "
                        f"last stderr: {afl_result.last_stderr[:300]}"
                    )
        elif spec_raw:
            # verify_commands all passed — now enforce the two-list resolution gate.
            # A clean verify-command exit is NECESSARY but NOT SUFFICIENT.
            # The after-state runner uses cwd=root so tests are collected from
            # the repo, not from the ambient working directory (CRITICAL #2 fix).
            after_runner = make_subprocess_test_runner(root)
            gate = gate_from_job(normalized, run_test=after_runner)
            if gate is not None:
                # Pass the pre-captured (pre-apply) baseline results.
                # This is the fix for CRITICAL #1: the baseline now reflects the
                # PRE-CHANGE state, not the already-fixed state.
                gate_result = gate.evaluate(baseline_runner=_baseline_runner_from_cache)
                elog.emit("gate_evaluated", {
                    "resolved": gate_result.resolved,
                    "failing_fail_to_pass": gate_result.failing_fail_to_pass,
                    "failing_pass_to_pass": gate_result.failing_pass_to_pass,
                    "hollow_tests": gate_result.hollow_tests,
                    "baseline_skipped": gate_result.baseline_skipped,
                })
                if not gate_result.resolved:
                    status = "failed"
                    if gate_result.hollow_tests:
                        failure_reason = (
                            f"resolution gate: hollow-green fail_to_pass tests "
                            f"(were already passing at baseline): "
                            f"{gate_result.hollow_tests}"
                        )
                    elif gate_result.failing_fail_to_pass:
                        failure_reason = (
                            f"resolution gate: fail_to_pass tests still failing: "
                            f"{gate_result.failing_fail_to_pass}"
                        )
                    else:
                        failure_reason = (
                            f"resolution gate: pass_to_pass regressions: "
                            f"{gate_result.failing_pass_to_pass}"
                        )
    except Exception as e:
        status = "failed"
        failure_reason = str(e)
        _write_diff(root, run_dir)

    elog.emit("job_finished", {
        "job_id": normalized["job_id"],
        "status": status,
        "failure_reason": failure_reason,
    })

    _write_commands(run_dir, command_rows)
    result = {
        "schema": WORKER_JOB_RESULT_SCHEMA,
        "job_id": normalized["job_id"],
        "status": status,
        "run_dir": str(run_dir),
        "job_path": str(run_dir / "job.json"),
        "plan_path": str(run_dir / "plan.md"),
        "memory_context_path": str(run_dir / "memory_context.json"),
        "memory_selected_count": memory_context["briefing"]["selected_count"],
        "memory_gaps": memory_context["briefing"]["gaps"],
        "diff_path": str(run_dir / "diff.patch"),
        "commands_path": str(run_dir / "commands.tsv"),
        "failure_path": str(run_dir / "failure.md") if failure_reason else None,
        "commands": command_rows,
        "typed_event_log_path": str(event_log_path),
    }
    if failure_reason:
        (run_dir / "failure.md").write_text(failure_reason.rstrip() + "\n", encoding="utf-8")
        result["failure_reason"] = failure_reason
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def render_worker_job_text(result: dict[str, Any]) -> str:
    lines = [
        f"Hydra worker job: {result['job_id']}",
        f"status: {result['status']}",
        f"run_dir: {result['run_dir']}",
        f"diff: {result['diff_path']}",
        f"commands: {result['commands_path']}",
    ]
    if result.get("failure_reason"):
        lines.append(f"failure: {result['failure_reason']}")
    return "\n".join(lines) + "\n"


def run_worker_batch(
    batch: dict[str, Any],
    *,
    repo_root: Path,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise WorkerJobError(f"repo_root is not a directory: {root}")
    normalized = _normalize_batch(batch)
    run_dir = _run_dir(root, evidence_root, normalized["batch_id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "batch.json").write_text(json.dumps(normalized, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "plan.md").write_text(normalized["goal"].rstrip() + "\n", encoding="utf-8")

    job_results: list[dict[str, Any]] = []
    status = "passed"
    failure_reason: str | None = None
    for job in normalized["jobs"]:
        job_result = run_worker_job(job, repo_root=root, evidence_root=run_dir / "jobs")
        job_results.append(job_result)
        if job_result["status"] != "passed":
            status = "failed"
            failure_reason = f"worker job failed: {job_result['job_id']}"
            break

    result = {
        "schema": WORKER_BATCH_RESULT_SCHEMA,
        "batch_id": normalized["batch_id"],
        "status": status,
        "run_dir": str(run_dir),
        "batch_path": str(run_dir / "batch.json"),
        "plan_path": str(run_dir / "plan.md"),
        "result_path": str(run_dir / "result.json"),
        "failure_path": str(run_dir / "failure.md") if failure_reason else None,
        "jobs": job_results,
    }
    if failure_reason:
        (run_dir / "failure.md").write_text(failure_reason + "\n", encoding="utf-8")
        result["failure_reason"] = failure_reason
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def render_worker_batch_text(result: dict[str, Any]) -> str:
    lines = [
        f"Hydra worker batch: {result['batch_id']}",
        f"status: {result['status']}",
        f"run_dir: {result['run_dir']}",
        f"jobs: {len(result['jobs'])}",
    ]
    if result.get("failure_reason"):
        lines.append(f"failure: {result['failure_reason']}")
    return "\n".join(lines) + "\n"


def _normalize_job(job: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(job, dict):
        raise WorkerJobError("job must be a JSON object")
    job_id = _required_str(job, "job_id")
    goal = _required_str(job, "goal")
    plan = job.get("plan", goal)
    if not isinstance(plan, str) or not plan.strip():
        raise WorkerJobError("plan must be a non-empty string")
    actions = job.get("actions", [])
    if not isinstance(actions, list):
        raise WorkerJobError("actions must be a list")
    verify_commands = job.get("verify_commands", [])
    if not isinstance(verify_commands, list) or not all(isinstance(command, str) and command.strip() for command in verify_commands):
        raise WorkerJobError("verify_commands must be a list of non-empty strings")
    normalized: dict[str, Any] = {
        "schema": job.get("schema", WORKER_JOB_SCHEMA),
        "job_id": job_id,
        "goal": goal,
        "plan": plan.strip(),
        "actions": actions,
        "verify_commands": verify_commands,
    }
    # Optional: two-list resolution gate spec (see hydra/resolution_gate.py).
    # Pass through as-is; gate_from_job validates it on use.
    if "resolution_spec" in job:
        normalized["resolution_spec"] = job["resolution_spec"]
    # Optional: auto_fix spec — enables the bounded stderr-feedback repair loop.
    # {"enabled": True} opts the job into run_repair_loop when verify fails.
    if "auto_fix" in job:
        normalized["auto_fix"] = job["auto_fix"]
    # Optional: network_enabled — allows verify commands to reach the network.
    # Default is False (sandbox network off). An operator-authored job that
    # legitimately needs outbound network (e.g. pip install) sets this True.
    if "network_enabled" in job:
        val = job["network_enabled"]
        if not isinstance(val, bool):
            raise WorkerJobError("network_enabled must be a boolean")
        normalized["network_enabled"] = val
    return normalized


def _normalize_batch(batch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(batch, dict):
        raise WorkerJobError("batch must be a JSON object")
    batch_id = _required_str(batch, "batch_id")
    goal = _required_str(batch, "goal")
    jobs = batch.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise WorkerJobError("jobs must be a non-empty list")
    normalized_jobs = [_normalize_job(job) for job in jobs]
    return {
        "schema": batch.get("schema", WORKER_BATCH_SCHEMA),
        "batch_id": batch_id,
        "goal": goal,
        "jobs": normalized_jobs,
    }


def _required_str(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkerJobError(f"{key} must be a non-empty string")
    return value.strip()


def _run_dir(root: Path, evidence_root: Path | None, job_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in job_id).strip(".-")
    if not safe_id:
        raise WorkerJobError("job_id does not contain a safe path segment")
    base = evidence_root.expanduser().resolve() if evidence_root else root / "evidence" / "worker-jobs"
    return base / safe_id


def _resolve_under_root(root: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise WorkerJobError("action path must be a non-empty string")
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise WorkerJobError(f"action path must stay under repo root: {raw_path}") from e
    return resolved


def _apply_single_action(root: Path, action: Any) -> None:
    """Apply a single action dict.  Extracted so the caller can emit an event per action."""
    if not isinstance(action, dict):
        raise WorkerJobError("each action must be a JSON object")
    kind = action.get("kind")
    if kind == "write_text":
        path = _resolve_under_root(root, action.get("path"))
        text = action.get("text")
        if not isinstance(text, str):
            raise WorkerJobError("write_text action requires string text")
        if path.exists():
            # Existing file: route through apply_patch (syntax gate + atomic write)
            old_block = path.read_text(encoding="utf-8", newline="")
            result = apply_patch(file=path, old_block=old_block, new_block=text, root=root)
            if isinstance(result, PatchFailure):
                raise WorkerJobError(f"write_text rejected for {path.relative_to(root)}: {result.reason}")
        else:
            # New file: route through create_file (syntax gate + atomic create)
            result = create_file(file=path, content=text, root=root)
            if isinstance(result, PatchFailure):
                raise WorkerJobError(f"write_text (create) rejected for {path.name}: {result.reason}")
        return
    if kind == "replace_text":
        path = _resolve_under_root(root, action.get("path"))
        old = action.get("old")
        new = action.get("new")
        if not isinstance(old, str) or not isinstance(new, str):
            raise WorkerJobError("replace_text action requires string old and new")
        # Route through apply_patch: exact-match + syntax gate + atomic write
        result = apply_patch(file=path, old_block=old, new_block=new, root=root)
        if isinstance(result, PatchFailure):
            raise WorkerJobError(f"replace_text rejected for {path.relative_to(root)}: {result.reason}")
        return
    if kind == "apply_patch":
        patch = action.get("patch")
        if not isinstance(patch, str) or not patch.strip():
            raise WorkerJobError("apply_patch action requires non-empty patch text")
        _apply_unified_patch(root, patch)
        return
    raise WorkerJobError(f"unsupported action kind: {kind!r}")


def _apply_actions(root: Path, actions: list[Any]) -> None:
    """Apply all actions. Preserved for back-compat (run_worker_batch uses this path)."""
    for action in actions:
        _apply_single_action(root, action)


def _parse_unified_diff(patch: str) -> list[tuple[str, str, str]]:
    """Parse a unified diff into a list of (filename, old_block, new_block) triples.

    Handles the standard unified diff format produced by git diff / diff -u:
      --- a/filename
      +++ b/filename
      @@ -l,n +l,n @@ (optional context label)
       context line
      -removed line
      +added line

    Returns a list of (filename, old_block, new_block) for each hunk.
    Multiple hunks on the same file are returned as separate triples — the
    caller applies them in order (each hunk is exact-matched against the
    current file state after the previous hunk has been applied).

    Raises WorkerJobError on malformed input.
    """
    lines = patch.splitlines(keepends=True)
    result: list[tuple[str, str, str]] = []
    i = 0
    n = len(lines)

    # Skip leading git metadata lines (index, mode, etc.) before the first ---
    while i < n and not lines[i].startswith("--- "):
        i += 1

    while i < n:
        # --- a/filename
        if not lines[i].startswith("--- "):
            i += 1
            continue
        src_line = lines[i].rstrip()
        i += 1
        if i >= n or not lines[i].startswith("+++ "):
            raise WorkerJobError(f"unified diff malformed: expected '+++ ' after '{src_line}'")
        dst_line = lines[i].rstrip()
        i += 1

        # Extract filename from +++ line (strip b/ prefix used by git diff)
        raw_name = dst_line[4:]  # strip "+++ "
        if raw_name.startswith("b/"):
            raw_name = raw_name[2:]
        filename = raw_name.strip()
        if filename in ("/dev/null", "dev/null"):
            # File deletion — skip (not creating or modifying, out of scope)
            # Advance past hunks
            while i < n and not lines[i].startswith("--- "):
                i += 1
            continue

        # Check for new-file creation: src is /dev/null
        src_name = src_line[4:]
        if src_name.startswith("a/"):
            src_name = src_name[2:]
        is_new_file = src_name.strip() in ("/dev/null", "dev/null")

        # Collect hunks for this file
        while i < n and lines[i].startswith("@@ "):
            hunk_header = lines[i]
            i += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while i < n and not lines[i].startswith(("@@", "--- ", "\\ No newline")):
                line = lines[i]
                if line.startswith("-"):
                    old_lines.append(line[1:])
                elif line.startswith("+"):
                    new_lines.append(line[1:])
                elif line.startswith(" "):
                    # Context line: present in both old and new
                    old_lines.append(line[1:])
                    new_lines.append(line[1:])
                elif line.startswith("\\ "):
                    # "\ No newline at end of file" — skip marker
                    pass
                else:
                    # Unknown line type — treat as context for robustness
                    old_lines.append(line)
                    new_lines.append(line)
                i += 1

            # Skip "\ No newline at end of file" markers
            while i < n and lines[i].startswith("\\ "):
                i += 1

            old_block = "".join(old_lines)
            new_block = "".join(new_lines)
            result.append((filename, old_block, new_block))

    return result


def _apply_unified_patch(root: Path, patch: str) -> None:
    """Apply a unified diff by routing each hunk through hydra.apply_patch.

    This is FAIL-CLOSED and exact-match: if any hunk's old_block does not
    match exactly, the entire operation is refused and the file remains
    byte-identical. No subprocess git-apply is used. The syntax gate is
    applied after ALL hunks for a file have been applied in order.

    For new-file creation (--- /dev/null), routes through create_file.
    """
    try:
        hunks = _parse_unified_diff(patch)
    except WorkerJobError:
        raise
    except Exception as e:
        raise WorkerJobError(f"unified diff parse error: {e}") from e

    if not hunks:
        raise WorkerJobError("apply_patch: no hunks found in unified diff")

    # Apply hunks in sequence. Each hunk is exact-matched against the current
    # on-disk state so that prior hunks in the same file are visible.
    for filename, old_block, new_block in hunks:
        target_path = _resolve_under_root_no_exist_check(root, filename)

        if not target_path.exists():
            # New file creation — route through create_file
            if old_block:
                raise WorkerJobError(
                    f"apply_patch: hunk for new file '{filename}' has non-empty old_block "
                    "(context mismatch — diff was not generated against this repo state)"
                )
            result = create_file(file=target_path, content=new_block, root=root)
            if isinstance(result, PatchFailure):
                raise WorkerJobError(
                    f"apply_patch create failed for '{filename}': {result.reason}"
                )
        else:
            # Existing file — exact-match via apply_patch
            result = apply_patch(file=target_path, old_block=old_block, new_block=new_block, root=root)
            if isinstance(result, PatchFailure):
                raise WorkerJobError(
                    f"apply_patch hunk rejected for '{filename}': {result.reason}"
                )


def _resolve_under_root_no_exist_check(root: Path, raw_path: str) -> Path:
    """Like _resolve_under_root but does NOT require the file to exist."""
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as e:
        raise WorkerJobError(f"action path must stay under repo root: {raw_path}") from e
    return resolved


def _write_diff(root: Path, run_dir: Path) -> None:
    proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--"],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    (run_dir / "diff.patch").write_text(proc.stdout, encoding="utf-8")


def _run_verify_commands(
    root: Path,
    commands: list[str],
    network_enabled: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for command in commands:
        started = time.monotonic()
        result = run_sandboxed_shell(
            command,
            workspace=root,
            network=network_enabled,
            timeout=120,
        )
        rows.append(
            {
                "command": command,
                "exit_code": result.returncode,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "output": result.stdout[-4000:],
            }
        )
    return rows


# Patterns that indicate a network / DNS failure in command stderr output.
_NETWORK_ERROR_PATTERNS: tuple[str, ...] = (
    "Temporary failure in name resolution",
    "Could not resolve host",
    "Network is unreachable",
    "connection timed out",
    "Name or service not known",
    "socket.gaierror",
    "Could not connect",
)


def _write_commands(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = ["command\texit_code\tduration_ms\toutput"]
    for row in rows:
        command = str(row["command"]).replace("\t", "    ").replace("\n", "\\n")
        output = str(row["output"]).replace("\t", "    ").replace("\n", "\\n")
        lines.append(f"{command}\t{row['exit_code']}\t{row['duration_ms']}\t{output}")
    (run_dir / "commands.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")
