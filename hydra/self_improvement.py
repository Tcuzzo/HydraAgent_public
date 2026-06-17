"""Failure-to-lesson promotion for Hydra worker evidence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


FAILURE_LESSONS_SCHEMA = "hydra.self_improvement.failure_lessons.v1"


class SelfImprovementError(Exception):
    """Self-improvement promotion failure."""


def promote_failure_lessons(*, repo_root: Path, evidence_root: Path | None = None) -> dict[str, Any]:
    repo = repo_root.expanduser().resolve()
    if not repo.is_dir():
        raise SelfImprovementError(f"repo_root is not a directory: {repo}")
    evidence = evidence_root.expanduser().resolve() if evidence_root else repo / "evidence"
    lessons = []
    orchestration_root = evidence / "worker-orchestrations"
    if orchestration_root.is_dir():
        for result_path in sorted(orchestration_root.glob("*/result.json")):
            result = _read_json(result_path)
            if result.get("verdict") != "rejected":
                continue
            lesson_path = _lesson_path(repo, result)
            if not lesson_path.is_file():
                lesson_path.parent.mkdir(parents=True, exist_ok=True)
                lesson_path.write_text(_render_lesson(result_path, result), encoding="utf-8")
            lessons.append(
                {
                    "orchestration_id": result.get("orchestration_id", result_path.parent.name),
                    "path": str(lesson_path),
                    "source": str(result_path),
                }
            )
    return {
        "schema": FAILURE_LESSONS_SCHEMA,
        "repo_root": str(repo),
        "evidence_root": str(evidence),
        "promoted_count": len(lessons),
        "lessons": lessons,
    }


def render_failure_lessons_text(result: dict[str, Any]) -> str:
    return (
        "Hydra self-improvement failure lessons\n"
        f"promoted: {result['promoted_count']}\n"
        f"evidence_root: {result['evidence_root']}\n"
    )


def _render_lesson(result_path: Path, result: dict[str, Any]) -> str:
    orchestration_id = result.get("orchestration_id", result_path.parent.name)
    failure_reason = result.get("failure_reason") or "rejected worker orchestration"
    failed_jobs = [job.get("job_id", "") for job in result.get("jobs", []) if isinstance(job, dict)]
    return "\n".join(
        [
            "---",
            "schema: hydra.self_improvement.lesson.v1",
            f"orchestration_id: {orchestration_id}",
            f"source: {result_path}",
            "---",
            f"# Worker Failure Lesson: {orchestration_id}",
            "",
            f"- failure_reason: {failure_reason}",
            f"- failed_jobs: {', '.join(job for job in failed_jobs if job)}",
            f"- source: `{result_path}`",
            "",
            "Lesson: rejected worker evidence must either trigger a proven recovery job or stop with a rejected verdict.",
            "",
        ]
    )


def _lesson_path(repo: Path, result: dict[str, Any]) -> Path:
    orchestration_id = str(result.get("orchestration_id") or "worker-failure")
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in orchestration_id).strip(".-")
    return repo / ".hydraAgent" / "wiki" / "lessons" / f"{safe}-worker-failure.md"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
