"""Quoted executable paths remain executable on every supported native shell."""
import json
import sys

from skills.bash import run


def test_quoted_python_executable_runs_in_selected_workspace(tmp_path):
    (tmp_path/'check.py').write_bytes(b"from pathlib import Path\nPath('proof.txt').write_bytes(b'executed')\n")
    result = run(f'"{sys.executable}" -B check.py', tmp_path, timeout=5)
    assert result['ok'], json.dumps(result)
    assert (tmp_path/'proof.txt').read_bytes() == b'executed'
