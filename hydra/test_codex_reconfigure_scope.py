"""Changing provider through /setup retains the active workspace permissions."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra.test_codex_setup_e2e import _fake_codex


@pytest.mark.parametrize("policy,sandbox", [
    ("deny", "read-only"),
    ("ask", "read-only"),
    ("allow", "workspace-write"),
])
def test_setup_switch_binds_native_execution_to_the_live_scope(monkeypatch, tmp_path, policy, sandbox):
    from gateways.tui import elite

    launch = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    launch.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(launch)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("HYDRA_CODEX_BIN", str(_fake_codex(tmp_path)))
    monkeypatch.setenv("CODEX_TEST_RECORD", str(tmp_path / "invocation.json"))
    monkeypatch.setenv("HYDRA_MODEL_HEALTH_PATH", str(tmp_path / "health.json"))
    monkeypatch.setattr(elite, "OperatorAuth", lambda: SimpleNamespace())
    monkeypatch.setattr(elite, "_startup_self_audit", lambda: None)
    # Replace account selection only; /setup, provider factory and the native
    # subprocess invocation all remain the production path.
    monkeypatch.setattr("hydra.setup_panel.run_setup_panel", lambda *args, **kwargs: "codex")
    tui = elite.EliteTUI(
        client=SimpleNamespace(), model="existing-chat-model",
        cfg=SimpleNamespace(name="existing-provider"), root=workspace,
        system_prompt="Use the requested workspace.", tools=[], approval_policy=policy,
    )

    tui._cmd_setup()
    reply = tui.client.chat([{"role": "user", "content": "Inspect the workspace."}], model=tui.model)

    assert reply.content == "Ready from the configured CLI transport."
    args = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))["args"]
    actual = (
        Path(args[args.index("-C") + 1]).resolve(),
        args[args.index("--sandbox") + 1] if "--sandbox" in args else None,
    )
    assert actual == (workspace.resolve(), sandbox)


def test_root_after_setup_rebinds_the_new_native_client(monkeypatch, tmp_path):
    from gateways.tui import elite
    from hydra import session_memory
    from hydra.__main__ import main

    initial = tmp_path / "initial"
    switched = tmp_path / "switched"
    initial.mkdir()
    switched.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("HYDRA_CODEX_BIN", str(_fake_codex(tmp_path)))
    monkeypatch.setenv("CODEX_TEST_RECORD", str(tmp_path / "invocation.json"))
    monkeypatch.setenv("HYDRA_MODEL_HEALTH_PATH", str(tmp_path / "health.json"))
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path / "sessions")
    monkeypatch.setattr(elite, "OperatorAuth", lambda: SimpleNamespace())
    monkeypatch.setattr(elite, "_startup_self_audit", lambda: None)
    monkeypatch.setattr("hydra.setup_panel.run_setup_panel", lambda *args, **kwargs: "codex")

    def run_commands(tui):
        previous = tui.client
        tui._cmd_setup()
        assert tui.client is not previous
        assert tui._command_handler(tui, f"/root {switched}")
        tui.client.chat([{"role": "user", "content": "Inspect the workspace."}], model=tui.model)

    monkeypatch.setattr(elite.EliteTUI, "run", run_commands)
    assert main([
        "chat", "--provider", "codex", "--root", str(initial),
        "--approval-policy", "deny", "--no-local-memory",
        "--memory-root", str(tmp_path / "memory"),
    ]) == 0
    args = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))["args"]
    actual = (
        Path(args[args.index("-C") + 1]).resolve(),
        args[args.index("--sandbox") + 1] if "--sandbox" in args else None,
    )
    assert actual == (switched.resolve(), "read-only")
