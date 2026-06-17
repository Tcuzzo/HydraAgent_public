"""Machine-readable build ledger for HydraAgent workbench slices."""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from hydra.file_lock import locked_path


LEDGER_SCHEMA = "hydra.work_ledger.v1"
DEFAULT_LEDGER_PATH = Path("evidence/build-ledger/ledger.jsonl")

STATUSES = (
    "planned",
    "running",
    "blocked",
    "failed",
    "needs_verification",
    "proven",
    "regressed",
    "superseded",
)

OWNER_LANES = ("codex", "local_worker", "cloud_model", "human")

ALLOWED_TRANSITIONS = {
    "planned": {"running", "blocked", "superseded"},
    "running": {"needs_verification", "blocked", "failed", "regressed"},
    "blocked": {"planned", "running", "superseded"},
    "failed": {"planned", "running", "superseded"},
    "needs_verification": {"proven", "failed", "running", "regressed"},
    "proven": {"regressed", "superseded"},
    "regressed": {"planned", "running", "superseded"},
    "superseded": set(),
}


class LedgerError(Exception):
    """Operator-facing ledger failure."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass
class LedgerRecord:
    slice_id: str
    title: str
    status: str
    owner_lane: str
    goal: str
    scope: list[str]
    non_goals: list[str]
    evidence_paths: list[str] = field(default_factory=list)
    regression_checks: list[str] = field(default_factory=list)
    verifier_packet: str | None = None
    promoted_at: str | None = None
    failure_reason: str | None = None
    # S6 — path (relative to repo root) of the life-support pause checkpoint
    # written when a provider failed mid-mission and the agent switched to the
    # local never-expires model. Set on the running->blocked transition.
    s6_fallback_checkpoint: str | None = None
    schema: str = LEDGER_SCHEMA
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.schema != LEDGER_SCHEMA:
            raise LedgerError(f"unsupported ledger schema: {self.schema!r}")
        for attr in ("slice_id", "title", "status", "owner_lane", "goal"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise LedgerError(f"{attr} must be a non-empty string")
        for attr in ("created_at", "updated_at"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise LedgerError(f"{attr} must be a non-empty string")
        for attr in ("verifier_packet", "promoted_at", "failure_reason", "s6_fallback_checkpoint"):
            value = getattr(self, attr)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise LedgerError(f"{attr} must be a non-empty string or None")
        if self.status not in STATUSES:
            raise LedgerError(f"invalid status {self.status!r}")
        if self.owner_lane not in OWNER_LANES:
            raise LedgerError(f"invalid owner_lane {self.owner_lane!r}")
        for attr in ("scope", "non_goals", "evidence_paths", "regression_checks"):
            value = getattr(self, attr)
            if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
                raise LedgerError(f"{attr} must be a list of non-empty strings")
        if not self.scope:
            raise LedgerError("scope must contain at least one item")
        if not self.non_goals:
            raise LedgerError("non_goals must contain at least one item")
        if self.status == "proven":
            if not self.evidence_paths:
                raise LedgerError("proven records require at least one evidence path")
            if not self.regression_checks:
                raise LedgerError("proven records require at least one regression check")
            if not self.verifier_packet:
                raise LedgerError("proven records require verifier_packet")
            if not self.promoted_at:
                raise LedgerError("proven records require promoted_at")
        if self.status in {"failed", "blocked", "regressed"} and not self.failure_reason:
            raise LedgerError(f"{self.status} records require failure_reason")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "slice_id": self.slice_id,
            "title": self.title,
            "status": self.status,
            "owner_lane": self.owner_lane,
            "goal": self.goal,
            "scope": list(self.scope),
            "non_goals": list(self.non_goals),
            "evidence_paths": list(self.evidence_paths),
            "regression_checks": list(self.regression_checks),
            "verifier_packet": self.verifier_packet,
            "promoted_at": self.promoted_at,
            "failure_reason": self.failure_reason,
            "s6_fallback_checkpoint": self.s6_fallback_checkpoint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _optional_str(row: dict[str, Any], attr: str) -> str | None:
    value = row.get(attr)
    if value is not None and not isinstance(value, str):
        raise LedgerError(f"{attr} must be a string or null")
    return value


def _timestamp_str(row: dict[str, Any], attr: str) -> str:
    value = row.get(attr, "")
    if value == "":
        return utc_now()
    if not isinstance(value, str):
        raise LedgerError(f"{attr} must be a string")
    return value


def _string_list(value: list[str], attr: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        raise LedgerError(f"{attr} must be a list of non-empty strings")
    return list(value)


def record_from_dict(row: dict[str, Any]) -> LedgerRecord:
    if not isinstance(row, dict):
        raise LedgerError(f"ledger row must be an object, got {type(row).__name__}")
    record = LedgerRecord(
        schema=row.get("schema", ""),
        slice_id=row.get("slice_id", ""),
        title=row.get("title", ""),
        status=row.get("status", ""),
        owner_lane=row.get("owner_lane", ""),
        goal=row.get("goal", ""),
        scope=row.get("scope", []),
        non_goals=row.get("non_goals", []),
        evidence_paths=row.get("evidence_paths", []),
        regression_checks=row.get("regression_checks", []),
        verifier_packet=_optional_str(row, "verifier_packet"),
        promoted_at=_optional_str(row, "promoted_at"),
        failure_reason=_optional_str(row, "failure_reason"),
        s6_fallback_checkpoint=_optional_str(row, "s6_fallback_checkpoint"),
        created_at=_timestamp_str(row, "created_at"),
        updated_at=_timestamp_str(row, "updated_at"),
    )
    record.validate()
    return record


def load_records(path: Path) -> list[LedgerRecord]:
    if not path.exists():
        return []
    if not path.is_file():
        raise LedgerError(f"ledger path is not a file: {path}")
    records: list[LedgerRecord] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise LedgerError(f"{path}:{line_no}: invalid JSON: {e}") from e
        try:
            records.append(record_from_dict(raw))
        except LedgerError as e:
            raise LedgerError(f"{path}:{line_no}: {e}") from e
    seen: set[str] = set()
    for record in records:
        if record.slice_id in seen:
            raise LedgerError(f"duplicate slice_id {record.slice_id!r}")
        seen.add(record.slice_id)
    return records


def _write_text_atomic(path: Path, text: str) -> None:
    if path.exists() and not path.is_file():
        raise LedgerError(f"ledger path is not a file: {path}")
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


def save_records(path: Path, records: Iterable[LedgerRecord]) -> None:
    rows = list(records)
    seen: set[str] = set()
    for record in rows:
        record.validate()
        if record.slice_id in seen:
            raise LedgerError(f"duplicate slice_id {record.slice_id!r}")
        seen.add(record.slice_id)
    text = "\n".join(json.dumps(r.to_dict(), sort_keys=True) for r in rows)
    _write_text_atomic(path, text + ("\n" if text else ""))


def create_record(
    *,
    slice_id: str,
    title: str,
    owner_lane: str,
    goal: str,
    scope: list[str],
    non_goals: list[str],
) -> LedgerRecord:
    record = LedgerRecord(
        slice_id=slice_id,
        title=title,
        status="planned",
        owner_lane=owner_lane,
        goal=goal,
        scope=scope,
        non_goals=non_goals,
    )
    record.validate()
    return record


def transition_record(
    record: LedgerRecord,
    *,
    status: str,
    evidence_paths: list[str] | None = None,
    regression_checks: list[str] | None = None,
    verifier_packet: str | None = None,
    failure_reason: str | None = None,
    s6_fallback_checkpoint: str | None = None,
) -> LedgerRecord:
    record.validate()
    if status not in STATUSES:
        raise LedgerError(f"invalid status {status!r}")
    if status not in ALLOWED_TRANSITIONS[record.status]:
        raise LedgerError(f"cannot transition {record.status!r} -> {status!r}")
    next_evidence_paths = (
        _string_list(evidence_paths, "evidence_paths") if evidence_paths is not None else list(record.evidence_paths)
    )
    next_regression_checks = (
        _string_list(regression_checks, "regression_checks")
        if regression_checks is not None
        else list(record.regression_checks)
    )
    next_record = LedgerRecord(
        schema=record.schema,
        slice_id=record.slice_id,
        title=record.title,
        status=status,
        owner_lane=record.owner_lane,
        goal=record.goal,
        scope=list(record.scope),
        non_goals=list(record.non_goals),
        evidence_paths=next_evidence_paths,
        regression_checks=next_regression_checks,
        verifier_packet=verifier_packet if verifier_packet is not None else record.verifier_packet,
        promoted_at=utc_now() if status == "proven" else record.promoted_at,
        failure_reason=failure_reason,
        s6_fallback_checkpoint=(
            s6_fallback_checkpoint if s6_fallback_checkpoint is not None else record.s6_fallback_checkpoint
        ),
        created_at=record.created_at,
        updated_at=utc_now(),
    )
    next_record.validate()
    return next_record


def update_record(path: Path, slice_id: str, **kwargs: Any) -> LedgerRecord:
    # Cross-process lock across read-modify-write so a concurrent writer cannot
    # clobber this update.
    with locked_path(path):
        records = load_records(path)
        out: list[LedgerRecord] = []
        updated: LedgerRecord | None = None
        for record in records:
            if record.slice_id == slice_id:
                updated = transition_record(record, **kwargs)
                out.append(updated)
            else:
                out.append(record)
        if updated is None:
            raise LedgerError(f"slice_id not found: {slice_id!r}")
        save_records(path, out)
        return updated


def append_record(path: Path, record: LedgerRecord) -> None:
    with locked_path(path):
        records = load_records(path)
        if any(r.slice_id == record.slice_id for r in records):
            raise LedgerError(f"duplicate slice_id {record.slice_id!r}")
        records.append(record)
        save_records(path, records)


def render_records(records: Iterable[LedgerRecord]) -> str:
    rows = list(records)
    if not rows:
        return "No ledger records.\n"
    lines = ["Hydra build ledger:"]
    for record in rows:
        lines.append(f"- {record.slice_id}: {record.status} [{record.owner_lane}] {record.title}")
    return "\n".join(lines) + "\n"
