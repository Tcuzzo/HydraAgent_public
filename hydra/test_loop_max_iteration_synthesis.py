from __future__ import annotations

import argparse
from typing import Any

from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop, Tool


_SYNTHESIS_MARKER = "SYNTHESIS REQUIRED"
_FINAL_SYNTHESIS_TEXT = "Synthesized findings from tool evidence."


def _response(
    *,
    content: str,
    model: str,
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
) -> ChatResponse:
    return ChatResponse(
        content=content,
        model=model,
        finish_reason=finish_reason,
        prompt_tokens=0,
        completion_tokens=0,
        raw={},
        tool_calls=tool_calls or [],
    )


def _tool_call(call_id: int) -> ToolCall:
    return ToolCall(
        id=f"tc-{call_id}",
        name="read",
        arguments_raw='{"path": "audit.txt"}',
        arguments={"path": "audit.txt"},
    )


def _read_tool() -> Tool:
    return Tool(
        name="read",
        description="read a file",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        invoke=lambda path: {"path": path, "finding": "audit evidence"},
    )


class _AlwaysToolThenSynthesizeClient:
    def __init__(self) -> None:
        self.chat_calls: list[dict[str, Any]] = []
        self.no_tools_calls = 0

    def chat(
        self,
        messages,
        *,
        model,
        max_tokens=0,
        temperature=0.0,
        timeout=0.0,
        tools=None,
    ) -> ChatResponse:
        self.chat_calls.append(
            {"messages": [dict(m) for m in messages], "tools": tools}
        )
        if tools is None:
            self.no_tools_calls += 1
            assert any(
                "out of iterations" in (m.get("content") or "").lower()
                for m in messages
            )
            return _response(content=_FINAL_SYNTHESIS_TEXT, model=model)
        return _response(
            content="",
            model=model,
            tool_calls=[_tool_call(len(self.chat_calls))],
            finish_reason="tool_calls",
        )


class _SmallCapToolClient:
    def __init__(self, *, tool_turns: int) -> None:
        self.tool_turns = tool_turns
        self.chat_messages: list[list[dict[str, Any]]] = []
        self.calls = 0

    def chat(
        self,
        messages,
        *,
        model,
        max_tokens=0,
        temperature=0.0,
        timeout=0.0,
        tools=None,
    ) -> ChatResponse:
        self.calls += 1
        self.chat_messages.append([dict(m) for m in messages])
        if self.calls <= self.tool_turns:
            return _response(
                content="",
                model=model,
                tool_calls=[_tool_call(self.calls)],
                finish_reason="tool_calls",
            )
        return _response(content="final answer", model=model)


class _ImmediateTextClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages,
        *,
        model,
        max_tokens=0,
        temperature=0.0,
        timeout=0.0,
        tools=None,
    ) -> ChatResponse:
        self.calls += 1
        return _response(content="done", model=model)


def test_max_iterations_after_tool_calls_forces_non_empty_final_synthesis():
    client = _AlwaysToolThenSynthesizeClient()
    loop = AgentLoop(client, model="test/model")

    result = loop.run("audit the project", tools=[_read_tool()], max_iterations=3)

    assert result.halted_reason == "max_iterations"
    assert result.iterations == 3
    assert result.tool_calls_made == 3
    assert result.final_response == _FINAL_SYNTHESIS_TEXT
    assert client.no_tools_calls == 1
    assert len(client.chat_calls) == 4


def test_synthesis_nudge_fires_for_small_cap_after_tool_calls():
    client = _SmallCapToolClient(tool_turns=5)
    loop = AgentLoop(client, model="test/model")

    result = loop.run("collect evidence", tools=[_read_tool()], max_iterations=6)

    assert result.final_response == "final answer"
    assert result.halted_reason == "natural"
    assert any(
        m.get("role") == "system" and _SYNTHESIS_MARKER in (m.get("content") or "")
        for m in result.messages
    )
    assert any(
        m.get("role") == "system" and _SYNTHESIS_MARKER in (m.get("content") or "")
        for m in client.chat_messages[3]
    )


def test_trivial_short_loop_is_unaffected_by_synthesis_changes():
    client = _ImmediateTextClient()
    loop = AgentLoop(client, model="test/model")

    result = loop.run("hello", max_iterations=3)

    assert result.final_response == "done"
    assert result.halted_reason == "natural"
    assert result.iterations == 1
    assert result.tool_calls_made == 0
    assert client.calls == 1
    assert not any(
        m.get("role") == "system" and _SYNTHESIS_MARKER in (m.get("content") or "")
        for m in result.messages
    )


def _parse_ask(monkeypatch, env_value: str | None = None) -> argparse.Namespace:
    from hydra.cli.cmd_ask import register_ask_command

    monkeypatch.delenv("HYDRA_ASK_MAX_ITERATIONS", raising=False)
    if env_value is not None:
        monkeypatch.setenv("HYDRA_ASK_MAX_ITERATIONS", env_value)
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register_ask_command(subparsers)
    return parser.parse_args(["ask", "hello"])


def test_ask_default_max_iterations_is_sane_for_real_tasks(monkeypatch):
    args = _parse_ask(monkeypatch)

    assert args.max_iterations == 20


def test_ask_max_iterations_default_can_be_overridden_by_env(monkeypatch):
    args = _parse_ask(monkeypatch, "12")

    assert args.max_iterations == 12
