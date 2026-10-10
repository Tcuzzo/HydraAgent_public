from types import SimpleNamespace

import pytest

from hydra.__main__ import _build_parser
from hydra.cli import cmd_ask as module
from hydra.llm import ChatResponse, ToolCall
from hydra.llm import LlmError
from hydra.loop import Tool


@pytest.mark.parametrize("exhausted,expected", [(True, 1), (False, 0)])
def test_cli_reports_real_loop_completion(tmp_path, monkeypatch, exhausted, expected):
    class Client:
        def chat(self, messages, *, model, tools=None, **kwargs):
            calls = [ToolCall(id="read-1", name="read", arguments_raw="{}", arguments={})] if exhausted and tools else []
            return ChatResponse(content="Partial summary" if exhausted else "Finished", model=model,
                                finish_reason="stop", prompt_tokens=0, completion_tokens=0, raw={}, tool_calls=calls)

    args = _build_parser().parse_args(["ask", "inspect repository", "--provider", "ollama", "--root", str(tmp_path), "--max-iterations", "1", "--memory-root", str(tmp_path / "memory")])
    monkeypatch.setattr(module, "_make_client_or_setup", lambda _: (Client(), SimpleNamespace(name="ollama", model="model")))
    monkeypatch.setattr(module, "_bind_tools", lambda *a, **kw: [Tool("read", "read", {"type": "object"}, lambda: {"content": "source"})])
    assert module.cmd_ask(args) == expected


@pytest.mark.parametrize("flag,value", [("--max-iterations", "0"), ("--timeout", "0"), ("--timeout", "nan"), ("--timeout", "inf")])
def test_invalid_run_budget_fails_before_model_setup(tmp_path, monkeypatch, flag, value):
    args = _build_parser().parse_args(["ask", "inspect", "--root", str(tmp_path), flag, value])
    monkeypatch.setattr(module, "_make_client_or_setup", lambda _: pytest.fail("must validate before model setup"))
    assert module.cmd_ask(args) == 2


def test_missing_optional_model_reports_error_without_traceback(tmp_path, monkeypatch, capsys):
    args = _build_parser().parse_args(["ask", "inspect", "--root", str(tmp_path), "--provider", "needle"])
    def unavailable(_):
        raise LlmError("install hydraagent[needle]")
    monkeypatch.setattr(module, "_make_client_or_setup", unavailable)
    assert module.cmd_ask(args) == 2
    assert "install hydraagent[needle]" in capsys.readouterr().err
