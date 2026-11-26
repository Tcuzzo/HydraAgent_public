"""Private services require an operator-selected host, before any request."""
from __future__ import annotations

import http.server
import threading
from pathlib import Path

import pytest

from hydra.cli.tool_binding import bind_tools
from skills.http_fetch import SkillError


@pytest.fixture()
def private_service():
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            body = b"synthetic-private-service-canary"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/canary", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def _tool(root: Path):
    return next(
        tool for tool in bind_tools(root, approval_policy="ask")
        if tool.name == "http_fetch"
    )


@pytest.mark.parametrize("setting", [None, "", "  ,  "])
def test_default_fetch_refuses_private_service_before_request(
    private_service, monkeypatch, tmp_path, setting
):
    url, requests = private_service
    if setting is None:
        monkeypatch.delenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", setting)
    with pytest.raises(SkillError, match="loopback|private"):
        _tool(tmp_path).invoke(url=url, timeout=2)
    assert requests == []


def test_explicit_operator_host_preserves_private_service_capability(
    private_service, monkeypatch, tmp_path
):
    url, requests = private_service
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "127.0.0.1")
    result = _tool(tmp_path).invoke(url=url, timeout=2)
    assert result["body"] == "synthetic-private-service-canary"
    assert requests == ["/canary"]


def test_explicit_other_host_does_not_authorize_private_service(
    private_service, monkeypatch, tmp_path
):
    url, requests = private_service
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "docs.example.org")
    with pytest.raises(SkillError, match="allow-list"):
        _tool(tmp_path).invoke(url=url, timeout=2)
    assert requests == []


def test_default_fetch_refuses_ipv6_loopback(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", raising=False)
    with pytest.raises(SkillError, match="loopback|private|reserved"):
        _tool(tmp_path).invoke(url="http://[::1]:1/canary", timeout=1)
