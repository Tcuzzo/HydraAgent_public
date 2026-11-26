"""A fresh CLI-transport setup can answer without inventing a model id."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def _fake_codex(tmp_path: Path) -> Path:
    """Replace only the account-backed executable, preserving the real CLI seam."""
    script = tmp_path / "fake_codex.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = sys.stdin.read()\n"
        "pathlib.Path(os.environ['CODEX_TEST_RECORD']).write_text(\n"
        "    json.dumps({'args': args, 'prompt': prompt}), encoding='utf-8')\n"
        "pathlib.Path(args[args.index('-o') + 1]).write_text(\n"
        "    'Ready from the configured CLI transport.', encoding='utf-8')\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        executable = tmp_path / "fake_codex.cmd"
        executable.write_text(
            f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8"
        )
    else:
        executable = tmp_path / "fake_codex"
        executable.write_text(
            f"#!{sys.executable}\n" + script.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        executable.chmod(0o700)
    return executable


@pytest.mark.parametrize("configured_model", [None, "operator-selected-model"])
def test_fresh_codex_setup_answers_with_the_cli_model_choice(tmp_path, configured_model):
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("HYDRA_", "BACKS_", "CODEX_", "OLLAMA_")):
            env.pop(name)
    env.update(
        HYDRA_ENV_DIR=str(tmp_path / "providers"),
        HYDRA_HOME=str(tmp_path / "state"),
        HYDRA_MODEL_HEALTH_PATH=str(tmp_path / "model-health.json"),
        HYDRA_CODEX_BIN=str(_fake_codex(tmp_path)),
        CODEX_TEST_RECORD=str(tmp_path / "invocation.json"),
        # A running local model must not decide this fresh-setup regression.
        OLLAMA_ENDPOINT="http://127.0.0.1:1",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")))
    )
    if configured_model:
        env["CODEX_MODEL"] = configured_model

    def cli(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "hydra", *args],
            cwd=tmp_path,
            env=env,
            text=True,
            capture_output=True,
            timeout=20,
        )

    setup = cli("setup", "--mode", "codex", "--non-interactive")
    assert setup.returncode == 0, setup.stdout + setup.stderr

    answer = cli(
        "ask", "Reply with a readiness confirmation.",
        "--approval-policy", "deny", "--memory-root", str(tmp_path / "memory"),
    )
    assert answer.returncode == 0, answer.stdout + answer.stderr
    assert "Ready from the configured CLI transport." in answer.stdout
    assert "halted=natural" in answer.stderr
    invocation = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))
    assert invocation["args"][0] == "exec"
    assert "Reply with a readiness confirmation." in invocation["prompt"]
    if configured_model:
        assert invocation["args"][invocation["args"].index("-m") + 1] == configured_model
    else:
        # The CLI's own configuration selects its default; Hydra only labels it.
        assert "-m" not in invocation["args"]


def test_codex_client_config_exposes_the_same_default_model_as_the_client(monkeypatch, tmp_path):
    from hydra.providers import make_client
    from hydra.setup import setup_codex_oauth

    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("HYDRA_CODEX_BIN", str(_fake_codex(tmp_path)))
    setup_codex_oauth(env_dir=tmp_path / "providers")

    client, config = make_client("codex", env_dir=tmp_path / "providers")

    assert config.model
    assert config.model == client.model
