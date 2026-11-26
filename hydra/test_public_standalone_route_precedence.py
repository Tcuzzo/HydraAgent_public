"""Exercise model precedence and explicit-directory isolation at the public CLI."""
from __future__ import annotations

import pytest

from hydra.test_public_standalone_boundary import cli, isolated_environment
from hydra.test_public_standalone_role_models import model_recorder


def _ask(env, workspace, env_dir, provider, *route_args):
    return cli(
        env, workspace, "ask", "Plan this task", "--profile", "local",
        "--provider", provider, "--root", str(workspace), "--env-dir", str(env_dir),
        "--approval-policy", "deny", "--max-iterations", "2", "--timeout", "2",
        *route_args,
    )


def _chat_requests(calls):
    return [(path, body["model"]) for path, body in calls if body is not None]


@pytest.mark.parametrize("configured", ["local-default", "other-provider", "codex-transport"])
def test_provider_setup_does_not_override_explicit_cli_model(tmp_path, configured):
    with model_recorder() as (endpoint, calls):
        env, workspace, providers = isolated_environment(tmp_path, endpoint)
        if configured == "local-default":
            (providers / ".env.ollama").write_text(
                f"OLLAMA_ENDPOINT={endpoint}\nOLLAMA_MODEL=setup-default\n", encoding="utf-8"
            )
        elif configured == "other-provider":
            (providers / ".env.aux").write_text(
                f"AUX_ENDPOINT={endpoint}/unused\nAUX_MODEL=unrelated-model\n", encoding="utf-8"
            )
        else:
            (providers / ".env.codex").touch()

        result = _ask(env, workspace, providers, "local", "--auto-route", "--model", "explicit-cli")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _chat_requests(calls) == [("/v1/chat/completions", "explicit-cli")], _chat_requests(calls)
        assert not any(path.startswith(("/unused", "/forbidden")) for path, _ in calls)


def test_explicit_role_model_completes_endpoint_only_provider(tmp_path):
    with model_recorder() as (endpoint, calls):
        env, workspace, providers = isolated_environment(tmp_path, endpoint)
        (providers / ".env.custom").write_text(f"CUSTOM_ENDPOINT={endpoint}/direct/v1\n", encoding="utf-8")
        role_dir = workspace / ".hydraAgent"
        role_dir.mkdir()
        (role_dir / "hydra.yaml").write_text(
            "agentic:\n  roles:\n    planner:\n      provider: custom\n"
            "      model: explicit-role\n      family: fixture\n", encoding="utf-8"
        )

        result = _ask(env, workspace, providers, "custom", "--auto-route")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _chat_requests(calls) == [("/direct/v1/chat/completions", "explicit-role")], _chat_requests(calls)
        assert not any(path.startswith("/forbidden") for path, _ in calls)


def test_explicit_directory_and_model_do_not_contact_ambient_ollama(tmp_path):
    with model_recorder() as (selected, selected_calls), model_recorder() as (ambient, ambient_calls):
        env, workspace, providers = isolated_environment(tmp_path, selected)
        ambient_dir = tmp_path / "ambient"
        ambient_dir.mkdir()
        (ambient_dir / ".env.ollama").write_text(f"OLLAMA_ENDPOINT={ambient}/ambient\n", encoding="utf-8")
        env["HYDRA_ENV_DIR"] = str(ambient_dir)

        result = _ask(env, workspace, providers, "local", "--model", "explicit-cli")

        assert result.returncode == 0, result.stdout + result.stderr
        assert _chat_requests(selected_calls) == [("/v1/chat/completions", "explicit-cli")]
        assert [path for path, _ in ambient_calls] == []
