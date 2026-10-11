"""Literal provider failures, durable cooldowns, and bounded configured fallback.

Health is evidence from actual calls, not a model catalog or a claim of model
quality. Family labels are operator supplied. No network probing or sleeping is
performed here. A request visits each configured endpoint/account/model once.
"""
from __future__ import annotations

import hashlib
import contextlib
import ipaddress
import json
import math
import os
import re
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path

from hydra.atomic_write import atomic_write_bytes
from hydra.file_lock import acquire_singleton_lock
from hydra.llm import LlmError


def safe_message(value: str) -> str:
    value = re.sub(r'(?i)(bearer\s+)[^\s"\x27,;]+', r'\1[REDACTED]', str(value))
    value = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|authorization|password|secret)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', value)
    value = re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', value)
    value = re.sub(r'(?i)("(?:api[_-]?key|access[_-]?token|authorization|password|secret)"\s*:\s*")[^"]*', r'\1[REDACTED]', value)
    value = re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1[REDACTED]@', value)
    def address(match):
        try:
            return '[PRIVATE_ADDRESS]' if not ipaddress.ip_address(match.group()).is_global else match.group()
        except ValueError:
            return match.group()
    value = re.sub(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', address, value)
    return value[:3000]


def http_failure(status: int, body: str, retry_after: str | None = None, *, now: float | None = None) -> LlmError:
    """Classify positive evidence; a bare 404 never proves model retirement."""
    now = time.time() if now is None else now
    code, message, metadata = '', body, {}
    try:
        payload = json.loads(body)
        error = payload.get('error', payload) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            code = str(error.get('code') or error.get('type') or '')
            message = str(error.get('message') or body)
            metadata = error.get('metadata') or {}
        elif isinstance(error, str):
            message = error
    except (ValueError, TypeError):
        pass
    seconds = None
    if retry_after:
        try:
            seconds = float(retry_after)
        except (TypeError, ValueError):
            try:
                seconds = parsedate_to_datetime(retry_after).timestamp() - now
            except (TypeError, ValueError, OverflowError):
                pass
        if seconds is not None:
            seconds = max(0.0, seconds) if math.isfinite(seconds) else None
    normalized = code.casefold()
    text = message.casefold()
    if isinstance(metadata, dict) and metadata.get('limit_source') == 'openrouter_in_flight_budget':
        kind = 'rate_limit'
    elif normalized in {'insufficient_quota', 'insufficient_credits', 'credit_balance_too_low', 'budget_exceeded', 'billing_hard_limit_reached', 'no_budget'} or any(term in text for term in ('credit balance is too low', 'insufficient credits', 'budget exhausted', 'no credits remaining')):
        kind = 'budget'
    elif normalized in {'model_retired', 'model_decommissioned', 'model_discontinued'} or re.search(r'\bmodel\b.{0,160}\b(?:has been retired|has been decommissioned|has been discontinued)\b', text):
        kind = 'retired'
    elif status == 429:
        kind = 'rate_limit'
    elif status == 401:
        kind = 'authentication'
    elif status == 403:
        kind = 'permission'
    elif status == 404 or normalized in {'model_not_found', 'unknown_model'}:
        kind = 'model_unavailable'
    elif status >= 500:
        kind = 'server'
    else:
        kind = 'request'
    return LlmError(f'HTTP {status}' + (f' [{code}]' if code else '') + f': {safe_message(message)}', kind=kind, status_code=status,
                    error_code=code or None, retry_after=seconds,
                    refresh_needed=kind in {'retired', 'model_unavailable'})


def default_health_path() -> Path:
    return Path(os.environ.get('HYDRA_MODEL_HEALTH_PATH') or Path.cwd() / '.hydra' / 'model-health.json').expanduser().resolve()


@dataclass(frozen=True)
class Route:
    provider: str
    model: str
    family: str = ''
    endpoint: str = ''
    account: str = ''  # one-way fingerprint; never a credential

    @property
    def key(self) -> str:
        transport = self.endpoint.rstrip('/') or 'native:' + self.provider
        return hashlib.sha256(json.dumps([transport, self.account, self.model], separators=(',', ':')).encode()).hexdigest()

    @property
    def account_key(self) -> str:
        transport = self.endpoint.rstrip('/') or 'native:' + self.provider
        return hashlib.sha256(json.dumps([transport, self.account], separators=(',', ':')).encode()).hexdigest()

    def public(self) -> dict:
        return {'provider': self.provider, 'model': self.model, 'family': self.family,
                'identity': self.key}


def route_for(client, cfg, model: str, family: str = '') -> Route:
    key = str(getattr(client, 'api_key', None) or getattr(cfg, 'api_key', None) or '')
    account = hashlib.sha256(key.encode()).hexdigest() if key else ''
    return Route(cfg.name, model, family, str(getattr(client, 'api_base', '') or getattr(client, 'endpoint', '') or getattr(cfg, 'endpoint', '') or ''), account)


def actual_route(route: Route, response) -> dict:
    returned = getattr(response, 'model', None)
    return {**route.public(), 'model': returned if isinstance(returned, str) and returned else route.model,
            'requested_model': route.model}


def configured_route(provider: str, model: str, family: str = '', *, env_dir=None) -> Route:
    """Resolve identity without constructing a native client or making a call."""
    from hydra.providers import resolve
    from hydra.llm import OllamaClient
    cfg = resolve(provider, env_dir=env_dir)
    client = OllamaClient(cfg.endpoint, api_key=cfg.api_key) if cfg.endpoint else None
    return route_for(client, cfg, model, family)


class HealthStore:
    def __init__(self, path: str | Path | None = None, *, lock_timeout: float = 1.0):
        self.path = Path(path) if path is not None else default_health_path()
        self.lock_timeout = lock_timeout

    @contextlib.contextmanager
    def _locked(self):
        deadline = time.monotonic() + self.lock_timeout
        while True:
            acquired, handle, _pid = acquire_singleton_lock(self.path)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise LlmError('Model health state lock is busy; no provider call was made', kind='health_state')
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            handle.close()  # kernel releases the lock; never unlink a live sidecar

    def _load(self) -> dict:
        if not self.path.exists():
            return {'schema': 'hydra.model-health.v1', 'routes': {}, 'accounts': {}}
        try:
            with self.path.open('rb') as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('file exceeds 4 MiB')
            state = json.loads(raw)
            if not isinstance(state, dict) or state.get('schema') != 'hydra.model-health.v1' or not isinstance(state.get('routes'), dict):
                raise ValueError('invalid health schema')
            state.setdefault('accounts', {})
            if not isinstance(state['accounts'], dict):
                raise ValueError('invalid account health schema')
            return state
        except (OSError, ValueError) as exc:
            raise LlmError(f'Model health state is unreadable: {exc}', kind='health_state') from exc

    def snapshot(self) -> dict:
        with self._locked():
            return self._load()

    def blocked(self, route: Route, *, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        state = self.snapshot()
        account = state['accounts'].get(route.account_key)
        if account and account.get('until', 0) > now:
            return account
        row = state['routes'].get(route.key)
        if row and (row.get('kind') == 'retired' or row.get('until', 0) > now):
            return row
        return None

    def record_failure(self, route: Route, error: LlmError, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        seconds = {'budget': 8 * 3600, 'rate_limit': 60, 'authentication': 300, 'permission': 300,
                   'model_unavailable': 300, 'connection': 30, 'timeout': 30, 'server': 30}.get(error.kind, 0)
        if error.kind == 'rate_limit' and error.retry_after is not None:
            seconds = error.retry_after
        failure = error.to_dict()
        row = {**route.public(), **{key: failure[key] for key in ('kind', 'message', 'status_code', 'error_code', 'retry_after', 'refresh_needed')}, 'observed_at': now,
               'until': None if error.kind == 'retired' else now + seconds,
               'action': 'refresh_catalog' if error.refresh_needed else 'wait_or_choose_configured_route'}
        row['scope'] = 'account' if error.kind == 'budget' else 'model'
        row['account_identity'] = route.account_key
        with self._locked():
            state = self._load()
            state['routes'][route.key] = row
            if error.kind == 'budget':
                state['accounts'][route.account_key] = row
            # Expired transient evidence is expendable. Active cooldowns and
            # positively retired routes are never evicted to make calls resume.
            if len(state['routes']) > 256 or len(state['accounts']) > 128:
                for section in ('routes', 'accounts'):
                    state[section] = {key: value for key, value in state[section].items()
                                      if value.get('kind') == 'retired' or value.get('until', 0) > now or key in {route.key, route.account_key}}
                if len(state['routes']) > 256 or len(state['accounts']) > 128:
                    raise LlmError('Model health state capacity reached; inspect and explicitly reset old entries', kind='health_state')
            atomic_write_bytes(self.path, json.dumps(state, ensure_ascii=False, indent=2).encode())
        return row

    def clear(self, identity: str) -> bool:
        """Explicit operator reset after credentials/catalog have been refreshed."""
        with self._locked():
            state = self._load()
            row = state['routes'].pop(identity, None)
            existed = row is not None
            account_identity = row.get('account_identity') if row and row.get('scope') == 'account' else identity
            account = state['accounts'].pop(account_identity, None)
            if account:
                state['routes'] = {key: value for key, value in state['routes'].items()
                                   if not (value.get('scope') == 'account' and value.get('account_identity') == account_identity)}
                existed = True
            atomic_write_bytes(self.path, json.dumps(state, ensure_ascii=False, indent=2).encode())
        return existed


def catalog_report(provider: str, *, env_dir=None, health_path=None, timeout: float = 10.0,
                   refresh: bool = False) -> dict:
    """Explicit catalog refresh with health evidence; absence is inconclusive."""
    from hydra.providers import make_client
    from hydra.llm import OllamaClient
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('catalog timeout must be positive and at most60 seconds')
    client, cfg = make_client(provider, env_dir=env_dir, health_path=health_path)
    report = {'schema': 'hydra.model-catalog.v1', 'provider': cfg.name, 'observed_at': time.time(),
              'status': 'available', 'models': [], 'retired': [],
              'note': 'Catalog listing is not a successful generation probe; absence does not prove retirement. Retired benches require explicit operator reset.'}
    try:
        names = client.list_models(timeout=timeout, **({'force_refresh': refresh} if isinstance(client, OllamaClient) else {}))
    except LlmError as exc:
        report.update(status='unavailable', failure=exc.to_dict())
        return report
    state = client.health_store.snapshot()
    for name in names:
        route = route_for(client, cfg, name)
        evidence = state['routes'].get(route.key)
        account = state['accounts'].get(route.account_key)
        blocked = account if account and account.get('until', 0) > time.time() else evidence
        active = blocked and (blocked.get('kind') == 'retired' or blocked.get('until', 0) > time.time())
        report['models'].append({'model': name, 'catalog_listed': True, 'state': blocked['kind'] if active else 'listed_unproven',
                                 'identity': route.key, 'until': blocked.get('until') if active else None})
    reference = route_for(client, cfg, '')
    report['retired'] = [row for row in state['routes'].values() if row.get('kind') == 'retired' and row.get('account_identity') == reference.account_key]
    return report


def attach_health(client, cfg, *, health_path=None):
    """Guard chat on the original instance, preserving native transport controls."""
    original = client.chat
    store = HealthStore(health_path)
    client.actual_route = None
    client.runtime_family = ''
    client.health_store = store
    client.attempts = []
    def chat(messages, *, model, **kwargs):
        route = route_for(client, cfg, model, client.runtime_family)
        client.requested_route = route.public()
        client.actual_route = None
        started = time.monotonic()
        blocked = store.blocked(route)
        if blocked:
            error = LlmError(blocked['message'], kind=blocked['kind'], status_code=blocked.get('status_code'),
                             error_code=blocked.get('error_code'), retry_after=blocked.get('retry_after'),
                             refresh_needed=blocked.get('refresh_needed', False),
                             details={'benched': True, 'until': blocked['until'], 'route': route.public()})
            client.attempts = [{'route': route.public(), 'status': 'benched', 'error': error.to_dict()}]
            raise error
        try:
            if 'timeout' in kwargs:
                kwargs['timeout'] -= time.monotonic() - started
                if kwargs['timeout'] <= 0:
                    raise LlmError('Provider deadline expired while reading model health', kind='timeout')
            response = original(messages, model=model, **kwargs)
        except LlmError as error:
            credential = str(getattr(client, 'api_key', None) or getattr(cfg, 'api_key', None) or '')
            if credential:
                error.args = (str(error).replace(credential, '[REDACTED]'),)
            if error.kind == 'unknown':
                # Native adapters may only expose text. Promote only literal,
                # positive budget/retirement evidence; otherwise retain unknown.
                classified = http_failure(0, str(error))
                if classified.kind in {'budget', 'retired'}:
                    error.kind, error.refresh_needed = classified.kind, classified.refresh_needed
            store.record_failure(route, error)
            error.details.setdefault('route', route.public())
            client.attempts = [{'route': route.public(), 'status': 'failed', 'error': error.to_dict()}]
            raise
        client.actual_route = actual_route(route, response)
        client.attempts = [{'route': client.actual_route, 'status': 'completed'}]
        return response
    client.chat = chat
    return client


class RoutedClient:
    """At most eight explicit routes; no retries, sleeps, or invented fallback."""
    def __init__(self, client, cfg, *, model: str, family: str, fallbacks, factory, health_path=None, on_route=None):
        if not isinstance(fallbacks, (list, tuple)) or len(fallbacks) > 7:
            raise ValueError('at most seven fallback routes are allowed')
        for route in fallbacks:
            if not isinstance(route, dict) or set(route) - {'provider', 'model', 'family'} or any(not isinstance(route.get(k), str) or not route[k].strip() or len(route[k]) > 256 for k in ('provider', 'model')) or not isinstance(route.get('family', ''), str) or len(route.get('family', '')) > 256:
                raise ValueError('fallback routes require bounded provider/model and optional family')
        self._primary, self._cfg, self._factory = client, cfg, factory
        self._model, self._family = model, family
        self._fallbacks, self._on_route = list(fallbacks), on_route
        self.actual_route = None
        self.attempts = []
        self.requested_route = route_for(client, cfg, model, family).public()

    def __getattr__(self, name):
        return getattr(self._primary, name)

    def chat(self, messages, *, model: str, timeout: float = 60, **kwargs):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a positive finite number')
        deadline = time.monotonic() + timeout
        seen, self.attempts, self.actual_route = set(), [], None
        rows = [{'provider': self._cfg.name, 'model': model or self._model, 'family': self._family}, *self._fallbacks]
        messages = list(messages)
        last = None
        for index, row in enumerate(rows):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last = LlmError('Configured routing deadline exhausted', kind='timeout')
                break
            try:
                client, cfg = (self._primary, self._cfg) if index == 0 and self._primary is not None else self._factory(row['provider'])
            except Exception as exc:
                last = LlmError(safe_message(str(exc)), kind='configuration')
                self.attempts.append({'route': row, 'status': 'configuration_error', 'error': last.to_dict()})
                continue
            route = route_for(client, cfg, row['model'], row.get('family', ''))
            if route.key in seen:
                continue
            seen.add(route.key)
            client.runtime_family = row.get('family', '')
            # Native subprocess permissions are configured by the caller; every
            # possible native fallback inherits them before any request is sent.
            for name in ('cd', 'sandbox'):
                if name in self.__dict__:
                    setattr(client, name, self.__dict__[name])
            if cfg.name == 'codex' and 'sandbox' not in self.__dict__ and self._cfg.name != 'codex':
                last = LlmError('Native fallback requires an explicit sandbox policy', kind='configuration')
                self.attempts.append({'route': route.public(), 'status': 'configuration_error', 'error': last.to_dict()})
                continue
            try:
                response = client.chat(messages, model=row['model'], timeout=remaining, **kwargs)
            except LlmError as exc:
                last = exc
                self.attempts.append({'route': route.public(), 'status': 'benched' if exc.details.get('benched') else 'failed', 'error': exc.to_dict()})
                continue
            self.actual_route = actual_route(route, response)
            self.attempts.append({'route': self.actual_route, 'status': 'completed'})
            if self._on_route:
                self._on_route(self.actual_route)
            return response
        if last is None:
            last = LlmError('No configured model route was available', kind='unavailable')
        last.details = {**last.details, 'attempts': self.attempts, 'requested_route': self.requested_route}
        raise last
