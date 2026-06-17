"""hydra.intake — fabric §5 step 1: classify(text, source) → Classification.

Rules live in intake_rules.yaml. This file is the thin function over config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


RULES_PATH = Path(__file__).with_name("intake_rules.yaml")

# Three classes from fabric §5
CONVO = "convo"
COLLAB = "collab"
STEERING = "steering"
VALID = {CONVO, COLLAB, STEERING}


@dataclass(frozen=True)
class Classification:
    """One classified intake — class + the rule that fired + the human reason."""
    kind: str           # "convo" | "collab" | "steering"
    rule_id: str        # which rule fired ("default" if none did)
    reason: str         # plain-English "why"


def classify(text: str, *, source: str = "operator") -> Classification:
    """Classify a single incoming message. First matching rule wins."""
    rules = _load_rules()
    stripped = text.strip()
    for rule in rules["rules"]:
        if _rule_matches(rule.get("when") or {}, stripped, source):
            kind = rule.get("classification")
            if kind not in VALID:
                continue
            return Classification(
                kind=kind,
                rule_id=str(rule.get("id", "?")),
                reason=str(rule.get("why", "")),
            )
    default = rules.get("default", CONVO)
    if default not in VALID:
        default = CONVO
    return Classification(
        kind=default,
        rule_id="default",
        reason="no rule matched — fell through to default",
    )


def _rule_matches(when: dict, text: str, source: str) -> bool:
    """A rule matches if every clause in `when` matches. Empty when-block never matches."""
    if not when:
        return False
    for clause, expected in when.items():
        if clause == "prefix":
            if not text.lower().startswith(str(expected).lower()):
                return False
        elif clause == "contains":
            if str(expected).lower() not in text.lower():
                return False
        elif clause == "regex":
            if not re.search(str(expected), text):
                return False
        elif clause == "source":
            if source != expected:
                return False
        elif clause == "source_prefix":
            if not source.startswith(str(expected)):
                return False
        else:
            # Unknown clause — refuse to match rather than guess.
            return False
    return True


@lru_cache(maxsize=1)
def _load_rules() -> dict:
    """Load and cache the YAML rule set."""
    with RULES_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if "rules" not in data or not isinstance(data["rules"], list):
        data["rules"] = []
    return data


def reload_rules() -> None:
    """Drop the rules cache (test helper / hot-reload)."""
    _load_rules.cache_clear()
