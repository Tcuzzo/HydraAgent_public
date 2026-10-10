"""Provider/runtime bug-hunt regressions; no external model calls."""
from types import SimpleNamespace

import pytest

from hydra.llm import ChatMessage, LlmError, OllamaClient, _parse_tool_calls
from hydra.model_router import ModelConfig, ModelRouter
from hydra.providers import resolve
from hydra.roles import resolve_roles_from_dict


@pytest.mark.parametrize("endpoint", ["https://example.test", "https://example.test/v1/"])
def test_chat_base_url_contains_one_version_prefix(monkeypatch, endpoint):
    client = OllamaClient(endpoint)
    urls = []
    monkeypatch.setattr(client, "_post_json", lambda url, body, **kw: urls.append(url) or {
        "choices": [{"message": {"content": "ok"}}]})
    client.chat([ChatMessage("user", "hi")], model="any-family")
    assert urls == ["https://example.test/v1/chat/completions"]


def test_local_openai_server_catalog_falls_back_from_ollama(monkeypatch):
    client = OllamaClient("http://127.0.0.1:8080/v1")
    urls = []
    def get(url, **kw):
        urls.append(url)
        if url.endswith("/api/tags"):
            raise LlmError("HTTP 404")
        return {"data": [{"id": "custom-family"}]}
    monkeypatch.setattr(client, "_get_json", get)
    assert client.list_models() == ["custom-family"]
    assert urls[-1] == "http://127.0.0.1:8080/v1/models"


def test_provider_custom_process_environment_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_ENDPOINT", "http://localhost:8080/v1")
    monkeypatch.setenv("CUSTOM_MODEL", "my-family/model")
    cfg = resolve("custom", env_dir=tmp_path)
    assert cfg.model == "my-family/model"
    assert cfg.api_key is None


def test_all_model_families_and_shared_transport_are_allowed():
    roles = {r: {"provider": "proxy", "family": "anthropic", "model": "claude-test"}
             for r in ("planner", "doer", "auditor")}
    assert resolve_roles_from_dict({"agentic": {"roles": roles}}).doer.model == "claude-test"


def test_router_tracks_model_id_separately_from_provider(monkeypatch, tmp_path):
    monkeypatch.setattr("hydra.model_router.make_client", lambda *a, **kw: (
        object(), SimpleNamespace(name="custom", model="provider-default")))
    router = ModelRouter(config_path=tmp_path / "missing.yaml")
    router._create_client(ModelConfig("worker", "custom", "chosen-model"))
    assert router.last_substitution["used"] == "chosen-model"
    assert router.last_substitution["used_provider"] == "custom"


def test_router_default_does_not_overwrite_provider_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("OLLAMA_ENDPOINT", "http://127.0.0.1:19999")
    router = ModelRouter(config_path=tmp_path / "missing.yaml", env_dir=tmp_path)
    assert router._create_client(router.models["worker"]).endpoint == "http://127.0.0.1:19999"


def test_router_explicit_role_endpoint_and_key_are_used(monkeypatch, tmp_path):
    monkeypatch.setenv("MY_MODEL_KEY", "test-key")
    router = ModelRouter(config_path=tmp_path / "missing.yaml", env_dir=tmp_path)
    client = router._create_client(ModelConfig("worker", "custom", "custom-family",
        base_url="http://localhost:18888/v1", api_key_env="MY_MODEL_KEY"))
    assert client.endpoint == "http://localhost:18888/v1"
    assert client.api_key == "test-key"


@pytest.mark.parametrize("name", ["../outside", "foo/bar", "foo\\bar", "a\nb", "", "-invalid"])
def test_provider_name_cannot_escape_env_directory(tmp_path, name):
    from hydra.providers import ProviderError
    with pytest.raises(ProviderError):
        resolve(name, env_dir=tmp_path)


@pytest.mark.parametrize("arguments", ["{bad", "[]", "null", 3])
def test_malformed_tool_arguments_never_become_empty_executable_call(arguments):
    with pytest.raises(LlmError):
        _parse_tool_calls([{"id": "1", "function": {"name": "delete", "arguments": arguments}}])


@pytest.mark.parametrize("payload", [[], {"choices": [None]}, {"choices": [{"message": []}]}])
def test_malformed_chat_response_is_llm_error(monkeypatch, payload):
    client = OllamaClient()
    monkeypatch.setattr(client, "_post_json", lambda *a, **kw: payload)
    with pytest.raises(LlmError):
        client.chat([ChatMessage("user", "hi")], model="test")
