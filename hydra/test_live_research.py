"""Native /live research: search, fetch, citations. No network."""
from __future__ import annotations

import asyncio
import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from hydra.cli.cmd_chat import DEFAULT_SYSTEM_PROMPT
from hydra.cli.cmd_live import cmd_live
from hydra.cli.tool_binding import bind_tools
from hydra.live_research import LiveResearchError, parse_duckduckgo_html, research

_HTML = """
<html><body>
<a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fdocs&amp;rut=1">Example Docs</a>
<a class="result__snippet">Current release notes.</a>
<a class="result__a" href="https://example.org/guide">Guide</a>
<a class="result__snippet">A short guide.</a>
</body></html>
"""


def test_duckduckgo_html_parser_reads_titles_urls_and_snippets():
    hits = parse_duckduckgo_html(_HTML)
    assert hits[0]["title"] == "Example Docs"
    assert hits[0]["url"] == "https://example.com/docs"
    assert "release notes" in hits[0]["snippet"]
    assert hits[1]["url"] == "https://example.org/guide"
    assert hits[1]["title"] == "Guide"


def test_research_fetches_hits_and_builds_a_citation_context():
    def search(query, limit):
        assert query == "current release" and limit == 2
        return [
            {"title": "Example Docs", "url": "https://example.com/docs", "snippet": "notes"},
        ]

    packet = research(
        "current release",
        search=search,
        fetch=lambda url: f"body of {url}",
        limit=2,
    )
    source = packet["sources"][0]
    assert source["url"] == "https://example.com/docs"
    assert source["extract"] == "body of https://example.com/docs"
    assert "https://example.com/docs" in packet["reasoning_context"]
    assert "current release" in packet["reasoning_context"]


def test_keyed_provider_uses_endpoint_and_key_from_env(monkeypatch):
    monkeypatch.setenv("HYDRA_LIVE_PROVIDER", "brave")
    monkeypatch.setenv("HYDRA_LIVE_ENDPOINT", "http://search.example/search")
    monkeypatch.setenv("HYDRA_LIVE_KEY", "test-key")
    seen = {}

    def post(url, body, headers):
        seen["url"] = url
        seen["body"] = body
        seen["auth"] = headers.get("Authorization")
        return 200, {"results": [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]}

    packet = research("recent release", post=post, fetch=lambda url: "page")
    assert seen["url"] == "http://search.example/search"
    assert seen["body"]["query"] == "recent release"
    assert seen["auth"] == "Bearer test-key"
    assert packet["sources"][0]["extract"] == "page"
    assert "https://example.com/a" in packet["reasoning_context"]


def test_keyed_provider_without_a_key_names_the_env_var(monkeypatch):
    monkeypatch.setenv("HYDRA_LIVE_PROVIDER", "brave")
    monkeypatch.setenv("HYDRA_LIVE_ENDPOINT", "http://search.example/search")
    monkeypatch.delenv("HYDRA_LIVE_KEY", raising=False)
    with pytest.raises(LiveResearchError, match="HYDRA_LIVE_KEY"):
        research("recent release", post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("posted")))


def test_live_fetch_refuses_the_benchmark_range_without_opt_in(monkeypatch):
    monkeypatch.setattr(
        "hydra.environment._is_ip_allowed",
        lambda host: (False, "benchmark/fake-IP range 198.18.0.1"),
    )
    with pytest.raises(LiveResearchError, match="benchmark"):
        research("current release", search=lambda query, limit: [{"title": "T", "url": "https://example.com/a", "snippet": "s"}])


def test_blank_query_is_rejected_before_search():
    with pytest.raises(LiveResearchError, match="query"):
        research("  ", search=lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))


def test_live_command_prints_sources_and_does_not_call_a_model(capsys, monkeypatch):
    monkeypatch.setattr(
        "hydra.cli.cmd_live.research",
        lambda query, **kwargs: {
            "query": query,
            "sources": [{
                "title": "Example Docs",
                "url": "https://example.com/docs",
                "snippet": "notes",
                "extract": "body",
            }],
            "reasoning_context": "Question: q\n[1] https://example.com/docs",
        },
    )
    rc = cmd_live(Namespace(query=["current", "release"], json=False, no_fetch=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "https://example.com/docs" in out
    assert "current release" in out


def test_live_command_json_includes_the_reasoning_context(capsys, monkeypatch):
    packet = {
        "query": "current release",
        "sources": [],
        "reasoning_context": "none",
    }
    monkeypatch.setattr("hydra.cli.cmd_live.research", lambda query, **kwargs: packet)
    rc = cmd_live(Namespace(query=["current", "release"], json=True, no_fetch=False))
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["reasoning_context"] == "none"


def test_reasoning_prompt_tells_the_model_to_research_stale_facts():
    text = DEFAULT_SYSTEM_PROMPT.lower()
    assert "live_research" in text
    assert "stale" in text or "recent" in text


def test_agent_tool_returns_citations(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hydra.live_research.research",
        lambda query, **kwargs: {
            "query": query,
            "sources": [{"title": "T", "url": "https://example.com/a", "snippet": "s", "extract": "e"}],
            "reasoning_context": "https://example.com/a",
        },
    )
    tools = {tool.name: tool for tool in bind_tools(tmp_path, include_mcp=False)}
    result = tools["live_research"].invoke(query="current release")
    assert result["sources"][0]["url"] == "https://example.com/a"


def test_textual_live_command_shows_sources_and_feeds_the_turn(monkeypatch):
    from gateways.tui.hydra_app import HydraApp

    monkeypatch.setattr(
        "hydra.live_research.research",
        lambda query, **kwargs: {
            "query": query,
            "sources": [{"title": "Example Docs", "url": "https://example.com/docs", "snippet": "notes", "extract": "body"}],
            "reasoning_context": "source https://example.com/docs",
        },
    )
    app = HydraApp(
        client=SimpleNamespace(),
        model="test-model",
        cfg=SimpleNamespace(name="test-provider"),
        system_prompt="test system prompt",
        tools=[],
    )
    submitted = {}

    def capture(text, kind="steering"):
        submitted["text"] = text
        submitted["kind"] = kind

    app._submit_turn = capture

    async def go():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.query_one("#operator-input").value = "/live current release"
            await pilot.press("enter")
            await pilot.pause()
            from textual.widgets import RichLog
            return "\n".join(str(line) for line in app.query_one("#chat-stream", RichLog).lines)

    shown = asyncio.run(go())
    assert "https://example.com/docs" in shown
    assert "https://example.com/docs" in submitted["text"]
    assert "current release" in submitted["text"]
