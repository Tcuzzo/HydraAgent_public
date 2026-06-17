from __future__ import annotations

from pathlib import Path

from gateways.telegram.live import (
    auth_extension_reply_markup,
    format_auth_extension_message,
    handle_auth_extension_callback,
    parse_auth_extension_callback,
)
from hydra.operator_auth import OperatorAuth, generate_totp


def test_auth_extension_callback_parser_is_separate_from_approval_callbacks() -> None:
    assert parse_auth_extension_callback("auth_extend:abc123") == "abc123"


def test_auth_extension_markup_uses_separate_namespace() -> None:
    markup = auth_extension_reply_markup("abc123")

    assert markup["inline_keyboard"][0][0]["callback_data"] == "auth_extend:abc123"
    assert "approve:" not in markup["inline_keyboard"][0][0]["callback_data"]


def test_auth_extension_callback_extends_yolo_window(tmp_path: Path) -> None:
    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    setup = auth.setup_totp(now=1000.0, force=True)
    auth.unlock_yolo(generate_totp(setup.secret_base32, for_time=1005.0), now=1005.0)
    poll = auth.prepare_extension_poll(now=4450.0)

    report = handle_auth_extension_callback(
        callback_data=f"auth_extend:{poll.nonce}",
        auth=auth,
        now=4460.0,
    )

    assert report["schema"] == "hydra.telegram.auth_extension_callback.v1"
    assert report["ok"] is True
    assert report["decision"] == "extended"
    assert report["status"]["mode"] == "yolo"
    assert report["status"]["network_authority"] is True


def test_auth_extension_message_names_yolo_timeout() -> None:
    text = format_auth_extension_message(minutes_remaining=5)

    assert "yolo" in text.lower()
    assert "5 minute" in text
