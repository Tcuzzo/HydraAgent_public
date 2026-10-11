"""Regression checks for resource, capability and publication boundaries."""
import json
from pathlib import Path
import sys
import time

import pytest


def test_noisy_worker_retains_bounded_bytes_without_deadlock():
    from hydra.orchestrate import dispatch, SubagentTask, MAX_OUTPUT_BYTES
    size = MAX_OUTPUT_BYTES * 20
    report = dispatch([SubagentTask('noise', [sys.executable, '-c', f'import os; os.write(1, b"a"*{size}); os.write(2, b"b"*{size})'])])
    row = report['results'][0]
    assert row['status'] == 'ok'
    assert row['stdout_bytes'] == row['stderr_bytes'] == size
    assert len(row['stdout'].encode()) <= MAX_OUTPUT_BYTES
    assert len(row['stderr'].encode()) <= MAX_OUTPUT_BYTES


def test_timeout_stops_nested_separate_process_group(tmp_path):
    import psutil
    from hydra.orchestrate import dispatch, SubagentTask
    pidfile = tmp_path / 'child.pid'
    child = "import time; time.sleep(40)"
    code = f"from hydra.proc import popen_portable; import time; from pathlib import Path; p=popen_portable([{sys.executable!r}, '-c', {child!r}]); Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(40)"
    start = time.monotonic()
    report = dispatch([SubagentTask('parent', [sys.executable, '-c', code], timeout_seconds=2)])
    assert report['results'][0]['status'] == 'timeout'
    assert time.monotonic() - start < 12
    pid = int(pidfile.read_text())
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_reader_mcp_restriction_survives_operator_override(tmp_path, monkeypatch):
    from hydra.mcp_bridge import bind_mcp_tools
    from hydra.policy import ApprovalDenied, ApprovalPolicy
    path = tmp_path / 'mcp.json'
    path.write_text(json.dumps({'servers': {'test': {'command': sys.executable, 'read_only_tools': ['read']}}}))
    def unexpected(*args, **kwargs):
        pytest.fail('mutating MCP transport must never be reached')
    monkeypatch.setattr('hydra.mcp_bridge.invoke', unexpected)
    policy = ApprovalPolicy('deny', authority_checker=lambda: True)
    tools = {t.name: t for t in bind_mcp_tools(path, policy, read_only=True)}
    with pytest.raises(ApprovalDenied):
        tools['mcp_call'].invoke(server='test', tool='delete_record', arguments={})


def test_exited_parent_does_not_leave_inherited_pipe_child(tmp_path):
    import psutil
    from hydra.orchestrate import dispatch, SubagentTask
    pidfile = tmp_path / 'orphan.pid'
    code = f"import subprocess; from pathlib import Path; p=subprocess.Popen([{sys.executable!r}, '-c', 'import time; time.sleep(30)']); Path({str(pidfile)!r}).write_text(str(p.pid))"
    child = None
    try:
        report = dispatch([SubagentTask('short_parent', [sys.executable, '-c', code], timeout_seconds=1)])
        child = psutil.Process(int(pidfile.read_text())) if psutil.pid_exists(int(pidfile.read_text())) else None
        assert report['results'][0]['status'] == 'timeout'
        assert child is None or not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if child is not None and child.is_running():
            child.kill()


@pytest.mark.parametrize('canon', ['api_key = "fixture-secret-value"', 'Authorization: Bearer fixture-secret-value', 'ghs_' + 'x' * 32, 'AKIA' + 'A' * 16])
def test_decoded_public_text_is_scanned_before_json_escaping(tmp_path, canon):
    from hydra.public_profiles import export_profiles, PublicProfileError
    data = {'id': 'vision', 'edition': 'generic-public', 'canon': canon, 'essence': 'Clarity', 'license': 'MIT', 'credits': ['Test author']}
    (tmp_path / 'vision.json').write_text(json.dumps(data))
    with pytest.raises(PublicProfileError, match='credential'):
        export_profiles(tmp_path, tmp_path / 'export', ['vision'])
    assert not (tmp_path / 'export').exists()


def test_private_edition_cannot_be_relabelled_public(tmp_path):
    from hydra.public_profiles import export_profiles, PublicProfileError
    data = {'id': 'vision', 'edition': 'private', 'canon': 'Confidential process', 'essence': 'Private style', 'license': 'MIT', 'credits': ['Test author']}
    (tmp_path / 'vision.json').write_text(json.dumps(data))
    with pytest.raises(PublicProfileError, match='generic-public'):
        export_profiles(tmp_path, tmp_path / 'export', ['vision'])


@pytest.mark.parametrize('ids', [('a', 'A'), ('con', 'review'), ('LPT1', 'review')])
def test_task_ids_are_portable(ids):
    from hydra.teams import validate_plan
    with pytest.raises(ValueError, match='identifiers'):
        validate_plan({'goal': 'Inspect', 'tasks': [{'id': i, 'specialist': 'vision'} for i in ids]})


def test_image_dot_segments_cannot_escape_workspace(tmp_path):
    from hydra.team_worker import _images
    root = tmp_path / 'workspace'
    root.mkdir()
    (tmp_path / 'private.png').write_bytes(b'\x89PNG\r\n\x1a\nprivate')
    with pytest.raises(ValueError, match='inside'):
        _images(['../private.png'], root)


def test_private_profiles_reject_distribution_sibling():
    from hydra.specialists import system_prompt
    with pytest.raises(ValueError, match='outside'):
        system_prompt('vision', private_root=Path(__file__).parent.parent / 'skills')
