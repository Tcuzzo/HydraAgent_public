from __future__ import annotations

import io
import json
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from hydra.llm import ChatResponse, LlmError, OllamaClient


def test_http_failure_preserves_budget_code_and_literal_message(monkeypatch):
    body = b'{"error":{"code":"insufficient_quota","message":"Credits exhausted for this account"}}'
    def fail(*a, **kw):
        raise urllib.error.HTTPError('https://example.invalid', 429, 'no budget', {'Retry-After': '120'}, io.BytesIO(body))
    monkeypatch.setattr('urllib.request.urlopen', fail)
    with pytest.raises(LlmError) as caught:
        OllamaClient('https://example.invalid').chat([{'role': 'user', 'content': 'hi'}], model='m')
    assert caught.value.kind == 'budget'
    assert caught.value.error_code == 'insufficient_quota'
    assert 'Credits exhausted' in str(caught.value)


def test_same_provider_distinct_families_are_independent():
    from hydra.roles import resolve_roles_from_dict
    roles = {name: {'provider': 'local', 'model': name, 'family': family}
             for name, family in [('planner', 'A'), ('doer', 'A'), ('auditor', 'B')]}
    assert resolve_roles_from_dict({'agentic': {'roles': roles, 'require_independent_auditor': True}}).auditor.family == 'B'


def test_budget_health_survives_new_store_and_expires_exactly_eight_hours(tmp_path):
    from hydra.provider_health import HealthStore, Route, http_failure
    route = Route('p', 'm', 'f', 'https://provider.invalid/v1', 'account-hash')
    path = tmp_path / 'health.json'
    HealthStore(path).record_failure(route, http_failure(429, '{"error":{"code":"insufficient_quota","message":"No budget"}}'), now=100)
    assert HealthStore(path).blocked(route, now=28899)['kind'] == 'budget'
    assert HealthStore(path).blocked(route, now=28900) is None


def test_rate_limit_date_and_seconds_retirement_requires_positive_evidence():
    from hydra.provider_health import http_failure
    assert http_failure(429, 'slow down', '90', now=100).retry_after == 90
    assert http_failure(429, 'slow down', 'Thu, 01 Jan 1970 00:03:20 GMT', now=100).retry_after == 100
    missing = http_failure(404, '{"error":{"code":"model_not_found","message":"not found"}}')
    assert missing.kind == 'model_unavailable' and missing.refresh_needed
    assert http_failure(404, '{"error":{"code":"model_retired","message":"retired"}}').kind == 'retired'


def test_route_identity_separates_accounts_and_endpoints_but_not_provider_aliases():
    from hydra.provider_health import Route
    first = Route('p', 'm', 'f', 'https://one.invalid/v1/', 'account1')
    assert first.key == Route('alias', 'm', 'f', 'https://one.invalid/v1', 'account1').key
    assert first.key != Route('p', 'm', 'f', 'https://two.invalid/v1', 'account1').key
    assert first.key != Route('p', 'm', 'f', 'https://one.invalid/v1', 'account2').key


def test_store_concurrent_updates_do_not_lose_routes(tmp_path):
    from hydra.provider_health import HealthStore, Route
    store = HealthStore(tmp_path / 'health.json')
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.record_failure(Route('p', str(i)), LlmError('timeout', kind='timeout')), range(24)))
    assert len(store.snapshot()['routes']) == 24


def test_make_client_preserves_type_and_blocks_without_second_network_call(tmp_path, monkeypatch):
    from hydra.providers import make_client
    monkeypatch.setenv('HYDRA_MODEL_HEALTH_PATH', str(tmp_path / 'health.json'))
    monkeypatch.setenv('TEST_ENDPOINT', 'https://test.invalid')
    calls = []
    def chat(self, messages, **kwargs):
        calls.append(kwargs['model'])
        raise LlmError('No credit left', kind='budget')
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    for _ in range(2):
        client, _cfg = make_client('test', env_dir=tmp_path)
        assert isinstance(client, OllamaClient)
        with pytest.raises(LlmError) as caught:
            client.chat([], model='m')
        assert caught.value.kind == 'budget'
    assert calls == ['m']


def test_runtime_fallback_attempts_each_identity_once_and_reports_actual(tmp_path, monkeypatch):
    from hydra.providers import make_runtime_client
    calls = []
    for provider in ('first', 'alias', 'second'):
        monkeypatch.setenv(provider.upper() + '_ENDPOINT', 'https://' + ('first' if provider == 'alias' else provider) + '.invalid')
    def chat(self, messages, **kwargs):
        calls.append((self.endpoint, kwargs['model']))
        if 'first' in self.endpoint:
            raise LlmError('credits zero', kind='budget')
        return ChatResponse('ok', kwargs['model'], 'stop', 1, 1, {})
    monkeypatch.setattr(OllamaClient, 'chat', chat)
    client, _cfg = make_runtime_client('first', model='a', family='F1', env_dir=tmp_path,
        health_path=tmp_path / 'health.json', fallbacks=[{'provider': 'alias', 'model': 'a', 'family': 'F1'},
                                                      {'provider': 'second', 'model': 'b', 'family': 'F2'}])
    assert client.chat([], model='a').content == 'ok'
    assert len(calls) == 2
    assert client.actual_route['provider'] == 'second'
    assert client.actual_route['model'] == 'b' and client.actual_route['family'] == 'F2'


def test_persisted_failure_redacts_credentials(tmp_path):
    from hydra.provider_health import HealthStore, Route
    path = tmp_path / 'health.json'
    HealthStore(path).record_failure(Route('p', 'm'), LlmError('Authorization: Bearer sk-testsecret API_KEY=secret'))
    saved = path.read_text()
    assert 'sk-testsecret' not in saved and 'API_KEY=secret' not in saved


def test_in_flight_reservation_is_transient_even_when_status402_mentions_budget():
    from hydra.provider_health import http_failure
    error = http_failure(402, json.dumps({'error': {'message': 'budget exhausted', 'metadata': {'limit_source': 'openrouter_in_flight_budget'}}}), '600')
    assert error.kind == 'rate_limit' and error.retry_after == 600


def test_actual_response_model_is_reported_separately_from_request(tmp_path, monkeypatch):
    from hydra.providers import make_runtime_client
    monkeypatch.setenv('TEST_ENDPOINT', 'https://test.invalid')
    monkeypatch.setattr(OllamaClient, 'chat', lambda *a, **kw: ChatResponse('ok', 'served-id', 'stop', 1, 1, {}))
    client, _ = make_runtime_client('test', model='alias', health_path=tmp_path / 'health.json', env_dir=tmp_path)
    client.chat([], model='alias')
    assert client.actual_route['model'] == 'served-id' and client.actual_route['requested_model'] == 'alias'


def test_missing_primary_configuration_can_use_only_explicit_fallback(tmp_path, monkeypatch):
    from hydra.providers import make_runtime_client
    monkeypatch.setenv('BACKUP_ENDPOINT', 'https://backup.invalid')
    monkeypatch.setattr(OllamaClient, 'chat', lambda *a, **kw: ChatResponse('ok', kw['model'], 'stop', 1, 1, {}))
    client, _ = make_runtime_client('absent', model='a', health_path=tmp_path / 'health.json', env_dir=tmp_path,
                                   fallbacks=[{'provider': 'backup', 'model': 'b', 'family': 'B'}])
    assert client.chat([], model='a').content == 'ok'
    assert client.actual_route['provider'] == 'backup'


def test_budget_benches_account_across_models_but_retirement_does_not(tmp_path):
    from hydra.provider_health import HealthStore, Route
    first, second = Route('p', 'a', endpoint='https://example.invalid', account='x'), Route('p', 'b', endpoint='https://example.invalid', account='x')
    store = HealthStore(tmp_path / 'health.json')
    store.record_failure(first, LlmError('retired', kind='retired'), now=100)
    assert store.blocked(second, now=101) is None
    store.record_failure(first, LlmError('no credits', kind='budget'), now=100)
    assert store.blocked(second, now=101)['scope'] == 'account'
    store.clear(first.key)
    assert store.blocked(second, now=101) is None


def test_busy_health_lock_is_bounded_and_never_deleted(tmp_path):
    import time
    from hydra.file_lock import acquire_singleton_lock, lock_path_for
    from hydra.provider_health import HealthStore
    path = tmp_path / 'health.json'
    ok, handle, _pid = acquire_singleton_lock(path)
    assert ok
    try:
        start = time.monotonic()
        with pytest.raises(LlmError, match='lock is busy'):
            HealthStore(path, lock_timeout=.05).snapshot()
        assert time.monotonic() - start < .5
        assert lock_path_for(path).exists()
    finally:
        handle.close()


def test_autonomous_failure_without_configured_recovery_pauses_without_inventing_model(tmp_path, monkeypatch):
    from hydra.loop import AgentLoop
    monkeypatch.delenv('HYDRA_EMERGENCY_PROVIDER', raising=False)
    monkeypatch.delenv('HYDRA_EMERGENCY_MODEL', raising=False)
    class Failed:
        calls = 0
        def chat(self, *args, **kwargs):
            self.calls += 1
            raise LlmError('No credits remaining', kind='budget')
    client = Failed()
    loop = AgentLoop(client, model='selected')
    result = loop.run('work', autonomous=True, requested_provider='chosen', mission_id='m', repo_root=tmp_path)
    assert client.calls == 1
    assert result.halted_reason == 'provider_unavailable' and not result.fallback_engaged
    assert loop.client is client
    checkpoint = json.loads((tmp_path / 'evidence/m/s6_pause_checkpoint.json').read_text())
    assert checkpoint['failure']['kind'] == 'budget' and checkpoint['life_support_model'] is None


def test_inline_critic_constructs_configured_endpoint_with_health(tmp_path, monkeypatch):
    from hydra.evaluator_optimizer import InlineCritic
    from hydra.model_router import ModelConfig
    monkeypatch.setenv('HYDRA_MODEL_HEALTH_PATH', str(tmp_path / 'health.json'))
    critic = InlineCritic(ModelConfig('critic', 'custom', 'model', base_url='https://custom.invalid/v1'), tmp_path)
    assert critic.client.endpoint == 'https://custom.invalid/v1'
    assert critic.client.health_store.path == tmp_path / 'health.json'


def test_catalog_refresh_annotates_retired_without_retiring_absent_models(tmp_path, monkeypatch):
    from hydra.provider_health import catalog_report, HealthStore, route_for
    from hydra.providers import make_client
    monkeypatch.setenv('TEST_ENDPOINT', 'https://test.invalid')
    path = tmp_path / 'health.json'
    client, cfg = make_client('test', health_path=path, env_dir=tmp_path)
    HealthStore(path).record_failure(route_for(client, cfg, 'old'), LlmError('model retired', kind='retired', refresh_needed=True))
    calls = []
    def models(self, **kwargs):
        calls.append(kwargs)
        return ['new']
    monkeypatch.setattr(OllamaClient, 'list_models', models)
    result = catalog_report('test', env_dir=tmp_path, health_path=path, refresh=True)
    assert calls == [{'timeout': 10.0, 'force_refresh': True}]
    assert result['models'][0]['state'] == 'listed_unproven'
    assert result['retired'][0]['model'] == 'old'
    assert len(HealthStore(path).snapshot()['routes']) == 1


def test_clearing_account_identity_removes_budget_for_original_model(tmp_path):
    from hydra.provider_health import HealthStore, Route
    route = Route('p', 'm')
    store = HealthStore(tmp_path / 'health.json')
    store.record_failure(route, LlmError('no credits', kind='budget'))
    assert store.clear(route.account_key)
    assert store.blocked(route) is None


def test_failure_message_scrubs_private_addresses():
    from hydra.provider_health import safe_message
    assert '192.168.0.1' not in safe_message('upstream 192.168.0.1 refused')


def test_real_client_catalog_refresh_bypasses_cache(monkeypatch):
    from hydra.llm import _cloud_model_cache
    _cloud_model_cache.clear()
    client = OllamaClient('https://catalog-regression.invalid/v1')
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        return {'data': [{'id': str(len(calls))}]}
    monkeypatch.setattr(client, '_get_json', get)
    assert client.list_models() == ['1']
    assert client.list_models() == ['1']
    assert client.list_models(force_refresh=True) == ['2']
