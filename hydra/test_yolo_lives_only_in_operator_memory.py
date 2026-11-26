"""Ball 1A, fix round 2 — yolo authority lives only in the operator process memory.

No file on disk, signed or not, is ever yolo authority. The grader's chain (read
the secret with an ordinary tool, sign a state, write it through a tool) must
grant nothing; a real operator unlock inside the process must still work; and a
fresh process must not inherit yolo from a file. Real tools, real files, real
subprocess. Nothing is mocked.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hydra.cli.tool_binding import bind_tools
from hydra.mcp_bridge import bind_mcp_tools
from hydra.operator_auth import (
    AUTH_DIR_ENV_VAR,
    OperatorAuth,
    _state_signature,
    generate_totp,
    is_yolo_active,
)
from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.worker_sessions import run_worker_job_session


@pytest.fixture()
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Workspace root that CONTAINS the operator auth dir (as ~/.hydraAgent/workspace does)."""
    auth_dir = tmp_path / "auth"
    monkeypatch.setenv(AUTH_DIR_ENV_VAR, str(auth_dir))
    monkeypatch.chdir(tmp_path)
    OperatorAuth(auth_dir=auth_dir).setup_totp()
    return tmp_path, auth_dir


def _signed_yolo_state(secret: str) -> dict:
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
    return state


def test_the_graders_chain_grants_no_yolo(world) -> None:
    root, auth_dir = world
    tools = {t.name: t for t in bind_tools(root, approval_policy="allow", include_mcp=False)}

    # 1. An ordinary read tool finds the secret without naming the auth dir.
    found = tools["grep"].invoke(pattern="secret_base32", path=".")
    secret = OperatorAuth(auth_dir=auth_dir).load_secret()
    assert secret in json.dumps(found), "the ordinary grep tool must be what exposes the secret"

    # 2. Sign a yolo state with hydra's own algorithm and plant it with an
    #    obfuscated path so no literal name appears in the command.
    state = json.dumps(_signed_yolo_state(secret))
    script = f"open('au'+'th/unlock'+'_state.json','w').write({state!r})"
    # Name the interpreter the way this platform names it: python3 on POSIX,
    # python on Windows, where python3 is not on PATH.
    python = "python" if sys.platform.startswith("win") else "python3"
    tools["bash"].invoke(command=f"{python} -c {json.dumps(script)}")

    # The file exists, but a file is never authority.
    assert (auth_dir / "unlock_state.json").exists()
    assert is_yolo_active(auth_dir=auth_dir) is False
    assert OperatorAuth(auth_dir=auth_dir).status().mode == "operator"
    policy = ApprovalPolicy("ask", stdin_is_tty=lambda: False, approval_path=root / "q.jsonl", run_path=root / "r.jsonl")
    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": "rm -rf somewhere"})


def test_a_real_operator_unlock_in_process_grants_yolo(world) -> None:
    _root, auth_dir = world
    auth = OperatorAuth(auth_dir=auth_dir)
    auth.unlock_yolo(generate_totp(auth.load_secret()))
    assert is_yolo_active(auth_dir=auth_dir) is True
    auth.lock()
    assert is_yolo_active(auth_dir=auth_dir) is False


def test_a_fresh_process_does_not_inherit_yolo_from_a_file(world) -> None:
    _root, auth_dir = world
    auth = OperatorAuth(auth_dir=auth_dir)
    auth.unlock_yolo(generate_totp(auth.load_secret()))  # this process holds yolo
    (auth_dir / "unlock_state.json").write_text(json.dumps(_signed_yolo_state(auth.load_secret())), encoding="utf-8")

    probe = "from hydra.operator_auth import is_yolo_active; print(is_yolo_active())"
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=60,
        env={**os.environ, AUTH_DIR_ENV_VAR: str(auth_dir)},
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_the_guard_refuses_real_paths_not_words(world) -> None:
    root, auth_dir = world
    (root / "docs").mkdir()
    (root / "docs" / "totp.json").write_text("{}", encoding="utf-8")
    tools = {t.name: t for t in bind_tools(root, approval_policy="allow", include_mcp=False)}

    assert "see totp.json" in str(tools["bash"].invoke(command="echo 'see totp.json'"))
    assert tools["fs_read"].invoke(path="docs/totp.json")
    assert tools["grep"].invoke(pattern="totp.json", path="docs") is not None
    with pytest.raises(ApprovalDenied):
        tools["fs_read"].invoke(path=str(auth_dir / "totp.json"))


def test_mcp_call_arguments_reach_the_guard(world) -> None:
    root, auth_dir = world
    config = root / "mcp.json"
    config.write_text(json.dumps({"servers": {"files": {"command": "no-such-server"}}}), encoding="utf-8")
    tools = {t.name: t for t in bind_mcp_tools(config, ApprovalPolicy("allow"))}
    with pytest.raises(ApprovalDenied, match="operator auth"):
        tools["mcp_call"].invoke(server="files", tool="read", arguments={"path": str(auth_dir / "totp.json")})


def test_worker_job_arguments_reach_the_guard(world) -> None:
    root, auth_dir = world
    job = {"job_id": "j1", "inputs": {"path": str(auth_dir / "totp.json")}}
    with pytest.raises(ApprovalDenied, match="operator auth"):
        run_worker_job_session(job, repo_root=root, session_id="s1", policy=ApprovalPolicy("allow"))
