from __future__ import annotations

from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.workbench_approvals import load_records as load_approval_records


def test_destructive_shell_requires_approval_when_yolo_is_locked(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        authority_checker=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": "rm -rf build"})

    approvals = load_approval_records(tmp_path / "approvals.jsonl")
    assert len(approvals) == 1


def test_yolo_unlock_allows_local_destructive_shell_without_queue(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        authority_checker=lambda: True,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    policy.require("bash", {"command": "rm -rf build"})
    policy.require("fs_write", {"path": "notes.txt", "content": "ok"})

    assert not (tmp_path / "approvals.jsonl").exists()


def test_network_commands_are_not_gated_operator_law(tmp_path: Path) -> None:
    """Policy: plain ssh/scp/network commands are NOT destructive and run free on
    the operator's trusted surface, with yolo LOCKED or UNLOCKED. (The old
    NETWORK_AUTHORITY blanket gate was undeclared and is removed — bugs #3/#4.)"""
    assert classify_tool_call("bash", {"command": "ssh alice@198.51.100.20 uptime"}, "ask")["decision"] == "auto_allow"
    assert classify_tool_call("bash", {"command": "nmap -sn 198.51.100.0/24"}, "ask")["decision"] == "auto_allow"

    # Yolo LOCKED — ssh must STILL run without queuing an approval.
    locked = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        authority_checker=lambda: False,
        approval_path=tmp_path / "locked-approvals.jsonl",
        run_path=tmp_path / "locked-runs.jsonl",
    )
    locked.require("bash", {"command": "ssh alice@198.51.100.20 uptime"})
    assert not (tmp_path / "locked-approvals.jsonl").exists()

    # Yolo UNLOCKED — same outcome.
    unlocked = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        authority_checker=lambda: True,
        approval_path=tmp_path / "unlocked-approvals.jsonl",
        run_path=tmp_path / "unlocked-runs.jsonl",
    )
    unlocked.require("bash", {"command": "ssh alice@198.51.100.20 uptime"})
    assert not (tmp_path / "unlocked-approvals.jsonl").exists()
