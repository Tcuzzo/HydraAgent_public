"""Operator-is-law: auth commands must work WITHOUT a leading slash.

A bare `yolo 837643` MUST unlock the operator — never fall through to chat /
skill-search (that was a silent gate). Slash forms keep working (pinned by
tests/test_tui_auth_commands.py). This pins the bare forms so the gate can't
silently come back.
"""
from __future__ import annotations

from gateways.tui.auth_commands import AuthCommand, parse_auth_command


def test_bare_yolo_with_code_unlocks():
    assert parse_auth_command("yolo 837643", awaiting_code=False) == AuthCommand(
        kind="unlock", code="837643"
    )


def test_bare_yolo_prompts_for_code():
    assert parse_auth_command("yolo", awaiting_code=False).kind == "prompt_code"
    assert parse_auth_command("mode yolo", awaiting_code=False).kind == "prompt_code"


def test_bare_mode_yolo_with_code_unlocks():
    assert parse_auth_command("mode yolo 654321", awaiting_code=False) == AuthCommand(
        kind="unlock", code="654321"
    )


def test_bare_lock_and_modes():
    assert parse_auth_command("lock", awaiting_code=False).kind == "lock"
    assert parse_auth_command("mode operator", awaiting_code=False).kind == "lock"
    assert parse_auth_command("mode iteration", awaiting_code=False).kind == "iteration"
    assert parse_auth_command("mode", awaiting_code=False).kind == "status"


def test_slash_forms_still_work():
    assert parse_auth_command("/yolo 123456", awaiting_code=False).kind == "unlock"
    assert parse_auth_command("/yolo", awaiting_code=False).kind == "prompt_code"


def test_plain_chat_is_still_not_an_auth_command():
    # The fix must NOT swallow ordinary chat as an auth command.
    assert parse_auth_command("build me a scraper", awaiting_code=False).kind == "none"
    assert parse_auth_command("you look on point today", awaiting_code=False).kind == "none"
