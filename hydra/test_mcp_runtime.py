"""Real protocol/process tests: no mocked MCP client, server or filesystem."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import pytest

pytest.importorskip('mcp')


def server_config(root):
    return {'command': sys.executable, 'args': ['-m', 'hydra', 'mcp', 'serve', '--root', str(root)], 'timeout': 15}


def test_stdio_discovery_read_schema_and_scope(tmp_path):
    from hydra.mcp_bridge import invoke
    (tmp_path / 'proof.txt').write_text('real MCP file content', encoding='utf-8')
    server = server_config(tmp_path)
    listed = invoke(server)
    names = {t['name'] for t in listed['tools']}
    assert {'fs_read', 'list_directory', 'hydra_specialists'} <= names
    assert 'bash' not in names and 'fs_write' not in names
    result = invoke(server, 'fs_read', {'path': 'proof.txt'})
    assert 'real MCP file content' in json.dumps(result)
    assert not result.get('isError')
    escaped = invoke(server, 'fs_read', {'path': '../outside.txt'})
    assert escaped['isError']
    with pytest.raises(RuntimeError):
        invoke(server, 'fs_read', {'path': 12})


def test_unknown_effect_denied_before_process_launch(tmp_path):
    from hydra.mcp_bridge import bind_mcp_tools
    from hydra.policy import ApprovalDenied, ApprovalPolicy
    marker = tmp_path / 'launched'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'servers': {'danger': {'command': sys.executable, 'args': ['-c', f'from pathlib import Path; Path({str(marker)!r}).touch()']}}}))
    tools = {t.name: t for t in bind_mcp_tools(config, ApprovalPolicy('deny'))}
    with pytest.raises(ApprovalDenied):
        tools['mcp_call'].invoke(server='danger', tool='write', arguments={})
    assert not marker.exists()


def test_initialization_deadline_is_bounded():
    from hydra.mcp_bridge import invoke
    start = time.monotonic()
    with pytest.raises(RuntimeError):
        invoke({'command': sys.executable, 'args': ['-c', 'import time; time.sleep(30)'], 'timeout': 0.25})
    assert time.monotonic() - start < 10


@pytest.mark.parametrize('text,category', [
    ('email person@company.test', 'email'),
    ('endpoint 192.0.2.23', 'ip_address'),
    ('endpoint [fd01:abcd::12]', 'ip_address'),
    ('api_key = "example-but-actually-secret-token"', 'credential'),
    ('https://user:password@service.test', 'credential'),
    ('C:\\Users\\owner\\secret', 'private_path'),
])
def test_public_profile_pattern_detection(text, category):
    from hydra.public_profiles import scan_text
    assert category in scan_text(text)


def test_streamable_http_round_trip(tmp_path):
    import socket
    import threading
    import uvicorn
    from hydra.mcp_server import create_server
    from hydra.mcp_bridge import invoke
    (tmp_path / 'http.txt').write_text('real HTTP transport evidence')
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    app = create_server(tmp_path).streamable_http_app(stateless_http=True, json_response=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        result = invoke({'url': f'http://127.0.0.1:{port}/mcp', 'timeout': 10}, 'fs_read', {'path': 'http.txt'})
        assert not result.get('isError')
        assert 'real HTTP transport evidence' in json.dumps(result)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
