"""Exercise the shipped UI, real AgentLoop, filesystem tools, and memory corpus.

Only the model/embedding transport is scripted. No AgentLoop, retrieval, learning,
tool invocation, or UI turn method is replaced. These contracts test wiring and
observed execution, not a model's answer quality.
"""
from __future__ import annotations

import asyncio
import copy
import json
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateways.tui.elite import EliteTUI
from gateways.tui.hydra_app import HydraApp
from hydra.cli.tool_binding import bind_tools
from hydra.execution_learning import execution_records
from hydra.lessons import remember_lesson
from hydra.llm import ChatResponse, ToolCall
from hydra.semantic_recall import build_semantic_memory_context
from hydra.work_executor import resolve_work_model


def unavailable_embedding(text):
    raise RuntimeError("test embedding transport offline")


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def chat(self, messages, *, model, tools=None, **kwargs):
        self.requests.append({"messages": copy.deepcopy(messages), "tools": tools, **kwargs})
        response = self.responses.pop(0) if self.responses else "Observed the file."
        calls = [response] if isinstance(response, ToolCall) else []
        return ChatResponse(content="" if calls else response, model=model,
                            finish_reason="tool_calls" if calls else "stop",
                            prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=calls)


def read_call(path="receipt.txt"):
    args = {"path": path}
    return ToolCall(id="read-receipt", name="fs_read", arguments_raw=json.dumps(args), arguments=args)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    (root / "receipt.txt").write_text("Actual source: argon transport tango.\n")
    memory = tmp_path / "memory"
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    return root, memory


def make_tui(workspace, client, *, enabled=True, max_chars=700):
    root, memory = workspace
    pair = resolve_work_model("steering") or ("scripted", "scripted")
    tools = [t for t in bind_tools(root, memory_root=memory, include_mcp=False) if t.name == "fs_read"]
    return EliteTUI(client, pair[1], SimpleNamespace(name=pair[0]), root, "Test grounded memory.",
                    tools=tools, memory_root=memory, memory_workspace_root=root,
                    recall_builder=partial(build_semantic_memory_context, embedder=unavailable_embedding)
                    if enabled else None, local_memory_chars=max_chars, max_iterations=4,
                    initial_messages=[])


def launch_app(tui):
    apps = []

    class HeadlessApp(HydraApp):
        def run(self, **kwargs):
            apps.append(self)

    # Launch exactly through the production Elite -> Textual constructor seam.
    tui._launch_textual_app(HeadlessApp)
    return apps[0]


async def submit(app, text):
    async with app.run_test(size=(100, 30)) as pilot:
        app.query_one("#operator-input").value = text
        await pilot.press("enter")
        await asyncio.wait_for(app.workers.wait_for_complete(), timeout=10)
        await pilot.pause()
        assert not app._turn_in_flight
        assert not any(role == "system" and "turn error" in content for role, content in app._chat_lines)


def test_textual_conversation_recalls_bounded_memory_without_tools(workspace):
    _, memory = workspace
    remember_lesson("Argon bridge uses transport tango.", source="operator", memory_root=memory)
    client = ScriptedModel("The stored bridge note names tango.")
    app = launch_app(make_tui(workspace, client))
    asyncio.run(submit(app, "What do you remember about the argon bridge?"))
    request = client.requests[0]
    recalled = [m["content"] for m in request["messages"] if "STORED MEMORY" in str(m.get("content", ""))]
    assert recalled and "transport tango" in recalled[0]
    assert len(recalled[0]) <= 700
    assert not request["tools"], "passive recall must not grant conversation tool execution"
    assert not any("STORED MEMORY" in str(m.get("content", "")) for m in app._chat_messages)
    assert execution_records(memory, workspace[0]) == []


def test_textual_work_is_learned_and_recalled_in_a_fresh_ui_session(workspace):
    root, memory = workspace
    first = ScriptedModel(read_call(), "I verified the whole world flawless.")
    app = launch_app(make_tui(workspace, first, max_chars=4000))
    asyncio.run(submit(app, "run inspection of receipt.txt for argon"))
    records = execution_records(memory, root)
    assert len(records) == 1
    record = records[0][1]
    assert record["observations"][0]["tool"] == "fs_read"
    assert record["quality_verified"] is False
    assert "flawless" not in json.dumps(record)
    assert any(m.get("role") == "tool" for m in app._chat_messages)
    second = ScriptedModel("A prior file inspection is recorded, with quality unverified.")
    fresh = launch_app(make_tui(workspace, second, max_chars=4000))
    asyncio.run(submit(fresh, "What do you remember about receipt.txt argon inspection?"))
    context = "\n".join(str(m.get("content", "")) for m in second.requests[0]["messages"])
    assert "fs_read" in context and "quality_verified=False" in context
    assert not second.requests[0]["tools"]


def test_textual_forced_rerun_preserves_actual_tool_messages_and_learning(workspace):
    root, memory = workspace
    client = ScriptedModel("I completed the inspection.", read_call(), "The source was read.")
    app = launch_app(make_tui(workspace, client, max_chars=4000))
    asyncio.run(submit(app, "run receipt.txt inspection"))
    assert len(client.requests) == 3
    records = execution_records(memory, root)
    assert len(records) == 1 and len(records[0][1]["observations"]) == 1
    assert any(m.get("role") == "tool" and "transport tango" in str(m.get("content", ""))
               for m in app._chat_messages)


def test_explicit_no_memory_disables_recall_and_execution_learning(workspace):
    root, memory = workspace
    remember_lesson("Secret historical argon transport quasar.", source="operator", memory_root=memory)
    client = ScriptedModel(read_call(), "Observed the source.")
    app = launch_app(make_tui(workspace, client, enabled=False))
    asyncio.run(submit(app, "run argon receipt.txt inspection"))
    assert "quasar" not in json.dumps(client.requests)
    assert execution_records(memory, root) == []


def test_legacy_work_and_forced_retry_also_learn_actual_execution(workspace):
    root, memory = workspace
    client = ScriptedModel("I completed the inspection.", read_call(), "Observed the source.")
    tui = make_tui(workspace, client)
    tui._stream_turn("run receipt.txt inspection", source="peer:reviewer")
    assert len(execution_records(memory, root)) == 1
    assert any(m.get("role") == "tool" for m in tui._chat_messages)


def test_default_and_missing_yaml_conversation_profiles_allow_passive_memory(tmp_path):
    from hydra.turn_profiles import load_turn_profile
    for profile in (load_turn_profile("convo"), load_turn_profile("convo", tmp_path / "missing.yaml")):
        assert profile.memory.enabled
        assert not profile.tools_enabled


def test_optional_chat_context_reads_requested_workspace(workspace):
    from hydra.cli.cmd_chat import _truth_context_message
    root, memory = workspace
    wiki = root / ".hydraAgent/wiki"
    wiki.mkdir(parents=True)
    (wiki / "argon.md").write_text("# argon workspace\nWorkspace transport uses tango.\n")
    message = _truth_context_message("argon workspace", memory, budget_chars=2000, workspace_root=root)
    assert message and "Workspace transport uses tango" in message["content"]
