from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra.llm import ChatResponse
from hydra.loop import AgentLoop, Tool


def test_inter_agent_message_envelope_validates_required_schema() -> None:
    from hydra.inter_agent import create_message, validate_message

    msg = create_message(
        from_role="orchestrator",
        to_role="harness_builder",
        message_type="command",
        action="implement_slice",
        trace_id="trace-123",
        data={"task": "wire protocol"},
    )

    assert set(msg) == {"envelope", "header", "payload", "meta"}
    assert msg["envelope"]["trace_id"] == "trace-123"
    assert msg["header"] == {
        "from": "orchestrator",
        "to": "harness_builder",
        "type": "command",
        "protocol_version": "1.0",
    }
    assert msg["payload"]["action"] == "implement_slice"
    assert validate_message(msg, role_manifest={"orchestrator", "harness_builder"}) == []

    broken = dict(msg)
    broken.pop("payload")
    assert "missing top-level field: payload" in validate_message(broken)


def test_inter_agent_message_creation_enforces_role_validation() -> None:
    from hydra.inter_agent import create_message

    with pytest.raises(ValueError, match="unknown role"):
        create_message(
            from_role="orchestrator",
            to_role="imaginary_agent",
            message_type="command",
            action="do_work",
            trace_id="trace-123",
        )


def test_inter_agent_redaction_handles_json_style_secrets() -> None:
    from hydra.inter_agent import redact_text

    raw = '{"api_key":"sk-live-secret","nested":{"token":"tok-secret"},"plain":"ok"}'

    redacted = redact_text(raw)

    assert "sk-live-secret" not in redacted
    assert "tok-secret" not in redacted
    assert '"api_key":"[redacted]"' in redacted
    assert '"token":"[redacted]"' in redacted


def test_agent_prompt_includes_inter_agent_protocol() -> None:
    from hydra.skill_spine import build_agent_system_prompt

    prompt = build_agent_system_prompt("Base prompt.")

    assert "INTER-AGENT COMMUNICATION PROTOCOL" in prompt
    assert "trace_id" in prompt
    assert "Never send messages to an agent role that does not exist" in prompt


def test_agent_loop_exposes_one_trace_id_to_all_tool_calls() -> None:
    from hydra.inter_agent import current_trace_id

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0
            self.first_messages: list[dict] | None = None

        def chat(self, _messages, *, model, max_tokens=0, temperature=0.0, timeout=0.0, tools=None):
            self.calls += 1
            if self.calls == 1:
                self.first_messages = [dict(m) for m in _messages]
            if self.calls == 1:
                return ChatResponse(
                    content="",
                    model=model,
                    finish_reason="tool_calls",
                    prompt_tokens=0,
                    completion_tokens=0,
                    raw={},
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            name="capture_trace",
                            arguments_raw="{}",
                            arguments={},
                        )
                    ],
                )
            return ChatResponse(
                content="done",
                model=model,
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[],
            )

    seen: list[str | None] = []

    def capture_trace() -> dict[str, str | None]:
        seen.append(current_trace_id())
        return {"trace_id": current_trace_id()}

    client = FakeClient()
    result = AgentLoop(client, model="fake").run(
        "do work",
        tools=[
            Tool(
                name="capture_trace",
                description="capture trace",
                parameters={"type": "object", "properties": {}},
                invoke=capture_trace,
            )
        ],
    )

    assert result.trace_id
    assert seen == [result.trace_id]
    assert client.first_messages is not None
    assert any(
        result.trace_id in str(message.get("content", ""))
        for message in client.first_messages
    )


# test_spawn_subagent_wraps_task_with_protocol_envelope,
# test_spawn_subagent_preserves_parent_correlation_from_context, and
# test_spawn_subagent_retries_once_after_timeout trimmed — they import
# hydra.parallel_subagents which is not present in this lean-core build.
