"""Repo-declared objective commands for Hydra mission loops."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hydra.autonomy import SAFE_BASH_COMMANDS, classify_tool_call
from hydra.orchestrate import SubagentTask


SCHEMA = "hydra.mission_objectives.v1"
DEFAULT_OBJECTIVES_PATH = Path(".hydraAgent/mission-objectives.json")


class MissionObjectiveError(Exception):
    """Operator-facing mission objective configuration failure."""


@dataclass(frozen=True)
class MissionObjective:
    id: str
    command: str
    success_pattern: str | None = None

    def validate(self) -> None:
        for attr in ("id", "command"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise MissionObjectiveError(f"objective {attr} must be a non-empty string")
        if "/" in self.id or "\\" in self.id or ".." in self.id:
            raise MissionObjectiveError("objective id must be a simple path segment")
        command = " ".join(self.command.split())
        decision = classify_tool_call("bash", {"command": command}, "ask")
        if decision["decision"] != "auto_allow" or command not in SAFE_BASH_COMMANDS:
            raise MissionObjectiveError(f"objective command is not exact auto-allowed bash: {command}")
        if self.success_pattern is not None and not isinstance(self.success_pattern, str):
            raise MissionObjectiveError("success_pattern must be a string or null")

    def to_task(self, root: Path) -> SubagentTask:
        self.validate()
        return SubagentTask(
            id=f"objective-{self.id}",
            command=["bash", "-lc", " ".join(self.command.split())],
            cwd=str(root),
            timeout_seconds=20,
            success_pattern=self.success_pattern,
        )


def load_objectives(root: Path) -> list[MissionObjective]:
    path = root / DEFAULT_OBJECTIVES_PATH
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        raise MissionObjectiveError(f"unsupported objectives schema: {raw.get('schema')!r}")
    rows = raw.get("objectives")
    if not isinstance(rows, list):
        raise MissionObjectiveError("objectives must be a list")
    objectives: list[MissionObjective] = []
    for row in rows:
        if not isinstance(row, dict):
            raise MissionObjectiveError("each objective must be an object")
        objective = MissionObjective(
            id=str(row.get("id", "")),
            command=" ".join(str(row.get("command", "")).split()),
            success_pattern=row.get("success_pattern"),
        )
        objective.validate()
        objectives.append(objective)
    return objectives
