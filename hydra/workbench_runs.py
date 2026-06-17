"""Durable workbench run records for HydraAgent."""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hydra.file_lock import locked_path


RUN_SCHEMA = "hydra.workbench_run.v1"
DEFAULT_RUNS_PATH = Path("evidence/workbench-runs/runs.jsonl")
STATUSES = ("queued", "running", "waiting_approval", "completed", "failed", "cancelled")


class RunError(Exception):
    """Operator-facing run record failure."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class RunRecord:
    run_id: str
    title: str
    lane: str
    status: str
    goal: str
    evidence_paths: list[str] = field(default_factory=list)
    approval_request_ids: list[str] = field(default_factory=list)
    command: str | None = None
    failure_reason: str | None = None
    schema: str = RUN_SCHEMA
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.schema != RUN_SCHEMA:
            raise RunError(f"unsupported run schema: {self.schema!r}")
        for attr in ("run_id", "title", "lane", "status", "goal", "created_at", "updated_at"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise RunError(f"{attr} must be a non-empty string")
        if self.status not in STATUSES:
            raise RunError(f"invalid run status {self.status!r}")
        for attr in ("evidence_paths", "approval_request_ids"):
            value = getattr(self, attr)
            if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
                raise RunError(f"{attr} must be a list of non-empty strings")
        if self.command is not None and (not isinstance(self.command, str) or not self.command.strip()):
            raise RunError("command must be a non-empty string or None")
        if self.failure_reason is not None and (not isinstance(self.failure_reason, str) or not self.failure_reason.strip()):
            raise RunError("failure_reason must be a non-empty string or None")
        if self.status == "failed" and not self.failure_reason:
            raise RunError("failed runs require failure_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "title": self.title,
            "lane": self.lane,
            "status": self.status,
            "goal": self.goal,
            "evidence_paths": list(self.evidence_paths),
            "approval_request_ids": list(self.approval_request_ids),
            "command": self.command,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def create_run(
    *,
    run_id: str,
    title: str,
    lane: str,
    status: str,
    goal: str,
    evidence_paths: list[str] | None = None,
    approval_request_ids: list[str] | None = None,
    command: str | None = None,
    failure_reason: str | None = None,
) -> RunRecord:
    record = RunRecord(
        run_id=run_id,
        title=title,
        lane=lane,
        status=status,
        goal=goal,
        evidence_paths=evidence_paths or [],
        approval_request_ids=approval_request_ids or [],
        command=command,
        failure_reason=failure_reason,
    )
    record.validate()
    return record


def record_from_dict(row: dict[str, Any]) -> RunRecord:
    if not isinstance(row, dict):
        raise RunError(f"run row must be an object, got {type(row).__name__}")
    record = RunRecord(
        schema=row.get("schema", ""),
        run_id=row.get("run_id", ""),
        title=row.get("title", ""),
        lane=row.get("lane", ""),
        status=row.get("status", ""),
        goal=row.get("goal", ""),
        evidence_paths=row.get("evidence_paths", []),
        approval_request_ids=row.get("approval_request_ids", []),
        command=_optional_str(row, "command"),
        failure_reason=_optional_str(row, "failure_reason"),
        created_at=_timestamp_str(row, "created_at"),
        updated_at=_timestamp_str(row, "updated_at"),
    )
    record.validate()
    return record


def load_records(path: Path) -> list[RunRecord]:
    if not path.exists():
        return []
    if not path.is_file():
        raise RunError(f"run records path is not a file: {path}")
    records: list[RunRecord] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise RunError(f"{path}:{line_no}: invalid JSON: {e}") from e
        try:
            records.append(record_from_dict(raw))
        except RunError as e:
            raise RunError(f"{path}:{line_no}: {e}") from e
    _check_duplicates(records)
    return records


def save_records(path: Path, records: Iterable[RunRecord]) -> None:
    rows = list(records)
    for record in rows:
        record.validate()
    _check_duplicates(rows)
    text = "\n".join(json.dumps(record.to_dict(), sort_keys=True) for record in rows)
    _write_text_atomic(path, text + ("\n" if text else ""))


def append_record(path: Path, record: RunRecord) -> None:
    # Cross-process lock across read-modify-write so a concurrent writer (chat
    # process vs telegram listener) cannot clobber this update.
    with locked_path(path):
        records = load_records(path)
        if any(row.run_id == record.run_id for row in records):
            raise RunError(f"duplicate run_id {record.run_id!r}")
        records.append(record)
        save_records(path, records)


def update_run_after_approval(path: Path, approval: Any) -> RunRecord:
    approval_status = getattr(approval, "status", None)
    run_id = getattr(approval, "run_id", None)
    request_id = getattr(approval, "request_id", None)
    if approval_status not in {"approved", "denied"}:
        raise RunError(f"approval must be decided before run update: {approval_status!r}")
    if not isinstance(run_id, str) or not run_id.strip():
        raise RunError("approval run_id must be a non-empty string")
    if not isinstance(request_id, str) or not request_id.strip():
        raise RunError("approval request_id must be a non-empty string")
    next_status = "queued" if approval_status == "approved" else "cancelled"
    with locked_path(path):
        return _update_run_after_approval_locked(path, run_id, request_id, next_status)


def _update_run_after_approval_locked(
    path: Path, run_id: str, request_id: str, next_status: str
) -> RunRecord:
    records = load_records(path)
    out: list[RunRecord] = []
    updated: RunRecord | None = None
    for record in records:
        if record.run_id == run_id and request_id in record.approval_request_ids:
            updated = RunRecord(
                schema=record.schema,
                run_id=record.run_id,
                title=record.title,
                lane=record.lane,
                status=next_status,
                goal=record.goal,
                evidence_paths=list(record.evidence_paths),
                approval_request_ids=list(record.approval_request_ids),
                command=record.command,
                failure_reason=record.failure_reason,
                created_at=record.created_at,
                updated_at=utc_now(),
            )
            out.append(updated)
        else:
            out.append(record)
    if updated is None:
        raise RunError(f"linked run not found for approval request: {request_id!r}")
    save_records(path, out)
    return updated


def _optional_str(row: dict[str, Any], attr: str) -> str | None:
    value = row.get(attr)
    if value is not None and not isinstance(value, str):
        raise RunError(f"{attr} must be a string or null")
    return value


def _timestamp_str(row: dict[str, Any], attr: str) -> str:
    value = row.get(attr, "")
    if value == "":
        return utc_now()
    if not isinstance(value, str):
        raise RunError(f"{attr} must be a string")
    return value


def _check_duplicates(records: Iterable[RunRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.run_id in seen:
            raise RunError(f"duplicate run_id {record.run_id!r}")
        seen.add(record.run_id)


def _write_text_atomic(path: Path, text: str) -> None:
    if path.exists() and not path.is_file():
        raise RunError(f"run records path is not a file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass
