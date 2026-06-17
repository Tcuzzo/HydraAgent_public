"""Hydra mission object model and JSON persistence."""
from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hydra.runtime_route import default_runtime_route


SCHEMA = "hydra.mission.v1"
DEFAULT_MISSION_ROOT = Path("evidence/missions")
VALID_STATUSES = frozenset(
    {"planned", "running", "needs_verification", "proven", "failed", "blocked"}
)
DEFAULT_RUNTIME_ROUTE = default_runtime_route()


class MissionError(Exception):
    """Operator-facing mission persistence failure."""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Mission:
    mission_id: str
    operator_prompt: str
    intent: str
    next_action: str
    status: str = "planned"
    runtime_route: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_RUNTIME_ROUTE))
    evidence: dict[str, str] = field(default_factory=dict)
    schema: str = SCHEMA
    created_at: str = field(default_factory=utc_now)

    def validate(self) -> None:
        if self.schema != SCHEMA:
            raise MissionError(f"unsupported schema: {self.schema!r}")
        for attr in ("mission_id", "operator_prompt", "intent", "next_action", "created_at"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise MissionError(f"{attr} must be a non-empty string")
        if "/" in self.mission_id or ".." in self.mission_id:
            raise MissionError("mission_id must be a simple path segment")
        if self.status not in VALID_STATUSES:
            raise MissionError(f"unsupported mission status: {self.status!r}")
        for key in ("conversation_provider", "planner_provider", "worker_provider", "verifier"):
            value = self.runtime_route.get(key)
            if not isinstance(value, str) or not value.strip():
                raise MissionError(f"runtime_route.{key} must be a non-empty string")
        run_dir = self.evidence.get("run_dir")
        if not isinstance(run_dir, str) or not run_dir.startswith("evidence/missions/"):
            raise MissionError("evidence.run_dir must point under evidence/missions/")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "mission_id": self.mission_id,
            "operator_prompt": self.operator_prompt,
            "intent": self.intent,
            "next_action": self.next_action,
            "status": self.status,
            "runtime_route": dict(self.runtime_route),
            "evidence": dict(self.evidence),
            "created_at": self.created_at,
        }


def create_mission(
    *,
    root: Path,
    operator_prompt: str,
    intent: str,
    next_action: str,
) -> Mission:
    prompt = _single_line(operator_prompt)
    mission_id = _mission_id(prompt)
    run_dir = DEFAULT_MISSION_ROOT / mission_id
    mission = Mission(
        mission_id=mission_id,
        operator_prompt=prompt,
        intent=intent,
        next_action=next_action,
        evidence={"run_dir": run_dir.as_posix()},
    )
    mission.validate()
    base = root / run_dir
    if base.exists():
        raise MissionError(f"mission already exists: {mission_id}")
    base.mkdir(parents=True, exist_ok=True)
    (base / "mission.json").write_text(
        json.dumps(mission.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return mission


def load_mission(root: Path, mission_id: str) -> Mission:
    _validate_mission_id(mission_id)
    path = root / DEFAULT_MISSION_ROOT / mission_id / "mission.json"
    if not path.is_file():
        raise MissionError(f"mission not found: {mission_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    mission = Mission(
        schema=raw.get("schema", ""),
        mission_id=raw.get("mission_id", ""),
        operator_prompt=raw.get("operator_prompt", ""),
        intent=raw.get("intent", ""),
        next_action=raw.get("next_action", ""),
        status=raw.get("status", ""),
        runtime_route=raw.get("runtime_route", {}),
        evidence=raw.get("evidence", {}),
        created_at=raw.get("created_at", ""),
    )
    mission.validate()
    return mission


def render_mission_text(mission: Mission) -> str:
    mission.validate()
    return "\n".join(
        [
            f"mission_id: {mission.mission_id}",
            f"status: {mission.status}",
            f"intent: {mission.intent}",
            f"next_action: {mission.next_action}",
            f"operator_prompt: {mission.operator_prompt}",
            f"conversation_provider: {mission.runtime_route['conversation_provider']}",
            f"planner_provider: {mission.runtime_route['planner_provider']}",
            f"worker_provider: {mission.runtime_route['worker_provider']}",
            f"verifier: {mission.runtime_route['verifier']}",
            f"run_dir: {mission.evidence['run_dir']}",
        ]
    )


def _single_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _mission_id(operator_prompt: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", operator_prompt.lower()).strip("-")
    slug = slug[:40].strip("-") or "mission"
    return f"{stamp}-{slug}"


def _validate_mission_id(mission_id: str) -> None:
    if not isinstance(mission_id, str) or not mission_id.strip():
        raise MissionError("mission_id must be a non-empty string")
    if "/" in mission_id or "\\" in mission_id or ".." in mission_id:
        raise MissionError("mission_id must be a simple path segment")
