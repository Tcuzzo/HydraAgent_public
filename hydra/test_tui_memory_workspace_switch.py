"""The real /root command must rebind live UI tools, recall, and learning."""
from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

from hydra.test_tui_memory_runtime import ScriptedModel, read_call, launch_app
from hydra.execution_learning import execution_records
from hydra.work_executor import resolve_work_model


def test_real_chat_root_command_rebinds_live_ui_and_keeps_memory_store(tmp_path, monkeypatch):
    from hydra.cli import cmd_chat
    from gateways.tui.elite import EliteTUI
    first, second, memory = (tmp_path / name for name in ("first", "second", "memory"))
    for root, name in ((first, "firstscope"), (second, "secondscope")):
        wiki = root / ".hydraAgent/wiki"
        wiki.mkdir(parents=True)
        (wiki / "scope.md").write_text(f"# scopedtoken\nscopedtoken means {name}.\n")
        (root / "receipt.txt").write_text(f"Actual artifact {name}.\n")
    monkeypatch.setattr("hydra.session_memory.SESSION_MEMORY_DIR", tmp_path / "sessions")
    config = tmp_path / "embedding.json"
    config.write_text(json.dumps({"backend": "ollama", "model": "test", "revision": "test-v1",
                                  "endpoint": "http://127.0.0.1:1"}))
    monkeypatch.setenv("HYDRA_EMBEDDING_CONFIG", str(config))

    def offline_embedding(*args):
        raise RuntimeError("scripted embedding transport offline")

    monkeypatch.setattr("hydra.embeddings.embed", offline_embedding)
    pair = resolve_work_model("steering") or ("scripted", "scripted")
    client = ScriptedModel("The first workspace note is present.", read_call(), "Observed the actual artifact.")
    monkeypatch.setattr(cmd_chat, "make_client", lambda *args, **kwargs:
                        (client, SimpleNamespace(name=pair[0], model=pair[1])))
    apps, shells = [], []

    class CapturedElite(EliteTUI):
        def run(self):
            shells.append(self)
            apps.append(launch_app(self))

    monkeypatch.setattr(cmd_chat, "EliteTUI", CapturedElite)
    args = argparse.Namespace(root=str(first), approval_policy="allow", memory_root=str(memory),
        no_local_memory=False, local_memory_chars=4000, with_context=False, truth_context=False,
        context_budget_bytes=4096, trace_out=None, profile="auto", provider=pair[0], model=pair[1],
        env_dir=None, setup_if_needed=False, session_history_limit=0, max_iterations=4, timeout=5.0)
    assert cmd_chat.cmd_chat(args) == 0
    app, shell = apps[0], shells[0]

    async def go():
        async with app.run_test(size=(100, 30)) as pilot:
            for text in ("What is scopedtoken?", f"/root {second}", "run receipt.txt scopedtoken inspection"):
                app.query_one("#operator-input").value = text
                await pilot.press("enter")
                await asyncio.wait_for(app.workers.wait_for_complete(), timeout=10)
                await pilot.pause()
            # Rejected scope changes must leave the accepted bindings intact.
            app.query_one("#operator-input").value = f"/root {tmp_path / 'missing'}"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(go())
    assert app.workspace_root == shell.root == second
    assert app.memory_workspace_root == shell.memory_workspace_root == second
    assert app.memory_root == shell.memory_root == memory
    first_request = json.dumps(client.requests[0]["messages"])
    work_request = json.dumps(client.requests[1]["messages"])
    assert "firstscope" in first_request and "secondscope" not in first_request
    assert "secondscope" in work_request and "firstscope" not in work_request
    tool_messages = [m for m in app._chat_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1 and "secondscope" in tool_messages[0]["content"]
    assert execution_records(memory, first) == []
    assert len(execution_records(memory, second)) == 1
