from __future__ import annotations

from pathlib import Path

import pytest

from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.workbench_approvals import load_records


def test_noninteractive_unknown_bash_is_queued_for_telegram_gate(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    with pytest.raises(ApprovalDenied) as excinfo:
        policy.require("bash", {"command": "sudo systemctl restart myservice"})

    assert "approval queued" in str(excinfo.value)
    approvals = load_records(tmp_path / "approvals.jsonl")
    assert len(approvals) == 1
    assert approvals[0].tool_name == "bash"
    assert approvals[0].arguments_preview["command"] == "sudo systemctl restart myservice"


def test_safe_bash_still_auto_runs_without_approval_queue(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    policy.require("bash", {"command": "git status --short"})

    assert not (tmp_path / "approvals.jsonl").exists()


def test_read_only_local_inspection_bash_commands_do_not_queue_approval(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    for command in [
        "ls -la /opt/agent-workspace",
        "find . -maxdepth 2 -type f",
        "rg -n AgentLoop hydra",
        "cat pyproject.toml",
        "ps aux | head -20",
        "df -h",
        "free -h",
        "nvidia-smi",
        "ollama ps",
        "docker ps",
        "systemctl status ollama",
    ]:
        policy.require("bash", {"command": command})

    assert not (tmp_path / "approvals.jsonl").exists()


def test_mutating_bash_still_queues_approval(tmp_path: Path) -> None:
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )

    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": "echo changed > file.txt"})

    approvals = load_records(tmp_path / "approvals.jsonl")
    assert len(approvals) == 1
