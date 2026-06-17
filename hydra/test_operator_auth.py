from __future__ import annotations

import json
from pathlib import Path

from hydra.operator_auth import (
    AUTH_STATE_SCHEMA,
    SECRET_SCHEMA,
    OperatorAuth,
    generate_totp,
    provisioning_uri,
    verify_totp,
)


def test_totp_secret_setup_writes_secret_outside_repo_with_private_mode(tmp_path: Path) -> None:
    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")

    setup = auth.setup_totp(now=1000.0, force=True)

    secret_path = tmp_path / "totp.json"
    state_path = tmp_path / "unlock_state.json"
    assert secret_path.exists()
    assert stat_mode(secret_path) == 0o600
    raw = json.loads(secret_path.read_text(encoding="utf-8"))
    assert raw["schema"] == SECRET_SCHEMA
    assert raw["issuer"] == "HydraAgent"
    assert raw["account"] == "operator@DUDE"
    assert raw["secret_base32"] == setup.secret_base32
    assert setup.provisioning_uri.startswith("otpauth://totp/HydraAgent%3Aoperator%40DUDE?")
    assert "secret=" in setup.provisioning_uri
    assert not state_path.exists()


def test_totp_generation_verifies_current_and_adjacent_window() -> None:
    secret = "JBSWY3DPEHPK3PXP"
    now = 1_700_000_000.0
    code = generate_totp(secret, for_time=now)

    assert verify_totp(secret, code, at_time=now)
    assert verify_totp(secret, code, at_time=now + 29)
    assert not verify_totp(secret, code, at_time=now + 90)


def test_yolo_unlock_is_single_scope_for_local_and_network_and_idle_expires(tmp_path: Path) -> None:
    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    setup = auth.setup_totp(now=1000.0, force=True)
    code = generate_totp(setup.secret_base32, for_time=1010.0)

    status = auth.unlock_yolo(code, now=1010.0)

    assert status.mode == "yolo"
    assert status.yolo_active is True
    assert status.local_authority is True
    assert status.network_authority is True
    assert status.unlocked_until == 4610.0
    state = json.loads((tmp_path / "unlock_state.json").read_text(encoding="utf-8"))
    assert state["schema"] == AUTH_STATE_SCHEMA
    assert "secret" not in json.dumps(state).lower()

    assert auth.status(now=4609.0).yolo_active is True
    assert auth.status(now=4611.0).yolo_active is False
    assert auth.status(now=4611.0).mode == "operator"


def test_operator_activity_extends_yolo_idle_window(tmp_path: Path) -> None:
    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    setup = auth.setup_totp(now=1000.0, force=True)
    auth.unlock_yolo(generate_totp(setup.secret_base32, for_time=1005.0), now=1005.0)

    refreshed = auth.record_operator_activity(now=1300.0)

    assert refreshed.yolo_active is True
    assert refreshed.unlocked_until == 4900.0


def test_extension_nonce_can_extend_yolo_without_second_totp(tmp_path: Path) -> None:
    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    setup = auth.setup_totp(now=1000.0, force=True)
    auth.unlock_yolo(generate_totp(setup.secret_base32, for_time=1005.0), now=1005.0)
    poll = auth.prepare_extension_poll(now=4450.0)

    extended = auth.extend_yolo(poll.nonce, now=4460.0)

    assert poll.should_send is True
    assert extended.yolo_active is True
    assert extended.unlocked_until == 8060.0


def test_provisioning_uri_is_google_authenticator_compatible() -> None:
    uri = provisioning_uri("JBSWY3DPEHPK3PXP", issuer="HydraAgent", account="operator@DUDE")

    assert uri.startswith("otpauth://totp/HydraAgent%3Aoperator%40DUDE?")
    assert "issuer=HydraAgent" in uri
    assert "digits=6" in uri
    assert "period=30" in uri


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777
