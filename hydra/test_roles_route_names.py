"""Roles name routing roles (BACKS rungs), never a model, provider family or tag."""
from __future__ import annotations

import logging

import pytest

from hydra import model_routing
from hydra import roles as roles_mod
from hydra.roles import (
    SELECTOR_DEFAULTS,
    RoleError,
    RoleSpec,
    resolve_roles_from_dict,
    should_unload_between_roles,
)

ROUTE_FAMILY = getattr(roles_mod, "ROUTE_FAMILY", "route:")


@pytest.fixture(autouse=True)
def _no_backs(monkeypatch):
    """Resolving roles must not need BACKS: every rung lookup here fails loudly."""
    def _down(*_a, **_k):
        raise model_routing.RungResolutionError("BACKS is down in this test")

    model_routing.set_resolver(_down)
    yield
    model_routing.set_resolver(None)


def test_selectors_name_routing_roles_only():
    roles = model_routing.load_routing().roles
    for selector, route in SELECTOR_DEFAULTS.items():
        assert isinstance(route, str), selector
        assert route in roles, f"{selector} -> {route!r} is not a routing role"


def test_default_roles_resolve_to_route_families_without_backs():
    rs = resolve_roles_from_dict({})
    for role in (rs.planner, rs.doer, rs.auditor):
        assert role.family.startswith(ROUTE_FAMILY), role
        assert ":" not in role.model.replace("router/", ""), role  # no model tag
    assert rs.planner.family != rs.auditor.family  # the grader is a different rung task


def test_route_key_selects_a_routing_role():
    rs = resolve_roles_from_dict({"agentic": {"roles": {"doer": {"route": "worker"}}}})
    assert rs.doer.family == ROUTE_FAMILY + "local"
    assert rs.doer.model == ""  # the local rung picks its model at call time


def test_unknown_route_is_a_role_error():
    with pytest.raises(RoleError):
        resolve_roles_from_dict({"agentic": {"roles": {"doer": {"route": "no-such-role"}}}})


def test_routed_roles_sharing_a_rung_warn_instead_of_failing(caplog):
    cfg = {"agentic": {"require_independent_auditor": True,
                       "roles": {"planner": {"route": "planner"}, "auditor": {"route": "planner"}}}}
    with caplog.at_level(logging.WARNING, logger="hydra.roles"):
        rs = resolve_roles_from_dict(cfg)
    assert rs.planner.family == rs.auditor.family
    assert "degraded" in caplog.text.lower()


def test_explicit_families_still_collide():
    cfg = {"agentic": {"require_independent_auditor": True, "roles": {
        "planner": {"provider": "p", "model": "m1", "family": "fam"},
        "doer": {"provider": "p", "model": "m2", "family": "other"},
        "auditor": {"provider": "p", "model": "m3", "family": "fam"}}}}
    with pytest.raises(RoleError):
        resolve_roles_from_dict(cfg)


def test_unload_only_between_different_local_models():
    a = RoleSpec("planner", "local", "m-a", ROUTE_FAMILY + "local")
    b = RoleSpec("doer", "local", "m-b", ROUTE_FAMILY + "local")
    gw = RoleSpec("auditor", "backs", "router/grade/auto", ROUTE_FAMILY + "grade")
    assert should_unload_between_roles(a, b)
    assert not should_unload_between_roles(a, a)
    assert not should_unload_between_roles(a, gw)
