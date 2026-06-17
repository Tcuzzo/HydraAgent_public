"""Deterministic friction signals for Hydra operator build."""
from __future__ import annotations

from pathlib import Path
from typing import Any


SCHEMA = "hydra.friction_signal.v1"
DEFAULT_FIXTURE_PATH = Path(".hydraAgent/friction/fixtures.txt")


RULES = (
    {
        "kind": "operator_chore",
        "keywords": ("setup work", "putting me to work", "do it myself", "tokens over and over", "keep setting"),
        "repair_target": "reduce repeated setup prompts and complete discoverable operator chores directly",
    },
    {
        "kind": "identity_failure",
        "keywords": ("filesystem helper", "what is your job", "tell me about yourself", "dumb", "trash"),
        "repair_target": "answer as Hydra operator kernel with mission, tools, model route, memory, and proof state",
    },
    {
        "kind": "weak_capability",
        "keywords": ("cannot build", "can't build", "weak", "spell checker", "not good enough", "basic"),
        "repair_target": "promote proven builder capabilities through native skills, evals, and worker receipts",
    },
    {
        "kind": "stopped_iteration",
        "keywords": ("keep going", "continue", "don't stop", "do not stop", "stopping"),
        "repair_target": "continue through the next planned slice after each verified checkpoint unless blocked",
    },
    {
        "kind": "ux_confusion",
        "keywords": ("two chats", "confusing", "how do i talk", "ux", "user experience", "local gpu"),
        "repair_target": "unify chat, runtime route, worker status, and evidence into one visible operator cockpit",
    },
)


def classify_friction(text: str) -> dict[str, Any]:
    normalized = _normalize(text)
    for rule in RULES:
        if any(keyword in normalized for keyword in rule["keywords"]):
            return _signal(str(rule["kind"]), str(rule["repair_target"]))
    return _signal("unclassified", "record a mission note for operator review before changing behavior")


def load_friction_signals(root: Path, *, limit: int = 10) -> list[dict[str, Any]]:
    fixture_path = root / DEFAULT_FIXTURE_PATH
    if not fixture_path.is_file():
        return []
    signals: list[dict[str, Any]] = []
    for line in fixture_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        signals.append(classify_friction(line))
        if len(signals) >= limit:
            break
    return signals


def render_friction_text(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "No friction signals."
    lines = ["Hydra friction signals:"]
    for signal in signals:
        lines.append(f"- {signal['kind']}: {signal['repair_target']}")
    return "\n".join(lines)


def _signal(kind: str, repair_target: str) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "kind": kind,
        "repair_target": repair_target,
    }


def _normalize(text: str) -> str:
    return " ".join(str(text).lower().split())
