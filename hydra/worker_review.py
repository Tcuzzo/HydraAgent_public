"""Machine review gate for Hydra worker job evidence."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from hydra.worker_jobs import WORKER_BATCH_RESULT_SCHEMA, WORKER_JOB_RESULT_SCHEMA


WORKER_REVIEW_SCHEMA = "hydra.worker_review.v1"


class WorkerReviewError(Exception):
    """Worker evidence review failure."""


def review_worker_run(run_dir: Path) -> dict[str, Any]:
    root = run_dir.expanduser().resolve()
    if not root.is_dir():
        raise WorkerReviewError(f"run_dir is not a directory: {root}")
    result_path = root / "result.json"
    if not result_path.is_file():
        raise WorkerReviewError(f"missing worker result: {result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    schema = result.get("schema")
    if schema == WORKER_BATCH_RESULT_SCHEMA:
        review = _review_batch(root, result)
    elif schema == WORKER_JOB_RESULT_SCHEMA:
        review = _review_job(root, result)
    else:
        raise WorkerReviewError(f"unsupported worker result schema: {schema!r}")
    (root / "review.json").write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return review


def render_worker_review_text(review: dict[str, Any]) -> str:
    lines = [
        f"Hydra worker review: {review['verdict']}",
        f"subject: {review['subject']}",
        f"run_dir: {review['run_dir']}",
    ]
    failed = [check for check in review["checks"] if not check["passed"]]
    if failed:
        lines.append("failed_checks: " + ", ".join(check["name"] for check in failed))
    return "\n".join(lines) + "\n"


def _review_job(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    diff_path = root / "diff.patch"
    commands_path = root / "commands.tsv"
    diff_text = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""
    commands = _read_commands(commands_path)
    failed_commands = [row for row in commands if row["exit_code"] != 0]
    checks = [
        {
            "name": "result_status_passed",
            "passed": result.get("status") == "passed",
            "detail": str(result.get("status")),
        },
        {
            "name": "commands_green",
            "passed": not failed_commands,
            "detail": f"{len(failed_commands)} failed of {len(commands)}",
        },
        {
            "name": "diff_nonempty",
            "passed": bool(diff_text.strip()),
            "detail": str(diff_path),
        },
    ]
    if result.get("status") != "passed" or failed_commands:
        verdict = "rejected"
    elif not diff_text.strip():
        verdict = "needs-human"
    else:
        verdict = "accepted"
    return {
        "schema": WORKER_REVIEW_SCHEMA,
        "subject": "job",
        "run_dir": str(root),
        "verdict": verdict,
        "result_path": str(root / "result.json"),
        "diff_path": str(diff_path),
        "commands_path": str(commands_path),
        "checks": checks,
    }


def _review_batch(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    child_reviews = []
    for job in result.get("jobs", []):
        run_dir = job.get("run_dir")
        if not isinstance(run_dir, str) or not run_dir:
            raise WorkerReviewError("batch job missing run_dir")
        child_reviews.append(review_worker_run(Path(run_dir)))
    rejected = [review for review in child_reviews if review["verdict"] == "rejected"]
    needs_human = [review for review in child_reviews if review["verdict"] == "needs-human"]
    checks = [
        {
            "name": "result_status_passed",
            "passed": result.get("status") == "passed",
            "detail": str(result.get("status")),
        },
        {
            "name": "child_reviews_accepted",
            "passed": not rejected and not needs_human,
            "detail": f"{len(rejected)} rejected, {len(needs_human)} needs-human of {len(child_reviews)}",
        },
    ]
    if result.get("status") != "passed" or rejected:
        verdict = "rejected"
    elif needs_human:
        verdict = "needs-human"
    else:
        verdict = "accepted"
    return {
        "schema": WORKER_REVIEW_SCHEMA,
        "subject": "batch",
        "run_dir": str(root),
        "verdict": verdict,
        "result_path": str(root / "result.json"),
        "checks": checks,
        "child_reviews": child_reviews,
    }


def _read_commands(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            try:
                exit_code = int(row.get("exit_code", "1"))
            except ValueError:
                exit_code = 1
            rows.append(
                {
                    "command": row.get("command", ""),
                    "exit_code": exit_code,
                    "duration_ms": row.get("duration_ms", ""),
                    "output": row.get("output", ""),
                }
            )
    return rows
