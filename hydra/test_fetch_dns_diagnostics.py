"""DNS failure is a failed fetch, not evidence that a host is private."""
import socket

import pytest

from hydra.environment import EnvironmentError, fetch_session_url


@pytest.mark.parametrize("empty", [False, True])
def test_dns_failure_is_explicit_and_never_admitted(tmp_path, monkeypatch, empty):
    def resolver(*args, **kwargs):
        if empty:
            return []
        raise socket.gaierror(socket.EAI_AGAIN, "temporary resolution failure")

    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    with pytest.raises(EnvironmentError) as error:
        fetch_session_url(tmp_path, "no-session-created", "https://example.com/")
    message = str(error.value)
    assert "DNS" in message
    assert "private/internal" not in message
