"""TDD tests for the free-model fallback tier.

Desired never-die ladder (primary failure):
  paid primary (cloud-planner) -> nemotron-mini (free, ollama-cloud)
                               -> phi3:mini (free, ollama-cloud)
                               -> local qwen2.5-coder:7b (ollama)

Each downgrade must emit a WARNING. No warning on the happy path.
"""
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


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Ladder order + downgrade-log tests (ModelRouter._create_client)
# ═══════════════════════════════════════════════════════════════════════════════

from hydra.model_router import ModelRouter, ModelConfig
from hydra.llm import LlmError


class _FakeClient:
    """A minimal chat client stub that returns a successful response."""
    def __init__(self, name: str = "fake"):
        self.name = name

    def chat(self, messages, *, model="", max_tokens=512, temperature=0.0, **kw):
        mock_resp = MagicMock()
        mock_resp.content = f"response from {self.name}"
        return mock_resp


class _FailClient:
    """A chat client stub that always raises LlmError."""
    def chat(self, messages, **kw):
        raise LlmError("quota exceeded")


def _make_router_with_mock_factory(make_client_side_effect, tmp_path=None) -> ModelRouter:
    """Build a ModelRouter whose make_client is replaced by a side-effect callable."""
    router = ModelRouter.__new__(ModelRouter)
    router.env_dir = None
    router.last_substitution = {"requested": None, "used": None,
                                "downgraded_to_local": False, "note": ""}
    # Minimal model config so _create_client can be called
    router.models = {}
    router._make_client_override = make_client_side_effect
    return router


def _router_with_patched_make_client(provider_outcomes: dict[str, Any]) -> ModelRouter:
    """
    Build a ModelRouter with a patched _mk() so we can test _create_client
    without real network calls.

    provider_outcomes maps provider -> either a client or an Exception class/instance.
    """
    router = ModelRouter.__new__(ModelRouter)
    router.env_dir = None
    router.last_substitution = {"requested": None, "used": None,
                                "downgraded_to_local": False, "note": ""}
    router.models = {}
    return router


class TestNeverDieLadderOrder:
    """_create_client walks: paid primary -> free cloud -> local, warns on each step."""

    def _make_client_patch(self, provider_map: dict):
        """
        Return a patcher context manager for hydra.model_router.make_client.
        provider_map: {provider: client_or_exception}
        """
        def _side_effect(provider, **kw):
            outcome = provider_map.get(provider)
            if outcome is None:
                # Unknown provider — raise ProviderError
                from hydra.providers import ProviderError
                raise ProviderError(f"unknown provider {provider!r}")
            if isinstance(outcome, type) and issubclass(outcome, Exception):
                raise outcome(f"simulated failure for {provider}")
            if isinstance(outcome, Exception):
                raise outcome
            # Return (client, config_stub)
            cfg_stub = MagicMock()
            cfg_stub.name = provider
            return outcome, cfg_stub
        return _side_effect

    def test_primary_succeeds_no_fallback_no_warning(self, caplog):
        """Happy path: primary works -> no fallback attempted, no WARNING."""
        good_client = _FakeClient("primary")
        side_effect = self._make_client_patch({"ollama-cloud": good_client})

        model_cfg = ModelConfig(
            name="doer", provider="ollama-cloud", model="qwen2.5:32b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
                client = router._create_client(model_cfg)

        assert client is good_client
        assert router.last_substitution["downgraded_to_local"] is False
        # No downgrade warning
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert not warnings, f"Expected no warnings on happy path, got: {[r.message for r in warnings]}"

    def test_primary_fails_falls_to_nemotron(self, caplog):
        """Primary (ollama-cloud) fails -> falls to nemotron-mini (same provider)."""
        from hydra.providers import ProviderError

        call_count: dict[str, int] = {"ollama-cloud": 0}

        nemotron_client = _FakeClient("nemotron-mini")

        def side_effect(provider, **kw):
            if provider == "ollama-cloud":
                call_count["ollama-cloud"] = call_count.get("ollama-cloud", 0) + 1
                if call_count["ollama-cloud"] == 1:
                    # First call (primary cloud model) fails
                    raise ProviderError("quota exceeded for primary model")
                # Subsequent calls (free model) succeed
                cfg_stub = MagicMock()
                cfg_stub.name = "ollama-cloud"
                return nemotron_client, cfg_stub
            from hydra.providers import ProviderError as PE
            raise PE(f"no {provider}")

        model_cfg = ModelConfig(
            name="doer", provider="ollama-cloud", model="qwen2.5:32b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
                client = router._create_client(model_cfg)

        assert client is nemotron_client
        assert router.last_substitution["downgraded_to_local"] is False
        # Must have warned about the downgrade
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, "Expected WARNING log on primary->free fallback"

    def test_full_ladder_order_primary_nemotron_phi_local(self, caplog):
        """Full ladder: primary fails -> nemotron-mini fails -> phi3:mini fails -> local."""
        from hydra.providers import ProviderError

        calls: list[str] = []
        local_client = _FakeClient("local")

        def side_effect(provider, **kw):
            calls.append(provider)
            if provider == "ollama":
                cfg_stub = MagicMock()
                cfg_stub.name = "ollama"
                return local_client, cfg_stub
            raise ProviderError(f"all cloud down: {provider}")

        model_cfg = ModelConfig(
            name="doer", provider="ollama-cloud", model="qwen2.5:32b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
                client = router._create_client(model_cfg)

        assert client is local_client
        assert router.last_substitution["downgraded_to_local"] is True

        # The ladder must have tried free models before local — confirm provider
        # sequence had ollama-cloud (primary), ollama-cloud (nemotron-mini), ollama-cloud
        # (phi3:mini), then ollama (local).  All except local use ollama-cloud provider.
        assert "ollama" in calls, "local must have been tried"
        # At least 4 calls: primary + 2 free + local
        assert len(calls) >= 4, f"Expected >= 4 provider calls, got {calls}"

        # Every step must have logged a WARNING
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 3, (
            f"Expected >= 3 WARNING logs (one per downgrade step), got {len(warnings)}: "
            + str([r.message for r in warnings])
        )

    def test_free_nemotron_succeeds_phi_never_tried(self, caplog):
        """If nemotron-mini answers, phi3:mini is never tried."""
        from hydra.providers import ProviderError

        call_count: dict[str, int] = {}
        nemotron_client = _FakeClient("nemotron")

        def side_effect(provider, **kw):
            n = call_count.get(provider, 0) + 1
            call_count[provider] = n
            if provider == "ollama-cloud":
                if n == 1:
                    raise ProviderError("primary quota gone")
                # Second call = nemotron free model -> succeed
                cfg_stub = MagicMock()
                cfg_stub.name = "ollama-cloud"
                return nemotron_client, cfg_stub
            raise ProviderError(f"unexpected provider {provider}")

        model_cfg = ModelConfig(
            name="planner", provider="ollama-cloud", model="qwen2.5:72b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
                client = router._create_client(model_cfg)

        # Nemotron served, local never touched
        assert client is nemotron_client
        assert call_count.get("ollama", 0) == 0, "local must NOT have been tried when nemotron succeeded"
        assert router.last_substitution["downgraded_to_local"] is False

    def test_local_only_called_as_last_resort(self, caplog):
        """After all free cloud models fail, local is called exactly once."""
        from hydra.providers import ProviderError

        local_client = _FakeClient("local")
        call_counts: dict[str, int] = {}

        def side_effect(provider, **kw):
            n = call_counts.get(provider, 0) + 1
            call_counts[provider] = n
            if provider == "ollama":
                cfg_stub = MagicMock()
                cfg_stub.name = "ollama"
                return local_client, cfg_stub
            raise ProviderError(f"cloud down: {provider} call #{n}")

        model_cfg = ModelConfig(
            name="doer", provider="ollama-cloud", model="qwen2.5:32b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            client = router._create_client(model_cfg)

        assert client is local_client
        assert call_counts.get("ollama", 0) == 1, "local tried exactly once"
        assert router.last_substitution["downgraded_to_local"] is True

    def test_free_model_log_message_contains_model_names(self, caplog):
        """WARNING log on free fallback includes from->to model info."""
        from hydra.providers import ProviderError

        call_count: dict[str, int] = {}
        nemotron_client = _FakeClient("nemotron")

        def side_effect(provider, **kw):
            n = call_count.get(provider, 0) + 1
            call_count[provider] = n
            if provider == "ollama-cloud" and n >= 2:
                cfg_stub = MagicMock()
                cfg_stub.name = "ollama-cloud"
                return nemotron_client, cfg_stub
            raise ProviderError("primary fail")

        model_cfg = ModelConfig(
            name="doer", provider="ollama-cloud", model="qwen2.5:32b",
            base_url="https://api.ollama.cloud",
        )
        router = ModelRouter.__new__(ModelRouter)
        router.env_dir = None
        router.last_substitution = {"requested": None, "used": None,
                                    "downgraded_to_local": False, "note": ""}
        router.models = {}

        with patch("hydra.model_router.make_client", side_effect=side_effect):
            with caplog.at_level(logging.WARNING, logger="hydra.model_router"):
                router._create_client(model_cfg)

        # At least one warning must mention "nemotron-mini"
        msgs = " ".join(r.message for r in caplog.records if r.levelno >= logging.WARNING)
        assert "nemotron-mini" in msgs, (
            f"Expected WARNING to mention 'nemotron-mini'; got: {msgs!r}"
        )


# TestReflexBrainFreeTier trimmed — it imports
# skills.a_removed_subsystem which is not present in this
# lean-core build.
