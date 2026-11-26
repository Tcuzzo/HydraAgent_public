"""Local MCP stays local even when the shell configures a remote proxy."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("mcp")


def test_local_mcp_read_ignores_an_unusable_ambient_socks_proxy(monkeypatch, tmp_path):
    import uvicorn

    from hydra.mcp_bridge import invoke
    from hydra.mcp_server import create_server

    # This must work whether or not an optional SOCKS adapter is installed.
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "socks5://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    (tmp_path / "local.txt").write_text("local MCP stayed on loopback", encoding="utf-8")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    app = create_server(tmp_path).streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        result = invoke(
            {"url": f"http://127.0.0.1:{port}/mcp", "timeout": 5},
            "fs_read", {"path": "local.txt"},
        )
        assert not result.get("isError")
        assert "local MCP stayed on loopback" in json.dumps(result)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


@pytest.mark.parametrize("url,trust_env", [
    ("http://127.0.0.1:8765/mcp", False),
    ("http://[::1]:8765/mcp", False),
    ("http://localhost:8765/mcp", False),
    ("https://mcp.example.test/mcp", True),
    ("https://localhost.example.test/mcp", True),
    ("https://127.0.0.1.example.test/mcp", True),
    ("https://10.0.0.5/mcp", True),
])
def test_proxy_bypass_only_applies_to_loopback_endpoints(monkeypatch, url, trust_env):
    import httpx2

    from hydra.mcp_bridge import connect

    class ClientConstructionObserved(Exception):
        pass

    options = []

    def capture_options(**kwargs):
        options.append(kwargs)
        raise ClientConstructionObserved

    monkeypatch.setattr(httpx2, "AsyncClient", capture_options)

    async def open_connection():
        async with connect({"url": url, "timeout": 2}):
            pytest.fail("client construction should stop this policy probe")

    with pytest.raises(ClientConstructionObserved):
        asyncio.run(open_connection())
    assert options[0].get("trust_env", True) is trust_env
    assert options[0]["follow_redirects"] is False
