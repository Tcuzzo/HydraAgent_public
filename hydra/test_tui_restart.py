"""TDD tests for /restart slash command (slice-tui-restart).

Verifies:
- `/restart` sets `_restart_requested = True` and triggers `app.exit()`.
- `/restart` writes a "restarting" confirmation line to the chat stream.
- `maybe_reexec()` calls `_reexec_hydra()` iff `_restart_requested` is True.
- `maybe_reexec()` is a no-op when `_restart_requested` is False.
- `_reexec_hydra` builds the correct argv (sys.executable + sys.argv).

No network: all clients are stubs.  NEVER calls the real os.execv.
"""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ── Fixture helper ─────────────────────────────────────────────────────────


def _make_app():
    """Build a HydraApp with a stub client — no network, no real models."""
    from gateways.tui.hydra_app import HydraApp

    return HydraApp(
        client=SimpleNamespace(),
        model="test-model",
        cfg=SimpleNamespace(name="test-provider"),
        system_prompt="test system prompt",
        tools=[],
    )


def _run(coro):
    return asyncio.run(coro)


# ── Test 1: /restart sets flag and exits ──────────────────────────────────


def test_restart_command_sets_flag_and_exits():
    """/restart sets _restart_requested=True, calls exit(), and prints a line."""
    app = _make_app()

    async def go():
        async with app.run_test() as pilot:
            await pilot.pause()
            # Spy on app.exit so we can confirm it was called
            original_exit = app.exit
            exit_calls = []

            def spy_exit(*args, **kwargs):
                exit_calls.append((args, kwargs))
                # Don't actually exit the test harness — just record the call
                # Call original with return_value=0 to close app gracefully
                original_exit(0)

            app.exit = spy_exit

            input_widget = app.query_one("#operator-input")
            input_widget.value = "/restart"
            await pilot.press("enter")
            await pilot.pause()

            # Flag must be set
            assert app._restart_requested is True, (
                "_restart_requested must be True after /restart"
            )
            # exit() must have been called
            assert len(exit_calls) >= 1, "app.exit() must have been called"

            # "restarting" confirmation must appear in the chat stream
            from textual.widgets import RichLog
            chat = app.query_one("#chat-stream", RichLog)
            lines_text = "\n".join(str(line) for line in chat.lines[-10:]).lower()
            assert "restart" in lines_text, (
                "Chat stream must contain a 'restarting' confirmation"
            )

    _run(go())


# ── Test 2: maybe_reexec calls exec when flagged ──────────────────────────


def test_maybe_reexec_execs_when_flagged():
    """maybe_reexec() calls _reexec_hydra() when _restart_requested is True."""
    app = _make_app()
    app._restart_requested = True

    spy = MagicMock()
    with patch("gateways.tui.hydra_app._reexec_hydra", spy):
        app.maybe_reexec()

    spy.assert_called_once()


# ── Test 3: maybe_reexec is no-op when not flagged ────────────────────────


def test_maybe_reexec_noop_when_not_flagged():
    """maybe_reexec() must NOT call _reexec_hydra() when flag is False."""
    app = _make_app()
    app._restart_requested = False

    spy = MagicMock()
    with patch("gateways.tui.hydra_app._reexec_hydra", spy):
        app.maybe_reexec()

    spy.assert_not_called()


# ── Test 4: _reexec_hydra builds correct argv ─────────────────────────────


def test_reexec_builds_correct_argv():
    """_reexec_hydra must exec sys.executable with [sys.executable] + sys.argv.

    os.execv is monkeypatched — the real exec never runs.
    """
    from gateways.tui.hydra_app import _reexec_hydra

    captured: list = []

    def fake_execv(prog, argv):
        captured.append((prog, argv))
        # Do NOT raise or exec — just record

    fake_argv = ["hydra_entry.py", "--model", "test"]
    with (
        patch("os.execv", fake_execv),
        patch.object(sys, "argv", fake_argv),
    ):
        _reexec_hydra()

    assert len(captured) == 1, "os.execv must be called exactly once"
    prog, argv = captured[0]
    assert prog == sys.executable, "execv first arg must be sys.executable"
    assert argv[0] == sys.executable, "argv[0] must be sys.executable"
    assert argv[1:] == fake_argv, "argv tail must be the original sys.argv"
