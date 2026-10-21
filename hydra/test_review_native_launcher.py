"""Actual subprocess contract for explicit Python native-provider launchers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import venv

from hydra.codex_client import CodexClient


def test_native_python_launcher_supports_spaces_without_executable_script(tmp_path):
    # Create an actual interpreter whose path contains spaces. The custom provider
    # is a script, not an OS executable: Windows cannot execute a shebang directly,
    # and the same contract is exercised on POSIX without an executable mode bit.
    environment = tmp_path / "Python runtime with spaces"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    interpreter = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    launcher = tmp_path / "custom native launcher"
    receipt = workspace / "execution.json"
    launcher.write_text(
        "#!" + str(interpreter) + "\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv\n"
        "root = Path(args[args.index('-C') + 1])\n"
        "(root / 'execution.json').write_text(json.dumps({'argv': args, 'stdin': sys.stdin.read(), 'python': sys.executable}))\n"
        "Path(args[args.index('-o') + 1]).write_text('Native launcher completed.')\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        launcher.chmod(0o600)
    # Shell metacharacters stay literal prompt text. They must never be executed.
    response = CodexClient(codex_bin=str(launcher), cd=str(workspace), sandbox="read-only").chat(
        [{"role": "user", "content": "Inspect $(touch injected); preserve `literal` text."}], timeout=10,
    )
    evidence = json.loads(receipt.read_text())
    assert response.content == "Native launcher completed."
    assert evidence["argv"][0] == str(launcher)
    assert evidence["argv"][1:4] == ["exec", "--sandbox", "read-only"]
    assert evidence["argv"][evidence["argv"].index("-C") + 1] == str(workspace)
    assert evidence["python"] == str(interpreter)
    assert evidence["stdin"] == "Inspect $(touch injected); preserve `literal` text."
    assert not (workspace / "injected").exists()
