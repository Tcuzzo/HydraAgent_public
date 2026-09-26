"""TDD tests for TUI dragon seam-time behavior (slice-tui-dragon-seam).

The dragon animates every 350ms regardless of state. Two failures flow from
that:

1. **No thinking state.** During an active model turn the dragon renders
   identically to idle — the operator cannot tell from the banner alone
   whether the agent is working or just sitting there.

2. **No pause on focus loss.** The 350ms tick keeps firing when the app is
   unfocused (terminal in the background, another window active). It
   wastes CPU and burns through the gate.

The fix: `_dragon_mode` on HydraApp, three states — idle / thinking / off.
`_tick_dragon` is a no-op when `_dragon_mode == "off"`. The banner shows a
distinct eyes pattern in thinking mode so the seam between idle and working
is visible.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ── Fixture ────────────────────────────────────────────────────────────────


def _make_app(tools=None):
    from gateways.tui.hydra_app import HydraApp

    stub_client = SimpleNamespace()
    return HydraApp(
        client=stub_client,
        model="ollama-cloud/llama-3.3-70b",
        cfg=SimpleNamespace(name="ollama-cloud"),
        system_prompt="test system prompt",
        tools=tools or [],
    )


# ── State machine ──────────────────────────────────────────────────────────


def test_dragon_mode_defaults_to_idle():
    """On construction, _dragon_mode must be 'idle' — the resting state."""
    app = _make_app()
    assert app._dragon_mode == "idle"


def test_dragon_mode_flips_to_thinking_on_turn_start():
    """When a turn starts, the dragon must enter 'thinking' mode so the
    operator sees a visual seam between idle and working."""
    app = _make_app()
    app._set_dragon_thinking()
    assert app._dragon_mode == "thinking"


def test_dragon_mode_returns_to_idle_on_turn_end():
    """When the turn ends, the dragon must return to 'idle'."""
    app = _make_app()
    app._set_dragon_thinking()
    assert app._dragon_mode == "thinking"
    app._set_dragon_idle()
    assert app._dragon_mode == "idle"


def test_dragon_mode_can_be_paused():
    """The fix must add an explicit pause — used on focus loss so the 350ms
    tick stops wasting CPU when the operator is not looking."""
    app = _make_app()
    app._set_dragon_off()
    assert app._dragon_mode == "off"
    app._set_dragon_idle()
    assert app._dragon_mode == "idle"


# ── Tick no-op when off ───────────────────────────────────────────────────


def test_tick_dragon_is_noop_when_off():
    """When the dragon is off, _tick_dragon must not advance the frame or
    trigger a banner re-render. (Pre-mount, we assert by stubbing the
    internal update path; a live Textual mount would assert zero widget
    updates.)"""
    app = _make_app()
    frame_before = app._dragon_frame
    # Make _render_banner a recorder
    rendered: list[int] = []
    app._render_banner = lambda: rendered.append(1) or SimpleNamespace()  # type: ignore[assignment]
    app._set_dragon_off()
    app._tick_dragon()
    # Frame unchanged AND no banner render attempted.
    assert app._dragon_frame == frame_before
    assert rendered == []


def test_tick_dragon_advances_when_idle():
    """The tick must still advance frames in idle mode — the resting
    animation is part of the visual identity.

    Pre-mount, the widget render path is unreachable (no header-band yet),
    so we stub `_render_banner` and verify the tick advanced the frame and
    called it. The full live behavior is wired via Textual's set_interval
    in on_mount.
    """
    app = _make_app()
    assert app._dragon_mode == "idle"
    frame_before = app._dragon_frame
    rendered: list[int] = []
    # Stub the render so the tick short-circuits BEFORE the query_one path,
    # which would raise ScreenStackError pre-mount.
    def _fake_render():
        rendered.append(1)
        return SimpleNamespace()
    app._render_banner = _fake_render  # type: ignore[assignment]
    # Also stub the query_one path so the update branch is reached without
    # requiring a Textual mount.
    app.query_one = lambda *_a, **_kw: SimpleNamespace(update=lambda _x: None)  # type: ignore[assignment]
    app._tick_dragon()
    assert app._dragon_frame != frame_before
    assert rendered == [1]


# ── Banner differs between idle and thinking ──────────────────────────────


def test_render_banner_differs_between_idle_and_thinking():
    """The banner string must differ between idle and thinking modes. The
    exact bytes are free-form; the test asserts non-equality so the operator
    sees a visible seam."""
    app = _make_app()
    idle = app._render_banner()
    app._set_dragon_thinking()
    thinking = app._render_banner()
    assert str(idle) != str(thinking)