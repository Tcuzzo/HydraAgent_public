import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from hydra.model_residency import residency


def test_explicit_load_unload_real_local_protocol(monkeypatch):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            response = b'{"done":true}'
            self.send_response(200)
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        def log_message(self, *a): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('OLLAMA_ENDPOINT', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('NO_PROXY', '')
    try:
        assert residency('load', 'local-model')['status'] == 'completed'
        assert residency('unload', 'local-model')['status'] == 'completed'
        assert [item[1]['keep_alive'] for item in calls] == ['5m', 0]
        assert all(item[0] == '/api/generate' and item[1]['prompt'] == '' for item in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_residency_does_not_contact_remote_server(monkeypatch):
    monkeypatch.setenv('OLLAMA_ENDPOINT', 'https://remote.example')
    with pytest.raises(ValueError, match='loopback'):
        residency('unload', 'm')
