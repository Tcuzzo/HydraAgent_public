"""Legacy receipts follow the chosen route and retain its real execution evidence."""
import json
from types import SimpleNamespace

import pytest

from gateways.tui.elite import EliteTUI
from hydra.llm import ToolCall
from hydra.loop import Tool
from hydra.test_tui_memory_runtime import ScriptedModel
from hydra.work_executor import resolve_work_model
from skills.bash import run as run_bash


@pytest.mark.parametrize("intent", ["chat", "task"])
def test_legacy_selected_route_preserves_actual_result(tmp_path, monkeypatch, intent):
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    pair = resolve_work_model("steering")
    args = {"command": "exit 7"}
    call = ToolCall("command-failure", "bash", json.dumps(args), args)
    model = ScriptedModel("Conversational answer.") if intent == "chat" else ScriptedModel(
        "Discarded speculative answer.", call, "The command failed.")
    tool = Tool("bash", "Test command", {"type": "object", "properties": {"command": {"type": "string"}}},
                lambda command: run_bash(command, root=tmp_path))
    trace = tmp_path / "trace.jsonl"
    tui = EliteTUI(model, pair[1], SimpleNamespace(name=pair[0]), tmp_path, "Test", tools=[tool],
                   initial_messages=[], trace_out_path=trace)
    tui._intent_client_cache = SimpleNamespace(chat=lambda **kwargs: SimpleNamespace(content=intent))
    final = tui._stream_turn("Hello operator")
    row = json.loads(trace.read_text())
    assert row["trace_id"] and row["halted_reason"] == "natural"
    assert row["final_response"] == final
    assert row["iterations"] == (1 if intent == "chat" else 2)
    assert row["tool_calls_made"] == (0 if intent == "chat" else 1)
    assert len(row["tool_steps"]) == (0 if intent == "chat" else 1)
    assert not any("Discarded speculative answer" in str(m.get("content", ""))
                   for m in tui._chat_messages)
    assert not any("CONVO_NO_TOOLS_GUARD" in str(m.get("content", ""))
                   for m in tui._chat_messages)
