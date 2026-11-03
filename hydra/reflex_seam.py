"""Reflex tier: a free rule hint, then one typed decision on the reflex rung.

The full model loop runs only when that decision says escalate, or when the
reflex rung cannot be resolved. This module never invents a model or a provider.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


DECISION_SCHEMA_ID = "hydra.reflex.decision.v1"
TURN_SCHEMA_ID = "hydra.reflex.turn.v1"
_MODEL_TAG = re.compile(
    r"(?<![\w/.-])[A-Za-z][\w.-]*:(?:\d+(?:\.\d+)?[bBmM]|cloud|latest|free)\b"
)
_READ = re.compile(r"\b(?:read|open|show|cat)\b.*?(\S+\.[A-Za-z0-9]+)", re.IGNORECASE)
_LIST = re.compile(
    r"\b(?:list|ls)\b.*\b(?:files?|director(?:y|ies)|folder)\b",
    re.IGNORECASE,
)
_SEARCH = re.compile(
    r"\b(?:search|grep)\b.*\b(?:for|pattern)\b(?:\s+the)?(?:\s+pattern)?\s+(\S+)",
    re.IGNORECASE,
)

_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "next_tool", "done", "retry", "escalate"],
    "properties": {
        "schema": {"const": DECISION_SCHEMA_ID},
        "next_tool": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "arguments"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "arguments": {"type": "object"},
                    },
                },
            ]
        },
        "done": {"type": "boolean"},
        "retry": {"type": "boolean"},
        "escalate": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}
_VALIDATOR = Draft202012Validator(_DECISION_SCHEMA)

DecisionFn = Callable[..., dict[str, Any]]
_DECISION_CLIENT: DecisionFn | None = None


class ReflexUnavailable(Exception):
    """The reflex rung cannot be used. Callers fall through to the full loop."""


class ReflexSchemaError(ValueError):
    """A reflex decision failed schema or cross-field checks."""


def set_decision_client(client: DecisionFn | None) -> None:
    """Install the typed decision callable. None clears it. No default model."""
    global _DECISION_CLIENT
    _DECISION_CLIENT = client


def reflex_rung_name(path: str | Path | None = None) -> str:
    """Return the configured reflex rung. Missing config is unavailable, not a default."""
    source = Path(path) if path else Path(__file__).with_name("model_routing.yaml")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ReflexUnavailable(f"reflex routing file unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ReflexUnavailable("reflex routing file is not a mapping")
    block = data.get("reflex")
    if not isinstance(block, dict) or not str(block.get("rung") or "").strip():
        raise ReflexUnavailable("reflex rung is not configured")
    stored = sorted({"model", "provider", "base_url"} & set(block))
    if stored:
        raise ReflexUnavailable(f"reflex entry stores {stored}")
    return str(block["rung"]).strip()


def rule_reflex(prompt: str) -> dict[str, Any] | None:
    """R1: map an intent to a tool hint with rules only."""
    text = str(prompt or "").strip()
    match = _READ.search(text)
    if match:
        return {"tool": "fs_read", "arguments": {"path": match.group(1).strip(".,")}}
    if _LIST.search(text):
        return {"tool": "list_directory", "arguments": {"path": "."}}
    match = _SEARCH.search(text)
    if match:
        return {"tool": "grep", "arguments": {"pattern": match.group(1).strip(".,")}}
    return None


def validate_decision(payload: Any) -> dict[str, Any]:
    """Accept one typed loop decision, or raise ReflexSchemaError."""
    if not isinstance(payload, dict):
        raise ReflexSchemaError("reflex decision must be an object")
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda err: list(err.path))
    if errors:
        raise ReflexSchemaError(_format_error(errors[0]))
    modes = sum(
        (
            bool(payload["escalate"]),
            bool(payload["done"]),
            bool(payload["retry"]),
            payload["next_tool"] is not None,
        )
    )
    if modes > 1:
        raise ReflexSchemaError(
            "decision must choose one of next_tool, done, retry, stop, or escalate"
        )
    return payload


def resolve_route(rung: str) -> str:
    """Resolve the reflex rung to a route token. Failure raises ReflexUnavailable."""
    if not str(rung or "").strip():
        raise ReflexUnavailable("reflex rung name is empty")
    try:
        from hydra.rung_resolver import resolve

        _transport, route = resolve(rung, task="decide")
    except ReflexUnavailable:
        raise
    except Exception as exc:
        raise ReflexUnavailable(str(exc)) from exc
    if not isinstance(route, str) or not route.strip() or _MODEL_TAG.search(route):
        raise ReflexUnavailable("reflex route is not a rung route")
    return route


def default_decider(*, prompt: str, hint: dict[str, Any] | None, rung: str, route: str) -> dict[str, Any]:
    """Ask the installed decision client. With none installed, the rung is unavailable."""
    if _DECISION_CLIENT is None:
        raise ReflexUnavailable(f"reflex decision client is not configured for rung {rung}")
    raw = _DECISION_CLIENT(prompt=prompt, hint=hint, rung=rung, route=route)
    return validate_decision(raw)


def run_turn(
    prompt: str,
    *,
    resolver: Callable[[str], str],
    decider: DecisionFn,
    big_loop: Callable[[str], Any] | None,
    tools: dict[str, Callable[..., Any]] | None = None,
    routing_path: str | Path | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Run R1, then the reflex decision. Fall through when the rung is missing."""
    del root
    hint = rule_reflex(prompt)
    try:
        rung = reflex_rung_name(routing_path)
        route = resolver(rung)
    except ReflexUnavailable:
        return _fallthrough(prompt, hint, big_loop)
    if not isinstance(route, str) or not route.strip() or _MODEL_TAG.search(route):
        return _fallthrough(prompt, hint, big_loop)
    try:
        raw = decider(prompt=prompt, hint=hint, rung=rung, route=route)
    except ReflexUnavailable:
        return _fallthrough(prompt, hint, big_loop)
    decision = validate_decision(raw)
    if decision["escalate"]:
        if big_loop is not None:
            big_loop(prompt)
        return _turn(
            tier="escalate",
            handled=big_loop is not None,
            big_model_loops=1 if big_loop is not None else 0,
            hint=hint,
            decision=decision,
            response=str(decision.get("reason") or ""),
            halted_reason="escalate",
        )
    if decision["next_tool"] is not None:
        spec = decision["next_tool"]
        fn = (tools or {}).get(spec["name"])
        if fn is None:
            result: Any = {"skipped": "tool not bound"}
            tool_calls = 0
        else:
            result = fn(**dict(spec.get("arguments") or {}))
            tool_calls = 1
        return _turn(
            tier="reflex",
            handled=True,
            big_model_loops=0,
            hint=hint,
            decision=decision,
            response=json.dumps(result, default=str),
            halted_reason="reflex_tool",
            tool_calls=tool_calls,
        )
    if decision["done"]:
        reason, halted = "reflex_done", "reflex_done"
    elif decision["retry"]:
        reason, halted = "reflex_retry", "reflex_retry"
    else:
        reason, halted = "reflex_stop", "reflex_stop"
    return _turn(
        tier="reflex",
        handled=True,
        big_model_loops=0,
        hint=hint,
        decision=decision,
        response=str(decision.get("reason") or reason),
        halted_reason=halted,
    )


def maybe_handle(
    prompt: str,
    tools: Any = None,
    *,
    root: str | Path | None = None,
) -> dict[str, Any] | None:
    """Settle a turn on the reflex tier when it is engaged. None means use today's loop."""
    if os.environ.get("HYDRA_REFLEX") != "1":
        return None
    try:
        outcome = run_turn(
            prompt,
            resolver=resolve_route,
            decider=default_decider,
            big_loop=None,
            tools=_invoke_map(tools),
            root=root,
        )
    except (ReflexUnavailable, ReflexSchemaError):
        return None
    if not outcome.get("handled"):
        return None
    return outcome


def _fallthrough(prompt: str, hint: dict[str, Any] | None, big_loop: Callable[[str], Any] | None) -> dict[str, Any]:
    if big_loop is not None:
        big_loop(prompt)
    return _turn(
        tier="fallthrough",
        handled=big_loop is not None,
        big_model_loops=1 if big_loop is not None else 0,
        hint=hint,
        decision=None,
        response="",
        halted_reason="fallthrough",
    )


def _turn(
    *,
    tier: str,
    handled: bool,
    big_model_loops: int,
    hint: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    response: str,
    halted_reason: str,
    tool_calls: int = 0,
) -> dict[str, Any]:
    return {
        "schema": TURN_SCHEMA_ID,
        "tier": tier,
        "handled": handled,
        "big_model_loops": big_model_loops,
        "hint": hint,
        "decision": decision,
        "response": response,
        "halted_reason": halted_reason,
        "tool_calls": tool_calls,
    }


def _invoke_map(tools: Any) -> dict[str, Callable[..., Any]]:
    if not tools:
        return {}
    if isinstance(tools, dict):
        return tools
    return {tool.name: tool.invoke for tool in tools}


def _format_error(err: ValidationError) -> str:
    location = ".".join(str(part) for part in err.path) or "decision"
    return f"{location}: {err.message}"
