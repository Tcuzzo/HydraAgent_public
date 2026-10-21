"""The public default environment destination cannot copy itself recursively."""
import json
import subprocess
import sys
from pathlib import Path


def test_default_cli_environment_is_one_finite_workspace_copy(tmp_path):
    source = tmp_path/'project'
    source.mkdir()
    (source/'operator.txt').write_bytes(b'original workspace contents\n')
    proc = subprocess.run([sys.executable, '-m', 'hydra', 'ops', 'env', 'create', '.',
                           '--title', 'demo', '--format', 'json'], cwd=source,
                          capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr[:2000]
    report = json.loads(proc.stdout)
    workspace = Path(report['workspace'])
    assert (workspace/'operator.txt').read_bytes() == b'original workspace contents\n'
    assert not (workspace/'evidence'/'environments').exists()
    assert len(list(source.rglob('operator.txt'))) == 2
