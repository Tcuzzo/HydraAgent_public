"""Hydra stores no model IDs: model_routing.yaml names BACKS rungs, resolved per call.

When the routing file itself can't be read, Hydra stops with a clear error that names
the file; it never swaps in a hidden in-code roster.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from hydra import model_routing as mrc

_SRC = Path(mrc.__file__)
_YAML = _SRC.with_name("model_routing.yaml")
_MODEL_LIKE = re.compile(r"^[a-z][a-z0-9.\-]*:[a-z0-9.\-]+$")


def _string_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_routing_yaml_entries_name_rungs_only():
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    for e in doc["roster"]:
        assert e.get("rung"), f"roster entry {e.get('id')} names no rung"
        stored = sorted({"model", "provider", "base_url"} & set(e))
        assert not stored, f"roster entry {e.get('id')} stores {stored}"


@pytest.fixture()
def fresh_cache():
    mrc.clear_cache()
    yield
    mrc.clear_cache()


def test_model_routing_code_names_no_model():
    consts = _string_constants(_SRC)
    leaked = sorted(c for c in consts if _MODEL_LIKE.match(c))
    assert not leaked, f"model IDs hardcoded in {_SRC.name}: {leaked}"


def test_no_in_code_default_roster():
    assert not hasattr(mrc, "DEFAULT"), "an in-code DEFAULT roster still exists"
    assert not hasattr(mrc, "_frozen_default")


def test_model_router_names_no_free_tier_literal():
    src = (_SRC.with_name("model_router.py")).read_text(encoding="utf-8")
    assert "DEFAULT.free_fallback_models" not in src
    assert 'free = ("nemotron' not in src


def test_missing_routing_file_fails_loudly(fresh_cache, tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(mrc.RoutingConfigError) as err:
        mrc.load_routing(missing)
    msg = str(err.value)
    assert str(missing) in msg and "model_routing" in msg


def test_malformed_routing_file_fails_loudly(fresh_cache, tmp_path):
    bad = tmp_path / "model_routing.yaml"
    bad.write_text("roster: [this is: not: valid\n", encoding="utf-8")
    with pytest.raises(mrc.RoutingConfigError) as err:
        mrc.load_routing(bad)
    assert str(bad) in str(err.value)


def test_unknown_roster_id_fails_loudly(fresh_cache, tmp_path):
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    doc["routing"]["chat_profiles"]["auto"] = "no-such-roster-id"
    bad = tmp_path / "model_routing.yaml"
    bad.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(mrc.RoutingConfigError) as err:
        mrc.load_routing(bad)
    assert "no-such-roster-id" in str(err.value)


def test_loud_error_is_not_cached(fresh_cache, tmp_path):
    path = tmp_path / "model_routing.yaml"
    with pytest.raises(mrc.RoutingConfigError):
        mrc.load_routing(path)
    path.write_text(_YAML.read_text(encoding="utf-8"), encoding="utf-8")
    assert mrc.load_routing(path).source_path == str(path)


def test_free_tier_without_key_comes_from_free_tagged_roster(fresh_cache, tmp_path):
    doc = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    path = tmp_path / "model_routing.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    want = tuple(
        f"rung-{e['rung']}" for e in doc["roster"]
        if "free" in (e.get("role_tags") or ()) and not e.get("local")
    )
    assert want
    assert mrc.load_routing(path).free_fallback_models == want
