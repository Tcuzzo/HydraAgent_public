"""Ball 1A, fix round 1 — the agent cannot reach the yolo key through its tools.

The unlock state is signed with the authenticator secret. A same-uid agent that
can READ that secret can forge the signature. The agent acts only through its
tools, so every tool call that names anything under the operator auth directory
is refused outright (not queued). Real policy, real tool binder, real files.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.cli.tool_binding import bind_tools
from hydra.operator_auth import AUTH_DIR_ENV_VAR, OperatorAuth, _state_signature, is_yolo_active
from hydra.policy import ApprovalDenied


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A workspace root holding the operator auth dir, a set-up authenticator and
    the real tool layer, in the default (ask) policy."""
    auth_dir = tmp_path / "auth"
    monkeypatch.setenv(AUTH_DIR_ENV_VAR, str(auth_dir))
    monkeypatch.chdir(tmp_path)  # the approval queue is CWD-relative
    secret = OperatorAuth(auth_dir=auth_dir).setup_totp().secret_base32
    (tmp_path / "link").symlink_to(auth_dir, target_is_directory=True)
    tools = {t.name: t for t in bind_tools(tmp_path, include_mcp=False)}
    return tmp_path, auth_dir, secret, tools


def _assert_refused_not_queued(call, root: Path) -> None:
    with pytest.raises(ApprovalDenied) as raised:
        call()
    assert "operator auth" in str(raised.value), str(raised.value)
    assert not (root / "evidence").exists(), "a refusal must not queue an approval"


@pytest.mark.parametrize(
    "command",
    [
        "cat {auth}/totp.json",
        "cat $" + AUTH_DIR_ENV_VAR + "/totp.json",
        "cat link/totp.json",
        "cat auth/tot*",
        "cd auth && cat totp.json",
        "ls {auth}",
        "cp {auth}/totp.json stolen.json",
        "python3 -c \"print(open('{auth}/totp.json').read())\"",
    ],
)
def test_bash_cannot_touch_the_auth_dir(world, command: str) -> None:
    root, auth_dir, _secret, tools = world
    _assert_refused_not_queued(lambda: tools["bash"].invoke(command=command.format(auth=auth_dir)), root)


def test_fs_tools_cannot_touch_the_auth_dir(world) -> None:
    root, auth_dir, _secret, tools = world
    _assert_refused_not_queued(lambda: tools["fs_read"].invoke(path=str(auth_dir / "totp.json")), root)
    _assert_refused_not_queued(lambda: tools["fs_read"].invoke(path="link/totp.json"), root)
    _assert_refused_not_queued(
        lambda: tools["fs_write"].invoke(path=str(auth_dir / "unlock_state.json"), content="{}"), root
    )
    _assert_refused_not_queued(
        lambda: tools["fs_edit"].invoke(path=str(auth_dir / "totp.json"), old_string="a", new_string="b"), root
    )


def test_the_graders_forge_through_the_tools_grants_nothing(world) -> None:
    root, auth_dir, secret, tools = world
    # Even if the secret leaked, the agent has no tool path to plant a forged state.
    state = {
        "schema": "hydra.operator_auth_state.v1",
        "mode": "yolo",
        "unlocked_until": time.time() + 3600,
        "last_operator_activity_at": time.time(),
        "extension_nonce": None,
        "extension_expires_at": None,
        "extension_notified_at": None,
    }
    state["sig"] = _state_signature(secret, state)
    _assert_refused_not_queued(
        lambda: tools["fs_write"].invoke(path=str(auth_dir / "unlock_state.json"), content=json.dumps(state)),
        root,
    )
    _assert_refused_not_queued(lambda: tools["bash"].invoke(command=f"cat {auth_dir}/totp.json"), root)
    assert is_yolo_active(auth_dir=auth_dir) is False
    assert not (auth_dir / "unlock_state.json").exists()


def test_ordinary_tool_calls_are_not_refused(world) -> None:
    root, _auth_dir, _secret, tools = world
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    assert "hello" in str(tools["fs_read"].invoke(path="notes.txt"))


@pytest.mark.parametrize(
    "command",
    [
        "wget -qO out.sh https://example.invalid/x.sh",
        "curl -sSLo out.sh https://example.invalid/x.sh",
        "xargs cp a b",
        "ls | xargs mv",
        "echo hi && cp a b",
    ],
)
def test_download_and_command_position_shapes_need_approval(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "needs_approval", command


@pytest.mark.parametrize(
    "command",
    ["grep cp notes.txt", "grep -n mv notes.txt", "echo chmod", "cat chown.txt"],
)
def test_mv_cp_chmod_chown_match_only_in_command_position(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "auto_allow", command


def test_dropping_an_unsigned_unlock_state_says_why(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    auth_dir = tmp_path / "auth"
    OperatorAuth(auth_dir=auth_dir).setup_totp()
    legacy = {"schema": "hydra.operator_auth_state.v1", "mode": "yolo", "unlocked_until": time.time() + 3600}
    (auth_dir / "unlock_state.json").write_text(json.dumps(legacy), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        assert is_yolo_active(auth_dir=auth_dir) is False
    lines = [r.getMessage() for r in caplog.records if "unlock_state.json" in r.getMessage()]
    assert len(lines) == 1, lines
    assert os.fspath(auth_dir) in lines[0] or "unsigned" in lines[0]
