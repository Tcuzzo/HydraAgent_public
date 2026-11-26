"""Codex's UTF-8 protocol must not inherit a Windows legacy text encoding."""
import json
import subprocess
import sys

from hydra.codex_client import CodexClient


def test_native_prompt_and_output_use_utf8_under_a_legacy_locale(tmp_path, monkeypatch):
    script = tmp_path / "native transport.py"
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "prompt = sys.stdin.buffer.read().decode('utf-8')\n"
        "Path(sys.argv[1]).write_text(json.dumps({'prompt': prompt}, ensure_ascii=False), encoding='utf-8')\n"
        "sys.stdout.buffer.write('transport \u2713\\n'.encode('utf-8'))\n",
        encoding="utf-8",
    )
    # Exercise real OS pipes while making their implicit encoding match the
    # Windows CI failure. The process protocol must choose its own encoding.
    monkeypatch.setattr(subprocess, "_text_encoding", lambda: "cp1252")

    def runner(argv, **options):
        output = argv[argv.index("-o") + 1]
        return subprocess.run([sys.executable, str(script), output], **options)

    prompt = "Inspect the r\u00e9sum\u00e9 \u2550 \u4e2d\u6587 \U0001f600"
    client = CodexClient(codex_bin="injected-native-transport", cd=str(tmp_path), runner=runner)
    response = client.chat([{"role": "user", "content": prompt}])

    assert json.loads(response.content)["prompt"].endswith(prompt)
    assert response.raw["stdout"] == "transport \u2713\n"
    assert response.native_execution.returncode == 0
