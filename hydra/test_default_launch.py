"""Bare `hydra` (no subcommand) routes to the chat surface on a terminal."""
from __future__ import annotations

from hydra.__main__ import resolve_default_action


def test_subcommand_always_passes_through():
    assert resolve_default_action(has_subcommand=True, is_tty=True) == "subcommand"
    assert resolve_default_action(has_subcommand=True, is_tty=False) == "subcommand"


def test_bare_on_a_terminal_opens_chat():
    assert resolve_default_action(has_subcommand=False, is_tty=True) == "chat"


def test_bare_without_a_terminal_shows_help():
    # No TTY (piped / CI) — can't draw a TUI, so fall back to the command list.
    assert resolve_default_action(has_subcommand=False, is_tty=False) == "help"
