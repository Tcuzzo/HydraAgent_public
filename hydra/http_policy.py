"""The HTTP egress door: one address policy, applied on every hop.

One classifier lives here — every address class in Hydra is decided in this
module — and :func:`open_checked` applies the caller's policy to the initial
request AND to every redirect, so a redirect cannot carry a request past a
guard the initial URL passed.

The door also pins the connection. A host is resolved ONCE per hop, the
caller's policy judges exactly those addresses, and the socket then dials one
of exactly those addresses. The transport never resolves the name again, so a
name that answers "public" to the check and "loopback" to the connect (DNS
rebinding) gets no second answer to exploit. For https the TLS handshake still
names the ORIGINAL host, so the certificate is verified against the name the
caller asked for, not against the pinned address. Only http and https are
followed, on every hop.

``guard_host(host, allow_private=False)`` refuses everything but public
addresses. That is the posture for a URL somebody else chose. With
``allow_private=True`` the operator's own loopback and LAN addresses are
reachable too — the posture for a general fetch tool on a local-first agent.

Either way, link-local (where the cloud metadata service answers with the
host's credentials), unspecified, multicast and reserved are refused. No
allow-list and no posture widens past those.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import threading
import urllib.parse
import urllib.request
from typing import Callable


class EgressDenied(Exception):
    """A host was refused by the egress policy. The message names the reason."""


#: Classes no posture and no allow-list may reach.
_ALWAYS_REFUSED = frozenset({"unspecified", "link-local", "multicast", "reserved"})

#: Classes that are the operator's own machine and LAN.
_OPERATOR_CLASSES = frozenset({"loopback", "private"})

#: IANA special-purpose ranges the ``ipaddress`` properties do not (or do not
#: reliably) cover. Checked before the properties, so a table row wins.
_SPECIAL = tuple(
    (ipaddress.ip_network(net), kind)
    for net, kind in (
        ("::ffff:0:0/96", "reserved"),  # IPv4-mapped; the stdlib's classification of this range moved between Python releases, so the guard owns it
        ("100.64.0.0/10", "private"),  # RFC 6598 carrier-grade NAT (Tailscale; a cloud metadata address)
        ("192.0.0.0/24", "private"),  # RFC 6890 IETF protocol assignments
        ("198.18.0.0/15", "benchmark"),  # RFC 2544; a proxy answers public names from it
        ("2001::/32", "reserved"),  # Teredo
        ("2001:2::/48", "reserved"),  # benchmarking
        ("2001:10::/28", "reserved"),  # ORCHID
        ("2001:db8::/32", "reserved"),  # documentation
        ("3fff::/20", "reserved"),  # documentation
        ("5f00::/16", "reserved"),  # SRv6 segment identifiers
        ("64:ff9b:1::/48", "reserved"),  # local-use NAT64
    )
)

#: ``ipaddress`` properties, in the order that decides an address's class.
_PROPERTY_KINDS = (
    ("is_unspecified", "unspecified"),
    ("is_link_local", "link-local"),
    ("is_multicast", "multicast"),
    ("is_reserved", "reserved"),
    ("is_loopback", "loopback"),
    ("is_private", "private"),
)

_NAT64 = ipaddress.ip_network("64:ff9b::/96")

_ALLOWED_SCHEMES = ("http", "https")


def _benchmark_range_allowed() -> bool:
    """Whether the benchmark/fake-IP range may be treated as public."""
    if os.environ.get("HYDRA_FETCH_ALLOW_FAKEIP", "").strip() == "1":
        return True
    return any(
        os.environ.get(name, "").strip()
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
    )


# --- resolution: one lookup per hop, shared by the check and the connect ----

_pin_scope = threading.local()


def _resolve(host: str):
    """The address answers for `host`: the hop's pinned answer, else a lookup.

    Inside :meth:`_Door.admit` the answer was fetched once and is reused, so
    the policy judges the very addresses the socket will dial.
    """
    pinned = getattr(_pin_scope, "pinned", None) or {}
    found = pinned.get(host.lower())
    if isinstance(found, BaseException):
        raise found
    if found is not None:
        return found
    return socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)


def _addresses(addr_info) -> list[tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address]]:
    """Every parseable address in a ``getaddrinfo`` answer."""
    found = []
    for _family, _type, _proto, _canon, sockaddr in addr_info:
        try:
            found.append((sockaddr[0], ipaddress.ip_address(sockaddr[0])))
        except ValueError:
            continue
    return found


# --- classification: one address in, one class out --------------------------


def _embedded_v4(ip) -> ipaddress.IPv4Address | None:
    """The v4 address a v6 transition form wraps, or None."""
    if ip.version != 6:
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _classify(ip) -> str:
    """The address class of one address: a refusal class, an operator class,
    ``benchmark`` or ``public``. A wrapped v4 address that is never reachable
    keeps that class, so a transition form cannot smuggle it past the guard."""
    inner = _embedded_v4(ip)
    if inner is not None:
        kind = _classify(inner)
        if kind in _ALWAYS_REFUSED:
            return kind
    for network, kind in _SPECIAL:
        if ip in network:
            return kind
    for attr, kind in _PROPERTY_KINDS:
        if getattr(ip, attr):
            return kind
    return "public"


# --- policy: a class and a posture in, a refusal reason (or None) out -------


def _reason(kind: str, ip_str: str, allow_private: bool) -> str | None:
    """Why an address of class `kind` may not be contacted, or None."""
    if kind == "benchmark" and not _benchmark_range_allowed():
        return (
            f"benchmark/fake-IP range {ip_str}. Set HYDRA_FETCH_ALLOW_FAKEIP=1 "
            "or configure HTTP_PROXY/HTTPS_PROXY when a proxy answers public names."
        )
    if kind in _ALWAYS_REFUSED:
        return f"{kind} address {ip_str} is never a legitimate fetch target"
    if kind in _OPERATOR_CLASSES and not allow_private:
        return f"{kind} address {ip_str}"
    return None


def _refusal(host: str, *, allow_private: bool) -> str | None:
    """Why `host` may not be contacted, or None when it may.

    The one decision. Every other function in this module is an adapter over
    it, so the product can never grow two answers to the same question.
    """
    try:
        addr_info = _resolve(host)
    except socket.gaierror as exc:
        return f"could not resolve host {host!r}: {exc}"
    for ip_str, ip in _addresses(addr_info):
        reason = _reason(_classify(ip), ip_str, allow_private)
        if reason is not None:
            return reason
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


# --- the door: pin each hop's address, then connect only to it --------------


class _Dialer:
    """Opens sockets, and only to addresses the policy has already judged."""

    def __init__(self, pinned: dict) -> None:
        self.pinned = pinned
        # With an HTTP proxy configured, the socket goes to the proxy, which
        # resolves the target itself; the proxy's own address is not pinned.
        self.proxied = any(name != "no" for name in urllib.request.getproxies())

    def dial(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        host, port = address[0], address[1]
        found = self.pinned.get(host.lower())
        if isinstance(found, BaseException):
            raise found
        if found is None and self.proxied:
            return socket.create_connection(address, timeout, source_address)
        if found is None:
            raise EgressDenied(f"refusing to connect to {host}: its address was not checked")
        return self._first_reachable(found, port, timeout, source_address)

    @staticmethod
    def _first_reachable(addr_info, port, timeout, source_address):
        last: OSError = OSError("no address to connect to")
        for ip_str, _ip in _addresses(addr_info):
            try:
                return socket.create_connection((ip_str, port), timeout, source_address)
            except OSError as exc:
                last = exc
        raise last

    def connection(self, base):
        """`base` (an http.client connection class) bound to this dialer.

        Everything else about the connection is stock: the Host header is the
        original name, and an https connection wraps the pinned socket with
        ``server_hostname`` set to that same original name.
        """
        dialer = self

        class Pinned(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._create_connection = dialer.dial

        return Pinned


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, dialer: _Dialer) -> None:
        super().__init__()
        self._dialer = dialer

    def http_open(self, req):
        return self.do_open(self._dialer.connection(http.client.HTTPConnection), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, dialer: _Dialer) -> None:
        super().__init__()
        self._dialer = dialer

    def https_open(self, req):
        kwargs = {"context": self._context}
        if getattr(self, "_check_hostname", None) is not None:
            kwargs["check_hostname"] = self._check_hostname
        return self.do_open(self._dialer.connection(http.client.HTTPSConnection), req, **kwargs)


class _Door:
    """One request's state: the caller's policy and each hop's pinned addresses."""

    def __init__(self, validate: Callable[[str], None]) -> None:
        self.validate = validate
        self.pinned: dict = {}
        self.dialer = _Dialer(self.pinned)

    @staticmethod
    def check_scheme(url: str) -> urllib.parse.SplitResult:
        """Refuse every scheme but http and https, before anything connects."""
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in _ALLOWED_SCHEMES:
            raise EgressDenied(f"refusing {parts.scheme!r} URL: only http and https are allowed")
        return parts

    def admit(self, url: str) -> None:
        """Check one hop's URL and pin the addresses its host resolved to."""
        host = (self.check_scheme(url).hostname or "").lower()
        if host:
            try:
                self.pinned[host] = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except socket.gaierror as exc:
                self.pinned[host] = exc
        before = getattr(_pin_scope, "pinned", None)
        _pin_scope.pinned = self.pinned
        try:
            self.validate(url)
        finally:
            _pin_scope.pinned = before


class PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-checks and re-pins every redirect target before following it."""

    def __init__(self, door: _Door) -> None:
        super().__init__()
        self.door = door

    def http_error_302(self, req, fp, code, msg, headers):
        # urllib would answer an odd scheme with its own HTTPError; the door
        # refuses it in its own words, before the redirect is even considered.
        target = headers.get("location") or headers.get("uri") or ""
        self.door.check_scheme(urllib.parse.urljoin(req.full_url, target))
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.door.admit(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_checked(request: urllib.request.Request, *, validate: Callable[[str], None], timeout: float):
    """Open `request` with the caller's policy applied to, and the socket bound
    to, every hop.

    `validate` is called on the initial URL and again on every redirect
    target, before the redirect is followed, while that hop's addresses are
    pinned: what `validate` judges is what the socket dials. A refusal raises
    whatever `validate` raises — the door does not swallow it. A URL whose
    scheme is not http or https raises :class:`EgressDenied`.
    """
    door = _Door(validate)
    door.admit(request.full_url)
    opener = urllib.request.build_opener(
        PolicyRedirectHandler(door),
        _PinnedHTTPHandler(door.dialer),
        _PinnedHTTPSHandler(door.dialer),
    )
    return opener.open(request, timeout=timeout)
