"""Legacy routing-file compatibility and explicit runtime fallback contracts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from hydra.model_routing import load_routing, clear_cache


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Loader tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreeFallbackLoader:
    """model_routing.py parses + exposes free_fallback_models correctly."""

    def _make_minimal_cfg(self, free_tagged=("cloud-auto",)):
        """Minimal valid v2 config: roster entries name BACKS rungs only."""
        def tags(eid, base):
            return base + (["free"] if eid in free_tagged else [])
        cfg = {
            "schema": "hydra.model_routing.v2",
            "roster": [
                {"id": "cloud-doer", "rung": "work", "role_tags": tags("cloud-doer", ["chat", "intent"])},
                {"id": "cloud-planner", "rung": "plan",
                 "role_tags": tags("cloud-planner", ["planner", "work", "auditor", "verifier"])},
                {"id": "cloud-auto", "rung": "auto", "role_tags": tags("cloud-auto", ["fallback"])},
                {"id": "local-worker", "rung": "local_gpu", "local": True,
                 "role_tags": ["worker", "local", "router", "read"]},
            ],
            "routing": {
                "chat_profiles": {"auto": "cloud-doer", "cloud": "cloud-planner", "local": "local-worker"},
                "complexity":    {"simple": "local-worker", "moderate": "cloud-doer",
                                  "complex": "cloud-planner", "critical": "cloud-planner"},
                "roles": {
                    "router": "local-worker", "worker": "local-worker",
                    "doer": "cloud-doer", "planner": "cloud-planner",
                    "auditor": "cloud-planner", "work": "cloud-planner", "intent": "cloud-doer",
                },
                "verifier": "cloud-planner",
            },
        }
        return cfg

    def _load(self, tmp_path, cfg):
        yaml_path = tmp_path / "model_routing.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        clear_cache()
        return load_routing(yaml_path)

    def test_free_tier_is_the_free_tagged_rungs_in_roster_order(self, tmp_path):
        """The free tier is whatever the free-tagged cloud rungs resolve to, in order."""
        routing = self._load(tmp_path, self._make_minimal_cfg(("cloud-doer", "cloud-auto")))
        assert list(routing.free_fallback_models) == ["rung-work", "rung-auto"]

    def test_free_tier_skips_an_unresolvable_rung(self, tmp_path, monkeypatch):
        """A free rung BACKS can't resolve is skipped, never fatal."""
        from hydra import model_routing as mr
        from hydra.rung_resolver import GATEWAY

        def resolver(rung, **_):
            if rung == "work":
                raise mr.RungResolutionError("work rung out")
            return GATEWAY, f"rung-{rung}"

        mr.set_resolver(resolver)
        routing = self._load(tmp_path, self._make_minimal_cfg(("cloud-doer", "cloud-auto")))
        assert list(routing.free_fallback_models) == ["rung-auto"]

    def test_no_in_code_default_free_tier(self):
        """No model names live in code: there is no in-code DEFAULT free tier."""
        import hydra.model_routing as mrc
        assert not hasattr(mrc, "DEFAULT")

    def test_production_yaml_free_tier_comes_from_free_tagged_rungs(self):
        """The live model_routing.yaml keeps at least one free-tagged cloud rung."""
        clear_cache()
        routing = load_routing()
        free = [e for e in routing.roster if "free" in e.role_tags and not e.local]
        assert free
        assert list(routing.free_fallback_models) == [e.model for e in free]


from types import SimpleNamespace
from hydra.model_router import ModelRouter, ModelConfig
from hydra.llm import ChatResponse, LlmError


def test_constructor_success_is_not_treated_as_provider_success(monkeypatch, tmp_path, caplog):
    calls = []
    class Client:
        def __init__(self, provider):
            self.provider = provider
        def chat(self, messages, *, model, **kwargs):
            calls.append((self.provider, model))
            if self.provider == 'primary':
                raise LlmError('No budget', kind='budget')
            return ChatResponse('actual reply', model, 'stop', 1, 1, {})
    def factory(provider, **kwargs):
        return Client(provider), SimpleNamespace(name=provider, model='default', endpoint='https://' + provider, api_key=None)
    monkeypatch.setattr('hydra.model_router.make_client', factory)
    router = ModelRouter(config_path=tmp_path / 'absent')
    client = router._create_client(ModelConfig('worker', 'primary', 'primary-model', family='A',
        fallbacks=[{'provider': 'backup', 'model': 'backup-model', 'family': 'B'}]))
    assert calls == []
    assert client.chat([], model='primary-model').content == 'actual reply'
    assert calls == [('primary', 'primary-model'), ('backup', 'backup-model')]
    assert router.last_substitution['used'] == 'backup-model'
    assert router.last_substitution['used_provider'] == 'backup'
    assert router.last_substitution['family'] == 'B'
    assert 'backup-model' in caplog.text


def test_no_hidden_cloud_or_local_fallback(monkeypatch, tmp_path):
    seen = []
    def factory(provider, **kwargs):
        from hydra.providers import ProviderError
        seen.append(provider)
        raise ProviderError('missing credentials')
    monkeypatch.setattr('hydra.model_router.make_client', factory)
    router = ModelRouter(config_path=tmp_path / 'absent')
    with pytest.raises(Exception, match='missing credentials'):
        router._create_client(ModelConfig('worker', 'requested', 'chosen'))
    assert seen == ['requested']


def test_explicit_fallback_configuration_loaded(tmp_path):
    config = tmp_path / 'hydra.yaml'
    config.write_text(yaml.safe_dump({'agentic': {'roles': {'worker': {
        'provider': 'p', 'model': 'a', 'family': 'A',
        'fallbacks': [{'provider': 'q', 'model': 'b', 'family': 'B'}]}}}}))
    router = ModelRouter(config_path=config)
    assert router.models['worker'].fallbacks == [{'provider': 'q', 'model': 'b', 'family': 'B'}]
    assert router.models['worker'].family == 'A'
