"""A redirect can never carry the live-research page fetch to a refused host.

The guard must run on EVERY hop, before the fetch connects to it, not on the
final URL after the transport has already followed the chain. These tests use
a real local HTTP server and the real address guard: the only thing relaxed is
the very first URL (the test server itself lives on loopback), which is allowed
by exact match. Every hop after it goes through the real `_guard_public_host`.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from hydra import live_research


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hits: list[str] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output clean
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        self.server.hits.append(self.path)
        port = self.server.server_address[1]
        targets = {
            "/to-loopback": f"http://127.0.0.1:{port}/internal",
            "/to-localhost": f"http://localhost:{port}/internal",
            "/hop1": f"http://127.0.0.1:{port}/hop2",
            "/to-metadata": "http://169.254.169.254/latest/meta-data/",
            "/to-private": "http://10.0.0.1/secret",
        }
        if self.path in targets:
            self.send_response(302)
            self.send_header("Location", targets[self.path])
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body><p>internal secret page</p></body></html>")


@pytest.fixture
def server():
    srv = _Server()
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _allow_only(start_url, monkeypatch):
    real_guard = live_research._guard_public_host

    def guard(url: str) -> None:
        if url == start_url:
            return
        real_guard(url)

    monkeypatch.setattr(live_research, "_guard_public_host", guard)


def _start(server, path: str) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


@pytest.mark.parametrize("path", ["/to-loopback", "/to-localhost"])
def test_single_hop_to_loopback_is_refused_before_it_is_contacted(server, monkeypatch, path):
    start = _start(server, path)
    _allow_only(start, monkeypatch)
    with pytest.raises(live_research.LiveResearchError, match="refused"):
        live_research._fetch_page(start)
    assert server.hits == [path], "the refused hop was contacted"


def test_multi_hop_chain_is_refused_at_the_first_internal_hop(server, monkeypatch):
    start = _start(server, "/hop1")
    _allow_only(start, monkeypatch)
    with pytest.raises(live_research.LiveResearchError, match="refused"):
        live_research._fetch_page(start)
    assert server.hits == ["/hop1"], "a later hop was contacted after an internal one"


@pytest.mark.parametrize("path", ["/to-metadata", "/to-private"])
def test_redirect_to_metadata_or_private_is_refused_by_the_guard(server, monkeypatch, path):
    start = _start(server, path)
    _allow_only(start, monkeypatch)
    with pytest.raises(live_research.LiveResearchError, match="refused"):
        live_research._fetch_page(start)


def test_the_search_post_also_guards_every_hop(monkeypatch):
    """The search request goes through the same per-hop door as the page fetch."""
    seen: list[str] = []

    def fake_open_checked(req, *, validate, timeout):
        seen.append(req.full_url)
        validate(req.full_url)
        raise live_research.LiveResearchError("stop after the door ran")

    monkeypatch.setattr(live_research, "open_checked", fake_open_checked, raising=False)
    monkeypatch.setattr(live_research, "_guard_public_host", lambda url: None)
    with pytest.raises(live_research.LiveResearchError):
        live_research._duckduckgo_search("q", 3)
    assert seen == [live_research._DDG_URL]
