"""TDD tests for TUI header dual-model display (slice-tui-header-models).

RED criteria:
- test_header_shows_both_chat_and_work_models: the on_mount welcome line in
  #chat-stream CONTAINS both "llama-3.3-70b" (chat) AND "qwen2.5:72b" (work).
  Currently the line only contains the chat model → RED.
- test_work_model_computed_at_init: app._work_model and app._work_provider are
  set at __init__ time (no turn needed).
- test_header_failsoft_when_no_work_model: when resolve_work_model returns None,
  the header still renders without crashing (chat model only).
- test_active_provider_tracks_work_turn: _active_provider reflects the work
  provider after _run_agent_loop_sync is called for a work kind.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


# ── shared helpers ─────────────────────────────────────────────────────────

_CHAT_MODEL = "llama-3.3-70b-versatile"
_CHAT_PROVIDER = "ollama-cloud"


def _make_app(resolve_work_model_override=None):
    """Build a HydraApp with stubbed clients (no real API)."""
    from gateways.tui.hydra_app import HydraApp

    return HydraApp(
        client=SimpleNamespace(),
        model=_CHAT_MODEL,
        cfg=SimpleNamespace(name=_CHAT_PROVIDER),
        system_prompt="test system prompt",
        tools=[],
    )


def _run(coro):
    return asyncio.run(coro)


# ── tests ──────────────────────────────────────────────────────────────────


def test_work_model_computed_at_init():
    """_work_model and _work_provider must be set at __init__ (no turn needed).

    resolve_work_model('steering') should return ('ollama-cloud', 'qwen2.5:72b')
    (or whatever the routing config has). We verify the attributes exist and are
    non-None strings — and that they equal what resolve_work_model('steering') returns.
    """
    from hydra.work_executor import resolve_work_model

    app = _make_app()

    pair = resolve_work_model("steering")
    assert pair is not None, (
        "resolve_work_model('steering') must return a pair — check model_routing.yaml"
    )
    expected_provider, expected_model = pair

    assert app._work_model == expected_model, (
        f"app._work_model should be {expected_model!r}, got {app._work_model!r}"
    )
    assert app._work_provider == expected_provider, (
        f"app._work_provider should be {expected_provider!r}, got {app._work_provider!r}"
    )


def test_header_shows_both_chat_and_work_models():
    """The welcome line written to #chat-stream on_mount must show BOTH models.

    chat model: 'llama-3.3-70b-versatile' (from self.model)
    work model: 'qwen2.5:72b' (from resolve_work_model('steering'))
    Both must appear in the rendered chat-stream text.
    """
    from textual.widgets import RichLog

    async def go():
        app = _make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one("#chat-stream", RichLog)
            lines_text = "\n".join(str(line) for line in chat.lines[:6])
            # Chat model must be visible
            assert "llama-3.3-70b" in lines_text, (
                f"chat model not found in header; got: {lines_text!r}"
            )
            # Work model must ALSO be visible (this is the RED → GREEN goal)
            assert "qwen2.5:72b" in lines_text, (
                f"work model 'qwen2.5:72b' not found in header; got: {lines_text!r}"
            )

    _run(go())


def test_header_failsoft_when_no_work_model():
    """When resolve_work_model returns None the header renders without crashing.

    The chat line must still appear (chat model only) — no crash, no empty output.
    """
    from textual.widgets import RichLog

    async def go():
        with patch(
            "gateways.tui.hydra_app.resolve_work_model", return_value=None
        ):
            app = _make_app()
        # _work_model / _work_provider should be None
        assert app._work_model is None
        assert app._work_provider is None

        async with app.run_test() as pilot:
            await pilot.pause()
            chat = app.query_one("#chat-stream", RichLog)
            lines_text = "\n".join(str(line) for line in chat.lines[:6])
            # Chat model must still be shown
            assert "llama-3.3-70b" in lines_text, (
                f"chat model not found after fail-soft; got: {lines_text!r}"
            )
            # No crash — test reaching here proves it

    _run(go())


def test_active_provider_tracks_work_turn():
    """After routing a work turn, _active_provider must equal the work provider.

    We call _run_agent_loop_sync with a stubbed AgentLoop so no real network I/O
    happens. After the call, _active_provider should reflect the work executor's
    provider (e.g. 'ollama-cloud'), not the initial chat provider.
    """
    from hydra.work_executor import resolve_work_model
    from hydra.loop import LoopResult

    pair = resolve_work_model("steering")
    if pair is None:
        pytest.skip("resolve_work_model('steering') returned None — skip provider tracking test")

    expected_provider, expected_model = pair

    app = _make_app()

    # Default: _active_provider should equal the chat provider
    assert app._active_provider == _CHAT_PROVIDER, (
        f"initial _active_provider should be {_CHAT_PROVIDER!r}, got {app._active_provider!r}"
    )

    # Stub the agent loop's run method so no real call is made.
    # LoopResult fields: steps, final_response, iterations, tool_calls_made,
    # halted_reason, messages.
    fake_result = LoopResult(
        steps=[],
        final_response="done",
        iterations=1,
        tool_calls_made=0,
        halted_reason="natural",
        messages=[],
    )

    # We need to stub out the work loop construction (make_client would try network)
    mock_loop = MagicMock()
    mock_loop.run.return_value = fake_result

    with patch("hydra.providers.make_client") as mock_make_client:
        mock_client = MagicMock()
        mock_cfg = MagicMock()
        mock_make_client.return_value = (mock_client, mock_cfg)

        # Also patch AgentLoop so it returns our mock
        with patch("gateways.tui.hydra_app.AgentLoop", return_value=mock_loop):
            app._run_agent_loop_sync("build a thing", "steering")

    assert app._active_provider == expected_provider, (
        f"_active_provider should be {expected_provider!r} after work turn, "
        f"got {app._active_provider!r}"
    )


def test_existing_active_model_banner_behavior_intact():
    """The _render_stat_bar must still show 'work:' prefix for active work turns.

    This verifies we didn't break the existing per-turn stat-bar behavior.
    """
    app = _make_app()

    # Simulate an active work turn
    app._active_model = "qwen2.5:72b"
    rendered = str(app._render_stat_bar())
    assert "work:" in rendered, (
        f"stat bar should show 'work:' prefix during work turn; got: {rendered!r}"
    )

    # Reset to chat model — no 'work:' prefix expected
    app._active_model = _CHAT_MODEL
    rendered = str(app._render_stat_bar())
    assert "work:" not in rendered, (
        f"stat bar should NOT show 'work:' for chat turns; got: {rendered!r}"
    )
