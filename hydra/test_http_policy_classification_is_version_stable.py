"""The egress classification must answer the same on every Python release.

CPython changes the standard library's own address tables between releases, so
the verdict for the SAME address can change with the interpreter that happens
to run the guard. Measured on the three interpreters on this host -- CPython
3.11.7, 3.12.3 and 3.14.3 -- the ranges whose class moved are these EIGHT:

  * ``::ffff:0:0/96`` (IPv4-mapped): 3.11 treats the whole range as reserved,
    3.12 and later judge an address in it by the IPv4 address inside it, so the
    very same address can come out public.
  * ``2002::/16`` (6to4): public on 3.11, private from 3.12 on.
  * ``2001:1::1/128`` (PCP anycast), ``2001:1::2/128`` (TURN anycast),
    ``2001:3::/32`` (AMT), ``2001:4:112::/48`` (AS112-v6), ``2001:20::/28``
    and ``2001:30::/28`` (ORCHIDv2): private on 3.11, public from 3.12 on.

The direction of that last group is the dangerous one: under the STRICT posture
(a URL somebody else chose) 3.11 REFUSED those six ranges and 3.12 and later
ALLOW them. The guard owns all eight ranges now, so its verdict is the guard's
own on every release and does not follow the standard library when the
standard library changes its mind. The guard's own table is checked BEFORE the
``ipaddress`` properties, so a pinned row wins.

The corpus test at the end is what catches the NEXT move. It reads the
standard library's own registry tables AT TEST TIME, so a range that a future
release adds or reclassifies shows up as a range with no expected class, and
the contract fails until that range is pinned in ``hydra/http_policy.py``'s
``_SPECIAL`` table. That failure IS the alarm.

Narrowing is INTENTIONAL and contract-mandated. The ``::ffff:0:0/96`` row
refuses the IPv4-mapped form of EVERY address in that range, including the
IPv4-mapped LOOPBACK under the PERMISSIVE posture, because the sealed
contract ``hydra/test_http_pinned_address_and_special_ranges.py`` demands it:
its ``_WRAPPED_FORMS`` list requires ``::ffff:7f00:1`` to be refused under
BOTH postures. Plain ``127.0.0.1``, ``10.0.0.1`` and ``192.168.0.1`` remain
ALLOWED under the permissive posture -- only the WRAPPED forms are refused --
so the operator keeps his own machine and his own LAN. A reader must know
that: the narrowing is a decision, not an accident.

No mock replaces the guard and no network call is made. The guard is called on
numeric literals, which resolve locally, and the classifier is called on
``ipaddress.ip_address`` values. The only thing patched here is the STANDARD
LIBRARY's own classification, because that is the ENVIRONMENT the contract has
to survive, not the code under test.

Target before running (sniper discipline):
  pytest hydra/test_http_policy_classification_is_version_stable.py -q
"""
from __future__ import annotations

import contextlib
import ipaddress

import pytest

from hydra import http_policy
from hydra.http_policy import EgressDenied, guard_host

#: The IPv4-mapped range. Every address in it is refused under both postures:
#: the guard owns the range instead of borrowing the standard library's opinion
#: of it, because that opinion changed between Python releases.
_MAPPED_RANGE = [
    "::ffff:0:0",  # 0.0.0.0
    "::ffff:0:1",  # 0.0.0.1
    "::ffff:5db8:d822",  # 93.184.216.34, a public v4
    "::ffff:7f00:1",  # 127.0.0.1, loopback
    "::ffff:808:808",  # 8.8.8.8, a public v4
    "::ffff:a00:1",  # 10.0.0.1, private
    "::ffff:a9fe:a9fe",  # 169.254.169.254, the cloud metadata address
    "::ffff:c0a8:1",  # 192.168.0.1, private
    "::ffff:6440:1",  # 100.64.0.1, carrier-grade NAT
    "::ffff:ffff:ffff",  # 255.255.255.255, limited broadcast
]

#: The transition forms that WRAP a v4 address without being in the mapped
#: range, and the verdict each of them keeps on every release. Expected outcome
#: per address: (class, refused under the strict posture, refused under the
#: permissive one).
_SIBLING_TRANSITION_FORMS = [
    ("64:ff9b::5db8:d822", ("reserved", True, True)),  # NAT64 wrapping a public v4
    ("64:ff9b::7f00:1", ("reserved", True, True)),  # NAT64 wrapping loopback
    ("64:ff9b::a00:1", ("reserved", True, True)),  # NAT64 wrapping a private v4
    ("64:ff9b::a9fe:a9fe", ("link-local", True, True)),  # NAT64 wrapping the metadata address
    ("64:ff9b:1::1", ("reserved", True, True)),  # local-use NAT64
    ("2002:a9fe:a9fe::", ("link-local", True, True)),  # 6to4 wrapping the metadata address
]

#: The 6to4 forms that wrap an ordinary v4 address. The standard library's own
#: opinion of the 6to4 range moved between Python releases (public on 3.11,
#: private from 3.12 on), so what the guard OWNS here is that its verdict does
#: not drift with the standard library. That is what the contract holds them to.
_RELEASE_VARIABLE_TRANSITION_FORMS = [
    "2002:a00:1::",  # 6to4 wrapping a private v4
    "2002:5db8:d822::",  # 6to4 wrapping a public v4
]

#: The 6to4 range itself. The standard library's opinion of it moved between
#: Python releases (public on 3.11, private from 3.12 on), so the guard owns
#: the range, exactly as it owns the IPv4-mapped range.
_SIXTOFOUR_NETWORK = ipaddress.ip_network("2002::/16")

#: The transition forms that WRAP the cloud metadata address. No posture and
#: no range may reach them.
_METADATA_FORMS = [
    "::ffff:a9fe:a9fe",  # v4-mapped metadata address
    "64:ff9b::a9fe:a9fe",  # NAT64 wrapping the metadata address
    "2002:a9fe:a9fe::",  # 6to4 wrapping the metadata address
]

#: The ordinary addresses. Expected outcome per address:
#: (class, refused under the strict posture, refused under the permissive one).
_PLAIN_ADDRESSES = [
    ("93.184.216.34", ("public", False, False)),  # public v4
    ("8.8.8.8", ("public", False, False)),  # public v4
    ("2606:4700::1111", ("public", False, False)),  # public v6
    ("10.0.0.1", ("private", True, False)),  # RFC 1918
    ("100.64.0.1", ("private", True, False)),  # RFC 6598 carrier-grade NAT
    ("192.0.0.1", ("private", True, False)),  # RFC 6890 protocol assignments
    ("169.254.169.254", ("link-local", True, True)),  # the cloud metadata address
    ("127.0.0.1", ("loopback", True, False)),  # the operator's own machine
    ("::", ("unspecified", True, True)),
    ("2001:db8::1", ("reserved", True, True)),  # documentation
]

# --------------------------------------------------------------------------
# the ranges whose class the standard library moved, and the corpus
# --------------------------------------------------------------------------

#: The six IETF protocol-assignment ranges whose class moved between Python
#: releases: private on 3.11, public from 3.12 on. The direction is the
#: dangerous one -- the strict posture refused them on 3.11 and ALLOWS them
#: from 3.12 on -- so the guard owns them.
_MOVED_RANGES = (
    ("2001:1::1/128", "PCP anycast"),
    ("2001:1::2/128", "TURN anycast"),
    ("2001:3::/32", "AMT"),
    ("2001:4:112::/48", "AS112-v6"),
    ("2001:20::/28", "ORCHIDv2"),
    ("2001:30::/28", "ORCHIDv2 (deprecated)"),
)

#: One address inside each moved range.
_MOVED_ADDRESSES = (
    "2001:1::1",
    "2001:1::2",
    "2001:3::1",
    "2001:4:112::1",
    "2001:20::1",
    "2001:30::1",
)

#: The moved ranges as network objects, for the standard-library views below.
_MOVED_NETWORKS = tuple(ipaddress.ip_network(text) for text, _name in _MOVED_RANGES)

#: The corpus the guard is held to, besides the standard library's own registry
#: tables: the ranges the guard pins, one address inside each of them, the
#: transition forms, and the ordinary addresses. Every entry is a network; the
#: address probed is the network's own address.
_STATIC_CORPUS = (
    # the ranges the guard pins
    "100.64.0.0/10", "100.64.0.1/32",
    "192.0.0.0/24", "192.0.0.1/32",
    "198.18.0.0/15", "198.18.0.1/32",
    "2001::/32", "2001::1/128",
    "2001:2::/48", "2001:2::1/128",
    "2001:10::/28", "2001:10::1/128",
    "2001:db8::/32", "2001:db8::1/128",
    "3fff::/20", "3fff::1/128",
    "5f00::/16", "5f00::1/128",
    "64:ff9b:1::/48", "64:ff9b:1::1/128",
    "::ffff:0:0/96", "::ffff:0:0/128", "::ffff:0:1/128",
    "::ffff:5db8:d822/128", "::ffff:7f00:1/128", "::ffff:a9fe:a9fe/128",
    "::ffff:a00:1/128", "::ffff:6440:1/128", "::ffff:c0a8:1/128",
    "::ffff:808:808/128", "::ffff:ffff:ffff/128",
    "2002::/16", "2002:a00:1::/128", "2002:5db8:d822::/128",
    "2002:a9fe:a9fe::/128",
    # the six ranges that moved, and one address in each
    "2001:1::1/128", "2001:1::2/128", "2001:3::/32", "2001:3::1/128",
    "2001:4:112::/48", "2001:4:112::1/128", "2001:20::/28", "2001:20::1/128",
    "2001:30::/28", "2001:30::1/128",
    # the transition forms
    "64:ff9b::/96", "64:ff9b::5db8:d822/128", "64:ff9b::7f00:1/128",
    "64:ff9b::a00:1/128", "64:ff9b::a9fe:a9fe/128",
    # the ordinary addresses
    "93.184.216.34/32", "8.8.8.8/32", "2606:4700::1111/128", "10.0.0.1/32",
    "169.254.169.254/32", "127.0.0.1/32", "::/128",
)

#: The expected class of every corpus entry, MEASURED on the three
#: interpreters and agreed by all three. A map, not a value read out of the
#: standard library: the point of the corpus is that the standard library's
#: opinion is not the standard. A range that a future release adds to its
#: registry tables has no entry here, and the corpus test fails until it is
#: pinned in ``hydra/http_policy.py``'s ``_SPECIAL`` table.
_EXPECTED = (
    ("0.0.0.0/8", "unspecified"),
    ("8.8.8.8/32", "public"),
    ("10.0.0.0/8", "private"),
    ("10.0.0.1/32", "private"),
    ("93.184.216.34/32", "public"),
    ("100.64.0.0/10", "private"),
    ("100.64.0.1/32", "private"),
    ("127.0.0.0/8", "loopback"),
    ("127.0.0.1/32", "loopback"),
    ("169.254.0.0/16", "link-local"),
    ("169.254.169.254/32", "link-local"),
    ("172.16.0.0/12", "private"),
    ("192.0.0.0/24", "private"),
    ("192.0.0.0/29", "private"),
    ("192.0.0.1/32", "private"),
    ("192.0.0.9/32", "private"),
    ("192.0.0.10/32", "private"),
    ("192.0.0.170/31", "private"),
    ("192.0.2.0/24", "private"),
    ("192.168.0.0/16", "private"),
    ("198.18.0.0/15", "benchmark"),
    ("198.18.0.1/32", "benchmark"),
    ("198.51.100.0/24", "private"),
    ("203.0.113.0/24", "private"),
    ("240.0.0.0/4", "reserved"),
    ("255.255.255.255/32", "reserved"),
    ("::/8", "unspecified"),
    ("::/128", "unspecified"),
    ("::1/128", "reserved"),
    ("::ffff:0:0/96", "unspecified"),
    ("::ffff:0:0/128", "unspecified"),
    ("::ffff:0:1/128", "reserved"),
    ("::ffff:808:808/128", "reserved"),
    ("::ffff:a00:1/128", "reserved"),
    ("::ffff:5db8:d822/128", "reserved"),
    ("::ffff:6440:1/128", "reserved"),
    ("::ffff:7f00:1/128", "reserved"),
    ("::ffff:a9fe:a9fe/128", "link-local"),
    ("::ffff:c0a8:1/128", "reserved"),
    ("::ffff:ffff:ffff/128", "reserved"),
    ("64:ff9b::/96", "unspecified"),
    ("64:ff9b::a00:1/128", "reserved"),
    ("64:ff9b::5db8:d822/128", "reserved"),
    ("64:ff9b::7f00:1/128", "reserved"),
    ("64:ff9b::a9fe:a9fe/128", "link-local"),
    ("64:ff9b:1::/48", "reserved"),
    ("64:ff9b:1::1/128", "reserved"),
    ("100::/8", "reserved"),
    ("100::/64", "reserved"),
    ("200::/7", "reserved"),
    ("400::/6", "reserved"),
    ("800::/5", "reserved"),
    ("1000::/4", "reserved"),
    ("2001::/23", "reserved"),
    ("2001::/32", "reserved"),
    ("2001::1/128", "reserved"),
    ("2001:1::1/128", "private"),
    ("2001:1::2/128", "private"),
    ("2001:2::/48", "reserved"),
    ("2001:2::1/128", "reserved"),
    ("2001:3::/32", "private"),
    ("2001:3::1/128", "private"),
    ("2001:4:112::/48", "private"),
    ("2001:4:112::1/128", "private"),
    ("2001:10::/28", "reserved"),
    ("2001:10::1/128", "reserved"),
    ("2001:20::/28", "private"),
    ("2001:20::1/128", "private"),
    ("2001:30::/28", "private"),
    ("2001:30::1/128", "private"),
    ("2001:db8::/32", "reserved"),
    ("2001:db8::1/128", "reserved"),
    ("2002::/16", "unspecified"),
    ("2002:a00:1::/128", "private"),
    ("2002:5db8:d822::/128", "private"),
    ("2002:a9fe:a9fe::/128", "link-local"),
    ("2606:4700::1111/128", "public"),
    ("3fff::/20", "reserved"),
    ("3fff::1/128", "reserved"),
    ("4000::/3", "reserved"),
    ("5f00::/16", "reserved"),
    ("5f00::1/128", "reserved"),
    ("6000::/3", "reserved"),
    ("8000::/3", "reserved"),
    ("a000::/3", "reserved"),
    ("c000::/3", "reserved"),
    ("e000::/4", "reserved"),
    ("f000::/5", "reserved"),
    ("f800::/6", "reserved"),
    ("fc00::/7", "private"),
    ("fe00::/9", "reserved"),
    ("fe80::/10", "link-local"),
)

#: The expected class of every corpus entry, keyed by the NETWORK rather than
#: by its printed text: CPython 3.14 prints the mapped range as
#: ``::ffff:0.0.0.0/96``, so a string key would drift exactly like the
#: property this contract exists to pin shut.
_EXPECTED_CLASSES = {
    ipaddress.ip_network(text): kind for text, kind in _EXPECTED
}


@contextlib.contextmanager
def _newer_cpython_classification():
    """Make the standard library classify IPv4-mapped addresses the newer way.

    Newer CPython releases decide ``IPv6Address.is_reserved`` and
    ``IPv6Address.is_private`` for an IPv4-mapped address by the IPv4 address
    embedded in it; older releases treat the whole ``::ffff:0:0/96`` range as
    reserved. Patching the two standard-library properties to the newer rule
    lets this one contract run against BOTH releases on one interpreter. The
    properties are restored on the way out.
    """
    real_reserved = ipaddress.IPv6Address.is_reserved
    real_private = ipaddress.IPv6Address.is_private

    def is_reserved(self):
        mapped = self.ipv4_mapped
        if mapped is not None:
            return mapped.is_reserved
        return real_reserved.fget(self)

    def is_private(self):
        mapped = self.ipv4_mapped
        if mapped is not None:
            return mapped.is_private
        return real_private.fget(self)

    ipaddress.IPv6Address.is_reserved = property(is_reserved)
    ipaddress.IPv6Address.is_private = property(is_private)
    try:
        yield
    finally:
        ipaddress.IPv6Address.is_reserved = real_reserved
        ipaddress.IPv6Address.is_private = real_private


@contextlib.contextmanager
def _sixtofour_view(private: bool):
    """Make the standard library read the 6to4 range the way one release does.

    CPython 3.11 calls ``2002::/16`` public; 3.12 and later call it private.
    That is the ENVIRONMENT the guard has to survive, so it is the only thing
    patched here: the two standard-library properties. The properties are
    restored on the way out, and the guard itself is never mocked.
    """
    real_reserved = ipaddress.IPv6Address.is_reserved
    real_private = ipaddress.IPv6Address.is_private

    def is_reserved(self):
        if self in _SIXTOFOUR_NETWORK:
            return False
        return real_reserved.fget(self)

    def is_private(self):
        if self in _SIXTOFOUR_NETWORK:
            return private
        return real_private.fget(self)

    ipaddress.IPv6Address.is_reserved = property(is_reserved)
    ipaddress.IPv6Address.is_private = property(is_private)
    try:
        yield
    finally:
        ipaddress.IPv6Address.is_reserved = real_reserved
        ipaddress.IPv6Address.is_private = real_private


@contextlib.contextmanager
def _protocol_assignment_view(private: bool):
    """Make the standard library read the moved ranges the way one release does.

    CPython 3.11 calls the six IETF protocol-assignment ranges private (they
    sit inside the old ``2001::/23`` private block); 3.12 and later carve them
    OUT of that block and call them public. That is the ENVIRONMENT the guard
    has to survive, so it is the only thing patched here: the standard
    library's own ``is_private`` property. The property is restored on the way
    out, and the guard, the classifier and the addresses are never mocked.
    """
    real_private = ipaddress.IPv6Address.is_private

    def is_private(self):
        for network in _MOVED_NETWORKS:
            if self in network:
                return private
        return real_private.fget(self)

    ipaddress.IPv6Address.is_private = property(is_private)
    try:
        yield
    finally:
        ipaddress.IPv6Address.is_private = real_private


def _refused_by_the_door(address: str, allow_private: bool) -> bool:
    """Whether the door refuses `address` under one posture."""
    try:
        guard_host(address, allow_private=allow_private)
    except EgressDenied:
        return True
    return False


def _outcome(address: str) -> tuple[str, bool, bool]:
    """The address's class, and whether it is refused under each posture."""
    kind = http_policy._classify(ipaddress.ip_address(address))
    return (
        kind,
        _refused_by_the_door(address, False),
        _refused_by_the_door(address, True),
    )


# --------------------------------------------------------------------------
# (a) the IPv4-mapped range is refused under both postures
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address", _MAPPED_RANGE)
def test_a_the_ipv4_mapped_range_is_never_reachable_under_either_posture(address):
    """Every address in ``::ffff:0:0/96`` is refused, on every release."""
    kind, strict_refused, permissive_refused = _outcome(address)
    assert kind in http_policy._ALWAYS_REFUSED, (
        f"{address} was classified {kind!r}: a class no posture may reach"
    )
    assert strict_refused, f"{address} was reachable under the strict posture"
    assert permissive_refused, f"{address} was reachable under the permissive posture"


def test_a_the_whole_ipv4_mapped_range_is_refused_not_just_the_samples():
    """Sweep the range: the refusal is the guard's own, not a per-address pact."""
    network = ipaddress.ip_network("::ffff:0:0/96")
    step = 1 << 24  # one address per 16M of the range, 256 samples
    for packed in range(0, 1 << 32, step):
        address = ipaddress.IPv6Address(int(network.network_address) + packed)
        kind = http_policy._classify(address)
        assert kind in http_policy._ALWAYS_REFUSED, (
            f"{address} was classified {kind!r} inside the IPv4-mapped range"
        )
        assert _refused_by_the_door(str(address), False), f"{address} was reachable"
        assert _refused_by_the_door(str(address), True), f"{address} was reachable"


# --------------------------------------------------------------------------
# (b) the same refusals hold when the standard library changes its mind
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address", _MAPPED_RANGE)
def test_b_the_ipv4_mapped_range_stays_refused_when_the_stdlib_judges_it_by_the_embedded_v4(address):
    """The verdict must not depend on the interpreter release."""
    with _newer_cpython_classification():
        kind, strict_refused, permissive_refused = _outcome(address)
    assert kind in http_policy._ALWAYS_REFUSED, (
        f"{address} was classified {kind!r} on the newer standard library"
    )
    assert strict_refused, f"{address} was reachable under the strict posture"
    assert permissive_refused, f"{address} was reachable under the permissive posture"


def test_b_the_whole_ipv4_mapped_range_stays_refused_on_the_newer_standard_library():
    """Sweep the range again, on the newer classification."""
    network = ipaddress.ip_network("::ffff:0:0/96")
    step = 1 << 24
    with _newer_cpython_classification():
        for packed in range(0, 1 << 32, step):
            address = ipaddress.IPv6Address(int(network.network_address) + packed)
            kind = http_policy._classify(address)
            assert kind in http_policy._ALWAYS_REFUSED, (
                f"{address} was classified {kind!r} on the newer standard library"
            )
            assert _refused_by_the_door(str(address), False), f"{address} was reachable"
            assert _refused_by_the_door(str(address), True), f"{address} was reachable"


# --------------------------------------------------------------------------
# (c) the sibling transition forms keep their verdicts, and do not drift
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address,expected", _SIBLING_TRANSITION_FORMS)
def test_c_the_sibling_transition_forms_keep_their_verdicts(address, expected):
    """NAT64 and 6to4 are untouched by the fix, on either release."""
    assert _outcome(address) == expected
    with _newer_cpython_classification():
        assert _outcome(address) == expected, "the verdict drifted with the standard library"


@pytest.mark.parametrize("address", _RELEASE_VARIABLE_TRANSITION_FORMS)
def test_c_the_release_variable_transition_forms_do_not_drift(address):
    """The 6to4 range is the standard library's opinion; the verdict may not drift.

    The standard library changed its own mind about the 6to4 range between
    Python releases. The guard's own decision must not move with it.
    """
    current = _outcome(address)
    with _newer_cpython_classification():
        newer = _outcome(address)
    assert newer == current, "the verdict drifted with the standard library"


# --------------------------------------------------------------------------
# (d) the ordinary addresses keep their verdicts
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address,expected", _PLAIN_ADDRESSES)
def test_d_the_ordinary_addresses_keep_their_verdicts(address, expected):
    """Public stays public, private stays private, reserved stays reserved."""
    assert _outcome(address) == expected
    with _newer_cpython_classification():
        assert _outcome(address) == expected, "the verdict drifted with the standard library"


# --------------------------------------------------------------------------
# (e) the 6to4 range is the guard's own, on every release
# --------------------------------------------------------------------------


def test_e_the_guard_owns_both_release_variable_ranges():
    """The guard's own table carries the ranges the standard library moved.

    Two ranges changed their class between Python releases: the IPv4-mapped
    range ``::ffff:0:0/96`` and the 6to4 range ``2002::/16``. The guard
    carries both, so its verdict cannot follow the standard library.

    The ranges are compared as NETWORK OBJECTS, not as their printed form:
    CPython 3.14 prints the mapped range as ``::ffff:0.0.0.0/96``, so a
    string key would drift exactly like the property this contract exists to
    pin shut.
    """
    owned = {network: kind for network, kind in http_policy._SPECIAL}
    assert owned.get(ipaddress.ip_network("::ffff:0:0/96")) == "reserved", owned
    assert owned.get(ipaddress.ip_network("2002::/16")) == "private", owned


@pytest.mark.parametrize("address", _RELEASE_VARIABLE_TRANSITION_FORMS)
def test_e_the_sixtofour_range_is_refused_by_the_strict_posture(address):
    """A 6to4-wrapped address is refused for a URL somebody else chose."""
    kind, strict_refused, _permissive_refused = _outcome(address)
    assert kind == "private", f"{address} was classified {kind!r}"
    assert strict_refused, f"{address} was reachable under the strict posture"


@pytest.mark.parametrize("address", _RELEASE_VARIABLE_TRANSITION_FORMS)
def test_e_the_sixtofour_verdict_does_not_drift_with_the_standard_library(address):
    """The verdict must not depend on the interpreter release.

    The standard library calls the 6to4 range public on 3.11 and private from
    3.12 on. The guard's own verdict is the same under BOTH opinions, and it
    is the refusing one.
    """
    with _sixtofour_view(private=False):  # the 3.11 view: the range is public
        older = _outcome(address)
    with _sixtofour_view(private=True):  # the 3.12+ view: the range is private
        newer = _outcome(address)
    assert older == newer, "the verdict drifted with the standard library"
    assert older[0] == "private", f"{address} was classified {older[0]!r}"
    assert older[1], f"{address} was reachable under the strict posture"


@pytest.mark.parametrize("address", _METADATA_FORMS)
def test_e_the_metadata_address_stays_unreachable_in_every_transition_form(address):
    """No transition form may smuggle the cloud metadata address through."""
    for private in (False, True):
        with _sixtofour_view(private=private):
            kind, strict_refused, permissive_refused = _outcome(address)
            assert kind in http_policy._ALWAYS_REFUSED, (
                f"{address} was classified {kind!r}: a class no posture may reach"
            )
            assert strict_refused, f"{address} was reachable under the strict posture"
            assert permissive_refused, f"{address} was reachable under the permissive posture"


@pytest.mark.parametrize("address", _MAPPED_RANGE)
def test_e_the_ipv4_mapped_range_stays_refused_under_every_sixtofour_view(address):
    """The range the guard closed first stays closed under either view."""
    for private in (False, True):
        with _sixtofour_view(private=private):
            kind, strict_refused, permissive_refused = _outcome(address)
            assert kind in http_policy._ALWAYS_REFUSED, (
                f"{address} was classified {kind!r} inside the IPv4-mapped range"
            )
            assert strict_refused, f"{address} was reachable under the strict posture"
            assert permissive_refused, f"{address} was reachable under the permissive posture"


@pytest.mark.parametrize("address,expected", _PLAIN_ADDRESSES)
def test_e_the_ordinary_addresses_keep_their_verdicts_under_every_sixtofour_view(address, expected):
    """Public stays public, private stays private, reserved stays reserved."""
    for private in (False, True):
        with _sixtofour_view(private=private):
            assert _outcome(address) == expected, "the verdict drifted with the standard library"


@pytest.mark.parametrize("address,expected", _SIBLING_TRANSITION_FORMS)
def test_e_the_sibling_transition_forms_keep_their_verdicts_under_every_sixtofour_view(address, expected):
    """NAT64 and 6to4 keep the verdict they have today, under either view."""
    for private in (False, True):
        with _sixtofour_view(private=private):
            assert _outcome(address) == expected, "the verdict drifted with the standard library"


# --------------------------------------------------------------------------
# (f) the six ranges that moved, and the structural corpus that catches the
#     NEXT move
# --------------------------------------------------------------------------


@pytest.mark.parametrize("address", _MOVED_ADDRESSES)
def test_f_the_moved_ranges_are_refused_by_the_strict_posture(address):
    """A URL somebody else chose never reaches a protocol-assignment range.

    On 3.11 these six ranges were refused under the strict posture; from 3.12
    on the standard library calls them public and the strict posture ALLOWED
    them. The guard owns them, so the refusal is the same on every release.
    """
    with pytest.raises(EgressDenied):
        guard_host(address, allow_private=False)


@pytest.mark.parametrize("address", _MOVED_ADDRESSES)
def test_f_the_moved_ranges_stay_reachable_under_the_permissive_posture(address):
    """The permissive posture keeps the operator's reach to his own networks.

    The permissive posture exists so the operator can reach his own services
    and overlay networks, and it allows these ranges on every release today.
    Pinning them for the strict posture must not take that reach away.
    """
    guard_host(address, allow_private=True)  # must not raise


@pytest.mark.parametrize("address", _MOVED_ADDRESSES)
def test_f_the_moved_ranges_keep_one_verdict_under_both_standard_library_views(address):
    """The verdict must not depend on the interpreter release.

    The 3.11 view calls the range private; the 3.12+ view calls it public.
    The guard's own verdict is the same under BOTH opinions, and it is the
    refusing one under the strict posture.
    """
    with _protocol_assignment_view(private=True):  # the 3.11 view
        older = _outcome(address)
    with _protocol_assignment_view(private=False):  # the 3.12+ view
        newer = _outcome(address)
    assert older == newer, "the verdict drifted with the standard library"
    assert older[0] == "private", f"{address} was classified {older[0]!r}"
    assert older[1], f"{address} was reachable under the strict posture"
    assert not older[2], f"{address} was refused under the permissive posture"


def _registry_networks():
    """Every network in the standard library's own registry tables, read now.

    The tables are read off the classes at test time, never hardcoded here, so
    a release that adds a range shows up in the corpus with nothing pinning
    it. ``_private_networks_exceptions`` is read as well: that is the table
    where a release CARVES a range out of a private block, which is exactly
    how the six ranges moved.
    """
    found = []
    for cls in (ipaddress.IPv4Address, ipaddress.IPv6Address):
        constants = cls._constants
        for attribute in (
            "_private_networks",
            "_private_networks_exceptions",
            "_reserved_networks",
        ):
            found.extend(list(getattr(constants, attribute, ())))
        reserved = getattr(constants, "_reserved_network", None)
        if reserved is not None:
            found.append(reserved)
    unique = {}
    for network in found:
        unique[network] = None
    return list(unique)


def _corpus():
    """The canonical corpus: the registry tables, the pinned ranges, the rest."""
    networks = _registry_networks()
    networks.extend(ipaddress.ip_network(text) for text in _STATIC_CORPUS)
    networks.extend(network for network, _kind in http_policy._SPECIAL)
    unique = {}
    for network in networks:
        unique[network] = None
    return list(unique)


def _corpus_problems(networks):
    """The corpus entries the guard does not hold to, as readable messages."""
    problems = []
    for network in networks:
        expected = _EXPECTED_CLASSES.get(network)
        if expected is None:
            problems.append(
                f"{network} is in the standard library's registry tables but has no "
                f"expected class: pin it in hydra/http_policy.py's _SPECIAL table and "
                f"add it to _EXPECTED"
            )
            continue
        kind = http_policy._classify(network.network_address)
        if kind != expected:
            problems.append(f"{network} was classified {kind!r}, expected {expected!r}")
    return problems


def test_f_the_structural_corpus_holds_every_verdict():
    """Every range in the corpus has the class the guard must give it.

    The corpus is the standard library's own registry tables at test time,
    the ranges the guard pins, the transition forms and the ordinary
    addresses. Every verdict is read from the EXPECTED MAP, never from the
    standard library: the standard library's opinion is what this contract
    refuses to depend on.
    """
    problems = _corpus_problems(_corpus())
    assert not problems, "the guard's verdict is not pinned: " + " | ".join(problems)


def test_f_the_structural_guard_names_the_range_it_does_not_know():
    """The alarm must name the range and say where to pin it.

    This is the failure the corpus test raises for the NEXT move, proved here
    on a range nobody pins: the message has to name the range and point at
    the guard's own table, or the alarm would be a shrug.
    """
    unknown = ipaddress.ip_network("2001:db9::/48")
    assert unknown not in _EXPECTED_CLASSES
    problems = _corpus_problems([unknown])
    assert problems, "the structural guard did not notice an unpinned range"
    assert "2001:db9::/48" in problems[0]
    assert "_SPECIAL" in problems[0]
    assert "hydra/http_policy.py" in problems[0]


def test_f_every_range_the_guard_pins_is_in_the_expected_map():
    """The map covers the guard's own table, so the pinning is proven.

    A range the guard pins but the map does not know is a range nothing holds
    the guard to.
    """
    unpinned = [
        str(network)
        for network, _kind in http_policy._SPECIAL
        if network not in _EXPECTED_CLASSES
    ]
    assert not unpinned, (
        "these ranges are pinned in the guard's table but have no expected class: "
        + ", ".join(unpinned)
    )


def test_f_the_moved_ranges_are_pinned_in_the_guard_table():
    """The guard's own table carries the six ranges that moved.

    Compared as NETWORK OBJECTS, not as their printed form: a string key
    would drift exactly like the property this contract exists to pin shut.
    """
    owned = {network: kind for network, kind in http_policy._SPECIAL}
    for text, _name in _MOVED_RANGES:
        network = ipaddress.ip_network(text)
        assert owned.get(network) == "private", (
            f"{text} is not pinned as private in the guard's table: {owned}"
        )
