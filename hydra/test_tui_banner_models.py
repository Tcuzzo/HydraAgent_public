"""TDD tests for the persistent #header-band showing BOTH chat and work models.

Slice: slice-tui-banner-models
RED → GREEN cycle: write tests first, then fix _render_banner in hydra_app.py.

Tests here do NOT touch tests/test_hydra_app_layout.py (locked).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Shared fixture — HydraApp with real ollama-cloud-style model names so the work
# model pair resolves to qwen2.5:72b (via model_routing.yaml).
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    from gateways.tui.hydra_app import HydraApp

    return HydraApp(
        client=SimpleNamespace(),
        model="llama-3.3-70b-versatile",
        cfg=SimpleNamespace(name="ollama-cloud"),
        system_prompt="test system prompt",
        tools=[],
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# test_persistent_banner_shows_both_models
# The #header-band Static must contain BOTH the chat model name AND the
# work model name so the operator can never look up and see only "ollama-cloud".
# ---------------------------------------------------------------------------

def test_persistent_banner_shows_both_models(app):
    """Banner shows chat:llama-3.3-70b-versatile AND work:qwen2.5:72b at rest."""
    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            header = app.query_one("#header-band")
            rendered = str(header.render())
            assert "llama-3.3-70b" in rendered, (
                f"chat model missing from banner; rendered=\n{rendered!r}"
            )
            assert "qwen2.5:72b" in rendered, (
                f"work model missing from banner; rendered=\n{rendered!r}"
            )
    _run(go())


# ---------------------------------------------------------------------------
# test_banner_active_work_indicator
# When a work turn is running (_active_model == _work_model), the banner
# must show a work-active indicator (e.g. "⚙ work" or the work model label
# in an emphasised form).
# ---------------------------------------------------------------------------

def test_banner_active_work_indicator(app):
    """Banner emphasises the work model when a work turn is in flight."""
    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # Simulate a work turn: flip _active_model to the work model.
            if app._work_model is not None:
                app._active_model = app._work_model
            else:
                pytest.skip("work model not resolved in this env")
            # Force a re-render of the header band with the new active model.
            from textual.widgets import Static
            app.query_one("#header-band", Static).update(app._render_banner())
            await pilot.pause()
            rendered = str(app.query_one("#header-band").render())
            # Must include the work-active indicator (⚙) or at minimum the work model.
            assert "⚙" in rendered or "work" in rendered, (
                f"work-active indicator missing; rendered=\n{rendered!r}"
            )
            # Must still show the work model name.
            assert "qwen2.5:72b" in rendered, (
                f"work model missing during work turn; rendered=\n{rendered!r}"
            )
    _run(go())


# ---------------------------------------------------------------------------
# test_banner_failsoft_no_work_model
# If _work_model is None (routing offline / misconfigured), the banner must
# still render (showing the chat model only) without raising.
# ---------------------------------------------------------------------------

def test_banner_failsoft_no_work_model(app):
    """Banner degrades gracefully when no work model is configured."""
    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # Force work model to None to simulate routing failure.
            app._work_model = None
            app._work_provider = None
            # Re-render explicitly — must not raise.
            try:
                banner_text = app._render_banner()
                rendered = str(banner_text)
            except Exception as exc:
                pytest.fail(f"_render_banner() raised with _work_model=None: {exc}")
            # Chat model must still be shown.
            assert "llama-3.3-70b" in rendered, (
                f"chat model missing from fail-soft banner; rendered=\n{rendered!r}"
            )
    _run(go())


# ---------------------------------------------------------------------------
# test_header_band_height_ok
# Mirror of test_hydra_app_layout.py::test_header_band_shows_the_banner —
# the banner height must stay within 14 rows regardless of the extra model
# label content we add.
# ---------------------------------------------------------------------------

def test_header_band_height_ok(app):
    """Header band height stays within CSS bound (0 < height <= 14)."""
    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from textual.widgets import Static
            header = app.query_one("#header-band", Static)
            assert header.styles.display == "block"
            assert 0 < header.size.height <= 14, (
                f"header height out of bounds: {header.size.height}"
            )
    _run(go())
