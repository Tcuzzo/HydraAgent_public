"""Team acceptance on the real CLI, subprocess workers, files and HTTP wire."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


@pytest.fixture
def bot_wire(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            system = str(data['messages'][0]['content'])
            results = [m for m in data['messages'] if m['role'] == 'tool']
            message = {'role': 'assistant', 'content': 'Completed the assigned work.'}
            if data['model'] != 'prose-only' and not results:
                writing = any(f'specialist {role}' in system for role in ('software_engineer', 'seam_bot'))
                tool = 'fs_write' if writing else 'fs_read'
                arguments = {'path': 'result.txt', 'content': 'repaired\n'} if writing else {'path': 'result.txt'}
                message = {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'operation-1', 'type': 'function', 'function': {'name': tool, 'arguments': json.dumps(arguments)}}]}
            payload = json.dumps({'model': data['model'], 'choices': [{'message': message, 'finish_reason': 'stop'}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HYDRABOTTEST_ENDPOINT', f'http://127.0.0.1:{server.server_port}/v1')
    try:
        yield requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def run_cli(tmp_path, tasks, **fields):
    root = tmp_path / 'workspace'
    root.mkdir(exist_ok=True)
    plan = {'goal': 'Write result.txt and inspect the actual result.', 'provider': 'hydrabottest',
            'model': 'file-operation', 'family': 'builder-family', 'timeout_seconds': 20,
            'max_iterations': 4, 'tasks': tasks, **fields}
    plan_file = tmp_path / 'plan.json'
    plan_file.write_text(json.dumps(plan))
    process = subprocess.run([sys.executable, '-m', 'hydra', 'team', 'run', str(plan_file),
                              '--root', str(root), '--output-root', str(tmp_path / 'runs'),
                              '--approval-policy', 'allow'], capture_output=True, text=True,
                             timeout=50, env=dict(os.environ))
    assert process.stdout.strip(), process.stderr
    return process, json.loads(process.stdout), root


def test_requested_bots_are_default_and_have_real_packaged_libraries():
    from hydra.specialists import catalog, system_prompt
    from hydra.teams import make_plan
    rows = {row['id']: row for row in catalog()}
    assert {'hydra_bot', 'grok_bot', 'seam_bot'} <= rows.keys()
    assert {'vision', 'design_taste', 'software_engineer', 'code_reviewer',
            'test_engineer', 'kaizen', 'lean_six_sigma'} <= rows.keys()
    assert [task['specialist'] for task in make_plan('Repair the project', 'local', 'configured')['tasks']] == [
        'hydra_bot', 'grok_bot', 'seam_bot', 'test_engineer', 'code_reviewer']
    for name in rows:
        assert rows[name]['library'], name
        prompt = system_prompt(name)
        assert 'Knowledge library' in prompt
        for entry in rows[name]['library']:
            assert len(entry['sha256']) == 64 and entry['source'].startswith('https://github.com/Tcuzzo/backs-aios-skills/')
            assert entry['skill'] in prompt
    assert 'Cactus' not in system_prompt('grok_bot')
    assert 'does not select or install' in system_prompt('grok_bot')


def test_prose_only_writer_is_incomplete_and_blocks_review(tmp_path, bot_wire):
    process, report, root = run_cli(tmp_path, [
        {'id': 'build', 'specialist': 'software_engineer'},
        {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['build']}], model='prose-only')
    assert process.returncode != 0
    assert report['verdict'] == 'RED'
    assert not (root / 'result.txt').exists()
    build = next(row for row in report['outcomes'] if row['id'] == 'build')
    assert not build['ok'] and build['acceptance']['status'] == 'failed'
    assert 'observation' in ' '.join(build['acceptance']['reasons'])
    assert next(row for row in report['results'] if row['id'] == 'review')['status'] == 'blocked'


def test_required_independent_review_changes_cli_exit_and_verdict(tmp_path, bot_wire):
    process, report, root = run_cli(tmp_path, [
        {'id': 'build', 'specialist': 'software_engineer'},
        {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['build']}], require_independent_review=True)
    assert (root / 'result.txt').read_text() == 'repaired\n'
    assert all(row['ok'] for row in report['outcomes'])
    assert report['independence']['status'] == 'degraded'
    assert process.returncode != 0 and report['verdict'] == 'RED'


def test_artifact_acceptance_checks_real_bytes_and_records_library_hashes(tmp_path, bot_wire):
    expected = hashlib.sha256(b'repaired\n').hexdigest()
    process, report, root = run_cli(tmp_path, [
        {'id': 'build', 'specialist': 'seam_bot', 'acceptance': {
            'artifacts': [{'path': 'result.txt', 'sha256': expected, 'must_change': True}]}}])
    assert process.returncode == 0, report
    assert (root / 'result.txt').read_bytes() == b'repaired\n'
    outcome = report['outcomes'][0]
    assert outcome['acceptance']['status'] == 'passed'
    assert outcome['acceptance']['artifacts'][0]['sha256'] == expected
    assert outcome['acceptance']['artifacts'][0]['changed'] is True
    assert outcome['library'] and outcome['verified'] is False
    assert report['verified'] is False


def test_missing_artifact_cannot_pass_because_another_tool_succeeded(tmp_path, bot_wire):
    process, report, root = run_cli(tmp_path, [
        {'id': 'build', 'specialist': 'software_engineer', 'acceptance': {
            'artifacts': [{'path': 'missing.txt'}]}}])
    assert process.returncode != 0 and report['verdict'] == 'RED'
    assert (root / 'result.txt').is_file()
    assert report['outcomes'][0]['acceptance']['status'] == 'failed'


def test_existing_unchanged_artifact_is_not_a_new_repair(tmp_path, bot_wire):
    root = tmp_path / 'workspace'
    root.mkdir()
    (root / 'result.txt').write_text('repaired\n')
    process, report, _ = run_cli(tmp_path, [
        {'id': 'build', 'specialist': 'software_engineer', 'acceptance': {
            'artifacts': [{'path': 'result.txt', 'must_change': True}]}}])
    assert process.returncode != 0 and report['verdict'] == 'RED'
    assert report['outcomes'][0]['acceptance']['artifacts'][0]['changed'] is False


@pytest.mark.parametrize('artifact', [
    {'path': '../outside'}, {'path': '/outside'}, {'path': 'C:\\outside'},
    {'path': 'ok', 'sha256': 'bad'}, {'path': 'ok', 'must_change': 'yes'},
])
def test_acceptance_contract_rejects_unsafe_or_ill_typed_paths(artifact):
    from hydra.teams import validate_plan
    with pytest.raises(ValueError):
        validate_plan({'goal': 'work', 'tasks': [{'id': 'build', 'specialist': 'software_engineer',
            'acceptance': {'artifacts': [artifact]}}]})


def test_seam_bot_is_a_writer_and_must_be_ordered():
    from hydra.teams import validate_plan
    with pytest.raises(ValueError, match='writer'):
        validate_plan({'goal': 'work', 'tasks': [
            {'id': 'seam', 'specialist': 'seam_bot'}, {'id': 'read', 'specialist': 'vision'}]})


def test_independence_contract_includes_test_and_seam_authors():
    from hydra.teams import team_independence, validate_plan
    plan = {'goal': 'work', 'require_independent_review': True, 'tasks': [
        {'id': 'seam', 'specialist': 'seam_bot'},
        {'id': 'test', 'specialist': 'test_engineer', 'depends_on': ['seam']},
        {'id': 'review', 'specialist': 'code_reviewer', 'depends_on': ['test']}]}
    validate_plan(plan)
    outcomes = [{'id': name, 'ok': True, 'family': family, 'provider': 'p', 'model': name}
                for name, family in [('seam', 'one'), ('test', 'two'), ('review', 'two')]]
    assert team_independence(plan, outcomes)['status'] == 'degraded'


@pytest.mark.parametrize('with_artifact', [False, True])
def test_native_client_completion_needs_runtime_artifact_proof(tmp_path, monkeypatch, with_artifact):
    # Only the external native model executable is scripted. The worker process,
    # routing, filesystem and acceptance verifier are the actual implementation.
    binary = tmp_path / 'native-model'
    binary.write_text('#!' + sys.executable + '\n' +
        'import sys\nfrom pathlib import Path\n'
        'args=sys.argv\nroot=Path(args[args.index("-C")+1])\n'
        '(root/"result.txt").write_text("native result\\n")\n'
        'Path(args[args.index("-o")+1]).write_text("Completed native task.")\n')
    binary.chmod(0o700)
    monkeypatch.setenv('HYDRA_CODEX_BIN', str(binary))
    task = {'id': 'build', 'specialist': 'software_engineer'}
    if with_artifact:
        task['acceptance'] = {'artifacts': [{'path': 'result.txt', 'must_change': True}]}
    process, report, root = run_cli(tmp_path, [task], provider='codex', model='codex', family='native-model')
    assert (root / 'result.txt').read_text() == 'native result\n'
    assert (process.returncode == 0) is with_artifact
    assert report['outcomes'][0]['ok'] is with_artifact
    assert report['verified'] is False


def test_artifact_link_cannot_turn_outside_file_into_workspace_evidence(tmp_path, bot_wire):
    root = tmp_path / 'workspace'
    root.mkdir()
    outside = tmp_path / 'outside.txt'
    outside.write_text('outside')
    try:
        (root / 'evidence.txt').symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable on this platform')
    process, report, _ = run_cli(tmp_path, [{'id': 'build', 'specialist': 'software_engineer',
        'acceptance': {'artifacts': [{'path': 'evidence.txt'}]}}])
    assert process.returncode != 0 and report['verdict'] == 'RED'
    assert outside.read_text() == 'outside'
