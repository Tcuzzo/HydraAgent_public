"""The egress classification must answer the same on every Python release.

CPython changed ``IPv6Address.is_reserved`` and ``IPv6Address.is_private`` so
that an IPv4-mapped address (``::ffff:a.b.c.d``) is judged by its EMBEDDED IPv4
address. Older releases treated the whole ``::ffff:0:0/96`` range as reserved,
so every address in it was refused; newer releases classify the range by the
IPv4 address inside it, so the very same address can come out public.

The guard therefore answered the same address differently depending on the
interpreter it happened to run on: a refusal on one release, a fetch on the
next. That is the drift this contract pins shut. The guard owns the decision
for the IPv4-mapped range itself, so the range is refused under BOTH postures
on every release, and it stays refused when the standard library classifies it
the newer way.

The sibling transition forms (NAT64, 6to4) and the ordinary addresses keep the
verdicts they have today, and they do not drift either: the fix must not widen
or narrow anything else.

Measured on CPython 3.11.7, 3.12.3, 3.12.13, 3.13.12 and 3.14.3: the mapped
range is the only thing that moved between them, apart from the two 6to4 forms
below, whose class the standard library itself changed in 3.12. Those two are
held to the property the guard owns -- their verdict must not drift with the
standard library -- rather than to one release's opinion of the 6to4 range.

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
