"""Hydra-native lifecycle hooks."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hydra.autonomy import SAFE_BASH_COMMANDS, classify_tool_call
from hydra.orchestrate import SubagentTask, dispatch


SCHEMA = "hydra.hooks.v1"
REPORT_SCHEMA = "hydra.hooks.report.v1"
DEFAULT_HOOKS_PATH = Path(".hydraAgent/hooks.json")


class HookError(Exception):
    """Operator-facing hook configuration failure."""


@dataclass(frozen=True)
class Hook:
    event: str
    id: str
    command: str

    def validate(self) -> None:
        for attr in ("event", "id", "command"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise HookError(f"hook {attr} must be a non-empty string")
        command = " ".join(self.command.split())
        decision = classify_tool_call("bash", {"command": command}, "ask")
        if decision["decision"] != "auto_allow" or command not in SAFE_BASH_COMMANDS:
            raise HookError(f"hook command is not exact auto-allowed bash: {command}")


def load_hooks(root: Path, event: str) -> list[Hook]:
    path = root / DEFAULT_HOOKS_PATH
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        raise HookError(f"unsupported hook schema: {raw.get('schema')!r}")
    rows = raw.get("hooks")
    if not isinstance(rows, list):
        raise HookError("hooks must be a list")
    hooks: list[Hook] = []
    for row in rows:
        if not isinstance(row, dict):
            raise HookError("each hook must be an object")
        hook = Hook(
            event=str(row.get("event", "")),
            id=str(row.get("id", "")),
            command=" ".join(str(row.get("command", "")).split()),
        )
        hook.validate()
        if hook.event == event:
            hooks.append(hook)
    return hooks


def run_hooks(root: Path, event: str, *, max_concurrency: int = 2) -> dict[str, Any]:
    hooks = load_hooks(root, event)
    tasks = [
        SubagentTask(
            id=f"hook-{hook.event}-{hook.id}",
            command=["bash", "-lc", hook.command],
            cwd=str(root),
            timeout_seconds=20,
        )
        for hook in hooks
    ]
    dispatch_report = dispatch(tasks, max_concurrency=max_concurrency) if tasks else None
    return {
        "schema": REPORT_SCHEMA,
        "event": event,
        "configured_count": len(hooks),
        "executed_count": len(tasks),
        "dispatch": dispatch_report,
    }
