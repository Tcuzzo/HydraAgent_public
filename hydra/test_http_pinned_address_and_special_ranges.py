"""The egress door connects to the address it checked, and refuses what it must.

Round 2 of the egress guard. The first round checked the host name on every
hop, but the transport then resolved the name AGAIN to connect. A name that
answers with a public address for the check and with loopback for the connect
walks straight past the guard (DNS rebinding). The fixes pinned here:

  a. one resolution per hop, and the socket dials that exact validated address;
  b. every IANA special-purpose range the old guard missed is refused
     (carrier-grade NAT, protocol assignments, benchmarking, reserved, the
     NAT64 / 6to4 / v4-mapped forms that wrap a private v4, documentation);
  c. only http and https are followed, on every hop including the first;
  d. the keyed-search POST never follows a redirect, so the bearer token
     cannot be carried to another host;
  e. the DuckDuckGo search POST is proven against a real redirecting server;
  f. a pinned HTTPS connection still verifies the certificate against the
     ORIGINAL host name, not the pinned address.

No mock replaces the guard or ``open_checked``. Only two leaves are patched:
the resolver (``socket.getaddrinfo``) and the TCP dial
(``socket.create_connection``), so a "public" test address can be made to
reach a real local server. Every server here is a real local socket.

Target before running (sniper discipline):
  pytest hydra/test_http_pinned_address_and_special_ranges.py -q
"""
from __future__ import annotations

import http.server
import ipaddress
import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from hydra import live_research
from hydra.http_policy import EgressDenied, guard_host, open_checked

#: example.com's address. It stands in for "a public host". It is never really
#: dialed: the dial leaf below either refuses it or routes it to a local server.
#: (RFC 5737 documentation ranges are not public to the guard, so they cannot
#: play this part.)
PUBLIC = "93.184.216.34"


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        return

    def _serve(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        srv = self.server
        srv.hits.append(self.path)
        srv.auth.append(self.headers.get("Authorization"))
        srv.hosts.append(self.headers.get("Host"))
        route = srv.routes.get(self.path)
        if route:
            self.send_response(route[0])
            self.send_header("Location", route[1])
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<html><body><p>served page</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _serve  # noqa: N815 - stdlib names
    do_POST = _serve  # noqa: N815


class _Srv(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.routes: dict[str, tuple[int, str]] = {}
        self.hits: list[str] = []
        self.auth: list[str | None] = []
        self.hosts: list[str | None] = []

    @property
    def port(self) -> int:
        return self.server_address[1]


@pytest.fixture(autouse=True)
def _no_proxy_env(monkeypatch):
    for name in (
        "HYDRA_FETCH_ALLOW_FAKEIP", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy",
        "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def serve():
    started: list[_Srv] = []

    def make() -> _Srv:
        srv = _Srv()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv

    yield make
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _fake_dns(monkeypatch, table) -> dict[str, int]:
    """Patch the resolver leaf. Returns the per-name lookup counter."""
    real = socket.getaddrinfo
    calls: dict[str, int] = {}

    def fake(host, *args, **kwargs):
        if host not in table:
            return real(host, *args, **kwargs)
        calls[host] = calls.get(host, 0) + 1
        answer = table[host]
        ips = answer(calls[host]) if callable(answer) else answer
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, 0, 0, 0) if ":" in ip else (ip, 0),
            )
            for ip in ips
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


def _dial_leaf(monkeypatch, *, local_port: int | None = None, public_names=()):
    """Patch the TCP dial leaf and record every address it is asked to dial.

    The public stand-in address (and the names that resolve to it) reach the
    real local server on ``local_port``, or are refused when there is none.
    Anything else dials for real.
    """
    dialed: list[tuple[str, int]] = []
    real = socket.create_connection

    def dial(address, *args, **kwargs):
        host, port = address[0], address[1]
        dialed.append((host, port))
        if host == PUBLIC or host in public_names:
            if local_port is None:
                raise ConnectionRefusedError("test: the public stand-in is unreachable")
            return real(("127.0.0.1", local_port), *args, **kwargs)
        return real(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", dial)
    return dialed


def _is_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------
# (a) DNS rebinding: the address that was checked is the address that is dialed
# --------------------------------------------------------------------------


def test_a_rebinding_name_never_reaches_the_internal_server(serve, monkeypatch):
    """Public for the checks, loopback for the connect: the connect must not happen.

    The old path looked the name up three times (page guard, door guard,
    transport connect). The first two answer public here and every later one
    answers loopback, so only a connect that is bound to a checked address is
    safe. The real internal server must never be contacted.
    """
    internal = serve()
    name = "rebind.example.test"
    _fake_dns(monkeypatch, {name: lambda n: [PUBLIC] if n <= 2 else ["127.0.0.1"]})
    dialed = _dial_leaf(monkeypatch)

    with pytest.raises((live_research.LiveResearchError, urllib.error.URLError, OSError)):
        live_research._fetch_page(f"http://{name}:{internal.port}/secret")

    assert internal.hits == [], "the internal server was reached through a rebinding name"
    assert dialed, "nothing was dialed at all: the test did not exercise the connect"
    assert all(_is_literal(host) for host, _ in dialed), (
        f"the transport dialed a NAME it could re-resolve: {dialed}"
    )
    assert {host for host, _ in dialed} == {PUBLIC}, f"dialed an unchecked address: {dialed}"


# --------------------------------------------------------------------------
# (b) special-purpose ranges
# --------------------------------------------------------------------------

#: Refused by the strict posture (a URL somebody else chose).
_STRICT_REFUSED = [
    "100.64.0.1", "100.100.100.200", "100.127.255.254",  # RFC 6598 carrier-grade NAT
    "192.0.0.1", "192.0.0.170",  # RFC 6890 IETF protocol assignments
    "198.18.0.1", "198.19.255.254",  # RFC 2544 benchmarking
    "240.0.0.1", "255.255.255.255",  # reserved, limited broadcast
    "64:ff9b::a00:1",  # NAT64 wrapping a private v4
    "64:ff9b::7f00:1",  # NAT64 wrapping loopback
    "64:ff9b:1::1",  # local-use NAT64
    "2001:db8::1",  # documentation
    "::ffff:7f00:1",  # v4-mapped loopback
    "2002:a00:1::",  # 6to4 wrapping a private v4
]

#: Refused under EVERY posture: never the operator's machine or LAN.
_NEVER_REACHABLE = [
    "240.0.0.1", "255.255.255.255", "2001:db8::1", "64:ff9b:1::1",
    "64:ff9b::a9fe:a9fe",  # NAT64 wrapping the cloud metadata address
    "::ffff:a9fe:a9fe",  # v4-mapped metadata address
    "2002:a9fe:a9fe::",  # 6to4 wrapping the metadata address
]

#: The operator's own network: refused strict, reachable under the permissive posture.
_OPERATOR_NETWORK = ["100.64.0.1", "192.0.0.1"]

#: Transition forms that wrap a v4 address (NAT64, v4-mapped). They are refused
#: under every posture today; a fix must keep refusing them, not open them up.
_WRAPPED_FORMS = [
    "64:ff9b::a00:1", "64:ff9b::7f00:1", "::ffff:7f00:1", "::ffff:5db8:d822", "64:ff9b::5db8:d822",
]

#: Ordinary public addresses.
_PUBLIC = [PUBLIC, "8.8.8.8", "2606:4700::1111"]


@pytest.mark.parametrize("ip", _STRICT_REFUSED)
def test_b_special_range_is_refused_by_the_strict_posture(monkeypatch, ip):
    _fake_dns(monkeypatch, {"special.example.test": [ip]})
    with pytest.raises(EgressDenied):
        guard_host("special.example.test", allow_private=False)


@pytest.mark.parametrize("ip", _NEVER_REACHABLE)
def test_b_never_reachable_range_is_refused_even_for_the_operator(monkeypatch, ip):
    _fake_dns(monkeypatch, {"special.example.test": [ip]})
    with pytest.raises(EgressDenied):
        guard_host("special.example.test", allow_private=True)


@pytest.mark.parametrize("ip", _OPERATOR_NETWORK)
def test_b_the_operators_own_network_stays_reachable_under_the_permissive_posture(monkeypatch, ip):
    """Tailscale lives in the carrier-grade NAT range: refusing it for the
    operator would be a lost path dressed as a guard."""
    _fake_dns(monkeypatch, {"special.example.test": [ip]})
    guard_host("special.example.test", allow_private=True)


@pytest.mark.parametrize("ip", _WRAPPED_FORMS)
@pytest.mark.parametrize("allow_private", [False, True])
def test_b_wrapped_v4_forms_stay_refused_under_both_postures(monkeypatch, ip, allow_private):
    _fake_dns(monkeypatch, {"special.example.test": [ip]})
    with pytest.raises(EgressDenied):
        guard_host("special.example.test", allow_private=allow_private)


@pytest.mark.parametrize("ip", _PUBLIC)
def test_b_public_addresses_still_pass_both_postures(monkeypatch, ip):
    _fake_dns(monkeypatch, {"special.example.test": [ip]})
    guard_host("special.example.test", allow_private=False)
    guard_host("special.example.test", allow_private=True)


# --------------------------------------------------------------------------
# (c) schemes: http and https only, on every hop
# --------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["ftp", "gopher"])
def test_c_a_redirect_to_a_non_http_scheme_is_refused_before_any_connect(serve, monkeypatch, scheme):
    server = serve()
    _fake_dns(monkeypatch, {"start.example.test": [PUBLIC], "other.example.test": [PUBLIC]})
    dialed = _dial_leaf(monkeypatch, local_port=server.port, public_names=("other.example.test",))
    server.routes["/go"] = (302, f"{scheme}://other.example.test/file")

    with pytest.raises(live_research.LiveResearchError, match="only http and https"):
        live_research._fetch_page(f"http://start.example.test:{server.port}/go")

    assert server.hits == ["/go"]
    assert len(dialed) == 1, f"a {scheme} hop was dialed: {dialed}"


@pytest.mark.parametrize(
    "url",
    ["ftp://other.example.test/file", "gopher://other.example.test/", "file:///etc/hostname", "data:text/plain,x"],
)
def test_c_the_first_url_is_scheme_checked_too(monkeypatch, url):
    _fake_dns(monkeypatch, {"other.example.test": [PUBLIC]})
    dialed = _dial_leaf(monkeypatch)
    with pytest.raises(live_research.LiveResearchError, match="only http and https"):
        live_research._fetch_page(url)
    assert dialed == []


# --------------------------------------------------------------------------
# (d) the keyed-search POST must not carry its bearer token across a redirect
# --------------------------------------------------------------------------


def test_d_post_json_does_not_follow_a_redirect_and_the_token_never_arrives(serve):
    provider, canary = serve(), serve()
    provider.routes["/search"] = (302, f"http://127.0.0.1:{canary.port}/steal")

    with pytest.raises(live_research.LiveResearchError, match=r"302.*/steal"):
        live_research._post_json(
            f"http://127.0.0.1:{provider.port}/search",
            {"query": "q"},
            {"Content-Type": "application/json", "Authorization": "Bearer SECRET-TOKEN"},
        )

    assert provider.hits == ["/search"]
    assert canary.hits == [], "the redirect was followed"
    assert "Bearer SECRET-TOKEN" not in canary.auth


def test_d_a_redirecting_provider_fails_the_keyed_research_loudly(serve, monkeypatch):
    provider, canary = serve(), serve()
    provider.routes["/search"] = (307, f"http://127.0.0.1:{canary.port}/steal")
    monkeypatch.setenv("HYDRA_LIVE_PROVIDER", "custom")
    monkeypatch.setenv("HYDRA_LIVE_ENDPOINT", f"http://127.0.0.1:{provider.port}/search")
    monkeypatch.setenv("HYDRA_LIVE_KEY", "SECRET-TOKEN")

    with pytest.raises(live_research.LiveResearchError, match="307"):
        live_research.research("q", no_fetch=True)

    assert canary.hits == []
    assert "Bearer SECRET-TOKEN" not in canary.auth


# --------------------------------------------------------------------------
# (e) the search POST against a real redirecting server
# --------------------------------------------------------------------------


def test_e_the_search_post_refuses_a_redirect_to_an_internal_canary(serve, monkeypatch):
    """Supplements the sealed fake-door test with real sockets and the real guard."""
    ddg, canary = serve(), serve()
    ddg.routes["/html/"] = (302, f"http://127.0.0.1:{canary.port}/internal")
    _fake_dns(monkeypatch, {"ddg.example.test": [PUBLIC]})
    _dial_leaf(monkeypatch, local_port=ddg.port, public_names=("ddg.example.test",))
    monkeypatch.setattr(live_research, "_DDG_URL", f"http://ddg.example.test:{ddg.port}/html/")

    with pytest.raises(live_research.LiveResearchError, match="refused"):
        live_research._duckduckgo_search("q", 3)

    assert ddg.hits == ["/html/"], "the search request never reached the real server"
    assert canary.hits == [], "the internal canary was contacted through a redirect"


# --------------------------------------------------------------------------
# one resolution per hop, original Host header kept
# --------------------------------------------------------------------------


def test_every_hop_resolves_exactly_once_and_keeps_the_original_host_header(serve, monkeypatch):
    server = serve()
    counts = _fake_dns(monkeypatch, {"start.example.test": [PUBLIC], "second.example.test": [PUBLIC]})
    dialed = _dial_leaf(
        monkeypatch, local_port=server.port, public_names=("start.example.test", "second.example.test")
    )
    server.routes["/hop"] = (302, f"http://second.example.test:{server.port}/final")

    text = live_research._fetch_page(f"http://start.example.test:{server.port}/hop")

    assert "served page" in text
    assert counts == {"start.example.test": 1, "second.example.test": 1}, counts
    assert [h for h, _ in dialed] == [PUBLIC, PUBLIC], dialed
    assert server.hosts == [f"start.example.test:{server.port}", f"second.example.test:{server.port}"]


# --------------------------------------------------------------------------
# (f) a pinned HTTPS connection still verifies the certificate by host name
# --------------------------------------------------------------------------


def _make_cert(tmp_path, name: str):
    key, crt = tmp_path / f"{name}.key", tmp_path / f"{name}.crt"
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
            "-keyout", str(key), "-out", str(crt), "-subj", f"/CN={name}",
            "-addext", f"subjectAltName=DNS:{name}",
            "-addext", "basicConstraints=critical,CA:TRUE",
        ],
        check=True,
        capture_output=True,
    )
    return key, crt


@pytest.fixture
def tls_server(tmp_path):
    started: list[_Srv] = []

    def make(cert_name: str) -> tuple[_Srv, object]:
        key, crt = _make_cert(tmp_path, cert_name)
        srv = _Srv()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(crt), str(key))
        srv.socket = context.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        started.append(srv)
        return srv, crt

    yield make
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _validate_public(url: str) -> None:
    guard_host(urllib.parse.urlparse(url).hostname or "", allow_private=False)


def test_f_a_pinned_https_fetch_verifies_the_certificate_against_the_original_name(tls_server, monkeypatch):
    server, crt = tls_server("pinned.example.test")
    monkeypatch.setenv("SSL_CERT_FILE", str(crt))
    _fake_dns(monkeypatch, {"pinned.example.test": [PUBLIC]})
    dialed = _dial_leaf(monkeypatch, local_port=server.port, public_names=("pinned.example.test",))

    req = urllib.request.Request(f"https://pinned.example.test:{server.port}/page")
    with open_checked(req, validate=_validate_public, timeout=10) as resp:
        body = resp.read()

    assert b"served page" in body
    assert [h for h, _ in dialed] == [PUBLIC], "the connect was not bound to the checked address"
    assert server.hosts == [f"pinned.example.test:{server.port}"]


def test_f_a_certificate_for_another_name_is_still_rejected_on_the_pinned_connection(tls_server, monkeypatch):
    server, crt = tls_server("other.example.test")
    monkeypatch.setenv("SSL_CERT_FILE", str(crt))  # trusted, but issued for a different name
    _fake_dns(monkeypatch, {"pinned.example.test": [PUBLIC]})
    _dial_leaf(monkeypatch, local_port=server.port, public_names=("pinned.example.test",))

    req = urllib.request.Request(f"https://pinned.example.test:{server.port}/page")
    with pytest.raises(urllib.error.URLError) as caught:
        open_checked(req, validate=_validate_public, timeout=10)

    assert isinstance(caught.value.reason, ssl.SSLCertVerificationError), caught.value
    assert server.hits == [], "the request was sent over a connection that failed verification"
