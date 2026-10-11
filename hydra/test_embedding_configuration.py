import json
from pathlib import Path

import pytest

from hydra.embeddings import config, embed, identity, memory_path


def test_embedding_identity_separates_equal_dimensions(tmp_path, monkeypatch):
    path = tmp_path / 'embedding.json'
    data = {'backend': 'openai', 'endpoint': 'http://127.0.0.1:8080/v1', 'model': 'model-a', 'revision': 'abc'}
    path.write_text(json.dumps(data))
    monkeypatch.setenv('HYDRA_EMBEDDING_CONFIG', str(path))
    first = memory_path(tmp_path / 'memory.sqlite')
    data['model'] = 'model-b'
    path.write_text(json.dumps(data))
    assert memory_path(tmp_path / 'memory.sqlite') != first
    monkeypatch.delenv('HYDRA_EMBEDDING_CONFIG')
    assert memory_path(tmp_path / 'memory.sqlite') == tmp_path / 'memory.sqlite'


def test_remote_embeddings_require_https_and_explicit_revision():
    with pytest.raises(ValueError, match='HTTPS'):
        embed('private', {'backend': 'openai', 'endpoint': 'http://example.org/v1', 'model': 'm', 'revision': '1'})


def test_real_openai_embedding_protocol(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            raw = json.dumps({'data': [{'embedding': [0.1, 0.2, 0.3]}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        data = {'backend': 'openai', 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
                'model': 'custom-model', 'revision': 'abc', 'query_prefix': 'query: '}
        assert embed('search_query: find code', data) == [0.1, 0.2, 0.3]
        assert requests == [('/v1/embeddings', {'model': 'custom-model', 'input': 'query: find code'})]
        from hydra.source_index import SourceIndex
        from hydra.source_semantic import fill, search
        repository = tmp_path / 'repo'
        repository.mkdir()
        (repository / 'a.py').write_text('def routing(): pass')
        configuration = tmp_path / 'embedding.json'
        configuration.write_text(json.dumps(data))
        monkeypatch.setenv('HYDRA_EMBEDDING_CONFIG', str(configuration))
        index = SourceIndex(repository, cache=tmp_path / 'source.db')
        assert fill(index)['embedded'] == 1
        assert search(index, 'routing')['matches'][0]['path'] == 'a.py'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_embedding_redirect_never_forwards_authorization(monkeypatch):
    import threading
    import urllib.error
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(302)
            self.send_header('Location', '/unexpected-destination')
            self.end_headers()
        def do_GET(self):
            calls.append(self.path)
            self.send_response(500)
            self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('TEST_EMBED_KEY', 'synthetic-test-token')
    try:
        with pytest.raises(urllib.error.HTTPError):
            embed('text', {'backend': 'openai', 'endpoint': f'http://127.0.0.1:{server.server_port}/v1',
                           'model': 'm', 'revision': '1', 'api_key_env': 'TEST_EMBED_KEY'})
        assert calls == ['/v1/embeddings']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
