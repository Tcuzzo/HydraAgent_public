"""Cancellation revokes remaining proposals without hiding completed work."""
import json
import threading

from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop, Tool


def test_cancel_between_two_tools_keeps_first_receipt_and_revokes_second():
    cancellation = threading.Event()
    effects = []

    def first():
        effects.append("first")
        cancellation.set()
        return {"ok": True, "observed": "first completed"}

    def second():
        effects.append("second")
        return {"ok": True}

    class Transport:
        calls = 0

        def chat(self, messages, *, model, **kwargs):
            self.calls += 1
            return ChatResponse(content="", model=model, finish_reason="tool_calls",
                prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=[
                    ToolCall(id=name, name=name, arguments_raw="{}", arguments={})
                    for name in ("first", "second")])

    transport = Transport()
    result = AgentLoop(transport, model="test").run("execute both", [
        Tool(name="first", description="first operation", parameters={"type": "object", "properties": {}}, invoke=first),
        Tool(name="second", description="second operation", parameters={"type": "object", "properties": {}}, invoke=second),
    ], max_iterations=2, cancel_event=cancellation)
    assert effects == ["first"]
    assert transport.calls == 1
    assert result.halted_reason == "cancelled"
    assert result.tool_calls_made == 1
    receipts = [step for step in result.steps if step.kind == "tool_result"]
    assert len(receipts) == 1 and receipts[0].tool_name == "first"
    assert json.loads(receipts[0].content)["observed"] == "first completed"
