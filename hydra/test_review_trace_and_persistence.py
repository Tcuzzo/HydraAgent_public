"""Durable chat failures are visible; traces describe the actual current turn."""
import asyncio
import json
from types import SimpleNamespace

from gateways.tui.elite import EliteTUI
from hydra.llm import ToolCall
from hydra.loop import Tool
from hydra.test_tui_memory_runtime import ScriptedModel, launch_app
from hydra.work_executor import resolve_work_model
from skills.bash import run as run_bash


def test_textual_session_storage_failure_is_visible_to_operator(tmp_path, monkeypatch):
    import hydra.session_memory as memory
    sessions = tmp_path / "sessions"
    sessions.write_text("This is a file; session storage cannot create its directory.")
    monkeypatch.setattr(memory, "SESSION_MEMORY_DIR", sessions)
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    pair = resolve_work_model("steering")
    tui = EliteTUI(ScriptedModel("Hello operator."), pair[1], SimpleNamespace(name=pair[0]),
        tmp_path, "Test", session_id="test-session", tools=[], initial_messages=[])
    app = launch_app(tui)
    logs = []
    original_log = app._post_log
    def capture(markup):
        logs.append(markup)
        original_log(markup)
    app._post_log = capture
    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one("#operator-input").value = "Hello"
            await pilot.press("enter")
            await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            await pilot.pause()
    asyncio.run(run())
    assert ("hydra", "Hello operator.") in app._chat_lines
    assert any("session" in line.lower() and ("not saved" in line.lower() or "failed" in line.lower())
               for line in logs), logs


def test_legacy_trace_keeps_each_turn_tool_evidence_and_counts(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    pair = resolve_work_model("steering")
    tool = Tool("bash", "Test command", {"type": "object", "properties": {"command": {"type": "string"}}},
                lambda command: run_bash(command, root=tmp_path))
    call = ToolCall("failure", "bash", '{"command":"exit 7"}', {"command": "exit 7"})
    trace = tmp_path / "trace.jsonl"
    tui = EliteTUI(ScriptedModel(call, "The command failed.", call, "It failed again."), pair[1],
        SimpleNamespace(name=pair[0]), tmp_path, "Test", tools=[tool], initial_messages=[], trace_out_path=trace)
    tui._stream_turn("run exit 7", source="peer:test")
    tui._stream_turn("run exit 7 again", source="peer:test")
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(rows) == 2
    for row in rows:
        assert row["tool_calls_made"] == 1 and row["iterations"] == 2
        assert row["trace_id"] and row["halted_reason"] == "natural"
        assert len(row["tool_steps"]) == 1 and row["tool_steps"][0]["tool_error"]
