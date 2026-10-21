"""Regression contracts for reviewed UI state and interrupted-provider evidence."""
import argparse
import asyncio
import json
import threading
from types import SimpleNamespace

from gateways.tui.hydra_app import HydraApp
from hydra.llm import ChatResponse
from hydra.test_tui_memory_runtime import ScriptedModel, launch_app


class PausedModel(ScriptedModel):
    def __init__(self):
        super().__init__("First answer.")
        self.started = threading.Event()
        self.release = threading.Event()

    def chat(self, *args, **kwargs):
        self.started.set()
        if not self.release.wait(timeout=10):
            raise RuntimeError("probe did not release the model transport")
        return super().chat(*args, **kwargs)


def test_rejected_busy_input_does_not_replace_persisted_active_prompt(tmp_path, monkeypatch):
    from hydra import session_memory
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path / "sessions")
    session_memory.create_session("probe", "review")
    client = PausedModel()
    def persist(user, reply):
        session_memory.add_message("probe", "user", user)
        session_memory.add_message("probe", "assistant", reply)
    app = HydraApp(client=client, model="scripted", cfg=SimpleNamespace(name="scripted"),
                   system_prompt="Test", tools=[], workspace_root=tmp_path, on_persist=persist)
    first, rejected = "What is the first question?", "What is the second question?"
    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one("#operator-input").value = first
            await pilot.press("enter")
            assert await asyncio.to_thread(client.started.wait, 3)
            app.query_one("#operator-input").value = rejected
            await pilot.press("enter")
            client.release.set()
            await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            await pilot.pause()
    try:
        asyncio.run(run())
    finally:
        client.release.set()
    rows = session_memory.get_session_messages("probe")
    users = [row["content"] for row in rows if row["role"] == "user"]
    assert len(client.requests) == 1
    assert users == [first], {"persisted_users": users, "actual_model_input": client.requests[0]["messages"]}


def make_live_chat(tmp_path, monkeypatch, *, truth_context=False):
    from hydra.cli import cmd_chat
    from gateways.tui.elite import EliteTUI
    from hydra import session_memory
    root = tmp_path / "first"
    root.mkdir()
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path / "sessions")
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    first_client, second_client = ScriptedModel("First model response."), ScriptedModel("Second model response.")
    def make_client(provider, **kwargs):
        second = provider == "ollama"
        return (second_client if second else first_client,
                SimpleNamespace(name=provider, model="second-model" if second else "first-model"))
    monkeypatch.setattr(cmd_chat, "make_client", make_client)
    apps, shells = [], []
    class CapturedElite(EliteTUI):
        def run(self):
            shells.append(self)
            apps.append(launch_app(self))
    monkeypatch.setattr(cmd_chat, "EliteTUI", CapturedElite)
    args = argparse.Namespace(root=str(root), approval_policy="deny", memory_root=str(tmp_path / "memory"),
        no_local_memory=not truth_context, local_memory_chars=4000, with_context=False, truth_context=truth_context,
        context_budget_bytes=4096, trace_out=None, profile="cloud", provider="ollama-cloud", model=None,
        env_dir=None, setup_if_needed=False, session_history_limit=0, max_iterations=4, timeout=5.0)
    return cmd_chat, args, root, apps, shells, first_client, second_client


def test_live_profile_switch_rebinds_the_textual_model(tmp_path, monkeypatch):
    cmd_chat, args, root, apps, shells, first_client, second_client = make_live_chat(tmp_path, monkeypatch)
    assert cmd_chat.cmd_chat(args) == 0
    app, shell = apps[0], shells[0]
    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one("#operator-input").value = "/local"
            await pilot.press("enter")
            app.query_one("#operator-input").value = "What is your current model?"
            await pilot.press("enter")
            await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            await pilot.pause()
    asyncio.run(run())
    assert shell.model == "second-model"
    assert len(second_client.requests) == 1 and len(first_client.requests) == 0, {
        "shell_model": shell.model, "live_model": app.model,
        "old_requests": len(first_client.requests), "new_requests": len(second_client.requests)}


def test_root_change_drops_prior_workspace_truth_context(tmp_path, monkeypatch):
    cmd_chat, args, first, apps, shells, client, _ = make_live_chat(tmp_path, monkeypatch, truth_context=True)
    wiki = first / ".hydraAgent/wiki"
    wiki.mkdir(parents=True)
    (wiki / "state.md").write_text("# Hydra mission memory\nprivate_first_workspace_marker uses argon.\n")
    second = tmp_path / "second"
    second.mkdir()
    def offline_embedding(*args):
        raise RuntimeError("scripted embedding transport offline")
    config = tmp_path / "embedding.json"
    config.write_text(json.dumps({"backend": "ollama", "model": "test", "revision": "test-v1", "endpoint": "http://127.0.0.1:1"}))
    monkeypatch.setenv("HYDRA_EMBEDDING_CONFIG", str(config))
    monkeypatch.setattr("hydra.embeddings.embed", offline_embedding)
    assert cmd_chat.cmd_chat(args) == 0
    app = apps[0]
    assert "private_first_workspace_marker" in json.dumps(app._chat_messages)
    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one("#operator-input").value = f"/root {second}"
            await pilot.press("enter")
            app.query_one("#operator-input").value = "What is the workspace context?"
            await pilot.press("enter")
            await asyncio.wait_for(app.workers.wait_for_complete(), 10)
            await pilot.pause()
    asyncio.run(run())
    assert app.workspace_root == second
    assert "private_first_workspace_marker" not in json.dumps(client.requests[0]["messages"])


def test_provider_fallback_retains_first_attempt_tool_evidence(tmp_path, monkeypatch):
    import copy
    from hydra.__main__ import _build_parser
    from hydra.cli import cmd_chat, cmd_ask
    from hydra.execution_learning import execution_records
    from hydra.llm import LlmError
    from hydra.test_tui_memory_runtime import read_call
    (tmp_path / "receipt.txt").write_text("actual_first_attempt_artifact")
    memory = tmp_path / "memory"
    class InterruptingModel(ScriptedModel):
        def chat(self, messages, **kwargs):
            if self.requests:
                self.requests.append({"messages": copy.deepcopy(messages)})
                raise LlmError("scripted external transport interruption")
            return super().chat(messages, **kwargs)
    first = InterruptingModel(read_call())
    second = ScriptedModel("I have no tool observations in this retry.")
    def make_client(provider, **kwargs):
        return (second if provider == "ollama" else first,
                SimpleNamespace(name=provider, model="retry-model" if provider == "ollama" else "initial-model"))
    monkeypatch.setattr(cmd_chat, "make_client", make_client)
    args = _build_parser().parse_args(["ask", "Read receipt.txt for the prior-attempt proof", "--provider", "scripted",
        "--root", str(tmp_path), "--memory-root", str(memory), "--approval-policy", "deny", "--max-iterations", "3"])
    assert cmd_ask.cmd_ask(args) == 0
    assert any(m.get("role") == "tool" and "actual_first_attempt_artifact" in str(m.get("content"))
               for m in first.requests[-1]["messages"])
    assert any(m.get("role") == "tool" and "actual_first_attempt_artifact" in str(m.get("content"))
               for m in second.requests[0]["messages"])
    records = execution_records(memory, tmp_path)
    assert records and any(row["tool"] == "fs_read" for _, record in records for row in record["observations"]), {
        "actual_first_attempt_tools": [m for m in first.requests[-1]["messages"] if m.get("role") == "tool"],
        "retained_episodes": records,
        "retry_messages": second.requests[0]["messages"]}
