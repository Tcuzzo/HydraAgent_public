from __future__ import annotations

import getpass
import json
import os
import subprocess
from pathlib import Path

import pytest

from hydra import operator_auth
from hydra.operator_auth import (
    AUTH_STATE_SCHEMA,
    SECRET_SCHEMA,
    OperatorAuth,
    OperatorAuthError,
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
    assert_private_to_owner(secret_path)
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


def _icacls_principals(path: Path) -> set[str]:
    """Every principal Windows' own ACL view grants access to ``path``.

    Reads the OS, not the product's claim about the OS.
    """
    proc = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    )
    principals: set[str] = set()
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("Successfully processed"):
            continue
        if line.startswith(str(path)):
            line = line[len(str(path)) :].strip()
        if ":(" not in line:
            continue
        principal = line.split(":(", 1)[0].strip()
        principals.add(principal.split("\\")[-1].lower())
    return principals


def assert_private_to_owner(path: Path) -> None:
    """Assert only the current account can read ``path``.

    POSIX and Windows express the property with different machinery -- mode bits
    vs a DACL -- so assert the *property*, not one platform's spelling of it.
    """
    if os.name == "nt":
        principals = _icacls_principals(path)
        assert principals == {getpass.getuser().lower()}, (
            f"{path} must be readable by the operator's account only, but the "
            f"Windows ACL grants: {sorted(principals)}"
        )
    else:
        assert stat_mode(path) == 0o600


def test_totp_secret_write_restricts_the_acl_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows chmod cannot make a file private -- an ACL restriction must run.

    Windows has no POSIX permission bits, so ``chmod(0o600)`` leaves the secret
    readable by every other local account. The write must use the platform's own
    mechanism instead.
    """
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(operator_auth, "_IS_WINDOWS", True)
    monkeypatch.setattr(operator_auth.subprocess, "run", fake_run)

    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    auth.setup_totp(now=1000.0, force=True)

    secret_path = tmp_path / "totp.json"
    restrictions = [c for c in calls if c and c[0] == "icacls"]
    assert restrictions, "secret write on Windows must restrict the file's ACL"

    on_secret = [c for c in restrictions if str(secret_path) in c]
    assert on_secret, f"no ACL restriction applied to {secret_path}: {restrictions}"
    for cmd in on_secret:
        assert "/inheritance:r" in cmd, (
            "inherited ACEs (which grant BUILTIN\\Users) must be dropped, "
            f"otherwise the secret stays world-readable: {cmd}"
        )
        assert any(part.startswith("/grant:r") for part in cmd), cmd


def test_totp_secret_write_fails_loudly_when_the_acl_cannot_be_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A secret we cannot make private must raise, never be written silently."""

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 5, "", "Access is denied.")

    monkeypatch.setattr(operator_auth, "_IS_WINDOWS", True)
    monkeypatch.setattr(operator_auth.subprocess, "run", fake_run)

    auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
    with pytest.raises(OperatorAuthError, match="refusing to leave"):
        auth.setup_totp(now=1000.0, force=True)


def test_totp_secret_is_never_world_readable_even_under_a_permissive_umask(
    tmp_path: Path,
) -> None:
    """The secret must be private from birth, not private one syscall later."""
    if os.name == "nt":
        pytest.skip("POSIX umask semantics; the Windows ACL path is covered above")
    previous = os.umask(0o000)
    try:
        auth = OperatorAuth(auth_dir=tmp_path, hostname="DUDE")
        auth.setup_totp(now=1000.0, force=True)
    finally:
        os.umask(previous)
    assert stat_mode(tmp_path / "totp.json") == 0o600
