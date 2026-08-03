"""RED contract — cloud provider model catalog must FAIL OPEN.

`hydra/llm.py:list_models()` used to return HARDCODED stale model catalogs
for cloud providers (openai -> [gpt-4o, ...]; anthropic -> [claude-...]).
`hydra/roles.py:provider_available` then rejected any model not in that
stale list, falsely gating off valid newer models (gpt-4.1, o4-mini, ...).

The fix: query the provider live (GET /v1/models) and FAIL OPEN (admit the
model) when the model is not in the catalog — the catalog is a known-stale
allowlist, NEVER a denylist. These tests mock ONLY the HTTP transport leaf
(`OllamaClient._get_json`) and exercise the real `list_models` /
`provider_available` seam.

Regression guard for the "stale hardcoded catalog falsely gates off valid
models" flaw class.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import hydra.llm as _llm_mod
from hydra.llm import LlmError, OllamaClient
from hydra.roles import RoleSpec, provider_available


@pytest.fixture(autouse=True)
def _clear_cloud_catalog_cache():
    """The live cloud catalog is backed by a process-wide TTL cache (inv_16:
    never hammer the provider). Clear it before each test so a cached success
    from one test cannot mask the next test's mocked transport behavior.

    Defensive (getattr): the cache is a fix-introduced symbol. On a pre-fix
    base it is absent — the fixture no-ops so the test fails on the BEHAVIORAL
    assertion (provider_available falsely rejects a valid model), not on an
    ImportError at collection. That keeps this a proper RED contract."""
    cache = getattr(_llm_mod, "_cloud_model_cache", None)
    if cache is not None:
        cache.clear()
    yield
    cache = getattr(_llm_mod, "_cloud_model_cache", None)
    if cache is not None:
        cache.clear()


def _write_openai_env(tmp_path: Path) -> Path:
    """Stand up a custom `openai` provider pointing at the real cloud URL.

    The endpoint is the real `https://api.openai.com/v1` so the cloud branch
    of `list_models` is exercised; the HTTP transport itself is mocked at the
    leaf (`_get_json`), so no real network call ever leaves the process.
    """
    (tmp_path / ".env.openai").write_text(
        "\n".join(
            [
                "OPENAI_ENDPOINT=https://api.openai.com/v1",
                "OPENAI_API_KEY=test-key-not-used",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_valid_model_not_in_live_catalog_is_admitted(
    monkeypatch, tmp_path: Path
) -> None:
    """A valid model absent from the provider's live /v1/models catalog must
    NOT be falsely gated off. The catalog is an allowlist, not a denylist."""
    _write_openai_env(tmp_path)

    # Mock ONLY the HTTP transport leaf. The live catalog deliberately omits
    # "gpt-4.1" to prove the fail-open path: a missing entry is inconclusive,
    # never a rejection.
    def fake_get_json(self: OllamaClient, url: str, *, timeout: float) -> dict:
        assert url.endswith("/models"), f"expected /v1/models hit, got {url!r}"
        return {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini"},
            ]
        }

    monkeypatch.setattr(OllamaClient, "_get_json", fake_get_json)

    role = RoleSpec(role="doer", provider="openai", model="gpt-4.1", family="openai")
    ok, detail = provider_available(role, env_dir=tmp_path)

    assert ok is True, (
        f"provider_available falsely rejected a valid model not in the live "
        f"catalog (stale-catalog denylist bug): ok={ok!r} detail={detail!r}"
    )


def test_live_catalog_query_failing_admits_model(
    monkeypatch, tmp_path: Path
) -> None:
    """When the live /v1/models query itself fails (timeout / 5xx / network),
    provider_available must FAIL OPEN — never falsely reject off a missing
    catalog. Exercises the real exception path through provider_available."""
    _write_openai_env(tmp_path)

    def failing_get_json(self: OllamaClient, url: str, *, timeout: float) -> dict:
        raise LlmError(f"timed out talking to {url} after {timeout}s")

    monkeypatch.setattr(OllamaClient, "_get_json", failing_get_json)

    role = RoleSpec(role="doer", provider="openai", model="gpt-4.1", family="openai")
    ok, detail = provider_available(role, env_dir=tmp_path)

    assert ok is True, (
        f"provider_available falsely rejected a model when the live catalog "
        f"query failed (must fail open): ok={ok!r} detail={detail!r}"
    )


def test_list_models_queries_live_endpoint_not_hardcoded(
    monkeypatch, tmp_path: Path
) -> None:
    """list_models() for a cloud provider must hit GET {endpoint}/models and
    return the live catalog — NOT a hardcoded stale list. Proves the live
    query is wired (the hardcoded catalogs are gone)."""
    client = OllamaClient(
        endpoint="https://api.openai.com/v1",
        api_key="test-key-not-used",
    )

    captured: dict = {}

    def fake_get_json(self: OllamaClient, url: str, *, timeout: float) -> dict:
        captured["url"] = url
        captured["timeout"] = timeout
        return {"data": [{"id": "gpt-4.1"}, {"id": "o4-mini"}]}

    monkeypatch.setattr(OllamaClient, "_get_json", fake_get_json)

    names = client.list_models(timeout=5.0)

    assert captured["url"] == "https://api.openai.com/v1/models", (
        f"list_models did not hit GET {{endpoint}}/models: {captured.get('url')!r}"
    )
    assert "gpt-4.1" in names and "o4-mini" in names, (
        f"list_models did not return the live catalog: {names!r}"
    )
    # No stale hardcoded entries leak in when the live query succeeds.
    assert "gpt-4o" not in names, (
        f"stale hardcoded entry leaked into live catalog: {names!r}"
    )