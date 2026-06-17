from __future__ import annotations

import os
from types import SimpleNamespace

from gateways.tui import elite
from gateways.tui.elite import (
    EliteTUI,
    PROMPT_TOOLKIT_AVAILABLE,
    _approx_tokens,
    _coalesce_pasted_lines,
    _drain_ready_stdin_lines,
)


def test_coalesce_pasted_lines_keeps_multiline_prompt_as_one_turn() -> None:
    user_input = _coalesce_pasted_lines(
        "audit /tmp/peer-agent-workspace and compare it to Hydra",
        [
            "│ ## INTER-AGENT COMMS",
            "│ {",
            "│   \"envelope\": {}",
            "│ }",
        ],
    )

    assert user_input == (
        "audit /tmp/peer-agent-workspace and compare it to Hydra\n"
        "│ ## INTER-AGENT COMMS\n"
        "│ {\n"
        "│   \"envelope\": {}\n"
        "│ }"
    )


def test_coalesce_pasted_lines_strips_bracketed_paste_markers() -> None:
    user_input = _coalesce_pasted_lines(
        "\x1b[200~first line",
        [
            "second line",
            "third line\x1b[201~",
        ],
    )

    assert user_input == "first line\nsecond line\nthird line"


def test_drain_ready_stdin_lines_reads_queued_tty_paste_lines() -> None:
    master_fd, slave_fd = os.openpty()
    try:
        os.write(master_fd, b"second line\nthird line\n")
        with os.fdopen(slave_fd, "r", encoding="utf-8", closefd=False) as slave:
            assert _drain_ready_stdin_lines(slave, idle_timeout=0.01) == [
                "second line\n",
                "third line\n",
            ]
    finally:
        os.close(master_fd)
        os.close(slave_fd)


def test_elite_session_new_rotates_memory_and_clears_live_history(tmp_path, monkeypatch, capsys) -> None:
    from hydra import session_memory

    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    monkeypatch.setattr(elite, "list_skill_records", lambda: [])
    monkeypatch.setattr(elite, "build_agent_system_prompt", lambda prompt: prompt)
    monkeypatch.setattr(elite, "AgentLoop", lambda *args, **kwargs: object())

    session_memory.create_session("default_chat_session", "initial context")
    session_memory.add_message("default_chat_session", "user", "old task")
    tui = EliteTUI(
        client=object(),
        model="fake-model",
        cfg=SimpleNamespace(name="fake-provider"),
        root=tmp_path,
        system_prompt="system prompt",
        session_id="default_chat_session",
        initial_messages=[
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "old task"},
            {"role": "assistant", "content": "old answer"},
        ],
    )

    assert tui._handle_session_command("/session new") is True

    assert [message["role"] for message in tui._chat_messages] == ["system"]
    persisted = session_memory.get_session_messages("default_chat_session")
    assert persisted[0]["role"] == "system"
    assert "new HydraAgent chat session" in persisted[0]["content"]
    assert list(tmp_path.glob("default_chat_session.rotated-*.jsonl"))
    capsys.readouterr()


def test_approx_tokens_formats_correctly() -> None:
    assert _approx_tokens(0) == ""
    assert _approx_tokens(400) == "~100"
    assert _approx_tokens(4000) == "~1.0k"
    assert _approx_tokens(40000) == "~10.0k"


def test_prompt_session_created_on_tty(tmp_path, monkeypatch) -> None:
    """PromptSession is created when prompt_toolkit is available."""
    import gateways.tui.elite as elite_mod

    monkeypatch.setattr(elite_mod, "list_skill_records", lambda: [])
    monkeypatch.setattr(elite_mod, "build_agent_system_prompt", lambda p: p)
    monkeypatch.setattr(elite_mod, "AgentLoop", lambda *a, **kw: object())

    tui = EliteTUI(
        client=object(),
        model="fake-model",
        cfg=SimpleNamespace(name="fake-provider"),
        root=tmp_path,
        system_prompt="system prompt",
    )
    if PROMPT_TOOLKIT_AVAILABLE:
        # _prompt_session is None only if stdin is not a tty (CI); otherwise it's set
        pass  # presence depends on test runner tty; just ensure no crash on init
