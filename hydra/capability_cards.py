"""Hydra-native capability cards and prompt routing."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "hydra.capability_cards.v1"
REQUIRED_FIELDS = (
    "id",
    "name",
    "source",
    "intent_patterns",
    "mission_phases",
    "evidence_required",
    "evals",
    "memory_links",
)


class CapabilityCardError(Exception):
    """Operator-facing capability-card failure."""


@dataclass(frozen=True)
class CapabilityCard:
    card_id: str
    name: str
    source: str
    intent_patterns: list[str]
    mission_phases: list[str]
    evidence_required: list[str]
    evals: list[str]
    memory_links: list[str]

    def validate(self) -> None:
        for attr in ("card_id", "name", "source"):
            value = getattr(self, attr)
            if not isinstance(value, str) or not value.strip():
                raise CapabilityCardError(f"{attr} must be a non-empty string")
        if "/" in self.card_id or ".." in self.card_id:
            raise CapabilityCardError("card_id must be a simple path segment")
        for attr in ("intent_patterns", "mission_phases", "evidence_required", "evals", "memory_links"):
            values = getattr(self, attr)
            if not values or not all(isinstance(item, str) and item.strip() for item in values):
                raise CapabilityCardError(f"{attr} must be non-empty strings")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "id": self.card_id,
            "name": self.name,
            "source": self.source,
            "intent_patterns": list(self.intent_patterns),
            "mission_phases": list(self.mission_phases),
            "evidence_required": list(self.evidence_required),
            "evals": list(self.evals),
            "memory_links": list(self.memory_links),
        }


def load_cards(root: Path) -> list[CapabilityCard]:
    if not root.is_dir():
        raise CapabilityCardError(f"capability directory not found: {root}")
    cards: list[CapabilityCard] = []
    for path in sorted(root.glob("*.y*ml")):
        for raw in _parse_simple_yaml_list(path):
            cards.append(_card_from_dict(raw, path))
    if not cards:
        raise CapabilityCardError(f"no capability cards found in {root}")
    return cards


def route_cards(prompt: str, cards: list[CapabilityCard]) -> list[CapabilityCard]:
    text = prompt.lower()
    routed = []
    for card in cards:
        if any(pattern.lower() in text for pattern in card.intent_patterns):
            routed.append(card)
    return routed


def render_card_list(cards: list[CapabilityCard]) -> str:
    lines = ["Hydra native capabilities:"]
    for card in cards:
        card.validate()
        lines.append(f"- {card.card_id}: {card.name}")
    return "\n".join(lines)


def render_route(prompt: str, cards: list[CapabilityCard]) -> str:
    routed = route_cards(prompt, cards)
    lines = ["Hydra capability route:", f"prompt: {prompt}"]
    if not routed:
        lines.append("- no native capability matched")
        return "\n".join(lines)
    for card in routed:
        lines.extend(
            [
                f"- {card.card_id}: {card.name}",
                f"  phases: {', '.join(card.mission_phases)}",
                f"  evidence: {', '.join(card.evidence_required)}",
            ]
        )
    return "\n".join(lines)


def _card_from_dict(raw: dict[str, Any], path: Path) -> CapabilityCard:
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise CapabilityCardError(f"{path}: missing fields: {', '.join(missing)}")
    card = CapabilityCard(
        card_id=str(raw["id"]),
        name=str(raw["name"]),
        source=str(raw["source"]),
        intent_patterns=_string_list(raw["intent_patterns"], "intent_patterns", path),
        mission_phases=_string_list(raw["mission_phases"], "mission_phases", path),
        evidence_required=_string_list(raw["evidence_required"], "evidence_required", path),
        evals=_string_list(raw["evals"], "evals", path),
        memory_links=_string_list(raw["memory_links"], "memory_links", path),
    )
    card.validate()
    return card


def _string_list(value: Any, field: str, path: Path) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise CapabilityCardError(f"{path}: {field} must be a list of strings")
    return [item.strip() for item in value]


def _parse_simple_yaml_list(path: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    active_key: str | None = None
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if indent == 0 and stripped.startswith("- "):
            if current is not None:
                cards.append(current)
            current = {}
            active_key = None
            item = stripped[2:].strip()
            if item:
                _set_key_value(current, item, path, line_no)
            continue
        if current is None:
            raise CapabilityCardError(f"{path}:{line_no}: expected list item")
        if active_key and stripped.startswith("- "):
            current.setdefault(active_key, []).append(_parse_scalar(stripped[2:].strip()))
            continue
        active_key = _set_key_value(current, stripped, path, line_no)
    if current is not None:
        cards.append(current)
    return cards


def _set_key_value(target: dict[str, Any], text: str, path: Path, line_no: int) -> str | None:
    if ":" not in text:
        raise CapabilityCardError(f"{path}:{line_no}: expected key: value")
    key, _, value = text.partition(":")
    key = key.strip()
    value = value.strip()
    if not value:
        target[key] = []
        return key
    target[key] = _parse_value(value, path, line_no)
    return None


def _parse_value(value: str, path: Path, line_no: int) -> Any:
    if value.startswith("["):
        if not value.endswith("]"):
            raise CapabilityCardError(f"{path}:{line_no}: invalid inline list")
        inner = value[1:-1].strip()
        if not inner:
            return []
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return [_parse_scalar(item.strip()) for item in inner.split(",")]
        if not isinstance(parsed, list):
            raise CapabilityCardError(f"{path}:{line_no}: inline value must be a list")
        return parsed
    return _parse_scalar(value)


def _parse_scalar(value: str) -> str:
    return value.strip().strip('"').strip("'")
