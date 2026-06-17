"""Source re-engineering programs for Hydra-native capability build."""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SCHEMA = "hydra.source_reengineering.v1"
RECEIPT_SCHEMA = "hydra.source_reengineering.receipt.v1"
DEFAULT_ROOT = Path("evidence/source-reengineering")


class SourceReengineeringError(Exception):
    """Operator-facing source re-engineering failure."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class SourceProgram:
    system_id: str
    sources: list[dict[str, str]]
    capabilities: list[str]
    hydra_plan: str
    license_notes: str
    verification_notes: str
    schema: str = SCHEMA
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.schema != SCHEMA:
            raise SourceReengineeringError(f"unsupported schema: {self.schema!r}")
        for attr in ("system_id", "hydra_plan", "license_notes", "verification_notes", "created_at"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise SourceReengineeringError(f"{attr} must be a non-empty string")
        if "/" in self.system_id or ".." in self.system_id:
            raise SourceReengineeringError("system_id must be a simple path segment")
        if not self.sources:
            raise SourceReengineeringError("sources must not be empty")
        for source in self.sources:
            if not isinstance(source, dict) or not source.get("kind") or not source.get("url"):
                raise SourceReengineeringError("each source needs kind and url")
        if not self.capabilities or not all(isinstance(item, str) and item.strip() for item in self.capabilities):
            raise SourceReengineeringError("capabilities must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "system_id": self.system_id,
            "sources": list(self.sources),
            "capabilities": list(self.capabilities),
            "hydra_plan": self.hydra_plan,
            "license_notes": self.license_notes,
            "verification_notes": self.verification_notes,
            "created_at": self.created_at,
        }


def create_program(
    *,
    root: Path,
    system_id: str,
    sources: list[dict[str, str]],
    capabilities: list[str],
    hydra_plan: str,
    license_notes: str,
    verification_notes: str,
) -> SourceProgram:
    _validate_system_id(system_id)
    program = SourceProgram(system_id, sources, capabilities, hydra_plan, license_notes, verification_notes)
    program.validate()
    base = root / DEFAULT_ROOT / system_id
    if base.exists():
        raise SourceReengineeringError(f"source program already exists: {system_id}")
    base.mkdir(parents=True, exist_ok=True)
    (base / "source.json").write_text(json.dumps(program.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (base / "capability_matrix.md").write_text(_capability_matrix(program), encoding="utf-8")
    (base / "hydra_reengineering_plan.md").write_text(program.hydra_plan.strip() + "\n", encoding="utf-8")
    (base / "implementation_receipts.md").write_text("# Implementation Receipts\n\nNo receipts recorded yet.\n", encoding="utf-8")
    (base / "comparison_notes.md").write_text("# Comparison Notes\n\nHydra-native design pending implementation receipts.\n", encoding="utf-8")
    (base / "license_notes.md").write_text(program.license_notes.strip() + "\n", encoding="utf-8")
    (base / "verification_notes.md").write_text(program.verification_notes.strip() + "\n", encoding="utf-8")
    return program


def load_program(root: Path, system_id: str) -> SourceProgram:
    _validate_system_id(system_id)
    path = root / DEFAULT_ROOT / system_id / "source.json"
    if not path.is_file():
        raise SourceReengineeringError(f"source program not found: {system_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    program = SourceProgram(
        schema=raw.get("schema", ""),
        system_id=raw.get("system_id", ""),
        sources=raw.get("sources", []),
        capabilities=raw.get("capabilities", []),
        hydra_plan=raw.get("hydra_plan", ""),
        license_notes=raw.get("license_notes", ""),
        verification_notes=raw.get("verification_notes", ""),
        created_at=raw.get("created_at", ""),
    )
    program.validate()
    return program


def list_programs(root: Path) -> list[SourceProgram]:
    base = root / DEFAULT_ROOT
    if not base.is_dir():
        return []
    programs: list[SourceProgram] = []
    for path in sorted(base.iterdir()):
        if path.is_dir() and (path / "source.json").is_file():
            programs.append(load_program(root, path.name))
    return programs


def record_receipt(
    *,
    root: Path,
    system_id: str,
    source_url: str,
    learned_pattern: str,
    hydra_feature: str,
    eval_path: str,
) -> dict[str, Any]:
    program = load_program(root, system_id)
    for attr, value in {
        "source_url": source_url,
        "learned_pattern": learned_pattern,
        "hydra_feature": hydra_feature,
        "eval_path": eval_path,
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise SourceReengineeringError(f"{attr} must be a non-empty string")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "system_id": program.system_id,
        "source_url": source_url.strip(),
        "learned_pattern": learned_pattern.strip(),
        "hydra_feature": hydra_feature.strip(),
        "eval_path": eval_path.strip(),
        "created_at": utc_now(),
    }
    base = root / DEFAULT_ROOT / system_id
    with (base / "receipts.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(receipt, sort_keys=True) + "\n")
    with (base / "implementation_receipts.md").open("a", encoding="utf-8") as f:
        f.write(
            "\n"
            f"- Source: {receipt['source_url']}\n"
            f"  Pattern: {receipt['learned_pattern']}\n"
            f"  Hydra feature: `{receipt['hydra_feature']}`\n"
            f"  Eval: `{receipt['eval_path']}`\n"
        )
    return receipt


def render_program_text(program: SourceProgram) -> str:
    program.validate()
    lines = [f"Source re-engineering program: {program.system_id}", "capabilities:"]
    lines.extend(f"- {item}" for item in program.capabilities)
    lines.extend(["hydra_plan:", program.hydra_plan])
    return "\n".join(lines)


def _capability_matrix(program: SourceProgram) -> str:
    rows = ["# Capability Matrix", "", "| Capability | Hydra target |", "|---|---|"]
    rows.extend(f"| {item} | Re-engineer into Hydra-native runtime capability |" for item in program.capabilities)
    return "\n".join(rows) + "\n"


def _validate_system_id(system_id: str) -> None:
    if not isinstance(system_id, str) or not system_id.strip():
        raise SourceReengineeringError("system_id must be a non-empty string")
    if "/" in system_id or "\\" in system_id or ".." in system_id:
        raise SourceReengineeringError("system_id must be a simple path segment")
