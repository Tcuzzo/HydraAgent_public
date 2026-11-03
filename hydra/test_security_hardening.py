"""Behavioral security bar — the protection the agent MUST enforce.

These tests are the immutable gate for the dependency-free SSRF protection: when
the agent fetches a URL, it must refuse internal / loopback / link-local /
cloud-metadata targets *before* opening any connection, so a malicious or
tricked prompt cannot reach services behind the user's machine. The refusal must
be deliberate (a security message), not an incidental connection error.

This file is the bar; it is not "fair game" for any automated hardening loop.
"""
from __future__ import annotations

import socket
import urllib.error

import pytest

from hydra import environment
from hydra.environment import fetch_session_url

# Targets that must never be fetched: loopback, link-local/metadata, RFC1918.
_INTERNAL_URLS = [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://169.254.169.254/latest/meta-data/",  # cloud instance metadata
    "http://10.0.0.1/",
    "http://192.168.0.1/",
    "http://172.16.0.1/",
    "http://[::1]/",
]

_REFUSAL_WORDS = ("private", "internal", "loopback", "link-local", "metadata",
                  "blocked", "refus", "not allowed", "disallow")


@pytest.mark.parametrize("url", _INTERNAL_URLS)
def test_fetch_session_url_refuses_internal_targets(tmp_path, url):
    with pytest.raises(Exception) as exc:  # noqa: PT011 - message asserted below
        fetch_session_url(tmp_path, "sec-bar", url, timeout_seconds=1)
    msg = str(exc.value).lower()
    assert any(w in msg for w in _REFUSAL_WORDS), (
        f"fetching {url} must be refused with a security message "
        f"(an incidental connection error does not count); got: {exc.value!r}"
    )


def test_fetch_session_url_still_allows_public_scheme_check(tmp_path, monkeypatch):
    # DNS is mocked so this does not depend on the network or on a fake-IP resolver.
    def public_dns(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(environment.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(
        environment,
        "open_checked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(urllib.error.URLError("timed out")),
    )
    try:
        fetch_session_url(tmp_path, "sec-bar", "http://example.com/", timeout_seconds=1)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        assert not any(w in msg for w in ("private", "internal", "loopback", "blocked", "benchmark")), (
            f"public host wrongly blocked by the SSRF guard: {exc!r}"
        )
