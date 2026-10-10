from argparse import Namespace

from hydra.cli.cmd_chat import _resolve_chat_runtime


def test_explicit_provider_uses_its_configured_model(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_ENDPOINT", "http://localhost:8080/v1")
    monkeypatch.setenv("CUSTOM_MODEL", "custom-family")
    result = _resolve_chat_runtime(Namespace(profile="auto", provider="custom", model=None, env_dir=tmp_path))
    assert result["provider"] == "custom"
    assert result["model"] == "custom-family"


def test_auto_uses_local_setup_when_cloud_is_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    (tmp_path / ".env.ollama").write_text("OLLAMA_MODEL=local-selected\n", encoding="utf-8")
    result = _resolve_chat_runtime(Namespace(profile="auto", provider=None, model=None, env_dir=tmp_path))
    assert result["provider"] == "ollama"
    assert result["model"] == "local-selected"


def test_auto_uses_custom_setup_when_cloud_is_unconfigured(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    (tmp_path / ".env.custom").write_text("CUSTOM_ENDPOINT=http://localhost:8080/v1\nCUSTOM_MODEL=custom-selected\n", encoding="utf-8")
    result = _resolve_chat_runtime(Namespace(profile="auto", provider=None, model=None, env_dir=tmp_path))
    assert result["provider"] == "custom"
    assert result["model"] == "custom-selected"


def test_unconfigured_auto_retains_setup_trigger_instead_of_assuming_ollama(monkeypatch, tmp_path):
    for name in ("HYDRA_PROVIDER", "OLLAMA_CLOUD_API_KEY", "OLLAMA_MODEL", "OLLAMA_ENDPOINT", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    result = _resolve_chat_runtime(Namespace(profile="auto", provider=None, model=None, env_dir=tmp_path))
    assert result["provider"] == "ollama-cloud"


def test_auto_accepts_explicit_process_ollama_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "process-selected")
    result = _resolve_chat_runtime(Namespace(profile="auto", provider=None, model=None, env_dir=tmp_path))
    assert result["provider"] == "ollama"
    assert result["model"] == "process-selected"


def test_explicit_cloud_profile_does_not_switch_to_local(monkeypatch, tmp_path):
    monkeypatch.delenv("HYDRA_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    (tmp_path / ".env.ollama").write_text("OLLAMA_MODEL=local-selected\n", encoding="utf-8")
    result = _resolve_chat_runtime(Namespace(profile="cloud", provider=None, model=None, env_dir=tmp_path))
    assert result["provider"] == "ollama-cloud"


def test_missing_optional_model_sdk_is_a_clean_chat_startup_error(monkeypatch, tmp_path, capsys):
    from hydra.cli import cmd_chat
    from hydra.llm import LlmError

    monkeypatch.setattr(cmd_chat, "_bind_tools", lambda *a, **kw: [])
    monkeypatch.setattr(cmd_chat, "session_exists", lambda *a: False)
    monkeypatch.setattr(cmd_chat, "create_session", lambda *a: None)
    def missing(args):
        raise LlmError("install hydraagent[needle]")
    monkeypatch.setattr(cmd_chat, "_make_client_or_setup", missing)
    args = Namespace(root=str(tmp_path), approval_policy="deny", memory_root=str(tmp_path / "memory"),
        no_local_memory=True, with_context=False, truth_context=False, profile="local", provider="needle",
        model="needle3", env_dir=tmp_path, setup_if_needed=False, session_history_limit=40,
        trace_out=None, max_iterations=1, timeout=1.0, context_budget_bytes=4096)
    assert cmd_chat.cmd_chat(args) == 2
    assert "install hydraagent[needle]" in capsys.readouterr().err
