"""Explicit local Ollama residency controls; no implicit eviction of other jobs."""
import json
import urllib.request


def residency(action: str, model: str, *, env_dir=None):
    from hydra.providers import resolve
    from hydra.embeddings import _NoRedirect
    from urllib.parse import urlsplit
    if action not in {'load', 'unload'} or not isinstance(model, str) or not model.strip() or len(model) > 256:
        raise ValueError('residency requires load/unload and an explicit model')
    cfg = resolve('ollama', env_dir=env_dir)
    endpoint = cfg.endpoint.rstrip('/')
    parsed = urlsplit(endpoint)
    if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('residency controls require a loopback Ollama endpoint; use a local tunnel for a remote owned node')
    if endpoint.endswith('/v1'):
        endpoint = endpoint[:-3]
    payload = {'model': model, 'prompt': '', 'stream': False, 'keep_alive': 0 if action == 'unload' else '5m'}
    request = urllib.request.Request(endpoint + '/api/generate', data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(_NoRedirect, urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('residency response exceeded limit')
    receipt = json.loads(raw)
    if not isinstance(receipt, dict) or receipt.get('error') or receipt.get('done') is not True:
        raise ValueError('Ollama did not confirm residency operation')
    return {'status': 'completed', 'action': action, 'model': model, 'provider': 'ollama',
            'note': 'Changes this model on the selected server; coordinate with other GPU users before unloading.'}
