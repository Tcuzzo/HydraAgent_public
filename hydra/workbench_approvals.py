"""Durable local approval queue for Hydra workbench runs."""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hydra.file_lock import locked_path


APPROVAL_SCHEMA = "hydra.workbench_approval.v1"
DEFAULT_APPROVAL_QUEUE_PATH = Path("evidence/workbench-approvals/queue.jsonl")
STATUSES = ("pending", "approved", "denied", "expired")
DECISIONS = ("approved", "denied")


class ApprovalError(Exception):
    """Operator-facing approval queue failure."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class ApprovalRecord:
    request_id: str
    run_id: str
    tool_name: str
    risk_tier: str
    summary: str
    arguments_preview: dict[str, Any]
    status: str = "pending"
    reason: str | None = None
    decided_at: str | None = None
    schema: str = APPROVAL_SCHEMA
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.schema != APPROVAL_SCHEMA:
            raise ApprovalError(f"unsupported approval schema: {self.schema!r}")
        for attr in ("request_id", "run_id", "tool_name", "risk_tier", "summary", "status"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise ApprovalError(f"{attr} must be a non-empty string")
        for attr in ("created_at", "updated_at"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise ApprovalError(f"{attr} must be a non-empty string")
        if self.status not in STATUSES:
            raise ApprovalError(f"invalid approval status {self.status!r}")
        if not isinstance(self.arguments_preview, dict):
            raise ApprovalError("arguments_preview must be an object")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ApprovalError("reason must be a non-empty string or None")
        if self.decided_at is not None and (not isinstance(self.decided_at, str) or not self.decided_at.strip()):
            raise ApprovalError("decided_at must be a non-empty string or None")
        if self.status in DECISIONS and not self.decided_at:
            raise ApprovalError(f"{self.status} approvals require decided_at")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "tool_name": self.tool_name,
            "risk_tier": self.risk_tier,
            "summary": self.summary,
            "arguments_preview": dict(self.arguments_preview),
            "status": self.status,
            "reason": self.reason,
            "decided_at": self.decided_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def create_request(
    *,
    request_id: str,
    run_id: str,
    tool_name: str,
    risk_tier: str,
    summary: str,
    arguments_preview: dict[str, Any],
) -> ApprovalRecord:
    record = ApprovalRecord(
        request_id=request_id,
        run_id=run_id,
        tool_name=tool_name,
        risk_tier=risk_tier,
        summary=summary,
        arguments_preview=arguments_preview,
    )
    record.validate()
    return record


def record_from_dict(row: dict[str, Any]) -> ApprovalRecord:
    if not isinstance(row, dict):
        raise ApprovalError(f"approval row must be an object, got {type(row).__name__}")
    record = ApprovalRecord(
        schema=row.get("schema", ""),
        request_id=row.get("request_id", ""),
        run_id=row.get("run_id", ""),
        tool_name=row.get("tool_name", ""),
        risk_tier=row.get("risk_tier", ""),
        summary=row.get("summary", ""),
        arguments_preview=row.get("arguments_preview", {}),
        status=row.get("status", ""),
        reason=_optional_str(row, "reason"),
        decided_at=_optional_str(row, "decided_at"),
        created_at=_timestamp_str(row, "created_at"),
        updated_at=_timestamp_str(row, "updated_at"),
    )
    record.validate()
    return record


def load_records(path: Path) -> list[ApprovalRecord]:
    if not path.exists():
        return []
    if not path.is_file():
        raise ApprovalError(f"approval queue path is not a file: {path}")
    records: list[ApprovalRecord] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise ApprovalError(f"{path}:{line_no}: invalid JSON: {e}") from e
        try:
            records.append(record_from_dict(raw))
        except ApprovalError as e:
            raise ApprovalError(f"{path}:{line_no}: {e}") from e
    _check_duplicates(records)
    return records


def save_records(path: Path, records: Iterable[ApprovalRecord]) -> None:
    rows = list(records)
    for record in rows:
        record.validate()
    _check_duplicates(rows)
    text = "\n".join(json.dumps(record.to_dict(), sort_keys=True) for record in rows)
    _write_text_atomic(path, text + ("\n" if text else ""))


def append_record(path: Path, record: ApprovalRecord) -> None:
    # Cross-process lock across read-modify-write so a concurrent writer (chat
    # process vs telegram listener) cannot clobber this update.
    with locked_path(path):
        records = load_records(path)
        if any(row.request_id == record.request_id for row in records):
            raise ApprovalError(f"duplicate request_id {record.request_id!r}")
        records.append(record)
        save_records(path, records)


def pending_records(records: Iterable[ApprovalRecord]) -> list[ApprovalRecord]:
    return [record for record in records if record.status == "pending"]


def decide_record(record: ApprovalRecord, decision: str, *, reason: str | None = None) -> ApprovalRecord:
    record.validate()
    if record.status != "pending":
        raise ApprovalError(f"approval request is already {record.status}: {record.request_id}")
    if decision not in DECISIONS:
        raise ApprovalError(f"invalid approval decision {decision!r}")
    now = utc_now()
    next_record = ApprovalRecord(
        schema=record.schema,
        request_id=record.request_id,
        run_id=record.run_id,
        tool_name=record.tool_name,
        risk_tier=record.risk_tier,
        summary=record.summary,
        arguments_preview=dict(record.arguments_preview),
        status=decision,
        reason=reason,
        decided_at=now,
        created_at=record.created_at,
        updated_at=now,
    )
    next_record.validate()
    return next_record


def decide_request(path: Path, request_id: str, decision: str, *, reason: str | None = None) -> ApprovalRecord:
    # Cross-process lock across read-modify-write so a concurrent writer cannot
    # clobber this decision (the operator's "race-time" symptom).
    with locked_path(path):
        return _decide_request_locked(path, request_id, decision, reason=reason)


def _decide_request_locked(
    path: Path, request_id: str, decision: str, *, reason: str | None = None
) -> ApprovalRecord:
    records = load_records(path)
    out: list[ApprovalRecord] = []
    decided: ApprovalRecord | None = None
    for record in records:
        if record.request_id == request_id:
            decided = decide_record(record, decision, reason=reason)
            out.append(decided)
        else:
            out.append(record)
    if decided is None:
        raise ApprovalError(f"approval request not found: {request_id!r}")
    save_records(path, out)
    return decided


def _optional_str(row: dict[str, Any], attr: str) -> str | None:
    value = row.get(attr)
    if value is not None and not isinstance(value, str):
        raise ApprovalError(f"{attr} must be a string or null")
    return value


def _timestamp_str(row: dict[str, Any], attr: str) -> str:
    value = row.get(attr, "")
    if value == "":
        return utc_now()
    if not isinstance(value, str):
        raise ApprovalError(f"{attr} must be a string")
    return value


def _check_duplicates(records: Iterable[ApprovalRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.request_id in seen:
            raise ApprovalError(f"duplicate request_id {record.request_id!r}")
        seen.add(record.request_id)


def _write_text_atomic(path: Path, text: str) -> None:
    if path.exists() and not path.is_file():
        raise ApprovalError(f"approval queue path is not a file: {path}")
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
