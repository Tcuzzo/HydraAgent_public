"""Loop-debug fixes batch 2 (2026-06-07) — L2, L7, L9, L10, L11, L12, L13, L14.

Each test is TDD-style: it encodes a confirmed bug, FAILS on pre-fix code, and
PASSES after the minimal root-cause fix. No fake-green.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared fake LLM plumbing (mirrors test_loop_bridge.py style)
# ---------------------------------------------------------------------------
from hydra.llm import ChatResponse
from hydra.loop import AgentLoop, LoopResult


class _SimpleFakeClient:
    """Returns a plain-text answer immediately (no tool calls)."""

    def chat(self, _messages, *, model, max_tokens=0, temperature=0.0,
             timeout=0.0, tools=None):
        return ChatResponse(
            content="done",
            model=model,
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            raw={},
            tool_calls=[],
        )


def _make_loop() -> AgentLoop:
    return AgentLoop(_SimpleFakeClient(), model="test/model")


# ===========================================================================
# L2 — trace-context accumulation
# ===========================================================================
_TRACE_MARKER = "Hydra inter-agent trace context"


def test_L2_trace_context_does_not_accumulate():
    """Feeding result.messages back as initial_messages must NOT grow the
    number of trace-context system messages beyond one per run()."""
    loop = _make_loop()

    result1 = loop.run("hello", max_iterations=3)
    # First run: exactly one trace-context message expected.
    count1 = sum(
        1 for m in result1.messages
        if m.get("role") == "system" and _TRACE_MARKER in m.get("content", "")
    )
    assert count1 == 1, f"First run should have exactly 1 trace-context msg, got {count1}"

    # Second run with result1.messages fed back.
    result2 = loop.run("hello again", max_iterations=3,
                       initial_messages=result1.messages)
    count2 = sum(
        1 for m in result2.messages
        if m.get("role") == "system" and _TRACE_MARKER in m.get("content", "")
    )
    assert count2 == 1, (
        f"Second run (messages fed back) should still have exactly 1 "
        f"trace-context msg, got {count2}"
    )


# ===========================================================================
# L9 — synthesis nudge accumulation
# ===========================================================================
_SYNTHESIS_MARKER = "SYNTHESIS REQUIRED"


class _ToolCallingFakeClient:
    """Returns a dummy tool call for the first N-1 turns, then plain text.

    Used to drive the loop to synthesis_iteration (max_iterations - 2) so the
    synthesis nudge fires, letting us verify it does not accumulate.
    """

    def __init__(self, total_tool_calls: int, tool_name: str = "noop") -> None:
        self._total = total_tool_calls
        self._calls = 0
        self._tool_name = tool_name

    def chat(self, msgs, *, model, max_tokens=0, temperature=0.0,
             timeout=0.0, tools=None):
        self._calls += 1
        if self._calls <= self._total:
            from hydra.llm import ToolCall as _TC
            tc = _TC(
                id=f"tc-{self._calls}",
                name=self._tool_name,
                arguments_raw="{}",
                arguments={},
            )
            return ChatResponse(
                content="",
                model=model,
                finish_reason="tool_calls",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[tc],
            )
        return ChatResponse(
            content="final answer",
            model=model,
            finish_reason="stop",
            prompt_tokens=0,
            completion_tokens=0,
            raw={},
            tool_calls=[],
        )


def test_L9_synthesis_nudge_does_not_accumulate():
    """Two qualifying runs fed back must never produce more than 1 synthesis
    nudge in the returned LoopResult.messages.

    Strategy: drive the first run() to synthesis_iteration so the nudge fires
    and lands in result.messages; then feed those messages back as
    initial_messages for a second run that also reaches synthesis_iteration —
    the fix must strip the prior nudge before appending so the count stays at 1.
    """
    # Drive the loop to iteration 13 (synthesis_iteration = 15 - 2):
    # need 12 tool calls before a plain answer.  Register a noop tool.
    from hydra.loop import Tool

    noop_tool = Tool(
        name="noop",
        description="no-op tool",
        parameters={"type": "object", "properties": {}},
        invoke=lambda: {},
    )

    # ---- Run 1: fires synthesis nudge at iteration 13 ----------------------
    client1 = _ToolCallingFakeClient(total_tool_calls=12)
    loop1 = AgentLoop(client1, model="test/model")
    result1 = loop1.run(
        "collect data",
        tools=[noop_tool],
        max_iterations=15,
    )
    synthesis_count_1 = sum(
        1 for m in result1.messages
        if m.get("role") == "system" and _SYNTHESIS_MARKER in m.get("content", "")
    )
    # The nudge should have fired exactly once on run 1.
    assert synthesis_count_1 >= 1, (
        "Synthesis nudge did not fire in run 1 — test setup is wrong"
    )

    # ---- Run 2: feed run1 messages back; synthesis fires again at iter 13 --
    client2 = _ToolCallingFakeClient(total_tool_calls=12)
    loop2 = AgentLoop(client2, model="test/model")
    result2 = loop2.run(
        "collect more data",
        tools=[noop_tool],
        max_iterations=15,
        initial_messages=result1.messages,
    )
    synthesis_count_2 = sum(
        1 for m in result2.messages
        if m.get("role") == "system" and _SYNTHESIS_MARKER in m.get("content", "")
    )
    assert synthesis_count_2 <= 1, (
        f"After two qualifying runs (messages fed back), result2.messages has "
        f"{synthesis_count_2} synthesis nudge(s) — must be at most 1"
    )


# L7, L10, L11, L12, L13, L14 trimmed — they import optional loop modules not present in this lean-core build.
