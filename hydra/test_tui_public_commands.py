"""Textual pilot tests for public slash commands and the approval card."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from textual.widgets import RichLog


def _make_app(tmp_path: Path):
    from gateways.tui.hydra_app import HydraApp

    app = HydraApp(
        client=SimpleNamespace(),
        model="test-model",
        cfg=SimpleNamespace(name="test-provider"),
        system_prompt="test system prompt",
        tools=[],
        workspace_root=tmp_path,
    )
    app._submit_turn = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("sent to the model"))
    return app


def _text(app) -> str:
    return "\n".join(str(line) for line in app.query_one("#chat-stream", RichLog).lines)


def test_root_cloud_and_local_update_the_running_app(tmp_path):
    app = _make_app(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    seen = {}

    def rebind(path):
        seen["root"] = Path(path)
        return []

    def switch(name):
        seen["profile"] = name
        return SimpleNamespace(), SimpleNamespace(name=name), "profile-model", f"chat profile changed to: {name}"

    app._rebind_tools = rebind
    app._switch_profile = switch

    async def go():
        async with app.run_test(size=(100, 28)) as pilot:
            await pilot.pause()
            app.query_one("#operator-input").value = f"/root {other}"
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#operator-input").value = "/cloud"
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#operator-input").value = "/local"
            await pilot.press("enter")
            await pilot.pause()
            app.query_one("#operator-input").value = "/foo"
            await pilot.press("enter")
            await pilot.pause()
            return _text(app)

    shown = asyncio.run(go())
    assert app.workspace_root == other.resolve()
    assert seen["root"] == other.resolve()
    assert "workspace root changed" in shown
    assert seen["profile"] == "local"
    assert "chat profile changed to: cloud" in shown
    assert "chat profile changed to: local" in shown
    assert "unknown command /foo" in shown


def test_stop_word_does_not_quit_and_slash_stop_does(tmp_path):
    app = _make_app(tmp_path)
    submitted = {}
    app._submit_turn = lambda text, kind="steering": submitted.setdefault("text", text)

    async def go():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert "compact" in app.classes
            app.query_one("#operator-input").value = "stop"
            await pilot.press("enter")
            await pilot.pause()
            assert submitted.get("text") == "stop"
            app.query_one("#operator-input").value = "/stop"
            await pilot.press("enter")
            await pilot.pause()

    asyncio.run(go())
    assert app.return_code == 0 or not app.is_running


def test_approval_modal_accepts_keyboard_yes():
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from gateways.tui.approval_modal import ApprovalModal

    class Host(App):
        def compose(self) -> ComposeResult:
            yield Static("ready")

        def on_mount(self) -> None:
            self.push_screen(ApprovalModal("fs_write → hello.txt"), self._done)

        def _done(self, value: str | None) -> None:
            self.exit(value or "")

    async def go():
        app = Host()
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
        return app

    app = asyncio.run(go())
    assert app.return_value == "y"


def test_yolo_without_authenticator_points_at_setup(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    app._auth.auth_dir = tmp_path

    async def go():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.query_one("#operator-input").value = "/yolo"
            await pilot.press("enter")
            await pilot.pause()
            return _text(app)

    shown = asyncio.run(go())
    assert "/mfa setup" in shown
    assert app._awaiting_yolo_code is False
