"""Deterministic reflex bench. A stub decider replays fixed tasks.

The receipt counts big-model loops with and without the reflex tier.
It is not a live measurement. Live measurement needs the real reflex rung.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.reflex_seam import DECISION_SCHEMA_ID, run_turn


SCHEMA = "hydra.reflex.bench.v1"
_ROUTE = "router/decide/reflex"

TASKS: tuple[dict[str, Any], ...] = (
    {
        "id": "read-readme",
        "prompt": "read README.md",
        "decision": {
            "schema": DECISION_SCHEMA_ID,
            "next_tool": {"name": "fs_read", "arguments": {"path": "README.md"}},
            "done": False,
            "retry": False,
            "escalate": False,
            "reason": "read the file",
        },
    },
    {
        "id": "list-workspace",
        "prompt": "list files in the workspace directory",
        "decision": {
            "schema": DECISION_SCHEMA_ID,
            "next_tool": {"name": "list_directory", "arguments": {"path": "."}},
            "done": False,
            "retry": False,
            "escalate": False,
            "reason": "list the workspace",
        },
    },
    {
        "id": "search-todo",
        "prompt": "search for the pattern TODO",
        "decision": {
            "schema": DECISION_SCHEMA_ID,
            "next_tool": {"name": "grep", "arguments": {"pattern": "TODO"}},
            "done": False,
            "retry": False,
            "escalate": False,
            "reason": "search",
        },
    },
    {
        "id": "inventory-done",
        "prompt": "is the inventory done",
        "decision": {
            "schema": DECISION_SCHEMA_ID,
            "next_tool": None,
            "done": True,
            "retry": False,
            "escalate": False,
            "reason": "inventory is done",
        },
    },
    {
        "id": "design-migration",
        "prompt": "design a migration plan for the storage layer",
        "decision": {
            "schema": DECISION_SCHEMA_ID,
            "next_tool": None,
            "done": False,
            "retry": False,
            "escalate": True,
            "reason": "needs the full loop",
        },
    },
)


def run_bench(out: str | Path | None = None) -> dict[str, Any]:
    """Replay the fixed tasks and write a JSON receipt when ``out`` is set."""
    tools = {
        "fs_read": lambda **_kwargs: {"ok": True},
        "list_directory": lambda **_kwargs: {"ok": True},
        "grep": lambda **_kwargs: {"ok": True},
    }
    by_prompt = {task["prompt"]: task for task in TASKS}

    def resolver(rung: str) -> str:
        if rung != "reflex":
            raise RuntimeError("stub resolver expected the reflex rung")
        return _ROUTE

    def decider(*, prompt: str, hint: dict[str, Any] | None, rung: str, route: str) -> dict[str, Any]:
        del hint, rung, route
        return by_prompt[prompt]["decision"]

    def big_loop(_prompt: str) -> str:
        return "big"

    per_task: list[dict[str, Any]] = []
    with_loops = 0
    for task in TASKS:
        outcome = run_turn(
            task["prompt"],
            resolver=resolver,
            decider=decider,
            big_loop=big_loop,
            tools=tools,
        )
        with_loops += int(outcome["big_model_loops"])
        per_task.append(
            {
                "id": task["id"],
                "tier": outcome["tier"],
                "big_model_loops": outcome["big_model_loops"],
            }
        )
    without_loops = len(TASKS)
    receipt = {
        "schema": SCHEMA,
        "measurement": "stub",
        "claim": "stub_only",
        "follow_up": "Live measurement needs the real reflex rung.",
        "without_reflex": {"big_model_loops": without_loops},
        "with_reflex": {"big_model_loops": with_loops},
        "loops_saved": without_loops - with_loops,
        "tasks": per_task,
    }
    if out is not None:
        Path(out).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt
