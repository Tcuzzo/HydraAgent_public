"""An unresolvable host must still be refused, but not mislabeled as private/internal."""
import socket

import pytest

from hydra import environment


def test_unresolvable_host_fails_closed_with_a_resolution_message(tmp_path, monkeypatch):
    def no_dns(*_args, **_kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(environment.socket, "getaddrinfo", no_dns)
    with pytest.raises(Exception) as exc:  # noqa: PT011 - message asserted below
        environment.fetch_session_url(tmp_path, "dns", "http://example.com/", timeout_seconds=1)
    msg = str(exc.value).lower()
    assert "resolve" in msg
    assert not any(w in msg for w in ("private", "internal", "loopback", "blocked"))
