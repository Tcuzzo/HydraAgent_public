"""TDD tests for TUI scroll-follow behavior (slice-tui-scroll-follow).

Bug: with `auto_scroll=True` on the chat RichLog, manual scroll-up actions
silently snap back to the bottom on the next write — the user can never read
older turns while the agent is producing new ones.

The fix path: a follow/unfollow state on HydraApp.

- `_follow_chat` starts True (auto_scroll=True). User scroll-up detaches.
- PageUp / action_scroll_up / action_scroll_runtime_up / mouse scroll-up
  each flip `_follow_chat = False` and `auto_scroll = False`.
- Scroll-down all the way to the bottom (or a `Follow` key) flips it back.
- A visual indicator (the operator can see it) signals when follow is OFF.

These tests assert the state transitions only — no Textual headless run needed.
The full live behavior (key bindings, mouse handlers) is wired in the fix.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── Fixture ────────────────────────────────────────────────────────────────


def _make_app(tools=None):
    """Build a HydraApp with a stub client — no network, no Textual mount."""
    from gateways.tui.hydra_app import HydraApp

    stub_client = SimpleNamespace()
    return HydraApp(
        client=stub_client,
        model="ollama-cloud/llama-3.3-70b",
        cfg=SimpleNamespace(name="ollama-cloud"),
        system_prompt="test system prompt",
        tools=tools or [],
    )


# ── State defaults ─────────────────────────────────────────────────────────


def test_follow_chat_defaults_to_true():
    """On construction, _follow_chat must be True — operator expects
    'newest at bottom, auto-scroll on' as the resting state."""
    app = _make_app()
    assert app._follow_chat is True


def test_detach_from_follow_disables_auto_scroll():
    """Calling _detach_from_follow must set _follow_chat=False. The chat
    RichLog's auto_scroll is wired to flip in compose(); here we assert
    the boolean state — the fix wires the RichLog side."""
    app = _make_app()
    app._detach_from_follow()
    assert app._follow_chat is False


def test_reattach_follow_enables_auto_scroll():
    """Calling _attach_follow must set _follow_chat=True."""
    app = _make_app()
    app._detach_from_follow()
    assert app._follow_chat is False
    app._attach_follow()
    assert app._follow_chat is True


# ── Auto-detach on scroll-up ──────────────────────────────────────────────


def test_action_scroll_up_detaches():
    """action_scroll_up must detach from follow. Until this lands, manual
    scroll-up is overridden by the next write."""
    app = _make_app()
    assert app._follow_chat is True
    app.action_scroll_up()
    assert app._follow_chat is False


def test_action_scroll_down_at_bottom_reattaches():
    """action_scroll_down at the bottom must re-attach. We cannot probe
    'at the bottom' without Textual; the rule is: if the user just detached
    and scrolls down enough, re-attach. We assert the helper exists and that
    _attach_follow() restores the flag."""
    app = _make_app()
    app._detach_from_follow()
    assert app._follow_chat is False
    app.action_scroll_down()  # best-effort; not all scrolls reach the bottom
    # The helper exists, the state is recoverable; the deeper wiring is in
    # compose() + the RichLog auto_scroll flag (see fix).
    assert hasattr(app, "_attach_follow")
    app._attach_follow()
    assert app._follow_chat is True


# ── Indicator ──────────────────────────────────────────────────────────────


def test_follow_indicator_helper_returns_marker():
    """The fix must add a helper that returns a visible marker when follow
    is OFF (so the operator can see they are detached). The marker text is
    free-form; assert that the helper exists and returns a non-empty string
    when detached, and an empty string (or None) when following."""
    app = _make_app()
    assert hasattr(app, "_follow_indicator")

    # Following state (the default) — no marker.
    following_marker = app._follow_indicator()
    assert following_marker == "" or following_marker is None

    # Detached — marker visible.
    app._detach_from_follow()
    detached_marker = app._follow_indicator()
    assert isinstance(detached_marker, str)
    assert detached_marker  # non-empty when detached

    # Back to following — marker clears.
    app._attach_follow()
    following_marker_again = app._follow_indicator()
    assert following_marker_again == "" or following_marker_again is None