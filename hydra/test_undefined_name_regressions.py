"""Regression contracts for three names ruff F821 reports as undefined."""
import typing
from pathlib import Path

import gateways.tui.elite as elite
from hydra import resolution_gate
from hydra.cli import cmd_chat


def test_plain_welcome_banner_renders_without_rich(monkeypatch, capsys):
    monkeypatch.setattr(elite, "RICH_AVAILABLE", False)
    tui = object.__new__(elite.EliteTUI)
    tui.tools = []
    tui.model_short = "m"
    tui.provider_name = "p"
    tui._skill_count = 0
    tui._print_welcome()
    out = capsys.readouterr().out
    assert "m  •  p  •  " in out


def test_chat_status_intent_reports_instead_of_crashing(capsys):
    runtime = {"profile": "x", "provider": "p", "model": "m", "local_worker_provider": "p"}
    handled = cmd_chat._handle_chat_operator_intent("/status", runtime, Path("."), "ask")
    assert handled is True
    captured = capsys.readouterr()
    assert "self-check" in (captured.out + captured.err)


def test_subprocess_runner_type_hints_resolve():
    hints = typing.get_type_hints(resolution_gate.make_subprocess_test_runner)
    assert "repo_root" in hints
