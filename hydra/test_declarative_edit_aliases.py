"""Declarative edit aliases remain compatible and never hide conflicting input."""
from __future__ import annotations

import copy

import pytest

from hydra.declarative_runtime import (
    DeclarativeRuntimeError, execute_agent_decision, load_runtime_catalog,
)
from hydra.edit_checkpoints import CheckpointStore, undo
from hydra.policy import ApprovalPolicy


def _decision(arguments):
    return {
        "schema": "hydra.agent_decision.v1", "intent": {"kind": "code_change"},
        "selected_skills": [], "selected_tools": [{"tool_id": "fs_edit"}],
        "execution_mode": "direct", "requires_approval": False, "verification": [],
        "plan": [{"tool_id": "fs_edit", "arguments": arguments}],
    }


def test_skill_argument_names_remain_compatible_without_mutating_the_decision(tmp_path):
    target = tmp_path / "proof.txt"
    target.write_bytes(b"before\r\n")
    decision = _decision({"path": "proof.txt", "old_string": "before", "new_string": "after"})
    original = copy.deepcopy(decision)

    result = execute_agent_decision(
        decision, load_runtime_catalog(tmp_path), root=tmp_path,
        approval_policy=ApprovalPolicy("allow", authority_checker=lambda: False),
    )

    assert result["validation"]["passed"]
    assert target.read_bytes() == b"after\r\n"
    assert decision == original
    store = CheckpointStore(tmp_path)
    assert len(store.peek_all()) == 1
    undo(store)
    assert target.read_bytes() == b"before\r\n"


@pytest.mark.parametrize("public_name", ["old", "new"])
def test_conflicting_edit_aliases_are_rejected_before_mutation(tmp_path, public_name):
    target = tmp_path / "proof.txt"
    target.write_bytes(b"before\r\n")
    arguments = {"path": "proof.txt", "old": "before", "new": "after"}
    arguments[f"{public_name}_string"] = "conflicting text"
    decision = _decision(arguments)
    original = copy.deepcopy(decision)

    with pytest.raises(DeclarativeRuntimeError, match=f"both.*{public_name}.*{public_name}_string"):
        execute_agent_decision(
            decision, load_runtime_catalog(tmp_path), root=tmp_path,
            approval_policy=ApprovalPolicy("allow", authority_checker=lambda: False),
        )

    assert decision == original
    assert target.read_bytes() == b"before\r\n"
    assert not CheckpointStore(tmp_path).peek_all()
