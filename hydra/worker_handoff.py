"""Planner-to-worker handoff runtime for bounded Hydra code work."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.llm import ChatMessage, LlmError
from hydra.planner_quality import evaluate_planner_packet_quality
from hydra.policy import ApprovalPolicy
from hydra.providers import ProviderError, make_client
from hydra.worker_jobs import (
    WORKER_BATCH_SCHEMA,
    WORKER_JOB_SCHEMA,
    WorkerJobError,
    run_worker_batch,
    run_worker_job,
)
from hydra.worker_review import WorkerReviewError, review_worker_run


HANDOFF_SCHEMA = "hydra.planner_worker_handoff.v1"


class WorkerHandoffError(Exception):
    """Planner handoff packet or execution failure."""


def run_planner_worker_handoff(
    *,
    prompt: str,
    planner_packet: dict[str, Any],
    repo_root: Path,
    handoff_id: str,
    evidence_root: Path | None = None,
    policy: ApprovalPolicy | None = None,
) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise WorkerHandoffError("prompt must be a non-empty string")
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise WorkerHandoffError(f"repo_root is not a directory: {root}")
    run_dir = _handoff_dir(root, evidence_root, handoff_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.md").write_text(prompt.strip() + "\n", encoding="utf-8")
    (run_dir / "planner_packet.json").write_text(json.dumps(planner_packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    planner_quality = evaluate_planner_packet_quality(prompt, planner_packet)
    (run_dir / "planner_quality.json").write_text(json.dumps(planner_quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if planner_quality["verdict"] != "accepted":
        result = {
            "schema": HANDOFF_SCHEMA,
            "handoff_id": handoff_id,
            "packet_kind": "rejected",
            "status": "failed",
            "run_dir": str(run_dir),
            "prompt_path": str(run_dir / "prompt.md"),
            "planner_packet_path": str(run_dir / "planner_packet.json"),
            "planner_quality_path": str(run_dir / "planner_quality.json"),
            "planner_quality": planner_quality,
            "worker_result": None,
            "review": {"verdict": "rejected", "reason": "planner quality gate rejected packet"},
        }
        (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return result

    if policy is not None:
        job_id = planner_packet.get("job_id", handoff_id)
        policy.require("worker_job", {"job_id": job_id})

    schema = planner_packet.get("schema")
    if schema == WORKER_JOB_SCHEMA:
        packet_kind = "job"
        worker_result = run_worker_job(planner_packet, repo_root=root, evidence_root=run_dir / "worker")
    elif schema == WORKER_BATCH_SCHEMA:
        packet_kind = "batch"
        worker_result = run_worker_batch(planner_packet, repo_root=root, evidence_root=run_dir / "worker")
    else:
        raise WorkerHandoffError(f"planner packet schema must be {WORKER_JOB_SCHEMA} or {WORKER_BATCH_SCHEMA}")

    review = review_worker_run(Path(worker_result["run_dir"]))
    status = "passed" if worker_result["status"] == "passed" and review["verdict"] == "accepted" else "failed"
    result = {
        "schema": HANDOFF_SCHEMA,
        "handoff_id": handoff_id,
        "packet_kind": packet_kind,
        "status": status,
        "run_dir": str(run_dir),
        "prompt_path": str(run_dir / "prompt.md"),
        "planner_packet_path": str(run_dir / "planner_packet.json"),
        "planner_quality_path": str(run_dir / "planner_quality.json"),
        "planner_quality": planner_quality,
        "worker_result": worker_result,
        "review": review,
    }
    (run_dir / "worker_result.json").write_text(json.dumps(worker_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "review.json").write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def planner_packet_from_model(
    *,
    prompt: str,
    provider: str,
    model: str | None = None,
    env_dir: Path | None = None,
) -> dict[str, Any]:
    client, cfg = make_client(provider, env_dir=env_dir)
    messages = [
        ChatMessage(
            role="system",
            content=(
                "Return only JSON for either hydra.worker_job.v1 or "
                "hydra.worker_batch.v1. Include actions and verify_commands."
            ),
        ),
        ChatMessage(role="user", content=prompt),
    ]
    try:
        response = client.chat(
            messages,
            model=model or cfg.model,
            max_tokens=4096,
            temperature=0.1,
        )
    except LlmError as exc:
        # S6 — life-support fallback. A provider failure here must not crash the
        # planner step: switch to the local never-expires model and retry once.
        # The substitution is surfaced via the raised classification, never
        # silently swallowed.
        from hydra.emergency_fallback import (
            classify_provider_error,
            _default_local_client_factory,
        )

        error_class = classify_provider_error(exc)
        local_client, local_model = _default_local_client_factory()
        try:
            response = local_client.chat(
                messages, model=local_model, max_tokens=4096, temperature=0.1
            )
        except LlmError as local_exc:
            raise WorkerHandoffError(
                f"planner provider {provider!r} failed ({error_class}) and local "
                f"life-support model {local_model!r} also failed: {local_exc}"
            ) from local_exc
    return parse_planner_packet_text(response.content)


def parse_planner_packet_text(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        packet = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise WorkerHandoffError(f"planner did not return JSON: {e}") from e
    if not isinstance(packet, dict):
        raise WorkerHandoffError("planner packet must be a JSON object")
    return packet


def _handoff_dir(root: Path, evidence_root: Path | None, handoff_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in handoff_id).strip(".-")
    if not safe_id:
        raise WorkerHandoffError("handoff_id does not contain a safe path segment")
    base = evidence_root.expanduser().resolve() if evidence_root else root / "evidence" / "worker-handoffs"
    return base / safe_id


HANDOFF_EXCEPTIONS = (WorkerHandoffError, WorkerJobError, WorkerReviewError, ProviderError, LlmError)
