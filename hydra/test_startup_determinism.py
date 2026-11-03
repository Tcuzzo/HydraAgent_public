"""Startup stays deterministic: no silent profile, no network for --runtime-only,
each rung resolved once, and the decision timeout tolerates a cold service."""
from __future__ import annotations

import socket
from argparse import Namespace

import pytest

from hydra.__main__ import _build_parser
from hydra.cli.cmd_chat import _resolve_chat_runtime


def test_work_executor_audit_checks_role_or_rung_not_model_name(monkeypatch):
    """A cloud work rung passes even when the live route string names no model family."""
    from hydra import self_audit

    monkeypatch.setattr(
        self_audit,
        "_resolve_work_model",
        lambda kind: ("gateway", "route-without-a-family"),
    )
    check = self_audit._check_work_executor_cloud_qwen()
    assert check.passed, check.detail
    assert "qwen" not in check.detail.lower()
    assert "qwen" not in check.description.lower()


def test_work_executor_audit_rejects_a_local_executor_rung(monkeypatch):
    """The check reads the executor's rung, so a local executor fails even if a
    resolved name would have looked like a cloud model."""
    from dataclasses import replace

    from hydra import self_audit
    from hydra.turn_profiles import load_turn_profile

    original = load_turn_profile

    def local_executor(kind: str):
        profile = original(kind)
        if kind in {"steering", "collab"}:
            return replace(profile, executor="local-worker")
        return profile

    monkeypatch.setattr("hydra.turn_profiles.load_turn_profile", local_executor)
    # A provider string the old name check accepts. The rung check must still fail.
    monkeypatch.setattr(
        self_audit,
        "_resolve_work_model",
        lambda kind: ("cloud-transport", "route-without-a-family"),
    )
    check = self_audit._check_work_executor_cloud_qwen()
    assert not check.passed
    assert "local" in check.detail.lower()


def test_missing_profile_is_not_silently_auto(tmp_path):
    with pytest.raises(ValueError, match="profile"):
        _resolve_chat_runtime(Namespace(provider=None, model=None, env_dir=tmp_path))


def test_unknown_profile_is_rejected(tmp_path):
    from hydra.model_routing import load_routing

    with pytest.raises(ValueError, match="mystery"):
        load_routing().chat_default("mystery")
    with pytest.raises(ValueError, match="mystery"):
        _resolve_chat_runtime(
            Namespace(profile="mystery", provider=None, model=None, env_dir=tmp_path)
        )


def test_each_rung_is_resolved_once_per_process():
    from hydra import model_routing

    calls: list[str] = []

    def resolver(rung: str, **_kwargs: object) -> tuple[str, str]:
        calls.append(rung)
        return ("gateway", f"rung-{rung}")

    model_routing.set_resolver(resolver)
    model_routing.resolve_rung("plan", local=False, task="plan")
    model_routing.resolve_rung("plan", local=False, task="plan")
    assert calls == ["plan"]


def test_decision_timeout_defaults_to_a_cold_start_and_is_configurable(monkeypatch):
    from hydra import rung_resolver

    seen: list[float] = []

    def post(url, body, timeout):
        seen.append(timeout)
        return 200, {"chain": [{"rung": "rung-a", "usable": True, "model": "model-of-a"}]}

    monkeypatch.setenv("HYDRA_BACKS_URL", "http://backs.test:8000")
    monkeypatch.delenv("HYDRA_DECISION_TIMEOUT", raising=False)
    rung_resolver._DOWN.clear()
    rung_resolver.resolve("plan", task="plan", post=post)
    assert seen[-1] >= 30

    monkeypatch.setenv("HYDRA_DECISION_TIMEOUT", "45")
    rung_resolver.resolve("plan", task="plan", post=post)
    assert seen[-1] == 45

    rung_resolver.resolve("plan", task="plan", post=post, timeout=7)
    assert seen[-1] == 7


def test_runtime_only_makes_no_network_calls(monkeypatch, tmp_path, capsys):
    """--runtime-only prints the configured rung and must not open a socket."""
    from hydra import model_routing
    from hydra.cli import cmd_ask

    model_routing.set_resolver(None)
    model_routing.clear_cache()
    opened: list[tuple] = []
    real_socket = socket.socket

    def blocked(*args, **kwargs):
        opened.append((args, kwargs))
        raise AssertionError("runtime-only opened a socket")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    args = _build_parser().parse_args([
        "ask", "status",
        "--runtime-only",
        "--profile", "auto",
        "--root", str(tmp_path),
        "--env-dir", str(tmp_path),
    ])
    try:
        rc = cmd_ask.cmd_ask(args)
    finally:
        monkeypatch.setattr(socket, "socket", real_socket)
    assert rc == 0, capsys.readouterr()
    assert opened == []
    out = capsys.readouterr().out
    assert "Hydra ask profile: auto" in out
    assert "provider=" in out
    assert "model=" in out
