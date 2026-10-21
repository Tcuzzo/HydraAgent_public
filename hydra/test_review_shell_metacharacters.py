"""Execute real quoted programs while preserving literal and active shell syntax."""
import json
import os
from pathlib import Path
import sys
import venv

import pytest

from skills.bash import run


def _exercise_shell(root: Path, executable: Path) -> None:
    (root / "check script.py").write_bytes(
        b"import json, sys\nfrom pathlib import Path\n"
        b"Path('arguments.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
    )
    result = run(
        f'"{executable}" -B "check script.py" "literal & pipe | (paren)" '
        '&& echo done > "redirect file.txt"',
        root,
        timeout=10,
    )
    assert result["ok"], json.dumps(result)
    assert json.loads((root / "arguments.json").read_text(encoding="utf-8")) == [
        "literal & pipe | (paren)"
    ]
    assert (root / "redirect file.txt").read_text(encoding="utf-8").strip() == "done"


def test_shell_preserves_quoted_arguments_and_conditional_redirection(tmp_path):
    _exercise_shell(tmp_path, Path(sys.executable))


@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows command parsing")
def test_windows_python_path_with_spaces_and_metacharacters(tmp_path):
    environment = tmp_path / "Python space & (probe)"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    _exercise_shell(tmp_path, environment / "Scripts" / "python.exe")
