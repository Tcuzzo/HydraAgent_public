"""Drive real Hydra workers using only a scripted external-model transport."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

import pytest


@pytest.fixture
def model_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            messages = data['messages']
            system = str(messages[0]['content'])
            results = [m for m in messages if m['role'] == 'tool']
            if data['model'] == 'failing-tool' and not results:
                import sys
                message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'fail1', 'type': 'function', 'function': {'name': 'bash', 'arguments': json.dumps({'command': f'"{sys.executable}" -c "raise SystemExit(3)"'})}}]}
            elif 'specialist software_engineer' in system and not results:
                message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'write1', 'type': 'function', 'function': {'name': 'fs_write', 'arguments': json.dumps({'path': 'result.txt', 'content': 'worker-produced evidence'})}}]}
            elif 'specialist code_reviewer' in system and not results:
                message = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'read1', 'type': 'function', 'function': {'name': 'fs_read', 'arguments': json.dumps({'path': 'result.txt'})}}]}
            else:
                message = {'role': 'assistant', 'content': 'Observed worker-produced evidence. Verification is limited to the recorded file operation.'}
            payload = json.dumps({'model': data['model'], 'choices': [{'message': message, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_worker_write_handoff_review_and_private_prompt(tmp_path, monkeypatch, model_server):
    from hydra.teams import run_team
    endpoint, requests = model_server
    monkeypatch.setenv('HYDRATEST_ENDPOINT', endpoint)
    root = tmp_path / 'workspace'
    root.mkdir()
    shadow = root / 'hydra'
    shadow.mkdir()
    (shadow / '__init__.py').write_text('raise RuntimeError("untrusted workspace package executed")')
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'software_engineer.json').write_text(json.dumps({'canon': 'LOCAL CANON SENTINEL', 'essence': 'LOCAL ESSENCE SENTINEL'}))
    plan = {'goal': 'Write result.txt and review its contents.', 'provider': 'hydratest', 'model': 'arbitrary-model',
            'family': 'arbitrary-family', 'max_iterations': 4, 'timeout_seconds': 20,
            'tasks': [{'id': 'build', 'specialist': 'software_engineer'}, {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['build']}]}
    report = run_team(plan, root=root, output_root=tmp_path / 'runs', approval_policy='allow', private_root=private)
    assert report['verdict'] == 'GREEN', report
    assert (root / 'result.txt').read_text() == 'worker-produced evidence'
    assert report['verified'] is False
    assert [r['tool_calls'] for r in report['outcomes']] == [1, 1]
    systems = [r['messages'][0]['content'] for r in requests]
    assert any('LOCAL CANON SENTINEL' in s and 'LOCAL ESSENCE SENTINEL' in s for s in systems)
    reviewer = next(r for r in requests if 'specialist code_reviewer' in r['messages'][0]['content'])
    assert 'fs_write' not in [t['function']['name'] for t in reviewer['tools']]
    assert 'Prior task observations' in json.dumps(reviewer['messages'])
    assert 'LOCAL CANON SENTINEL' not in json.dumps(report)


def test_failed_worker_blocks_dependents(tmp_path, monkeypatch):
    from hydra.teams import run_team
    monkeypatch.delenv('UNCONFIGUREDTEST_ENDPOINT', raising=False)
    plan = {'goal': 'Inspect', 'provider': 'unconfiguredtest', 'model': 'some-model', 'timeout_seconds': 10,
            'tasks': [{'id': 'first', 'specialist': 'vision'}, {'id': 'second', 'specialist': 'design_taste', 'depends_on': ['first']}]}
    result = run_team(plan, root=tmp_path, output_root=tmp_path / 'runs')
    assert result['verdict'] == 'RED'
    assert next(r for r in result['results'] if r['id'] == 'second')['status'] == 'blocked'
    assert not (Path(result['run_directory']) / 'second.result.json').exists()


def test_failed_real_command_blocks_dependency_even_if_model_finishes(tmp_path, monkeypatch, model_server):
    from hydra.teams import run_team
    monkeypatch.setenv('HYDRATEST_ENDPOINT', model_server[0])
    plan = {'goal': 'Run a command', 'provider': 'hydratest', 'model': 'failing-tool', 'timeout_seconds': 20,
            'tasks': [{'id': 'build', 'specialist': 'software_engineer'}, {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['build']}]}
    report = run_team(plan, root=tmp_path, output_root=tmp_path / 'runs', approval_policy='allow')
    assert report['verdict'] == 'RED'
    assert report['outcomes'][0]['evidence'][0]['error'] is True
    assert next(r for r in report['results'] if r['id'] == 'review')['status'] == 'blocked'
