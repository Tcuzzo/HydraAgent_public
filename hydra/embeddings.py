"""Explicit embedding transports with stable model identity and bounded HTTP.

Configure HYDRA_EMBEDDING_CONFIG with a local JSON file. A new identity uses
a new memory database; equal dimensions do not mean compatible vector spaces.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import urllib.request


def config():
    path = os.environ.get('HYDRA_EMBEDDING_CONFIG')
    if not path:
        return None
    from hydra.specialists import read_json
    data = read_json(Path(path), 16384)
    if not isinstance(data, dict) or data.get('backend') not in {'ollama', 'openai', 'openvino'}:
        raise ValueError('embedding backend must be ollama, openai, or openvino')
    if not isinstance(data.get('model'), str) or not data['model'].strip():
        raise ValueError('embedding model must be explicit')
    if not isinstance(data.get('revision'), str) or not data['revision'].strip():
        raise ValueError('embedding revision must identify the weights; change it after model replacement')
    if data['backend'] != 'openvino' and not isinstance(data.get('endpoint'), str):
        raise ValueError('embedding endpoint must be explicit')
    return data


def identity(data=None):
    data = config() if data is None else data
    if data is None:
        return 'legacy-nomic-embed-text'
    fields = {k: data.get(k) for k in ('backend', 'model', 'revision', 'endpoint', 'document_prefix', 'query_prefix')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def memory_path(default: Path):
    current = config()
    return default if current is None else default.with_name(f'memory-{identity(current)[:24]}.sqlite')


_LOCAL = {}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def embed(text: str, data=None):
    data = config() if data is None else data
    if data is None:
        raise ValueError('no embedding configuration')
    if not isinstance(text, str) or len(text.encode()) > 1024 * 1024:
        raise ValueError('embedding text must be <=1 MiB')
    # Existing callers use nomic prefixes. Translate at the adapter boundary.
    for old, key in [('search_document: ', 'document_prefix'), ('search_query: ', 'query_prefix')]:
        if text.startswith(old):
            text = str(data.get(key, '')) + text[len(old):]
            break
    if data['backend'] == 'openvino':
        from openvino import Core
        from sentence_transformers import SentenceTransformer
        device = data.get('device', 'CPU')
        devices = Core().available_devices
        if device not in devices and not (device == 'GPU' and any(d.startswith('GPU') for d in devices)):
            raise ValueError(f'requested OpenVINO device {device!r} is unavailable; detected {devices}')
        key = identity(data) + ':' + device
        if key not in _LOCAL:
            _LOCAL.clear()  # one configured embedding model per process
            _LOCAL[key] = SentenceTransformer(data['model'], revision=data['revision'], backend='openvino',
                                              model_kwargs={'device': device})
        vector = _LOCAL[key].encode(text, normalize_embeddings=True).tolist()
    else:
        from urllib.parse import urlsplit
        endpoint = data['endpoint'].rstrip('/')
        parsed = urlsplit(endpoint)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
            raise ValueError('embedding endpoint must not contain credentials or query parameters')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}):
            raise ValueError('use HTTPS for remote embeddings or HTTP loopback')
        ollama = data['backend'] == 'ollama'
        payload = {'model': data['model'], 'prompt' if ollama else 'input': text}
        headers = {'Content-Type': 'application/json'}
        if data.get('api_key_env'):
            key = os.environ.get(data['api_key_env'])
            if not key:
                raise ValueError('configured embedding API key environment variable is unset')
            headers['Authorization'] = 'Bearer ' + key
        url = endpoint + ('/api/embeddings' if ollama else '/embeddings')
        request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
        handlers = [_NoRedirect]
        if parsed.hostname in {'localhost', '127.0.0.1', '::1'}:
            handlers.append(urllib.request.ProxyHandler({}))
        with urllib.request.build_opener(*handlers).open(request, timeout=20) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('embedding response exceeds 1 MiB')
        body = json.loads(raw)
        vector = body.get('embedding') if ollama else body.get('data', [{}])[0].get('embedding')
    if not isinstance(vector, list) or not 1 <= len(vector) <= 65536:
        raise ValueError('embedding vector missing or invalid size')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
        raise ValueError('embedding vector must contain finite numbers')
    return [float(x) for x in vector]


def unload():
    count = len(_LOCAL)
    _LOCAL.clear()
    return {'status': 'unloaded', 'models_released': count, 'scope': 'this_process'}
