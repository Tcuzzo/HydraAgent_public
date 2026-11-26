#!/usr/bin/env python3
"""Contract: the egress door refuses the addresses that must never be reachable.

``.hydraAgent/tools/web.yaml`` declares ``private_address_forbidden: true`` and
promises an "SSRF-guarded" fetch, and names ``skills.http_fetch:run`` as the
executor. The executor never enforced it: the only guard in the ``http_fetch``
path was the host allow-list, and an unset allow-list made the allow-list
"whatever host the agent asked for" (``hydra/cli/tool_binding.py``).

That left the cloud metadata service reachable — ``169.254.169.254`` answers
with credentials for the host it runs on — including through a redirect, which
no allow-list check covered on its own.

What this file pins:

  1. a metadata / link-local target is refused BEFORE a socket is opened, and
     the refusal names the blocked address class;
  2. a redirect to such a target is refused even when the allow-list names it
     (the allow-list must not be able to widen past the guard);
  3. the operator's explicitly configured loopback service stays reachable through the bound
     tool (the guard is not a blanket ban), and the strict posture still
     refuses it for the paths that need strict;
  4. a private LAN address is permitted only under the permissive posture;
  5. a host that cannot be resolved is refused, not attempted.

No mocking. The refusals are real, the stub servers are real local sockets, the
address resolution is real, and no request ever leaves the host.

Target before running (sniper discipline):
  pytest hydra/test_http_egress_policy.py -q
"""
from __future__ import annotations

import http.server
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hydra.cli.tool_binding import bind_tools

#: The cloud metadata service. Answering it returns credentials for the host.
METADATA_HOST = "169.254.169.254"

#: A link-local address that is not the metadata service.
LINK_LOCAL_HOST = "169.254.10.10"

#: RFC 1918. The operator's own LAN — legal under the permissive posture only.
PRIVATE_HOST = "10.0.0.1"


@pytest.fixture()
def stub_server():
    """A real local HTTP server. Paths starting with /redirect answer a 302."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://{METADATA_HOST}/latest/meta-data/")
                self.end_headers()
                return
            if self.path == "/redirect-private":
                self.send_response(302)
                self.send_header("Location", f"http://{PRIVATE_HOST}/")
                self.end_headers()
                return
            body = b"<html><head><title>Local</title></head><body><h1>Local Service</h1></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)


def _http_fetch_tool():
    return next(
        t for t in bind_tools(Path("/"), approval_policy="allow") if t.name == "http_fetch"
    )


def test_metadata_address_is_refused_before_any_request(monkeypatch):
    """The metadata service is refused by the guard, not by a socket timeout.

    The allow-list NAMES the metadata host, so the allow-list cannot be what
    refuses it. At the base commit the request is attempted instead and dies
    on a socket timeout — a different failure, and the wrong one.
    """
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", METADATA_HOST)
    with pytest.raises(Exception) as caught:
        _http_fetch_tool().invoke(url=f"http://{METADATA_HOST}/latest/meta-data/", timeout=1)
    message = str(caught.value).lower()
    assert "link-local" in message or "metadata" in message, (
        "the refusal did not name the blocked address class: " + str(caught.value)
    )
    assert "timeout" not in message, (
        "the request was attempted instead of refused by the guard: " + str(caught.value)
    )


def test_link_local_address_is_refused(monkeypatch):
    """Any link-local address is refused, not only the metadata one."""
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", LINK_LOCAL_HOST)
    with pytest.raises(Exception) as caught:
        _http_fetch_tool().invoke(url=f"http://{LINK_LOCAL_HOST}/", timeout=1)
    message = str(caught.value).lower()
    assert "link-local" in message or "metadata" in message, (
        "the refusal did not name the blocked address class: " + str(caught.value)
    )
    assert "timeout" not in message, (
        "the request was attempted instead of refused by the guard: " + str(caught.value)
    )


def test_redirect_to_a_metadata_address_is_refused(stub_server, monkeypatch):
    """A redirect cannot carry the request past the guard.

    The allow-list names BOTH the stub and the metadata host, so the
    allow-list check passes on the redirect target and the guard is the only
    thing that can refuse it.
    """
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", f"127.0.0.1,{METADATA_HOST}")
    with pytest.raises(Exception) as caught:
        _http_fetch_tool().invoke(url=f"{stub_server}/redirect", timeout=1)
    message = str(caught.value).lower()
    assert "link-local" in message or "metadata" in message, (
        "the redirect was not refused by the address guard: " + str(caught.value)
    )
    assert "timeout" not in message, (
        "the redirect was followed instead of refused: " + str(caught.value)
    )


def test_loopback_stays_reachable_for_the_operator(stub_server, monkeypatch):
    """The guard is not a blanket ban: the operator's own service still works.

    The private service is explicitly configured by the operator; automatic
    discovery must not be needed for this positive control.
    """
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "127.0.0.1")
    out = _http_fetch_tool().invoke(url=f"{stub_server}/page.html", timeout=5)
    assert out["ok"] is True and out["status"] == 200
    assert "Local Service" in out["text"]


def test_the_strict_posture_still_refuses_loopback(stub_server, tmp_path):
    """The strict posture — the browser/environment path — refuses loopback."""
    from hydra import environment

    source = tmp_path / "source"
    source.mkdir()
    session = environment.create_session(
        source_repo=source, env_root=tmp_path / "sessions", title="strict"
    )
    with pytest.raises(environment.EnvironmentError):
        environment.fetch_session_url(
            tmp_path / "sessions", session["session_id"], f"{stub_server}/page.html"
        )


def test_a_private_lan_address_is_permitted_only_under_the_permissive_posture():
    """The operator's own LAN is legal for a general fetch, not for a search result."""
    from hydra.http_policy import guard_host

    with pytest.raises(Exception) as caught:
        guard_host(PRIVATE_HOST, allow_private=False)
    assert "private" in str(caught.value).lower(), str(caught.value)

    guard_host(PRIVATE_HOST, allow_private=True)  # must not raise


def test_an_unresolvable_host_is_refused(monkeypatch):
    """A host that cannot be resolved is refused, not attempted.

    ``.invalid`` is reserved by RFC 2606 and never resolves, so this is
    hermetic — it does not depend on the host's DNS.
    """
    monkeypatch.setenv("HYDRA_HTTP_FETCH_ALLOWED_HOSTS", "does-not-exist.invalid")
    with pytest.raises(Exception) as caught:
        _http_fetch_tool().invoke(url="http://does-not-exist.invalid/", timeout=1)
    message = str(caught.value).lower()
    assert "resolve" in message or "unresolvable" in message, (
        "the refusal did not say the host could not be resolved: " + str(caught.value)
    )
