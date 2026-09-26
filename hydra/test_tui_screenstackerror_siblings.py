# hydra/test_tui_screenstackerror_siblings.py — RED contract for the
# (NoMatches, ScreenStackError) sibling seam in gateways/tui/hydra_app.py.
#
# CLASS: methods that call self.query_one() while the screen may be torn
# down must catch BOTH NoMatches (widget gone) AND ScreenStackError (no
# active screen on the main thread after unmount). The model is _tick_dragon
# and the scroll actions — every other sibling that calls query_one must
# follow the same primitive.
#
# Today the siblings _post_log, _update_stat_bar, _post_chat_line,
# _set_code_prompt (x2 blocks), action_clear_runtime,
# on_chat_input_submitted, and on_input_submitted catch only NoMatches.
# After HydraApp.run() returns and the screen stack is gone, worker-thread
# callbacks (call_from_thread -> _post_log etc.) hit ScreenStackError — a
# class the current except clause does NOT catch, so the exception
# propagates out and surfaces as an unhandled traceback.
#
# Per BACKS testing invariant 2: no mocks of the SUT. We instantiate the
# real HydraApp (with minimal stub objects), mount/unmount it via Textual's
# run_test harness, and patch ONLY the seam under test (query_one) to
# raise ScreenStackError — exactly mirroring the failure path the operator
# hit when the worker callback fires after the app has been torn down.

import asyncio
import ast
import pathlib

import pytest
from textual.app import ScreenStackError
from unittest.mock import MagicMock, patch

from gateways.tui import hydra_app as ha
from gateways.tui.hydra_app import HydraApp


def _make_app() -> HydraApp:
    """Build a minimal HydraApp. No real client/network — only the methods
    under test get exercised."""
    client = MagicMock()
    cfg = MagicMock()
    cfg.name = "test"
    return HydraApp(
        client=client,
        model="test-model",
        cfg=cfg,
        system_prompt="test prompt",
        tools=[],
    )


# ── Forced-ScreenStackError seam tests (the contract for each sibling) ──


def test_post_log_does_not_raise_when_query_one_raises_screenstackerror():
    app = _make_app()
    initial_chat_lines = list(app._chat_lines)
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        app._post_log("[dim]after unmount[/dim]")  # MUST NOT raise
    assert app._chat_lines == initial_chat_lines  # state unchanged


def test_update_stat_bar_does_not_raise_when_query_one_raises_screenstackerror():
    app = _make_app()
    snapshot = (
        app._stat_iterations,
        app._stat_tools,
        app._stat_tokens_chars,
        app._stat_live,
    )
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        app._update_stat_bar()  # MUST NOT raise
    assert (app._stat_iterations, app._stat_tools,
            app._stat_tokens_chars, app._stat_live) == snapshot


def test_post_chat_line_does_not_raise_when_query_one_raises_screenstackerror():
    app = _make_app()
    initial_len = len(app._chat_lines)
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        app._post_chat_line("hydra", "test reply")  # MUST NOT raise
    # _chat_lines is appended BEFORE the widget write, so the in-memory
    # record survives the unmount — the widget write is the only thing
    # that can fail, and the fix must keep that record intact.
    assert len(app._chat_lines) == initial_len + 1
    assert app._chat_lines[-1] == ("hydra", "test reply")


def test_set_code_prompt_does_not_raise_when_query_one_raises_screenstackerror():
    app = _make_app()
    initial_yolo = app._awaiting_yolo_code
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        app._set_code_prompt(True)  # MUST NOT raise
    assert app._awaiting_yolo_code is True  # state mutation survived


def test_action_clear_runtime_does_not_raise_when_query_one_raises_screenstackerror():
    app = _make_app()
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        ha.HydraApp.action_clear_runtime(app)  # MUST NOT raise


def test_on_chat_input_submitted_does_not_raise_when_query_one_raises_screenstackerror():
    """Sweep #7 — on_chat_input_submitted (line 638). Same class, missed in
    first-pass sibling enumeration."""
    app = _make_app()
    event = MagicMock()
    event.value = ""
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        # Async handler; run synchronously via asyncio.run.
        asyncio.run(app.on_chat_input_submitted(event))  # MUST NOT raise


def test_on_input_submitted_does_not_raise_when_query_one_raises_screenstackerror():
    """Sweep #8 — on_input_submitted (line 648). Same class, missed in
    first-pass sibling enumeration."""
    app = _make_app()
    event = MagicMock()
    event.value = ""
    with patch.object(app, "query_one", side_effect=ScreenStackError("no screen")):
        asyncio.run(app.on_input_submitted(event))  # MUST NOT raise


# ── Pre-existing NoMatches path still works (additive fix, not a regression)


def test_post_log_still_catches_no_matches():
    app = _make_app()
    with patch.object(app, "query_one", side_effect=ha.NoMatches("nope")):
        app._post_log("[dim]test[/dim]")  # MUST NOT raise


def test_update_stat_bar_still_catches_no_matches():
    app = _make_app()
    with patch.object(app, "query_one", side_effect=ha.NoMatches("nope")):
        app._update_stat_bar()  # MUST NOT raise


def test_post_chat_line_still_catches_no_matches():
    app = _make_app()
    with patch.object(app, "query_one", side_effect=ha.NoMatches("nope")):
        app._post_chat_line("hydra", "x")  # MUST NOT raise


# ── End-to-end: real mount + unmount via Textual's run_test harness ─────
# This is the path the operator actually hits when the worker callback
# fires after app.run() returns. If a sibling still catches only NoMatches,
# this test fails with the unhandled ScreenStackError traceback.


def test_post_log_survives_real_mount_unmount_cycle():
    app = _make_app()

    async def _drive():
        async with app.run_test() as pilot:
            pass  # Mounted; nothing to assert; just exercise the lifecycle.
        # Snapshot state BEFORE the post-unmount call — the in-memory
        # record should be unchanged because the widget write is the only
        # thing that can fail and it's a no-op once the screen is gone.
        pre_lines = list(app._chat_lines)
        # Now unmounted. The screen stack is gone. query_one may raise
        # ScreenStackError instead of NoMatches.
        app._post_log("[dim]after real unmount[/dim]")  # MUST NOT raise
        assert app._chat_lines == pre_lines  # state untouched

    asyncio.run(_drive())


def test_update_stat_bar_survives_real_mount_unmount_cycle():
    app = _make_app()

    async def _drive():
        async with app.run_test() as pilot:
            pass
        snapshot = (
            app._stat_iterations,
            app._stat_tools,
            app._stat_tokens_chars,
            app._stat_live,
        )
        app._update_stat_bar()  # MUST NOT raise
        assert (app._stat_iterations, app._stat_tools,
                app._stat_tokens_chars, app._stat_live) == snapshot

    asyncio.run(_drive())


def test_post_chat_line_survives_real_mount_unmount_cycle():
    app = _make_app()

    async def _drive():
        async with app.run_test() as pilot:
            pass
        initial_len = len(app._chat_lines)
        app._post_chat_line("hydra", "after real unmount")  # MUST NOT raise
        # _chat_lines is appended BEFORE the widget write, so the in-memory
        # record survives the unmount.
        assert len(app._chat_lines) == initial_len + 1
        assert app._chat_lines[-1] == ("hydra", "after real unmount")

    asyncio.run(_drive())


# ── Structural guard: every query_one-calling sibling catches both classes
# This is the AST/class-sweep guard that catches the next offender. If a
# future sibling is added with `except NoMatches:` only, this test fails
# red and names the offender.


def _load_hydra_app_source() -> str:
    return pathlib.Path(ha.__file__).read_text(encoding="utf-8")


def test_no_query_one_call_has_except_only_no_matches():
    """Structural guard: any method in hydra_app.py that calls self.query_one
    AND has a try/except must catch (NoMatches, ScreenStackError) together —
    not NoMatches alone. Catches the next sibling added with the wrong
    exception class."""
    src = _load_hydra_app_source()
    tree = ast.parse(src)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            caught_names = []
            if handler.type is None:
                caught_names.append("bare")
            elif isinstance(handler.type, ast.Name):
                caught_names.append(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                for elt in handler.type.elts:
                    if isinstance(elt, ast.Name):
                        caught_names.append(elt.id)
            # If this try has a body containing a self.query_one call AND
            # catches only NoMatches, it's an offender.
            if "NoMatches" in caught_names and "ScreenStackError" not in caught_names:
                try_src = ast.get_source_segment(src, node)
                if try_src and "self.query_one(" in try_src:
                    offenders.append(ast.unparse(node).splitlines()[0].strip())

    assert offenders == [], (
        f"Found query_one try/except that catches only NoMatches — must also "
        f"catch ScreenStackError. Offenders: {offenders}"
    )
