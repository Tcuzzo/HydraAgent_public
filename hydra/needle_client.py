"""Needle tool-selection adapter. Hydra retains all tool execution and policy."""
from __future__ import annotations

from importlib.util import find_spec
import os
from uuid import uuid4

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from hydra.llm import ChatMessage, ChatResponse, LlmError, _parse_tool_calls
from hydra.local_model_worker import predict_local


def configured_needle_model(model: str | None = None) -> str:
    return model or os.environ.get("HYDRA_NEEDLE_MODEL") or os.environ.get("NEEDLE_MODEL") or "needle3"


def _schemas(tools) -> dict:
    if not isinstance(tools, (list, tuple)) or not tools:
        raise LlmError("Needle selects tools; supply tool schemas and use a chat model for prose")
    schemas = {}
    for entry in tools:
        schema = entry.get("function", entry) if isinstance(entry, dict) else None
        if not isinstance(schema, dict) or not isinstance(schema.get("name"), str) or not schema["name"]:
            raise LlmError("Needle requires named function schema objects")
        if schema["name"] in schemas:
            raise LlmError("Needle tool names must be unique")
        parameters = schema.get("parameters", {"type": "object"})
        if not isinstance(parameters, dict):
            raise LlmError("Needle tool parameters must be JSON schema objects")
        try:
            Draft202012Validator.check_schema(parameters)
        except SchemaError as exc:
            raise LlmError("Needle received an invalid tool parameter schema") from exc
        schemas[schema["name"]] = parameters
    return schemas


class NeedleClient:
    def __init__(self, *, model: str | None = None) -> None:
        if find_spec("needle") is None:
            raise LlmError("Needle is optional; install hydraagent[needle] to use provider 'needle'")
        self.model = configured_needle_model(model)

    def list_models(self, *, timeout: float = 10.0) -> list[str]:
        return [self.model]

    def chat(self, messages, *, model: str, max_tokens: int = 512,
             temperature: float = 0.0, timeout: float = 60.0, tools=None) -> ChatResponse:
        schemas = _schemas(tools)
        msgs = [m.to_dict() if isinstance(m, ChatMessage) else m for m in messages]
        if not msgs or any(not isinstance(m, dict) or not isinstance(m.get("content"), (str, type(None))) for m in msgs):
            raise LlmError("Needle requires text messages")
        raw = predict_local({"backend": "needle", "model": model or self.model,
                             "tools": tools, "messages": msgs, "max_tokens": max_tokens}, timeout=timeout)
        if raw.get("success") is False:
            raise LlmError(f"Needle inference failed: {raw.get('error') or raw.get('error_code')}")
        # The SDK run() enforces grounding itself; complete() requires the caller
        # to do so. Never release an explicitly ungrounded call to Hydra tools.
        validation = raw.get("validation") or {}
        if not isinstance(validation, dict) or validation.get("ungrounded"):
            raise LlmError("Needle returned ungrounded tool arguments")
        if validation.get("negation"):
            raise LlmError("Needle returned a tool call that contradicts a negated request")
        calls = raw.get("function_calls", [])
        if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
            raise LlmError("Needle returned invalid function calls")
        parsed = _parse_tool_calls([{"id": f"needle_{uuid4().hex}", "function": c} for c in calls])
        if any(c.name not in schemas for c in parsed):
            raise LlmError("Needle returned a tool outside the supplied schemas")
        for call in parsed:
            try:
                Draft202012Validator(schemas[call.name]).validate(call.arguments)
            except ValidationError as exc:
                raise LlmError(f"Needle returned invalid arguments for {call.name!r}: {exc.message}") from exc
        # Reasoning is metadata, not a fabricated user-facing chat answer.
        return ChatResponse("", model or self.model, "tool_calls" if parsed else "stop", 0, 0, raw, parsed)
