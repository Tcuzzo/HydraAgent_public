from __future__ import annotations

import argparse
import builtins
from pathlib import Path
from types import SimpleNamespace


def test_ask_binds_memory_tools_to_configured_memory_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from hydra.cli import cmd_ask

    seen: dict[str, object] = {}
    memory_root = tmp_path / "memory"

    class FakeLoop:
        def __init__(self, client: object, *, model: str, system_prompt: str | None = None) -> None:
            pass

        def run(self, user_prompt: str, tools: object, *, max_iterations: int, timeout: float):
            return SimpleNamespace(
                final_response="ok",
                iterations=1,
                tool_calls_made=0,
                halted_reason="natural",
                steps=[],
                messages=[],
            )

    def fake_bind_tools(
        root: Path,
        approval_policy: str,
        *,
        memory_root: str | Path | None = None,
        memory_workspace_root: str | Path | None = None,
    ):
        seen["root"] = root
        seen["approval_policy"] = approval_policy
        seen["memory_root"] = memory_root
        seen["memory_workspace_root"] = memory_workspace_root
        return []

    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda args: {"provider": "fake", "model": "fake-model"})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup", lambda args: ("client", SimpleNamespace(name="fake", model="fake-model")))
    monkeypatch.setattr(cmd_ask, "_bind_tools", fake_bind_tools)
    monkeypatch.setattr(cmd_ask, "AgentLoop", FakeLoop)

    rc = cmd_ask.cmd_ask(
        argparse.Namespace(
            prompt="hello",
            root=str(tmp_path),
            profile="auto",
            provider=None,
            model=None,
            env_dir=None,
            runtime_only=False,
            setup_if_needed=False,
            auto_route=False,
            with_context=False,
            truth_context=False,
            context_budget_bytes=4096,
            memory_root=str(memory_root),
            approval_policy="allow",
            max_iterations=1,
            timeout=1.0,
            trace_out=None,
            judge_rubric=None,
            judge_threshold=1.0,
            judge_out=None,
            judge_fail_exit=False,
        )
    )

    assert rc == 0
    assert seen["memory_root"] == memory_root
    capsys.readouterr()


def test_chat_session_projection_drops_empty_assistant_and_paste_fragments() -> None:
    from hydra.cli.cmd_chat import _llm_messages_from_session

    messages = _llm_messages_from_session(
        [
            {"role": "user", "content": "normal request"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "│                     ┌────────────┐                    │"},
            {"role": "assistant", "content": "normal answer"},
        ]
    )

    assert messages == [
        {"role": "user", "content": "normal request"},
        {"role": "assistant", "content": "normal answer"},
    ]


def test_chat_session_projection_omits_oversized_prior_assistant_artifacts() -> None:
    from hydra.cli.cmd_chat import _llm_messages_from_session

    oversized_artifact = "INTER-AGENT COMMUNICATION PROTOCOL\n" + ("x" * 5000)

    messages = _llm_messages_from_session(
        [
            {"role": "user", "content": "before"},
            {"role": "assistant", "content": oversized_artifact},
            {"role": "user", "content": "after"},
            {"role": "assistant", "content": "next answer"},
        ]
    )

    assert messages == [
        {"role": "user", "content": "before"},
        {
            "role": "assistant",
            "content": "[prior assistant response omitted from live context: 5035 chars]",
        },
        {"role": "user", "content": "after"},
        {"role": "assistant", "content": "next answer"},
    ]


def test_chat_session_projection_drops_unanswered_trailing_user_turns() -> None:
    from hydra.cli.cmd_chat import _llm_messages_from_session

    messages = _llm_messages_from_session(
        [
            {"role": "user", "content": "completed task"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "old unfinished task"},
        ]
    )

    assert messages == [
        {"role": "user", "content": "completed task"},
        {"role": "assistant", "content": "done"},
    ]


def test_chat_session_projection_drops_unanswered_user_turn_before_next_user() -> None:
    from hydra.cli.cmd_chat import _llm_messages_from_session

    messages = _llm_messages_from_session(
        [
            {"role": "user", "content": "completed task"},
            {"role": "assistant", "content": "done"},
            {"role": "user", "content": "old unfinished task"},
            {"role": "user", "content": "fresh chat"},
            {"role": "assistant", "content": "fresh answer"},
        ]
    )

    assert messages == [
        {"role": "user", "content": "completed task"},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "fresh chat"},
        {"role": "assistant", "content": "fresh answer"},
    ]


def test_chat_binds_memory_tools_to_configured_memory_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from hydra.cli import cmd_chat

    seen: dict[str, object] = {}
    elite_kwargs: dict[str, object] = {}
    memory_root = tmp_path / "memory"

    def fake_bind_tools(
        root: Path,
        approval_policy: str,
        *,
        memory_root: str | Path | None = None,
        memory_workspace_root: str | Path | None = None,
        **_extra,
    ):
        seen["root"] = root
        seen["approval_policy"] = approval_policy
        seen["memory_root"] = memory_root
        seen["memory_workspace_root"] = memory_workspace_root
        return []

    monkeypatch.setattr(cmd_chat, "_bind_tools", fake_bind_tools)
    monkeypatch.setattr(cmd_chat, "_resolve_chat_runtime", lambda args: {"profile": "auto", "provider": "fake", "model": "fake-model", "runtime_route": "fake", "local_gpu_policy": "fake", "workbench_api": "fake", "local_worker_provider": "fake-worker", "worker_provider": "fake-worker"})
    monkeypatch.setattr(cmd_chat, "_make_client_or_setup", lambda args: ("client", SimpleNamespace(name="fake", model="fake-model")))
    monkeypatch.setattr(cmd_chat, "session_exists", lambda session_id: False)
    monkeypatch.setattr(cmd_chat, "create_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(cmd_chat, "_print_chat_identity", lambda *args, **kwargs: None)
    # EliteTUI is the new interactive layer — stub it so the test stays unit-scoped.
    class _FakeEliteTUI:
        def __init__(self, **kw):
            elite_kwargs.update(kw)
        def run(self): pass
    monkeypatch.setattr(cmd_chat, "EliteTUI", _FakeEliteTUI)
    monkeypatch.setattr(builtins, "input", lambda prompt="": (_ for _ in ()).throw(EOFError()))

    rc = cmd_chat.cmd_chat(
        argparse.Namespace(
            root=str(tmp_path),
            approval_policy="allow",
            memory_root=str(memory_root),
            no_local_memory=True,
            local_memory_chars=12000,
            with_context=False,
            truth_context=False,
            context_budget_bytes=4096,
            trace_out=None,
            profile="auto",
            provider=None,
            model=None,
            env_dir=None,
            setup_if_needed=False,
            session_history_limit=40,
            max_iterations=1,
            timeout=1.0,
        )
    )

    assert rc == 0
    assert seen["memory_root"] == memory_root
    assert seen["memory_workspace_root"] == tmp_path
    assert elite_kwargs["tools"] == []
    assert elite_kwargs["memory_root"] == memory_root
    assert callable(elite_kwargs["command_handler"])
    capsys.readouterr()


def test_chat_wires_per_turn_semantic_recall_into_tui(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from hydra.cli import cmd_chat

    seen: dict[str, object] = {}
    elite_kwargs: dict[str, object] = {}
    memory_root = tmp_path / "memory"

    def fake_semantic_recall(query, *, root, workspace_root, **kwargs):
        # The new query-aware recall builder (replaces the old static ~12KB dump).
        seen["query"] = query
        seen["root"] = root
        seen["workspace_root"] = workspace_root
        return SimpleNamespace(
            status="OK",
            context="",
            data={"chunks_scored": 0},
            report="ok",
        )

    monkeypatch.setattr(cmd_chat, "_bind_tools", lambda *args, **kwargs: [])
    monkeypatch.setattr(cmd_chat, "_resolve_chat_runtime", lambda args: {"profile": "auto", "provider": "fake", "model": "fake-model", "runtime_route": "fake", "local_gpu_policy": "fake", "workbench_api": "fake", "local_worker_provider": "fake-worker", "worker_provider": "fake-worker"})
    monkeypatch.setattr(cmd_chat, "_make_client_or_setup", lambda args: ("client", SimpleNamespace(name="fake", model="fake-model")))
    monkeypatch.setattr(cmd_chat, "session_exists", lambda session_id: False)
    monkeypatch.setattr(cmd_chat, "create_session", lambda *args, **kwargs: None)
    monkeypatch.setattr(cmd_chat, "_print_chat_identity", lambda *args, **kwargs: None)
    monkeypatch.setattr(cmd_chat, "build_semantic_memory_context", fake_semantic_recall)

    class _FakeEliteTUI:
        def __init__(self, **kw):
            elite_kwargs.update(kw)
        def run(self): pass
    monkeypatch.setattr(cmd_chat, "EliteTUI", _FakeEliteTUI)

    rc = cmd_chat.cmd_chat(
        argparse.Namespace(
            root=str(tmp_path),
            approval_policy="allow",
            memory_root=str(memory_root),
            no_local_memory=False,
            local_memory_chars=12000,
            with_context=False,
            truth_context=False,
            context_budget_bytes=4096,
            trace_out=None,
            profile="auto",
            provider=None,
            model=None,
            env_dir=None,
            setup_if_needed=False,
            session_history_limit=40,
            max_iterations=1,
            timeout=1.0,
        )
    )

    assert rc == 0
    # Chat memory is now recalled PER TURN by the user's own query INSIDE the TUI via a
    # query-aware semantic recall builder, NOT baked as a static ~12KB block into
    # initial_messages (that flood was the "chat acting weird" root cause). Assert the
    # NEW wiring: the recall builder + memory roots reach EliteTUI, and the startup
    # availability probe ran with those roots.
    assert elite_kwargs["recall_builder"] is fake_semantic_recall
    assert elite_kwargs["memory_root"] == memory_root
    assert elite_kwargs["memory_workspace_root"] == tmp_path
    assert seen["root"] == memory_root and seen["workspace_root"] == tmp_path
    capsys.readouterr()
