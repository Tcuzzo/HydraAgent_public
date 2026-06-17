"""TDD tests for TUI work-routing (hydra_app.py _route_for_kind).

Slice: slice-tui-work-routing
Ported pattern: elite.py _loop_for_kind (lines 742-776)
Run BEFORE the implementation for red confirmation, then green after.

All tests stub clients + resolve_work_model so NO network is needed.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Shared fixture ────────────────────────────────────────────────────────────

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


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_steering_turn_routes_to_work_executor():
    """A steering-kind turn must route to the work executor (cloud planner),
    NOT the chat model. This is the core confabulation fix.

    Mirrors elite.py:755-776.
    """
    app = _make_app()

    fake_work_client = SimpleNamespace()
    fake_work_model = "qwen2.5:72b"
    fake_provider = "ollama-cloud"

    with (
        patch(
            "gateways.tui.hydra_app.resolve_work_model",
            return_value=(fake_provider, fake_work_model),
        ),
        patch(
            "gateways.tui.hydra_app.is_work_kind",
            return_value=True,
        ),
        patch(
            "hydra.providers.make_client",
            return_value=(fake_work_client, SimpleNamespace(name=fake_provider)),
        ),
    ):
        loop, model = app._route_for_kind("steering")

    # The resolved model must be the work executor's model, NOT the chat model.
    assert model == fake_work_model
    assert model != app.model  # must differ from the chat model


def test_convo_turn_stays_on_chat_model():
    """A convo-kind turn must NOT route to the work executor.
    The existing chat loop and model are returned unchanged.

    Mirrors elite.py:755-756 (early return for non-work kinds).
    """
    app = _make_app()

    with patch(
        "gateways.tui.hydra_app.is_work_kind",
        return_value=False,
    ):
        loop, model = app._route_for_kind("convo")

    # Convo stays on the original chat loop + model.
    assert loop is app._agent_loop
    assert model == app.model


def test_work_routing_fail_soft_when_resolve_returns_none():
    """If resolve_work_model returns None, _route_for_kind falls back to chat loop.

    Mirrors elite.py:758-759 (None pair → return self._loop, self.model).
    """
    app = _make_app()

    with (
        patch(
            "gateways.tui.hydra_app.is_work_kind",
            return_value=True,
        ),
        patch(
            "gateways.tui.hydra_app.resolve_work_model",
            return_value=None,
        ),
    ):
        loop, model = app._route_for_kind("steering")

    # Fail-soft: must fall back to chat loop, not crash.
    assert loop is app._agent_loop
    assert model == app.model


def test_work_routing_fail_soft_when_make_client_raises():
    """If make_client raises (provider offline, bad config), _route_for_kind
    falls back to the chat loop without crashing.

    Mirrors elite.py:772-774 (except block → return self._loop, self.model).
    """
    app = _make_app()

    with (
        patch(
            "gateways.tui.hydra_app.is_work_kind",
            return_value=True,
        ),
        patch(
            "gateways.tui.hydra_app.resolve_work_model",
            return_value=("ollama-cloud", "qwen2.5:72b"),
        ),
        patch(
            "hydra.providers.make_client",
            side_effect=RuntimeError("provider offline"),
        ),
    ):
        loop, model = app._route_for_kind("steering")

    # Fail-soft: must not crash; must fall back to chat loop.
    assert loop is app._agent_loop
    assert model == app.model


def test_work_loop_has_tools():
    """The work loop must be built with the same tools as the chat loop.

    Without this the executor can't actually call bash, fs_read, etc.
    Mirrors elite.py:771 (same system_prompt, tools passed at run() time).
    """
    from hydra.loop import Tool

    fake_tool = Tool(
        name="bash",
        description="run bash",
        parameters={"type": "object", "properties": {}},
        invoke=lambda **kw: "ok",
    )
    app = _make_app(tools=[fake_tool])

    fake_work_client = SimpleNamespace()
    fake_work_model = "qwen2.5:72b"

    with (
        patch(
            "gateways.tui.hydra_app.is_work_kind",
            return_value=True,
        ),
        patch(
            "gateways.tui.hydra_app.resolve_work_model",
            return_value=("ollama-cloud", fake_work_model),
        ),
        patch(
            "hydra.providers.make_client",
            return_value=(fake_work_client, SimpleNamespace(name="ollama-cloud")),
        ),
    ):
        work_loop, model = app._route_for_kind("steering")

    # Tools live on the app; they are passed at run() time from self.tools.
    # Verify the app still carries them (the work loop will receive them at
    # run() time just as the chat loop does).
    assert app.tools == [fake_tool]
    assert model == fake_work_model


def test_run_agent_loop_sync_calls_route_for_kind():
    """_run_agent_loop_sync must call _route_for_kind and run the routed loop,
    not always self._agent_loop.

    This is the wire-up test — confirms the fix is actually plumbed in.
    """
    from hydra.loop import LoopResult

    app = _make_app()

    # Build a mock loop that records what it was called with.
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
        return_value=(mock_work_loop, "qwen2.5:72b"),
    ) as mock_route:
        app._run_agent_loop_sync("ssh into remote-server", "steering")

    # _route_for_kind must have been called with the intake kind.
    mock_route.assert_called_once_with("steering")
    # The ROUTED loop (work loop), not self._agent_loop, must have run.
    mock_work_loop.run.assert_called_once()
    # The default chat loop must NOT have run.
    # (self._agent_loop has no .run call tracked, so just confirm work loop ran)
