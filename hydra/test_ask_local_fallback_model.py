"""When the configured cloud provider fails, `hydra ask` falls back to the
local GPU provider. Live finding: with an explicit --model for the cloud
provider, the fallback asked the LOCAL server for that cloud model name and
died with "model not found", so a cloud quota stop still meant no answer.
The fallback must use the local provider's own model and say why it moved."""
from __future__ import annotations

from types import SimpleNamespace

from hydra.__main__ import main
from hydra.cli import cmd_ask
from hydra.llm import ChatResponse, LlmError


class _Cloud:
    def chat(self, messages, *, model, **kw):
        raise LlmError("HTTP 429 from https://cloud.test/v1/chat/completions: monthly usage limit reached")


class _Local:
    def __init__(self):
        self.models: list[str] = []

    def chat(self, messages, *, model, **kw):
        self.models.append(model)
        return ChatResponse(content="local answer", model=model, finish_reason="stop",
                            prompt_tokens=1, completion_tokens=1, raw={})


def test_cloud_quota_stop_with_explicit_model_falls_back_to_local_model(tmp_path, monkeypatch, capsys):
    local = _Local()
    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda args: {"provider": "cloudy"})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup",
                        lambda args: (_Cloud(), SimpleNamespace(name="cloudy", model="cloud-m")))
    monkeypatch.setattr(cmd_ask, "_local_gpu_fallback_client",
                        lambda args: (local, SimpleNamespace(name="localgpu", model="local-m")))
    monkeypatch.setattr(cmd_ask, "_bind_tools", lambda *a, **k: [])
    rc = main(["ask", "say hi", "--provider", "cloudy", "--model", "cloud-m", "--root", str(tmp_path),
               "--memory-root", str(tmp_path / "mem")])
    out = capsys.readouterr()
    assert local.models and set(local.models) == {"local-m"}, local.models
    assert rc == 0 and "local answer" in out.out
    assert "localgpu/local-m" in out.err and "429" in out.err
