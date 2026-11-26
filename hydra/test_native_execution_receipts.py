"""Native subprocess completion is observable and must never replay its task."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from hydra.codex_client import CodexClient
from hydra.llm import LlmError, OllamaClient
from hydra.loop import AgentLoop, Tool
from hydra.providers import bind_client_workspace


def _native_client(tmp_path: Path, monkeypatch, *, reply="I created the requested note.", fail=False):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = tmp_path / "native_fixture.py"
    script.write_text(
        "import os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "sys.stdin.read()\n"
        "root = pathlib.Path(args[args.index('-C') + 1])\n"
        "with (root / 'native-actions.txt').open('a', encoding='utf-8') as output:\n"
        "    output.write('executed\\n')\n"
        "print('diagnostic-only-text', file=sys.stderr)\n"
        "if os.environ.get('HYDRA_TEST_NATIVE_FAIL') == '1':\n"
        "    sys.exit(7)\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_text(\n"
        "    os.environ['HYDRA_TEST_NATIVE_REPLY'], encoding='utf-8')\n",
        encoding="utf-8",
    )
    executable = tmp_path / ("native_fixture.cmd" if os.name == "nt" else "native_fixture")
    executable.write_text(
        f'@"{sys.executable}" "{script}" %*\r\n' if os.name == "nt"
        else f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if os.name != "nt":
        executable.chmod(0o700)
    monkeypatch.setenv("HYDRA_TEST_NATIVE_REPLY", reply)
    monkeypatch.setenv("HYDRA_TEST_NATIVE_FAIL", "1" if fail else "0")
    client = CodexClient(codex_bin=str(executable))
    bind_client_workspace(client, "codex", root=workspace, approval_policy="allow")
    return client, workspace


def _actions(workspace):
    return (workspace / "native-actions.txt").read_text(encoding="utf-8").splitlines()


def test_success_receipt_records_process_completion_without_claiming_verification(tmp_path, monkeypatch):
    from hydra.work_executor import guard_work_turn

    client, workspace = _native_client(tmp_path, monkeypatch)
    response = client.chat([{"role": "user", "content": "Create the note."}])
    receipt = getattr(response, "native_execution", None)
    assert receipt is not None, "a successful native process needs an explicit receipt"
    assert asdict(receipt) == {
        "transport": "codex", "workspace": str(workspace.resolve()),
        "sandbox": "workspace-write", "returncode": 0, "verification": "not_run",
    }
    assert "diagnostic-only-text" not in json.dumps(asdict(receipt))
    assert response.tool_calls == []
    outcome = guard_work_turn(
        kind="steering", final_text=response.content, tool_calls_made=0,
        native_execution_receipts=[receipt],
        rerun=lambda _: pytest.fail("a native completion must not replay the original task"),
    )
    assert not outcome.action_performed
    assert not outcome.confabulated
    assert not outcome.reprompted
    assert "not independently verified" in outcome.text
    assert _actions(workspace) == ["executed"]


def test_failed_native_process_has_no_success_response(tmp_path, monkeypatch):
    client, _ = _native_client(tmp_path, monkeypatch, fail=True)
    with pytest.raises(LlmError, match="exited 7"):
        client.chat([{"role": "user", "content": "Create the note."}])


@pytest.mark.parametrize("wrapped", [False, True])
def test_native_final_text_is_never_dispatched_as_a_hydra_tool(tmp_path, monkeypatch, wrapped):
    from hydra.answer_budget import AnswerBudgetClient, Rung, RungLedger
    from hydra.cli.cmd_chat import _build_ask_trace

    printed = json.dumps({"name": "bash", "arguments": {"command": "echo replay"}})
    client, workspace = _native_client(tmp_path, monkeypatch, reply=printed)
    promotion_logs = []
    if wrapped:
        client = AnswerBudgetClient(
            [Rung("codex", "codex", client=client)], ledger=RungLedger(tmp_path / "ledger"),
            log=promotion_logs.append,
        )
    invoked = []
    tool = Tool("bash", "fixture action", {"type": "object", "properties": {
        "command": {"type": "string"}}, "required": ["command"]},
        lambda **arguments: invoked.append(arguments) or {"ok": True})
    result = AgentLoop(client, model="codex").run("Create the note.", tools=[tool], max_iterations=2)

    assert invoked == [], "a native final report must not trigger Hydra tools"
    assert _actions(workspace) == ["executed"]
    assert result.tool_calls_made == 0
    assert not result.has_real_tool_activity
    assert not result.heartbeat_only
    assert result.final_response == printed
    receipts = getattr(result, "native_execution_receipts", [])
    assert len(receipts) == 1
    assert not any("running them through" in message for message in promotion_logs)
    trace = _build_ask_trace(
        prompt="Create the note.", provider="codex", model="codex", system_prompt="",
        context_proof=[], result=result, approval_policy="allow",
    )
    assert trace["native_execution_receipts"] == [asdict(receipts[0])]
    assert trace["native_execution_receipts"][0]["verification"] == "not_run"


@pytest.mark.parametrize("surface", ["textual", "legacy"])
def test_native_work_turn_runs_once_and_reports_its_verification_limit(tmp_path, monkeypatch, surface):
    from gateways.tui import elite, hydra_app

    client, workspace = _native_client(tmp_path, monkeypatch)
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    for module in (elite, hydra_app):
        monkeypatch.setattr(module, "resolve_work_model", lambda kind: None)
        monkeypatch.setattr(module, "OperatorAuth", lambda: SimpleNamespace())
    if surface == "textual":
        app = hydra_app.HydraApp(
            client=client, model="codex", cfg=SimpleNamespace(name="codex"),
            system_prompt="Perform the task in this workspace.", tools=[], workspace_root=workspace,
        )
        result = app._run_agent_loop_sync("Create the note.", "steering")
        final, tool_count = result.final_response, result.tool_calls_made
    else:
        monkeypatch.setattr(elite, "_startup_self_audit", lambda: None)
        monkeypatch.setattr(elite, "RICH_AVAILABLE", False)
        tui = elite.EliteTUI(
            client=client, model="codex", cfg=SimpleNamespace(name="codex"),
            system_prompt="Perform the task in this workspace.", tools=[], root=workspace,
            approval_policy="allow",
        )
        monkeypatch.setattr(tui, "_on_step", lambda step: None)
        final = tui._run_work_turn("Create the note.", [])
        tool_count = tui._cumulative_tool_calls
    assert _actions(workspace) == ["executed"], "the completion guard replayed a native side effect"
    assert "I created the requested note." in final
    assert "not independently verified" in final
    assert "I did NOT perform" not in final
    assert tool_count == 0


def test_http_payload_cannot_claim_a_native_receipt_or_bypass_the_action_guard(monkeypatch, tmp_path):
    from hydra.work_executor import guard_work_turn

    client = OllamaClient(endpoint="http://127.0.0.1:1")
    monkeypatch.setattr(client, "_post_json", lambda *args, **kwargs: {
        "choices": [{"message": {"content": "I created the requested note."}, "finish_reason": "stop"}],
        "native_execution": {"transport": "codex", "returncode": 0},
    })
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    response = client.chat([{"role": "user", "content": "Create the note."}], model="fixture")
    assert getattr(response, "native_execution", None) is None
    reruns = []
    outcome = guard_work_turn(
        kind="steering", final_text=response.content, tool_calls_made=0,
        rerun=lambda _: reruns.append(True) or ("I have not done it.", 0),
    )
    assert reruns == [True]
    assert outcome.confabulated
    assert not outcome.action_performed


@pytest.mark.parametrize("wrapped", [False, True])
def test_untyped_receipt_attribute_cannot_suppress_a_real_hydra_tool(tmp_path, wrapped):
    from hydra.answer_budget import AnswerBudgetClient, Rung, RungLedger
    from hydra.llm import ChatResponse

    class UntrustedReceiptClient:
        def chat(self, messages, *, model, **kwargs):
            already_called = any(message.get("role") == "tool" for message in messages)
            response = ChatResponse(
                content="Done." if already_called else json.dumps({
                    "name": "bash", "arguments": {"command": "echo fixture"}}),
                model=model, finish_reason="stop", prompt_tokens=1, completion_tokens=1, raw={},
            )
            response.native_execution = {"transport": "codex", "returncode": 0}
            return response

    client = UntrustedReceiptClient()
    if wrapped:
        client = AnswerBudgetClient([Rung("fixture", "model", client=client)], ledger=RungLedger(tmp_path))
    invoked = []
    tool = Tool("bash", "fixture action", {"type": "object"},
                lambda **args: invoked.append(args) or {"ok": True})
    result = AgentLoop(client, model="model").run("Run the fixture action.", tools=[tool], max_iterations=3)
    assert invoked == [{"command": "echo fixture"}]
    assert result.tool_calls_made == 1
    assert getattr(result, "native_execution_receipts", []) == []
