"""The HTTP egress door: one address policy, applied on every hop.

One classifier lives here — every address class in Hydra is decided in this
module — and :func:`open_checked` applies the caller's policy to the initial
request AND to every redirect, so a redirect cannot carry a request past a
guard the initial URL passed.

``guard_host(host, allow_private=False)`` refuses everything but public
addresses. That is the posture for a URL somebody else chose. With
``allow_private=True`` the operator's own loopback and LAN addresses are
reachable too — the posture for a general fetch tool on a local-first agent.

Either way, link-local (where the cloud metadata service answers with the
host's credentials), unspecified, multicast and reserved are refused. No
allow-list and no posture widens past those.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from typing import Callable
import urllib.request


class EgressDenied(Exception):
    """A host was refused by the egress policy. The message names the reason."""


#: RFC 2544 benchmark range. A proxy or a fake-IP resolver answers public
#: names from it, so it is only public when the operator says a proxy answers.
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")

#: Classes no posture and no allow-list may reach.
_ALWAYS_REFUSED = frozenset({"unspecified", "link-local", "multicast", "reserved"})

#: Classes that are the operator's own machine and LAN.
_OPERATOR_CLASSES = frozenset({"loopback", "private"})


def _benchmark_range_allowed() -> bool:
    """Whether the benchmark/fake-IP range may be treated as public."""
    if os.environ.get("HYDRA_FETCH_ALLOW_FAKEIP", "").strip() == "1":
        return True
    return any(
        os.environ.get(name, "").strip()
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
    )


def _refusal(host: str, *, allow_private: bool) -> str | None:
    """Why `host` may not be contacted, or None when it may.

    The one decision. Every other function in this module is an adapter over
    it, so the product can never grow two answers to the same question.
    """
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return f"could not resolve host {host!r}: {exc}"
    for _family, _type, _proto, _canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_unspecified:
            kind = "unspecified"
        elif ip.is_link_local:
            kind = "link-local"
        elif ip.is_multicast:
            kind = "multicast"
        elif ip.is_reserved:
            kind = "reserved"
        elif ip.is_loopback:
            kind = "loopback"
        elif isinstance(ip, ipaddress.IPv4Address) and ip in _BENCHMARK_NETWORK:
            kind = "benchmark"
        elif ip.is_private:
            kind = "private"
        else:
            continue
        if kind == "benchmark":
            if _benchmark_range_allowed():
                continue
            return (
                f"benchmark/fake-IP range {ip_str}. Set HYDRA_FETCH_ALLOW_FAKEIP=1 "
                "or configure HTTP_PROXY/HTTPS_PROXY when a proxy answers public names."
            )
        if kind in _ALWAYS_REFUSED:
            return f"{kind} address {ip_str} is never a legitimate fetch target"
        if kind in _OPERATOR_CLASSES and not allow_private:
            return f"{kind} address {ip_str}"
    return None


def guard_host(host: str, *, allow_private: bool = False) -> None:
    """Refuse `host` when the egress policy forbids it.

    Raises :class:`EgressDenied` naming the blocked address class.
    """
    if not host:
        raise EgressDenied("refusing to fetch a URL with no host")
    reason = _refusal(host, allow_private=allow_private)
    if reason is not None:
        raise EgressDenied(f"refusing {host}: {reason}")


def is_ip_allowed(host: str) -> tuple[bool, str | None]:
    """Whether every address `host` resolves to is public.

    (True, None) when all resolved addresses are public. (False, reason)
    otherwise. Fails closed: a host that cannot be resolved is refused — an
    unresolvable host is not a private address, it is an unknown one.
    """
    reason = _refusal(host, allow_private=False)
    if reason is None:
        return True, None
    if reason.startswith("could not resolve"):
        raise EgressDenied(reason)
    return False, reason


class PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-applies the caller's policy to every redirect, before following it."""

    def __init__(self, validate: Callable[[str], None]):
        super().__init__()
        self.validate = validate

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.validate(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_checked(request: urllib.request.Request, *, validate: Callable[[str], None], timeout: float):
    """Open `request` with the caller's policy applied to every hop.

    `validate` is called on the initial URL and again on every redirect
    target, before the redirect is followed. A refusal raises whatever
    `validate` raises — the door does not swallow it.
    """
    validate(request.full_url)
    opener = urllib.request.build_opener(PolicyRedirectHandler(validate))
    return opener.open(request, timeout=timeout)
