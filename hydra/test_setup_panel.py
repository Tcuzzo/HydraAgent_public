"""Tests for the in-surface setup panel (the non-programmer 'connect a model' flow)."""
from __future__ import annotations

import io

import pytest
from rich.console import Console

from hydra.setup_panel import parse_panel_choice, run_setup_panel


class FakeConsole:
    """Renders through a real Rich Console into a buffer, so assertions see the
    actual text the user reads — including inside Panels."""

    def __init__(self):
        self._buf = io.StringIO()
        self._console = Console(file=self._buf, width=100, color_system=None)

    def print(self, *args, **kwargs):
        self._console.print(*args, **kwargs)

    def text(self) -> str:
        return self._buf.getvalue()


def _scripted(answers, secrets=()):
    a, s = iter(answers), iter(list(secrets))
    return (
        (lambda prompt="": next(a)),
        (lambda prompt="": next(s, "")),
    )


# ── choice parsing ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("1", "local"), ("local", "local"), ("ollama", "local"),
    ("2", "cloud"), ("cloud", "cloud"), ("key", "cloud"),
    ("3", "chatgpt"), ("chatgpt", "chatgpt"), ("gpt", "chatgpt"),
    ("q", "cancel"), ("", "cancel"), ("skip", "cancel"),
    ("banana", None),
])
def test_parse_choice(raw, expected):
    assert parse_panel_choice(raw) == expected


# ── each provider path writes the right config + returns the provider ───────


def test_local_path_configures_ollama(tmp_path):
    con = FakeConsole()
    ask, secret = _scripted(["1"])
    prov = run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path)
    assert prov == "ollama"
    assert (tmp_path / ".env.ollama").exists()


def test_cloud_path_openai_writes_key(tmp_path):
    con = FakeConsole()
    # choose Cloud -> choose OpenAI service -> (key via secret prompt)
    ask, secret = _scripted(["2", "1"], secrets=["sk-test-abc"])
    prov = run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path)
    assert prov == "cloud"
    env = (tmp_path / ".env.cloud").read_text()
    assert "CLOUD_API_KEY=sk-test-abc" in env
    assert "openai.com" in env.lower()


def test_chatgpt_path_when_codex_present(tmp_path):
    con = FakeConsole()
    ask, secret = _scripted(["3", ""])  # choose ChatGPT, press Enter when signed in
    prov = run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path,
                           which=lambda name: "/usr/bin/codex")
    assert prov == "codex"
    assert (tmp_path / ".env.codex").exists()


def test_chatgpt_path_when_codex_missing_guides_and_returns_none(tmp_path):
    con = FakeConsole()
    ask, secret = _scripted(["3", ""])
    prov = run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path,
                           which=lambda name: None)  # codex not installed
    assert prov is None
    assert "codex" in con.text().lower()  # told them to install it


def test_cancel_returns_none(tmp_path):
    con = FakeConsole()
    ask, secret = _scripted(["q"])
    assert run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path) is None


def test_reprompts_on_garbage_then_accepts(tmp_path):
    con = FakeConsole()
    ask, secret = _scripted(["wat", "1"])  # garbage, then local
    assert run_setup_panel(con, ask=ask, secret_ask=secret, env_dir=tmp_path) == "ollama"
