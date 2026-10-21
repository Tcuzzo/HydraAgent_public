"""A timed-out real team writer must leave a recoverable file pre-image."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest


@pytest.mark.parametrize('operation', ['fs_write', 'fs_edit'])
def test_public_file_tools_preserve_exact_preimage_for_undo(tmp_path, monkeypatch, operation):
    from hydra.cli.tool_binding import bind_tools
    from hydra.edit_checkpoints import CheckpointStore, undo
    monkeypatch.setenv('HYDRA_CHECKPOINTS_DIR', str(tmp_path / 'snapshots'))
    root = tmp_path / 'project'
    root.mkdir()
    target = root / 'source.txt'
    original = b'original contents\r\n'
    target.write_bytes(original)
    tool = next(t for t in bind_tools(root, approval_policy='allow', include_mcp=False) if t.name == operation)
    if operation == 'fs_write':
        tool.invoke(path='source.txt', content='changed\n', overwrite=True)
    else:
        tool.invoke(path='source.txt', old_string='original', new_string='changed')
    assert target.read_bytes() != original
    undo(CheckpointStore(root))
    assert target.read_bytes() == original


@pytest.fixture
def timeout_wire(monkeypatch):
    requests = []
    second_request = threading.Event()
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            if any(message.get('role') == 'tool' for message in data['messages']):
                second_request.set()
                release.wait(15)
                return
            message = {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'overwrite-1', 'type': 'function', 'function': {
                    'name': 'fs_write', 'arguments': json.dumps({
                        'path': 'notes.txt', 'content': 'partial candidate\n', 'overwrite': True})}}]}
            payload = json.dumps({'model': data['model'], 'choices': [
                {'message': message, 'finish_reason': 'tool_calls'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HYDRAREVTEST_ENDPOINT', f'http://127.0.0.1:{server.server_port}/v1')
    try:
        yield requests, second_request
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_team_timeout_has_recoverable_file_preimage(tmp_path, monkeypatch, timeout_wire):
    from hydra.edit_checkpoints import CheckpointStore, list_stack, undo
    requests, second_request = timeout_wire
    root = tmp_path / 'workspace'
    root.mkdir()
    original = b'Unique operator notes predating the run: keep recovery bytes 832d\n'
    target = root / 'notes.txt'
    target.write_bytes(original)
    monkeypatch.setenv('HYDRA_CHECKPOINTS_DIR', str(tmp_path / 'checkpoints'))
    monkeypatch.setenv('HYDRA_MEMORY_ROOT', str(tmp_path / 'memory'))
    plan = {'goal': 'Update notes.txt in the workspace.', 'provider': 'hydrarevtest',
            'model': 'local-scripted-transport', 'family': 'test-transport',
            'timeout_seconds': 4, 'max_iterations': 3,
            'tasks': [{'id': 'writer', 'specialist': 'seam_bot', 'acceptance': {
                'artifacts': [{'path': 'notes.txt', 'must_change': True}]}}]}
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan))
    process = subprocess.run([sys.executable, '-m', 'hydra', 'team', 'run', str(plan_path),
        '--root', str(root), '--output-root', str(tmp_path / 'runs'),
        '--memory-root', str(tmp_path / 'memory'), '--approval-policy', 'allow'],
        capture_output=True, text=True, timeout=15, env=dict(os.environ))
    assert process.stdout.strip(), process.stderr
    report = json.loads(process.stdout)
    assert second_request.is_set(), 'Did not reach post-write model call'
    assert target.read_bytes() == b'partial candidate\n'
    assert report['verdict'] == 'RED' and report['timed_out'] == 1
    store = CheckpointStore(root)
    checkpoint_rows = list_stack(store)
    undo_message = undo(store)
    evidence = {'returncode': process.returncode, 'verdict': report['verdict'],
                'timed_out': report['timed_out'], 'outcomes': report['outcomes'],
                'checkpoint_count': len(checkpoint_rows), 'undo': undo_message,
                'post_undo_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                'original_sha256': hashlib.sha256(original).hexdigest(),
                'request_count': len(requests), 'run_directory': report['run_directory']}
    (tmp_path / 'timeout-evidence.json').write_text(json.dumps(evidence, indent=2))
    assert target.read_bytes() == original, json.dumps(evidence, indent=2)
