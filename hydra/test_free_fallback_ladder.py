"""Legacy routing-file compatibility and explicit runtime fallback contracts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
import yaml

from hydra.model_routing import load_routing, clear_cache, DEFAULT


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Loader tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestFreeFallbackLoader:
    """model_routing.py parses + exposes free_fallback_models correctly."""

    def _make_minimal_cfg(self, free_fallback_models=None):
        """Minimal valid model_routing config for loader tests."""
        cfg = {
            "schema": "hydra.model_routing.v1",
            "roster": [
                {"id": "cloud-doer",   "provider": "ollama-cloud", "model": "qwen2.5:32b",
                 "role_tags": ["chat", "intent"], "base_url": "https://api.ollama.cloud"},
                {"id": "cloud-planner","provider": "ollama-cloud", "model": "qwen2.5:72b",
                 "role_tags": ["planner","work","auditor","verifier"],
                 "base_url": "https://api.ollama.cloud"},
                {"id": "local-worker", "provider": "ollama",        "model": "qwen2.5-coder:7b",
                 "role_tags": ["worker","local","router","read"],
                 "base_url": "http://127.0.0.1:11434"},
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
                "cloud_fallback_ladder": ["ollama-cloud"],
                "local_fallback_provider": "ollama",
            },
        }
        if free_fallback_models is not None:
            cfg["routing"]["free_fallback_models"] = free_fallback_models
        return cfg

    def test_yaml_free_fallback_models_loaded(self, tmp_path):
        """routing.free_fallback_models comes from yaml in declared order."""
        cfg = self._make_minimal_cfg(free_fallback_models=["nemotron-mini", "phi3:mini"])
        yaml_path = tmp_path / "model_routing.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        clear_cache()
        routing = load_routing(yaml_path)
        assert list(routing.free_fallback_models) == ["nemotron-mini", "phi3:mini"]

    def test_yaml_missing_free_fallback_uses_default(self, tmp_path):
        """When yaml omits free_fallback_models, loader supplies safe default."""
        cfg = self._make_minimal_cfg()  # free_fallback_models deliberately omitted
        yaml_path = tmp_path / "model_routing.yaml"
        yaml_path.write_text(yaml.dump(cfg))
        clear_cache()
        routing = load_routing(yaml_path)
        # Safe default must be non-empty
        assert len(routing.free_fallback_models) >= 2

    def test_frozen_default_has_free_fallback_models(self):
        """The in-code frozen DEFAULT always provides the free tier."""
        assert "nemotron-mini" in DEFAULT.free_fallback_models
        assert "phi3:mini" in DEFAULT.free_fallback_models

    def test_frozen_default_order_is_nemotron_first(self):
        """Ladder order: nemotron-mini before phi3:mini."""
        ladder = list(DEFAULT.free_fallback_models)
        assert ladder.index("nemotron-mini") < ladder.index("phi3:mini")

    def test_production_yaml_has_free_fallback_models(self):
        """The live model_routing.yaml exposes free_fallback_models."""
        clear_cache()
        routing = load_routing()
        assert list(routing.free_fallback_models) == ["nemotron-mini", "phi3:mini"]



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
