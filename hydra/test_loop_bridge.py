#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hydra.llm import ChatResponse
from hydra.loop import AgentLoop, Tool


class _FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, _messages, *, model, max_tokens=0, temperature=0.0, timeout=0.0, tools=None):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content='function: read path="README.md"',
                model=model,
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[],
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


class TestLoopBridge(unittest.TestCase):
    def test_function_prefixed_output_dispatches_tool(self):
        seen: dict[str, str] = {}

        def fake_read(path: str) -> dict:
            seen["path"] = path
            return {"path": path}

        loop = AgentLoop(_FakeClient(), model="ollama/qwen2.5-coder:7b")
        result = loop.run(
            "Read the file",
            tools=[
                Tool(
                    name="read",
                    description="read file",
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                    invoke=fake_read,
                )
            ],
            max_iterations=3,
        )

        self.assertEqual(seen["path"], "README.md")
        self.assertEqual(result.tool_calls_made, 1)
        self.assertEqual(result.final_response, "done")

    def test_loop_reports_steps_to_observer(self):
        observed = []

        def fake_read(path: str) -> dict:
            return {"path": path}

        loop = AgentLoop(_FakeClient(), model="ollama/qwen2.5-coder:7b")
        result = loop.run(
            "Read the file",
            tools=[
                Tool(
                    name="read",
                    description="read file",
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                    invoke=fake_read,
                )
            ],
            max_iterations=3,
            on_step=observed.append,
        )

        self.assertEqual(result.final_response, "done")
        self.assertEqual([step.kind for step in observed], ["assistant", "tool_result", "assistant"])
        self.assertEqual(observed[1].tool_name, "read")


if __name__ == "__main__":
    unittest.main()
