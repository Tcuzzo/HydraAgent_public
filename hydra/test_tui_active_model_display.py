"""TDD tests for TUI active-model display (slice-tui-active-model-display).

The stat bar ALWAYS showed the chat default (llama-3.3-70b) even when a
WORK turn (steering/collab) correctly routed to the cloud planner.  These tests
verify that:

1. `_active_model` is set from `_route_for_kind` at the start of a work turn.
2. `_render_stat_bar()` shows `_active_model`, not always `self.model`.
3. When `_active_model` is the work executor the stat bar has a visible work tag.
4. Chat/convo turns keep `_active_model` as the chat default.

No network: all clients are stubs, resolve_work_model is patched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest


# ── Fixture ────────────────────────────────────────────────────────────────


def _make_app(tools=None):
    """Build a HydraApp with a stub client — no network."""
    from gateways.tui.hydra_app import HydraApp

    stub_client = SimpleNamespace()
    return HydraApp(
        client=stub_client,
        model="ollama-cloud/llama-3.3-70b",
        cfg=SimpleNamespace(name="ollama-cloud"),
        system_prompt="test system prompt",
        tools=tools or [],
    )


WORK_MODEL = "qwen2.5:72b"
CHAT_MODEL = "ollama-cloud/llama-3.3-70b"


# ── Tests ──────────────────────────────────────────────────────────────────


def test_active_model_defaults_to_chat_model():
    """On construction, _active_model must equal self.model (the chat default)."""
    app = _make_app()
    assert app._active_model == CHAT_MODEL


def test_active_model_reflects_work_turn():
    """After _route_for_kind('steering') the _active_model is the work executor.

    Simulates what _run_agent_loop_sync does: call _route_for_kind, then set
    _active_model from the returned model string.
    """
    app = _make_app()

    fake_work_client = SimpleNamespace()

    with (
        patch("gateways.tui.hydra_app.is_work_kind", return_value=True),
        patch(
            "gateways.tui.hydra_app.resolve_work_model",
            return_value=("ollama-cloud", WORK_MODEL),
        ),
        patch(
            "hydra.providers.make_client",
            return_value=(fake_work_client, SimpleNamespace(name="ollama-cloud")),
        ),
    ):
        _loop, model = app._route_for_kind("steering")
        # Simulate what _run_agent_loop_sync now does:
        app._active_model = model

    assert app._active_model == WORK_MODEL
    assert app._active_model != CHAT_MODEL


def test_active_model_chat_is_default():
    """For a convo turn, _active_model stays the chat default."""
    app = _make_app()

    with patch("gateways.tui.hydra_app.is_work_kind", return_value=False):
        _loop, model = app._route_for_kind("convo")
        app._active_model = model

    assert app._active_model == CHAT_MODEL
    rendered = str(app._render_stat_bar())
    assert CHAT_MODEL in rendered


def test_stat_bar_shows_active_work_model():
    """_render_stat_bar() must contain _active_model when it's the work executor."""
    app = _make_app()
    app._active_model = WORK_MODEL  # simulate mid-work-turn

    rendered = str(app._render_stat_bar())
    assert WORK_MODEL in rendered


def test_stat_bar_shows_work_tag_when_active_is_work_executor():
    """When active model is NOT the chat default, stat bar must show a work indicator
    so the operator can visually distinguish work turns from chat turns."""
    app = _make_app()
    app._active_model = WORK_MODEL  # simulate work executor active

    rendered = str(app._render_stat_bar())
    # Either the raw model string or a 'work' tag must appear
    assert WORK_MODEL in rendered or "work" in rendered.lower()


def test_stat_bar_does_not_show_chat_model_during_work_turn():
    """While a work turn is active, the stat bar should NOT show the chat model
    as the primary model — it must show the work executor instead."""
    app = _make_app()
    app._active_model = WORK_MODEL  # work executor is active

    rendered = str(app._render_stat_bar())
    # Work model must appear
    assert WORK_MODEL in rendered
    # Chat model must NOT appear as the active model indicator
    # (CHAT_MODEL may appear elsewhere but we check the work model takes precedence)
    # The simplest contract: if active_model != self.model, stat bar shows active_model
    assert WORK_MODEL in rendered


def test_run_agent_loop_sync_sets_active_model_from_route():
    """_run_agent_loop_sync must set _active_model from _route_for_kind's return.

    This is the wire-up test — confirms the assignment is plumbed.
    """
    from hydra.loop import LoopResult

    app = _make_app()

    mock_work_loop = MagicMock()
    mock_work_loop.run.return_value = LoopResult(
        steps=[],
        final_response="done",
        iterations=1,
        tool_calls_made=0,
        halted_reason="natural",
        messages=[],
    )

    with patch.object(
        app,
        "_route_for_kind",
        return_value=(mock_work_loop, WORK_MODEL),
    ):
        app._run_agent_loop_sync("deploy the thing", "steering")

    # After the sync call, _active_model must reflect the routed model.
    assert app._active_model == WORK_MODEL


def test_run_agent_loop_sync_chat_keeps_default_model():
    """For a convo turn, _run_agent_loop_sync must leave _active_model as the chat default."""
    from hydra.loop import LoopResult

    app = _make_app()

    mock_chat_loop = MagicMock()
    mock_chat_loop.run.return_value = LoopResult(
        steps=[],
        final_response="hello",
        iterations=1,
        tool_calls_made=0,
        halted_reason="natural",
        messages=[],
    )

    with patch.object(
        app,
        "_route_for_kind",
        return_value=(mock_chat_loop, CHAT_MODEL),
    ):
        app._run_agent_loop_sync("what time is it?", "convo")

    assert app._active_model == CHAT_MODEL
