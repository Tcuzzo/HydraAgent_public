"""Needle tool-selection adapter. Hydra retains all tool execution and policy."""
from __future__ import annotations

from importlib.util import find_spec
from uuid import uuid4

from hydra.llm import ChatMessage, ChatResponse, LlmError, _parse_tool_calls
from hydra.local_model_worker import predict_local


class NeedleClient:
    def __init__(self, *, model: str = "needle3") -> None:
        if find_spec("needle") is None:
            raise LlmError("Needle is optional; install hydraagent[needle] to use provider 'needle'")
        self.model = model

    def list_models(self, *, timeout: float = 10.0) -> list[str]:
        return [self.model]

    def chat(self, messages, *, model: str, max_tokens: int = 512,
             temperature: float = 0.0, timeout: float = 60.0, tools=None) -> ChatResponse:
        if not tools:
            raise LlmError("Needle selects tools; supply tool schemas and use a chat model for prose")
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
        calls = raw.get("function_calls", [])
        if not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls):
            raise LlmError("Needle returned invalid function calls")
        parsed = _parse_tool_calls([{"id": f"needle_{uuid4().hex}", "function": c} for c in calls])
        allowed = {t.get("function", t).get("name") for t in tools if isinstance(t, dict)}
        if any(c.name not in allowed for c in parsed):
            raise LlmError("Needle returned a tool outside the supplied schemas")
        # Reasoning is metadata, not a fabricated user-facing chat answer.
        return ChatResponse("", model or self.model, "tool_calls" if parsed else "stop", 0, 0, raw, parsed)
