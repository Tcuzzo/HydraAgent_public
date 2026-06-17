from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from hydra.llm import LlmError
from hydra.model_router import ModelRouter, TaskComplexity, route_and_execute
from hydra.providers import resolve


def test_ollama_cloud_alias_reads_setup_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env.ollama_cloud").write_text(
        "\n".join(
            [
                "OLLAMA_CLOUD_ENDPOINT=https://ollama.example",
                "OLLAMA_CLOUD_MODEL=deepseek-test",
                "OLLAMA_CLOUD_API_KEY=secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    cfg = resolve("ollama-cloud", env_dir=tmp_path)

    assert cfg.name == "ollama-cloud"
    assert cfg.endpoint == "https://ollama.example"
    assert cfg.model == "deepseek-test"
    assert cfg.api_key == "secret"


def test_ollama_base_url_process_env_alias(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen-test")

    cfg = resolve("ollama", env_dir=tmp_path)

    assert cfg.endpoint == "http://127.0.0.1:9999"
    assert cfg.model == "qwen-test"


def test_model_router_uses_provider_factory_and_falls_back(monkeypatch) -> None:
    seen = []

    class FailingClient:
        def chat(self, *args, **kwargs):
            raise LlmError("boom")

    def fake_make_client(provider):
        seen.append(provider)
        return FailingClient(), SimpleNamespace(model="unused")

    monkeypatch.setattr("hydra.model_router.make_client", fake_make_client)

    router = ModelRouter(config_path=Path("/no/such/config.yaml"))
    decision = router.classify_task("refactor the runtime provider stack")

    assert seen == ["ollama"]
    assert decision.complexity is TaskComplexity.COMPLEX
    assert decision.recommended_model == "planner"
    assert "Heuristic fallback" in decision.reasoning


def test_route_and_execute_passes_actual_model_not_role(monkeypatch) -> None:
    class FakeRouter:
        models = {
            "doer": SimpleNamespace(model="qwen3-coder:480b", provider="ollama-cloud", latency_target_ms=0),
            "auditor": SimpleNamespace(model="llama-3.3-70b-versatile", provider="ollama-cloud"),
        }

        def get_client_for_task(self, task):
            return object(), SimpleNamespace(
                recommended_model="doer",
                complexity=TaskComplexity.MODERATE,
                reasoning="test",
                estimated_cost_usd=0.0,
                estimated_latency_ms=0,
                requires_verifier=False,
                requires_human_approval=False,
            )

        def create_verification_stack(self, generator_model):
            return "auditor"

    calls = []

    def factory(client, model):
        calls.append(model)
        return object()

    result = route_and_execute("build it", factory, tools=[], router=FakeRouter())

    assert calls == ["qwen3-coder:480b"]
    assert result["routing_decision"]["role"] == "doer"
    assert result["routing_decision"]["model"] == "qwen3-coder:480b"
