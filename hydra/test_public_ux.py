"""Public-operator UX: approvals, user-agent, auth errors, SSRF benchmark range."""
from __future__ import annotations

import socket
import urllib.error
from types import SimpleNamespace

import pytest

from hydra.http_identity import attribution_headers, summarize_error_body, user_agent
from hydra.llm import OllamaClient
from hydra.policy import ApprovalDenied, ApprovalPolicy, set_ui_approval
from hydra.provider_health import http_failure


def test_user_agent_does_not_contain_hydra_and_sends_attribution(monkeypatch):
    monkeypatch.delenv("HYDRA_USER_AGENT", raising=False)
    agent = user_agent()
    assert "hydra" not in agent.lower()
    headers = attribution_headers()
    assert headers["User-Agent"] == agent
    assert headers["HTTP-Referer"].startswith("https://")
    assert headers["X-Title"]
    client = OllamaClient(endpoint="http://127.0.0.1:9")
    sent = client._default_headers()
    assert "hydra" not in sent["User-Agent"].lower()
    assert "HTTP-Referer" in sent and "X-Title" in sent


def test_html_error_pages_are_summarized():
    body = "<!DOCTYPE html><html><body>cloudflare 1010</body></html>"
    summary = summarize_error_body(403, body, host="openrouter.ai")
    assert "403" in summary
    assert "openrouter.ai" in summary
    assert "<html" not in summary.lower()
    err = http_failure(403, body)
    assert "<!doctype" not in str(err).lower()
    assert "403" in str(err)


def test_ui_approval_replaces_builtin_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("input")))
    answers = iter(["n", "y"])
    set_ui_approval(lambda _summary: next(answers))
    try:
        policy = ApprovalPolicy(mode="ask", stdin_is_tty=lambda: True)
        with pytest.raises(ApprovalDenied):
            policy.require("fs_write", {"path": "hello.txt"})
        policy.require("fs_write", {"path": "hello.txt"})
    finally:
        set_ui_approval(None)


def test_auth_failure_does_not_fall_back_to_an_empty_model(tmp_path, monkeypatch, capsys):
    from hydra.__main__ import main
    from hydra.cli import cmd_ask
    from hydra.llm import LlmError

    class _Cloud:
        def chat(self, messages, *, model, **kw):
            raise LlmError("HTTP 400: Please pass a valid API key", kind="request", status_code=400)

    called = {"local": 0}

    def fallback(_args):
        called["local"] += 1
        return object(), SimpleNamespace(name="ollama", model=None)

    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda args: {"provider": "gemini"})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup", lambda args: (_Cloud(), SimpleNamespace(name="gemini", model="from-env")))
    monkeypatch.setattr(cmd_ask, "_local_gpu_fallback_client", fallback)
    monkeypatch.setattr(cmd_ask, "_bind_tools", lambda *a, **k: [])
    rc = main(["ask", "say hi", "--provider", "gemini", "--model", "from-env", "--root", str(tmp_path),
               "--memory-root", str(tmp_path / "mem")])
    err = capsys.readouterr().err
    assert rc != 0
    assert called["local"] == 0
    assert "GEMINI_API_KEY" in err
    assert "Traceback" not in err
    assert "empty" not in err.lower() or "model" in err.lower()


def test_quota_fallback_refuses_an_empty_local_model(tmp_path, monkeypatch, capsys):
    from hydra.__main__ import main
    from hydra.cli import cmd_ask
    from hydra.llm import LlmError

    class _Cloud:
        def chat(self, messages, *, model, **kw):
            raise LlmError("HTTP 429: quota", kind="rate_limit", status_code=429)

    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda args: {"provider": "cloudy"})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup", lambda args: (_Cloud(), SimpleNamespace(name="cloudy", model="cloud-m")))
    monkeypatch.setattr(cmd_ask, "_local_gpu_fallback_client", lambda args: (object(), SimpleNamespace(name="ollama", model="")))
    monkeypatch.setattr(cmd_ask, "_bind_tools", lambda *a, **k: [])
    rc = main(["ask", "say hi", "--provider", "cloudy", "--model", "cloud-m", "--root", str(tmp_path),
               "--memory-root", str(tmp_path / "mem")])
    err = capsys.readouterr().err
    assert rc != 0
    assert "Traceback" not in err
    assert "model" in err.lower()


def test_benchmark_range_is_refused_until_opt_in(tmp_path, monkeypatch):
    from hydra import environment

    def fake_dns(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0))]

    monkeypatch.setattr(environment.socket, "getaddrinfo", fake_dns)
    monkeypatch.delenv("HYDRA_FETCH_ALLOW_FAKEIP", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    with pytest.raises(Exception) as exc:
        environment.fetch_session_url(tmp_path, "sec", "http://example.com/", timeout_seconds=1)
    text = str(exc.value).lower()
    assert "198.18.0.1" in text
    assert "fake" in text or "benchmark" in text


def test_benchmark_range_is_allowed_with_explicit_opt_in(tmp_path, monkeypatch):
    from hydra import environment

    def fake_dns(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.1", 0))]

    monkeypatch.setattr(environment.socket, "getaddrinfo", fake_dns)
    monkeypatch.setenv("HYDRA_FETCH_ALLOW_FAKEIP", "1")
    monkeypatch.setattr(environment, "open_checked", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("timed out")))
    with pytest.raises(Exception) as exc:
        environment.fetch_session_url(tmp_path, "sec", "http://example.com/", timeout_seconds=1)
    text = str(exc.value).lower()
    assert "benchmark" not in text and "fake-ip" not in text
    assert "private" not in text


def test_public_fetch_guard_does_not_depend_on_live_dns(tmp_path, monkeypatch):
    from hydra import environment

    def public_dns(host, *_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(environment.socket, "getaddrinfo", public_dns)
    monkeypatch.setattr(environment, "open_checked", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("timed out")))
    with pytest.raises(Exception) as exc:
        environment.fetch_session_url(tmp_path, "sec", "http://example.com/", timeout_seconds=1)
    text = str(exc.value).lower()
    assert "private" not in text and "benchmark" not in text


def test_connect_status_uses_a_short_connect_budget():
    from hydra.llm import connect_budget, connect_status_line

    assert connect_budget(120) == 15
    assert connect_budget(5) == 5
    line = connect_status_line("openrouter.ai", 15)
    assert "openrouter.ai" in line
    assert "15" in line
