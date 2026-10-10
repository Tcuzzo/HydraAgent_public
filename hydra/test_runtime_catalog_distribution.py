"""A catalog belongs to the installed product; evidence belongs to a workspace."""
from pathlib import Path

import pytest

from hydra.declarative_runtime import (
    DeclarativeRuntimeError, doctor_runtime_catalog, execute_agent_decision,
    load_runtime_catalog, run_declarative_turn,
)
from hydra.policy import ApprovalDenied, ApprovalPolicy


def decision(tool=None, arguments=None):
    return {"schema": "hydra.agent_decision.v1", "intent": {"kind": "chat"},
            "selected_skills": [], "execution_mode": "direct", "requires_approval": False,
            "verification": [],
            "selected_tools": [{"tool_id": tool}] if tool else [],
            "plan": [{"tool_id": tool, "arguments": arguments or {}}] if tool else []}


def test_empty_workspace_uses_product_catalog_and_local_evidence(tmp_path):
    catalog = load_runtime_catalog(tmp_path)
    assert catalog.root == tmp_path.resolve()
    assert doctor_runtime_catalog(catalog)["status"] == "OK"
    assert catalog.tools["shell"]["policy"]["non_destructive_auto_allow"] is False
    run_declarative_turn("hello", catalog, root=tmp_path, planner=lambda _: decision())
    assert (tmp_path / ".hydraAgent/memory/episodic.jsonl").is_file()
    with pytest.raises(ApprovalDenied):
        execute_agent_decision(decision("shell", {"command": "echo safe"}), catalog,
                               root=tmp_path, approval_policy=ApprovalPolicy("deny"))


def test_partial_workspace_override_keeps_other_product_defaults(tmp_path):
    override = tmp_path / ".hydraAgent/ux/response-contracts.yaml"
    override.parent.mkdir(parents=True)
    override.write_text("voice: workspace voice\n", encoding="utf-8")
    catalog = load_runtime_catalog(tmp_path)
    assert catalog.ux["voice"] == "workspace voice"
    assert "shell" in catalog.tools


@pytest.mark.parametrize("reference", ["../outside.yaml", ".hydraAgent/../outside.yaml", "/outside.yaml", "C:\\outside.yaml"])
def test_workspace_registry_cannot_escape_catalog(tmp_path, reference):
    registry = tmp_path / ".hydraAgent/tools/registry.yaml"
    registry.parent.mkdir(parents=True)
    # This is a valid external tool contract: confinement, not YAML parsing,
    # must reject the attempted read.
    (tmp_path / "outside.yaml").write_text("tool_id: outside\n", encoding="utf-8")
    import yaml
    registry.write_text(yaml.safe_dump({"tools": [reference]}), encoding="utf-8")
    with pytest.raises(DeclarativeRuntimeError, match="stay within|relative"):
        load_runtime_catalog(tmp_path)


def test_registry_list_replaces_default_tool_inventory(tmp_path):
    registry = tmp_path / ".hydraAgent/tools/registry.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text("tools: []\n", encoding="utf-8")
    assert load_runtime_catalog(tmp_path).tools == {}


def test_declarative_todo_uses_requested_workspace(tmp_path):
    # Source catalog here deliberately differs from the tool workspace.
    catalog = load_runtime_catalog(Path(__file__).resolve().parents[1])
    result = execute_agent_decision(decision("todo", {"action": "add", "text": "task"}),
                                    catalog, root=tmp_path)
    assert result["results"][0]["ok"]
    assert (tmp_path / ".hydra_todos.json").is_file()
