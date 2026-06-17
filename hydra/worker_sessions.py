"""Isolated worker execution sessions."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hydra.policy import ApprovalPolicy
from hydra.worker_jobs import WorkerJobError, run_worker_job


WORKER_SESSION_SCHEMA = "hydra.worker_session.v1"


class WorkerSessionError(Exception):
    """Worker session setup or execution failure."""


def run_worker_job_session(
    job: dict[str, Any],
    *,
    repo_root: Path,
    session_id: str,
    evidence_root: Path | None = None,
    policy: ApprovalPolicy | None = None,
) -> dict[str, Any]:
    if policy is not None:
        job_id = job.get("job_id", session_id)
        policy.require("worker_job", {"job_id": job_id})
    root = repo_root.expanduser().resolve()
    if not root.is_dir():
        raise WorkerSessionError(f"repo_root is not a directory: {root}")
    run_dir = _session_dir(root, evidence_root, session_id)
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    session_repo = run_dir / "repo"
    _copy_tracked_repo(root, session_repo)
    worker_result = run_worker_job(job, repo_root=session_repo, evidence_root=run_dir / "worker")
    session_diff = Path(worker_result["diff_path"]).read_text(encoding="utf-8") if Path(worker_result["diff_path"]).is_file() else ""
    session_diff_path = run_dir / "session_diff.patch"
    session_diff_path.write_text(session_diff, encoding="utf-8")
    result = {
        "schema": WORKER_SESSION_SCHEMA,
        "session_id": session_id,
        "status": worker_result["status"],
        "run_dir": str(run_dir),
        "source_repo": str(root),
        "session_repo": str(session_repo),
        "worker_result": worker_result,
        "worker_result_path": str(run_dir / "worker_result.json"),
        "session_diff_path": str(session_diff_path),
    }
    (run_dir / "worker_result.json").write_text(json.dumps(worker_result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def render_worker_session_text(result: dict[str, Any]) -> str:
    return (
        f"Hydra worker session: {result['session_id']}\n"
        f"status: {result['status']}\n"
        f"run_dir: {result['run_dir']}\n"
        f"session_repo: {result['session_repo']}\n"
        f"diff: {result['session_diff_path']}\n"
    )


def _copy_tracked_repo(root: Path, session_repo: Path) -> None:
    files = _tracked_files(root)
    session_repo.mkdir(parents=True, exist_ok=True)
    for rel in files:
        src = root / rel
        dst = session_repo / rel
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    _init_session_git(session_repo)


def _tracked_files(root: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise WorkerSessionError(f"git ls-files failed: {proc.stdout.decode(errors='replace').strip()}")
    return [Path(raw.decode()) for raw in proc.stdout.split(b"\0") if raw]


def _init_session_git(session_repo: Path) -> None:
    commands = [
        ["git", "init"],
        ["git", "config", "user.email", "hydra-worker-session@example.test"],
        ["git", "config", "user.name", "Hydra Worker Session"],
        ["git", "add", "."],
        ["git", "commit", "-m", "session baseline"],
    ]
    for command in commands:
        proc = subprocess.run(
            command,
            cwd=session_repo,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise WorkerSessionError(f"{' '.join(command)} failed: {proc.stderr.strip()}")


def _session_dir(root: Path, evidence_root: Path | None, session_id: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in session_id).strip(".-")
    if not safe_id:
        raise WorkerSessionError("session_id does not contain a safe path segment")
    base = evidence_root.expanduser().resolve() if evidence_root else root / "evidence" / "worker-sessions"
    return base / safe_id


SESSION_EXCEPTIONS = (WorkerSessionError, WorkerJobError)
