"""Profile selection honors the explicit directory through live and offline entrypoints."""
from contextlib import contextmanager
import subprocess
import sys

import pytest

from hydra.test_public_standalone_boundary import cli, isolated_environment
from hydra.test_public_standalone_role_models import model_recorder


@contextmanager
def profile_directories(tmp_path, *, pin_models=False):
    with model_recorder() as (selected, selected_calls), model_recorder() as (ambient, ambient_calls):
        env, workspace, providers = isolated_environment(tmp_path, selected)
        ambient_root = tmp_path / "ambient-providers"
        ambient_root.mkdir()
        env["HYDRA_ENV_DIR"] = str(ambient_root)
        for root, endpoint, model in ((ambient_root, ambient, "ambient-model"),
                                      (providers, selected, "selected-model")):
            config = f"OLLAMA_ENDPOINT={endpoint}\n"
            if pin_models:
                config += f"OLLAMA_MODEL={model}\n"
            (root / ".env.ollama").write_text(config, encoding="utf-8")
        yield env, workspace, providers, selected_calls, ambient_calls


@pytest.mark.parametrize("profile", ["local", "auto", "cloud"])
def test_ask_profile_uses_only_explicit_directory(tmp_path, profile):
    with profile_directories(tmp_path) as (env, workspace, providers, selected, ambient):
        result = cli(env, workspace, "ask", "hello", "--profile", profile,
                     "--env-dir", str(providers), "--model", "explicit-cli", "--root", str(workspace),
                     "--approval-policy", "deny", "--max-iterations", "2", "--timeout", "2")
        assert result.returncode == 0, result.stdout + result.stderr
        assert [(p, b["model"]) for p, b in selected if b is not None] == [
            ("/v1/chat/completions", "explicit-cli")
        ]
        assert [path for path, _ in ambient] == []


def test_chat_profile_startup_uses_only_explicit_directory(tmp_path):
    with profile_directories(tmp_path) as (env, workspace, providers, _selected, ambient):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "hydra", "chat", "--profile", "local", "--env-dir",
             str(providers), "--model", "explicit-cli", "--no-local-memory", "--root", str(workspace),
             "--approval-policy", "deny"],
            cwd=workspace, env=env, input="/exit\n", capture_output=True, text=True, timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"root={workspace}" in result.stdout
        assert [path for path, _ in ambient] == []


def test_runtime_only_profile_reports_model_from_explicit_directory(tmp_path):
    with profile_directories(tmp_path, pin_models=True) as (env, workspace, providers, selected, ambient):
        result = cli(env, workspace, "ask", "hello", "--runtime-only", "--profile", "local",
                     "--env-dir", str(providers), "--root", str(workspace), "--approval-policy", "deny")
        assert result.returncode == 0, result.stdout + result.stderr
        assert not selected and not ambient
        assert "model=selected-model" in result.stdout.splitlines()
        assert "ambient-model" not in result.stdout
