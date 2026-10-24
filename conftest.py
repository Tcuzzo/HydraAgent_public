"""Shared test setup for hydra/test_*.

Unit tests never reach BACKS: each rung resolves offline to a placeholder route named
after the rung itself, so tests check routing by rung and no test names a real model.
The live proof (hydra ask against BACKS) exercises the real resolver.
"""
from __future__ import annotations

import pytest

from hydra import model_routing, rung_resolver


def offline_resolver(rung: str, *, local: bool = False, **_: object) -> tuple[str, str]:
    if local:
        return rung_resolver.LOCAL, f"rung-{rung}"
    return rung_resolver.GATEWAY, f"rung-{rung}"


@pytest.fixture(autouse=True)
def _offline_rungs():
    model_routing.set_resolver(offline_resolver)
    yield
    model_routing.set_resolver(None)
