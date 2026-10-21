"""Native subprocess transport must not depend on the host text encoding."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_native_prompt_uses_utf8_with_a_non_utf8_host_locale(tmp_path):
    launcher = tmp_path / "unicode-native-model"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\nfrom pathlib import Path\n"
        "args=sys.argv\nroot=Path(args[args.index('-C')+1])\n"
        "prompt=sys.stdin.buffer.read().decode('utf-8')\n"
        "(root/'wire.json').write_text(json.dumps({'prompt':prompt}))\n"
        "Path(args[args.index('-o')+1]).write_text('Received Unicode.',encoding='utf-8')\n",
        encoding="utf-8",
    )
    launcher.chmod(0o700)
    prompt = "Inspect the bot library: \U0001f409 \u2200 \u2192 \u4f60\u597d"
    driver = (
        "import json\nfrom hydra.codex_client import CodexClient\n"
        f"client=CodexClient(codex_bin={str(launcher)!r},cd={str(tmp_path)!r})\n"
        f"response=client.chat(json.loads({json.dumps([{'role':'user','content':prompt}])!r}),timeout=10)\n"
        "print(json.dumps({'content':response.content}))\n"
    )
    environment = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0")
    process = subprocess.run([sys.executable, "-X", "utf8=0", "-c", driver],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=15, env=environment)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout)["content"] == "Received Unicode."
    assert json.loads((tmp_path / "wire.json").read_text())["prompt"] == prompt
