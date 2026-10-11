"""Required confinement never becomes implicit host execution."""
import socket
import sys

import pytest

from hydra import exec_backend as backend


@pytest.mark.parametrize('network', [False, True])
def test_unavailable_sandbox_never_launches_host_payload(tmp_path, monkeypatch, network):
    monkeypatch.setattr(backend, '_bwrap_available', lambda: False)
    launched = []
    def host(*args, **kwargs):
        launched.append(args)
        return backend.ExecResult(0, 'payload executed')
    monkeypatch.setattr(backend, '_run_on_host', host)
    result = backend.run_sandboxed([sys.executable, '-c', 'print(1)'], workspace=tmp_path, network=network)
    assert launched == []
    assert result.returncode != 0
    assert result.sandboxed is False
    assert 'not executed' in result.stdout.lower()


def test_unavailable_sandbox_shell_refuses_before_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, '_bwrap_available', lambda: False)
    monkeypatch.setattr(backend, '_run_on_host', lambda *a, **kw: pytest.fail('host launch'))
    assert backend.run_sandboxed_shell('echo payload > marker.txt', workspace=tmp_path).returncode != 0
    assert not (tmp_path / 'marker.txt').exists()


def test_interpreter_mounts_are_read_only_and_workspace_is_only_writable_bind(tmp_path, monkeypatch):
    env_root, base_root, workspace = (tmp_path / name for name in ('venv', 'python', 'workspace'))
    for path in (env_root, base_root, workspace):
        path.mkdir()
    monkeypatch.setattr(backend.sys, 'prefix', str(env_root))
    monkeypatch.setattr(backend.sys, 'base_prefix', str(base_root))
    command = backend._build_bwrap_cmd(['python', '-V'], workspace=workspace, network=False)
    for path in (env_root, base_root):
        assert any(command[i:i+3] == ['--ro-bind', str(path), str(path)] for i in range(len(command)))
    assert command.count('--bind') == 1
    index = command.index('--bind')
    assert command[index:index+3] == ['--bind', str(workspace), str(workspace)]
    assert '--unshare-net' in command


@pytest.fixture
def real_sandbox():
    backend._reset_capability_cache()
    if not backend._bwrap_available():
        pytest.skip('positive isolation requires usable Linux bwrap; refusal is independently tested')


def test_real_sandbox_imports_current_python_dependencies(tmp_path, real_sandbox):
    result = backend.run_sandboxed([sys.executable, '-c', 'import pytest; print("verifier-ready")'], workspace=tmp_path)
    assert result.returncode == 0, result.stdout
    assert result.sandboxed and 'verifier-ready' in result.stdout


def test_real_sandbox_blocks_writes_outside_workspace(tmp_path, real_sandbox):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside.txt'
    result = backend.run_sandboxed(['/usr/bin/python3', '-c',
        f'from pathlib import Path; Path("inside.txt").write_text("ok"); Path({str(outside)!r}).write_text("escape")'],
        workspace=workspace)
    assert result.sandboxed
    assert (workspace / 'inside.txt').read_text() == 'ok'
    assert not outside.exists()


@pytest.mark.parametrize('network', [False, True])
def test_real_sandbox_network_setting_controls_loopback(tmp_path, real_sandbox, network):
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        port = server.getsockname()[1]
        result = backend.run_sandboxed(['/usr/bin/python3', '-c',
            f'import socket; socket.create_connection(("127.0.0.1", {port}), timeout=2).close()'],
            workspace=tmp_path, network=network, timeout=10)
    assert result.sandboxed
    assert (result.returncode == 0) is network
