"""The agent's bound ``http_fetch`` tool must actually fetch.

Live finding: every call died with ``TypeError: run() got an unexpected
keyword argument 'method'`` because the binding passed method/headers/body to
a GET-only skill that also requires an allow-list. These tests drive the real
bound tool against a real local socket.
"""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from hydra.cli.tool_binding import bind_tools

PAGE = b"<html><head><title>What's New</title></head><body><h1>What's New In Python 9.9</h1></body></html>"


@pytest.fixture()
def server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/whatsnew/index.html"
    srv.shutdown()
    srv.server_close()


def _fetch_tool():
    return next(t for t in bind_tools(Path("/"), approval_policy="allow") if t.name == "http_fetch")


def test_bound_http_fetch_reads_a_page_with_just_a_url(server, monkeypatch):
    monkeypatch.delenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", raising=False)
    out = _fetch_tool().invoke(url=server)
    assert out["ok"] is True and out["status"] == 200
    assert out["url"] == server
    assert "What's New In Python 9.9" in out["text"]


def test_bound_http_fetch_accepts_explicit_get_and_defaults(server, monkeypatch):
    monkeypatch.delenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", raising=False)
    out = _fetch_tool().invoke(url=server, method="get", headers={"Accept": "text/html"}, timeout=5, max_bytes=100000)
    assert out["ok"] is True and "Python 9.9" in out["text"]


def test_bound_http_fetch_refuses_non_get_plainly(server):
    with pytest.raises(Exception, match="GET"):
        _fetch_tool().invoke(url=server, method="POST", body="x")


def test_operator_allow_list_still_binds_when_set(server, monkeypatch):
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "docs.example.org")
    with pytest.raises(Exception, match="allow-list"):
        _fetch_tool().invoke(url=server)
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "docs.example.org, 127.0.0.1")
    assert _fetch_tool().invoke(url=server)["ok"] is True


def test_html_comes_back_as_readable_text_not_markup(server, monkeypatch):
    """84 KB of raw HTML pushed the question out of a local model's context;
    the tool hands back the page's readable text (raw=True keeps markup)."""
    monkeypatch.delenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", raising=False)
    out = _fetch_tool().invoke(url=server)
    assert "What's New In Python 9.9" in out["text"]
    assert "<h1>" not in out["text"] and "body" not in out
    assert out["title"] == "What's New"
    raw = _fetch_tool().invoke(url=server, raw=True)
    assert "<h1>" in raw["body"]


def test_long_page_text_is_capped(monkeypatch):
    from hydra.cli import tool_binding

    big = "<html><body>" + "<p>word </p>" * 20000 + "</body></html>"
    monkeypatch.setattr(tool_binding.skill_http_fetch, "run", lambda url, allowed, **kw: {
        "ok": True, "url": url, "status": 200, "content_type": "text/html", "bytes_read": len(big), "body": big})
    out = _fetch_tool().invoke(url="https://docs.example.org/x")
    assert out["truncated"] is True and len(out["text"]) <= tool_binding.HTTP_FETCH_TEXT_CHARS + 20
