"""Sniper tests for the dead-code / stripped-feature seam closure batch.

Each test pins one finding in the batch and proves the seam is closed on the
REAL path (no mock theater): real module imports, real source text, a real
local HTTP catalog socket for the probe, and a real captured render for the
operator-facing banner. Touched seams only.
"""
from __future__ import annotations

import argparse
import inspect
import socketserver
import threading
import http.server
from pathlib import Path

import pytest


# --- Finding 1: live.py — dead group_relay import + stripped fabric mirror ----


def test_live_module_imports_clean_without_group_relay():
    """The dead ``from gateways.telegram import group_relay`` import is gone,
    so importing the listener no longer depends on a non-existent module."""
    import gateways.telegram.live as live_mod  # noqa: F401

    assert live_mod is not None


def test_forward_group_message_to_fabric_removed():
    """The stripped group-chat -> fabric mirror function and its call site are
    removed cleanly (inv_10) — no orphaned definition left behind."""
    import gateways.telegram.live as live_mod

    assert not hasattr(live_mod, "_forward_group_message_to_fabric")
    # And the call site is gone from the source.
    source = inspect.getsource(live_mod)
    assert "_forward_group_message_to_fabric" not in source
    assert "group_relay" not in source


# --- Finding 2: cmd_execution.py — orphaned _make_patch_doer ------------------


def test_make_patch_doer_removed_and_module_imports_clean():
    """_make_patch_doer referenced SurgeryError (never imported, hydra.surgery
    stripped) and had no callers. It is removed; the module still imports."""
    from hydra.cli import cmd_execution

    assert not hasattr(cmd_execution, "_make_patch_doer")
    source = inspect.getsource(cmd_execution)
    assert "_make_patch_doer" not in source
    assert "SurgeryError" not in source


# --- Finding 3: cmd_chat.py — unreachable legacy REPL + orphaned helpers ------


def test_cmd_chat_legacy_repl_block_and_orphans_removed():
    """The unreachable legacy REPL block (after ``return 0``, marked
    ``# noqa: unreachable``) is gone, along with the orphaned helpers and
    name that only it referenced (chat_turn_index, _build_chat_trace_turn,
    _ensure_chat_mission_context, _write_current_mission_marker)."""
    from hydra.cli import cmd_chat

    assert not hasattr(cmd_chat, "_build_chat_trace_turn")
    assert not hasattr(cmd_chat, "_ensure_chat_mission_context")
    assert not hasattr(cmd_chat, "_write_current_mission_marker")
    source = inspect.getsource(cmd_chat)
    assert "chat_turn_index" not in source
    assert "_build_chat_trace_turn" not in source
    assert "_ensure_chat_mission_context" not in source
    assert "_write_current_mission_marker" not in source
    assert "# noqa: unreachable" not in source
    # The now-unused imports are cleaned too.
    assert "route_chat_intent" not in source
    assert "create_mission" not in source


# --- Finding 4: cmd_code.py — literal backslash-n in operator-facing banners ---


def test_cmd_code_banners_use_real_newlines(tmp_path, capsys):
    """Operator-facing print strings must use real ``\\n`` newlines, not the
    literal two-character ``\\n`` text. Drives the real ``cmd_code`` path with
    an unsupported language (prints the 'No executor' banner) and captures the
    rendered output."""
    from hydra.cli.cmd_code import cmd_code

    f = tmp_path / "sample.txt"
    f.write_text("plain content with no backslashes\n", encoding="utf-8")
    args = argparse.Namespace(file=f, lang=None, highlight=False)
    rc = cmd_code(args)
    assert rc == 0
    out = capsys.readouterr().out
    # A real newline must separate the banner from the body...
    assert "\n" in out
    # ...and the literal two-character backslash-n must NOT appear (the bug
    # rendered "\\n" as visible text instead of a line break).
    assert "\\n" not in out


def test_cmd_code_source_has_no_literal_backslash_n_in_fstrings():
    """Structural guard: no f-string in cmd_code.py carries the literal
    ``\\\\n`` (two backslashes + n) that encodes the bug class. Catches the
    next offender anywhere in the module, not just the four known lines."""
    from hydra.cli import cmd_code

    source = inspect.getsource(cmd_code)
    bad_marker = "\\\\n"  # two literal backslashes + n (3 chars)
    for line in source.splitlines():
        if 'f"' in line and bad_marker in line:
            pytest.fail(f"literal backslash-n in f-string: {line.strip()}")


# --- Finding 5: emergency_fallback.py — probe_model probes the actual provider -


class _CatalogHandler(http.server.BaseHTTPRequestHandler):
    """A real local OpenAI-compatible catalog endpoint. Counts requests so the
    test can prove probe_model actually reached the network for a non-ollama
    provider (old code fell through to ``return False`` without probing)."""

    requests = 0

    def do_GET(self):  # noqa: N802
        type(self).requests += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"data":[]}')

    def log_message(self, *args):  # noqa: N802, ARG002
        pass


def _start_catalog_server():
    srv = socketserver.TCPServer(("127.0.0.1", 0), _CatalogHandler)
    _CatalogHandler.requests = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_probe_model_probes_non_ollama_provider(monkeypatch):
    """probe_model must probe the ACTUAL configured provider, not just ollama.
    Stands up a real local HTTP catalog socket, points a builtin non-ollama
    provider (minimax) at it via env, and asserts the probe reaches the
    endpoint and returns True. Old code returned False for any non-ollama
    provider without ever issuing a request."""
    from hydra.emergency_fallback import probe_model

    srv = _start_catalog_server()
    port = srv.server_address[1]
    try:
        monkeypatch.setenv("MINIMAX_ENDPOINT", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        # minimax is a builtin cloud (non-ollama) provider. The fix must
        # resolve it and hit its catalog endpoint.
        assert probe_model("minimax", "MiniMax-Text-01", timeout=5.0) is True
        # Prove the probe really hit the network (not a silent fallthrough).
        assert _CatalogHandler.requests >= 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_probe_model_unconfigured_provider_returns_false_without_raising():
    """A provider that cannot be resolved (no key / unknown) returns False
    loudly-via-false — never raises, never blanket-swallows a programming
    error. This is a non-ollama provider, so old code also returned False but
    for the wrong reason (fell through the ``if provider == 'ollama'``); the
    fix returns False because resolution failed, which is the honest reason."""
    from hydra.emergency_fallback import probe_model

    # "no-such-provider" is not a builtin and has no env file -> ProviderError.
    assert probe_model("no-such-provider", "x", timeout=2.0) is False