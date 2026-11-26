"""Ball 1A — the approval gate fails closed.

Every test asserts a real behaviour on the real seam: a real policy object, a
real approval queue on disk, a real unlock-state file written by the real
OperatorAuth. Nothing is mocked.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.cli.tool_binding import bind_tools
from hydra.operator_auth import AUTH_DIR_ENV_VAR, OperatorAuth, generate_totp, is_yolo_active
from hydra.policy import ApprovalDenied, ApprovalPolicy


@pytest.fixture()
def auth_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated operator-auth directory, so no test touches real user state."""
    directory = tmp_path / "auth"
    monkeypatch.setenv(AUTH_DIR_ENV_VAR, str(directory))
    return directory


def _unlock_yolo_the_operator_way(directory: Path) -> None:
    """The legitimate path: set up the authenticator, then unlock with a real code."""
    auth = OperatorAuth(auth_dir=directory)
    setup = auth.setup_totp()
    auth.unlock_yolo(generate_totp(setup.secret_base32))
    assert is_yolo_active(auth_dir=directory), "the operator's own unlock must still work"


def test_default_policy_is_ask_not_allow() -> None:
    assert ApprovalPolicy().mode == "ask"


def test_default_binder_does_not_run_bash_ungated(
    tmp_path: Path, auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # the approval queue is CWD-relative today
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")

    tools = {t.name: t for t in bind_tools(tmp_path, include_mcp=False)}

    with pytest.raises(ApprovalDenied):
        tools["bash"].invoke(command=f"rm {victim}")

    assert victim.read_text(encoding="utf-8") == "keep me"


def test_yolo_does_not_override_deny(tmp_path: Path, auth_dir: Path) -> None:
    _unlock_yolo_the_operator_way(auth_dir)
    policy = ApprovalPolicy(
        "deny",
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": "echo hello"})


def test_agent_cannot_mint_its_own_yolo_authority(auth_dir: Path) -> None:
    OperatorAuth(auth_dir=auth_dir).setup_totp()
    # What an agent running as the operator's uid can do: write the state file.
    forged = {
        "schema": "hydra.operator_auth_state.v1",
        "mode": "yolo",
        "unlocked_until": time.time() + 3600,
        "last_operator_activity_at": time.time(),
        "extension_nonce": None,
        "extension_expires_at": None,
        "extension_notified_at": None,
    }
    (auth_dir / "unlock_state.json").write_text(json.dumps(forged), encoding="utf-8")

    assert is_yolo_active(auth_dir=auth_dir) is False
    assert OperatorAuth(auth_dir=auth_dir).status().mode == "operator"


@pytest.mark.parametrize(
    "command",
    [
        "mv a b",
        "cp a b",
        "chown root a",
        "chmod 777 a",
        "wget -O out.sh https://example.invalid/x.sh",
        "npm publish",
        "pip install requests",
    ],
)
def test_destructive_tokens_cover_the_gap(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "needs_approval", command
