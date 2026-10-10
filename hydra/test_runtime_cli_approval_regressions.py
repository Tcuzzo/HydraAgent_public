"""Real workspace undo and CLI approval boundary regressions."""
from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from hydra.cli import tool_binding
from hydra.cli.cmd_undo import cmd_undo, register_undo_command
from hydra.edit_checkpoints import CheckpointStore
from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.workbench_approvals import load_records


def _tools(monkeypatch, tmp_path, *, mode="ask", interactive=False, wait=False, notify=False):
    prompts = []
    policy = ApprovalPolicy(
        mode,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
        stdin_is_tty=lambda: interactive,
        authority_checker=lambda: False,
        input_fn=lambda prompt: prompts.append(prompt) or "yes",
        wait_for_approval=wait,
        notify_telegram=notify,
        approval_decision_reader=lambda request_id: SimpleNamespace(status="approved"),
    )
    monkeypatch.setattr(tool_binding, "ApprovalPolicy", lambda *args, **kwargs: policy)
    return {
        t.name: t for t in tool_binding.bind_tools(tmp_path, approval_policy=mode, notify_telegram=notify)
    }, policy, prompts


def test_undo_defaults_to_current_customer_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "customer"
    workspace.mkdir()
    target = workspace / "notes.txt"
    target.write_text("after", encoding="utf-8")
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    CheckpointStore(workspace).record(target_path=target, op="modify", pre_image=b"before")
    parser = argparse.ArgumentParser()
    register_undo_command(parser.add_subparsers())
    monkeypatch.chdir(workspace)

    assert cmd_undo(parser.parse_args(["undo"])) == 0
    assert target.read_bytes() == b"before"


def test_default_cli_shell_requires_approval_without_waiting(monkeypatch, tmp_path):
    tools, policy, _ = _tools(monkeypatch, tmp_path)
    monkeypatch.setattr(policy, "_wait_for_decision", lambda *_: pytest.fail("no approval consumer was configured"))

    with pytest.raises(ApprovalDenied, match="approval queued"):
        tools["bash"].invoke(command="echo hello")
    assert [r.tool_name for r in load_records(policy.approval_path)] == ["bash"]


def test_noninteractive_file_write_refuses_without_polling(monkeypatch, tmp_path):
    tools, policy, _ = _tools(monkeypatch, tmp_path)
    monkeypatch.setattr(policy, "_wait_for_decision", lambda *_: pytest.fail("no approval consumer was configured"))

    # Retrying an existing, undelivered request must also refuse immediately.
    for _ in range(2):
        with pytest.raises(ApprovalDenied, match="approval queued"):
            tools["fs_write"].invoke(path="new.txt", content="must not write")
    assert not (tmp_path / "new.txt").exists()
    assert len(load_records(policy.approval_path)) == 1


def test_interactive_write_prompts_once_and_runs(monkeypatch, tmp_path):
    tools, _, prompts = _tools(monkeypatch, tmp_path, interactive=True)

    tools["fs_write"].invoke(path="new.txt", content="approved")
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "approved"
    assert len(prompts) == 1


def test_explicit_waiting_policy_runs_approved_write_once(monkeypatch, tmp_path):
    tools, policy, _ = _tools(monkeypatch, tmp_path, wait=True)
    decisions = []
    policy.approval_decision_reader = lambda request_id: decisions.append(request_id) or SimpleNamespace(status="approved")

    tools["fs_write"].invoke(path="new.txt", content="approved")
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "approved"
    assert len(load_records(policy.approval_path)) == 1
    assert len(decisions) == 1


@pytest.mark.parametrize("delivery", ["unavailable", "held", "failed"])
def test_unavailable_approval_notification_does_not_block(monkeypatch, tmp_path, delivery):
    tools, policy, _ = _tools(monkeypatch, tmp_path, notify=True)

    def notify(*args, **kwargs):
        if delivery == "unavailable":
            raise RuntimeError("Telegram is not configured")
        return {"ok": delivery == "held", "held": delivery == "held"}

    monkeypatch.setattr("gateways.telegram.live.notify_approval", notify)
    monkeypatch.setattr(policy, "_wait_for_decision", lambda *_: pytest.fail("approval notification was not delivered"))

    for _ in range(2):
        with pytest.raises(ApprovalDenied, match="approval queued"):
            tools["fs_write"].invoke(path="new.txt", content="must not write")
    assert not (tmp_path / "new.txt").exists()
    assert len(load_records(policy.approval_path)) == 1


def test_delivered_approval_resumes_the_tool(monkeypatch, tmp_path):
    tools, policy, _ = _tools(monkeypatch, tmp_path, notify=True)
    monkeypatch.setattr("gateways.telegram.live.notify_approval", lambda *a, **k: {"ok": True})

    tools["fs_write"].invoke(path="new.txt", content="approved")
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "approved"
    assert len(load_records(policy.approval_path)) == 1


def test_cli_shell_honors_explicit_workspace_auto_allow(monkeypatch, tmp_path):
    shell_contract = tmp_path / ".hydraAgent" / "tools" / "shell.yaml"
    shell_contract.parent.mkdir(parents=True)
    shell_contract.write_text(
        "tool_id: shell\nexecutor: skills.bash.run\npolicy:\n  non_destructive_auto_allow: true\n",
        encoding="utf-8",
    )
    tools, policy, _ = _tools(monkeypatch, tmp_path)

    result = tools["bash"].invoke(command="echo approved-by-contract")
    assert result["exit_code"] == 0
    assert "approved-by-contract" in result["stdout"]
    assert not policy.approval_path.exists()


def test_explicit_allow_policy_runs_shell_and_file_write(monkeypatch, tmp_path):
    tools, policy, _ = _tools(monkeypatch, tmp_path, mode="allow")

    assert tools["bash"].invoke(command="echo allowed")["exit_code"] == 0
    tools["fs_write"].invoke(path="new.txt", content="allowed")
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "allowed"
    assert not policy.approval_path.exists()
