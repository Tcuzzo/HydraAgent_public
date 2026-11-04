"""Regressions found by driving main after PR #58 live on a VM.

1. The approval card swallowed its own key hints and rendered tool text as markup.
2. With no provider set up, `hydra ask` printed an empty `--provider` hint and
   repeated the provider error.
3. A rejected key printed a broken summary such as "(HTTP 400: [{)".
4. Live research handed the model raw page HTML instead of page text.
"""
from __future__ import annotations

import asyncio
import io
import types

import pytest


# 1. Approval card ------------------------------------------------------------

def _modal_texts(summary: str) -> tuple[str, list[str]]:
    from textual.app import App, ComposeResult
    from textual.widgets import Button, Static

    from gateways.tui.approval_modal import ApprovalModal

    seen: dict = {}

    class Host(App):
        def compose(self) -> ComposeResult:
            yield Static("ready")

        def on_mount(self) -> None:
            self.push_screen(ApprovalModal(summary), lambda value: self.exit(value or ""))

    async def go():
        app = Host()
        async with app.run_test(size=(100, 20)) as pilot:
            await pilot.pause()
            screen = app.screen
            prompt = screen.query_one("#approval-card Static", Static)
            seen["prompt"] = str(prompt.render())
            seen["labels"] = [str(b.label) for b in screen.query(Button)]
            await pilot.press("n")
            await pilot.pause()

    asyncio.run(go())
    return seen["prompt"], seen["labels"]


def test_approval_buttons_show_their_key_hints():
    _, labels = _modal_texts("fs_write on 'a.txt'")
    assert labels == ["Yes [y]", "No [n]", "Always [a]"]


def test_approval_prompt_shows_tool_text_verbatim():
    summary = "bash: echo [bold]hi[/bold] [/oops]"
    prompt, _ = _modal_texts(summary)
    assert summary in prompt


# 2. No provider set up -------------------------------------------------------

@pytest.mark.parametrize("unset", [None, ""])
def test_no_provider_hint_names_setup_without_an_empty_provider(capsys, unset):
    from hydra.cli import cmd_chat
    from hydra.providers import ProviderError

    args = types.SimpleNamespace(provider=unset, env_dir=None, setup_if_needed=False)
    with pytest.raises(ProviderError) as caught:
        cmd_chat._make_client_or_setup(args)
    err = capsys.readouterr().err
    assert "--provider `" not in err
    assert "--provider None" not in err
    assert "hydra setup`" in err
    assert getattr(caught.value, "hydra_reported", False) is True


def test_ask_does_not_repeat_a_reported_provider_error(monkeypatch, tmp_path, capsys):
    from hydra.cli import cmd_ask
    from hydra.providers import ProviderError

    def boom(_args):
        print("provider error: nothing set up", file=__import__("sys").stderr)
        err = ProviderError("nothing set up")
        err.hydra_reported = True
        raise err

    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda a: {"provider": None, "model": None})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup", boom)
    args = types.SimpleNamespace(
        root=str(tmp_path), prompt="hi", runtime_only=False, provider=None,
        max_iterations=1, timeout=10.0, approval_policy="ask",
    )
    assert cmd_ask.cmd_ask(args) == 2
    err = capsys.readouterr().err
    assert err.count("provider error:") == 1


# 3. Rejected key summary -----------------------------------------------------

def test_rejected_key_line_carries_the_provider_message():
    from hydra.cli.cmd_ask import _auth_rejection_message
    from hydra.llm import LlmError

    body = 'HTTP 400: [{\n  "error": {\n    "code": 400,\n    "message": "Please pass a valid API key",\n    "status": "INVALID_ARGUMENT"\n  }\n}\n]'
    line = _auth_rejection_message("gemini", LlmError(body, kind="http", status_code=400))
    assert line is not None
    assert "Please pass a valid API key" in line
    assert "[{" not in line
    assert line.endswith("Check GEMINI_API_KEY.")


# 4. Live research page text --------------------------------------------------

_PAGE = (
    "<!doctype html><html><head><title>Release</title>"
    "<script>var tracking = 1;</script><style>body{color:red}</style>"
    "<meta charset='utf-8'></head><body><nav>Menu</nav>"
    "<h1>Example 9.9.9</h1><p>Example 9.9.9 is the latest stable release.</p>"
    "</body></html>"
)


class _Resp(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetched_page_is_text_not_html(monkeypatch):
    from hydra import live_research

    monkeypatch.setattr(live_research, "_guard_public_host", lambda url: None)
    monkeypatch.setattr(
        live_research.urllib.request,
        "urlopen",
        lambda req, timeout=20: _Resp(_PAGE.encode("utf-8")),
    )
    text = live_research._fetch_page("https://example.invalid/release")
    assert "Example 9.9.9 is the latest stable release." in text
    assert "<" not in text
    assert "tracking" not in text
    assert "color:red" not in text


def test_page_text_after_a_long_head_still_reaches_the_extract(monkeypatch):
    from hydra import live_research

    head = "<head>" + ("<meta name='x' content='" + "y" * 50 + "'>") * 400 + "</head>"
    page = "<!doctype html><html>" + head + "<body><p>Body fact 42.</p></body></html>"
    monkeypatch.setattr(live_research, "_guard_public_host", lambda url: None)
    monkeypatch.setattr(
        live_research.urllib.request,
        "urlopen",
        lambda req, timeout=20: _Resp(page.encode("utf-8")),
    )
    assert "Body fact 42." in live_research._fetch_page("https://example.invalid/long")


def test_page_without_closing_head_still_yields_body_text():
    from hydra.live_research import _page_text

    html = "<html><head><title>T</title><noscript>x<body><p>Visible body.</p>"
    assert "Visible body." in _page_text(html)
