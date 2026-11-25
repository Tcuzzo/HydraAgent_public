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
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        if os.environ.get(name, "").strip():
            return True
    return False


def resolve_addresses(host: str) -> list[str]:
    """Every IP address `host` resolves to.

    Fails closed: a host that cannot be resolved is refused, never attempted.
    """
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise EgressDenied(f"could not resolve host {host!r}: {exc}") from exc
    addresses: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in addr_info:
        ip_str = sockaddr[0]
        if ip_str not in addresses:
            addresses.append(ip_str)
    return addresses


def classify_address(ip_str: str) -> str | None:
    """The address class of `ip_str`, or None when it is a public address.

    "link-local" covers the cloud metadata range. "loopback" and "private" are
    the operator's own machine and LAN. "benchmark" is checked before
    "private" because Python counts 198.18.0.0/15 as private.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return None
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_link_local:
        return "link-local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_loopback:
        return "loopback"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _BENCHMARK_NETWORK:
        return "benchmark"
    if ip.is_private:
        return "private"
    return None


def guard_host(host: str, *, allow_private: bool = False) -> None:
    """Refuse `host` when the egress policy forbids it.

    Raises :class:`EgressDenied` naming the blocked address class.

    `allow_private=False` — the strict posture: only public addresses.
    `allow_private=True` — the permissive posture: the operator's own loopback
    and LAN addresses are reachable too. Link-local (metadata), unspecified,
    multicast and reserved are refused under BOTH.
    """
    if not host:
        raise EgressDenied("refusing to fetch a URL with no host")
    for ip_str in resolve_addresses(host):
        address_class = classify_address(ip_str)
        if address_class is None:
            continue
        if address_class == "benchmark":
            if _benchmark_range_allowed():
                continue
            raise EgressDenied(
                f"refusing {host} ({ip_str}): benchmark/fake-IP range {ip_str}. "
                "Set HYDRA_FETCH_ALLOW_FAKEIP=1 or configure HTTP_PROXY/HTTPS_PROXY "
                "when a proxy answers public names."
            )
        if address_class in _ALWAYS_REFUSED:
            raise EgressDenied(
                f"refusing {host} ({ip_str}): {address_class} address {ip_str} is "
                "never a legitimate fetch target"
            )
        if address_class in _OPERATOR_CLASSES and not allow_private:
            raise EgressDenied(f"refusing {host} ({ip_str}): {address_class} address {ip_str}")


def is_ip_allowed(host: str) -> tuple[bool, str | None]:
    """Whether every address `host` resolves to is public.

    (True, None) when all resolved addresses are public. (False, reason)
    otherwise. Fails closed by raising :class:`EgressDenied` when the host
    cannot be resolved — an unresolvable host is not a private address, it is
    an unknown one.
    """
    for ip_str in resolve_addresses(host):
        address_class = classify_address(ip_str)
        if address_class is None:
            continue
        if address_class == "benchmark":
            if _benchmark_range_allowed():
                continue
            return False, (
                f"benchmark/fake-IP range {ip_str}. Set HYDRA_FETCH_ALLOW_FAKEIP=1 "
                "or configure HTTP_PROXY/HTTPS_PROXY when a proxy answers public names."
            )
        if address_class == "loopback":
            return False, f"loopback address {ip_str}"
        if address_class == "link-local":
            return False, f"link-local address {ip_str}"
        if address_class == "private":
            return False, f"private address {ip_str}"
        if address_class == "multicast":
            return False, f"multicast address {ip_str}"
        if address_class == "unspecified":
            return False, f"unspecified address {ip_str}"
        if address_class == "reserved":
            return False, f"reserved address {ip_str}"
    return True, None


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
