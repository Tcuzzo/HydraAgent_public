from __future__ import annotations

import json
from pathlib import Path

from hydra.declarative_runtime import (
    DeclarativeRuntimeError,
    RuntimeCatalog,
    build_runtime_brief,
    doctor_runtime_catalog,
    execute_agent_decision,
    load_runtime_catalog,
    parse_agent_decision,
    run_declarative_turn,
)
from hydra.cli.cmd_runtime import cmd_declarative, register_runtime_commands
from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.workbench_approvals import load_records


def test_load_runtime_catalog_reads_contracts():
    catalog = load_runtime_catalog(Path("."))

    assert "skill_list" in catalog.tools
    assert "skill_show" in catalog.tools
    assert "skill_route" in catalog.tools
    assert "spawn_subagent" in catalog.tools
    assert "shell" in catalog.tools
    assert "skill_library.search" in catalog.tools
    assert catalog.ux["status"]["planning"] == "Building runtime brief"


def test_build_runtime_brief_includes_contract_sources():
    catalog = load_runtime_catalog(Path("."))
    brief = build_runtime_brief("search skill library for architecture patterns", catalog, root=Path("."))

    assert brief["schema"] == "hydra.runtime_brief.v1"
    assert brief["operator_input"] == "search skill library for architecture patterns"
    tool = next(tool for tool in brief["tools"] if tool["tool_id"] == "skill_library.search")
    assert tool["input_schema_ref"] == ".hydraAgent/tools/skill-library.yaml"
    assert "approval_required_when" in brief["policies"]
    assert "memory" in brief


def test_runtime_brief_advertises_single_destructive_policy_path():
    catalog = load_runtime_catalog(Path("."))
    brief = build_runtime_brief("run safe diagnostics", catalog, root=Path("."))

    gates = brief["policies"]["approval_required_when"]
    assert len(gates) == 1
    assert "Destructive or authority-class actions" in gates[0]


def test_load_runtime_catalog_reads_declarative_skill_index():
    catalog = load_runtime_catalog(Path("."))

    skill_ids = {item["skill_id"] for item in catalog.skills["skills"]}
    assert "systematic-debugging" in skill_ids
    assert "subagent-driven-development" in skill_ids
    assert "task_planner" in skill_ids
    # curated schemes bundles are merged: software-design skills are discoverable
    assert "system-architecture-design" in skill_ids
    assert catalog.skills["materialized_skill_count"] >= 1


def test_doctor_runtime_catalog_validates_contract_wiring():
    catalog = load_runtime_catalog(Path("."))

    report = doctor_runtime_catalog(catalog)

    assert report["schema"] == "hydra.declarative_doctor.v1"
    assert report["status"] == "OK"
    assert report["counts"]["tools"] >= 6
    assert report["counts"]["skills"] >= 5
    assert report["findings"] == []


def test_runtime_brief_exposes_materialized_skill_inventory_without_hiding_search_tool():
    catalog = load_runtime_catalog(Path("."))
    brief = build_runtime_brief("fix system architecture runtime and verify", catalog, root=Path("."))

    skill_ids = {item["skill_id"] for item in brief["skills"]}
    assert "task_planner" in skill_ids
    assert "system-architecture-design" in skill_ids
    assert any(tool["tool_id"] == "skill_library.search" for tool in brief["tools"])


def test_run_declarative_turn_auto_prefights_broad_work(monkeypatch, tmp_path):
    catalog = load_runtime_catalog(Path("."))
    calls = []

    def fake_spawn_many(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "schema": "hydra.subagents.spawn.v1", "count": len(kwargs["tasks"]), "results": []}

    monkeypatch.setattr("hydra.skill_connectors.spawn_many", fake_spawn_many)

    def fake_planner(_brief):
        return {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "code_change", "confidence": 0.99, "target": "runtime"},
            "selected_skills": [],
            "selected_tools": [],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [],
            "verification": [{"check": "preflight ran", "command": "none", "required": True}],
        }

    result = run_declarative_turn(
        "fix runtime end to end and check all seams",
        catalog,
        root=Path("."),
        planner=fake_planner,
        memory_root=tmp_path,
    )

    assert result.handled is True
    assert result.decision is not None
    assert [step["tool_id"] for step in result.decision["plan"][:2]] == [
        "skill_library.search",
        "spawn_subagents",
    ]
    assert result.execution is not None
    assert result.execution["results"][0]["schema"] == "hydra.skill_library_search.v1"
    assert result.execution["results"][0]["total_scanned"] >= 1
    assert calls[0]["runtime_only"] is True
    assert calls[0]["max_workers"] == 3
    assert len(calls[0]["tasks"]) == 3


def test_doctor_runtime_catalog_rejects_skill_allowed_tools_that_are_not_declared():
    catalog = load_runtime_catalog(Path("."))
    first_skill = dict(catalog.skills["skills"][0])
    first_skill["allowed_tools"] = ["remote.runtime_audit", "missing.tool"]
    bad_catalog = RuntimeCatalog(
        root=catalog.root,
        tools=catalog.tools,
        ux=catalog.ux,
        policies=catalog.policies,
        skills={"skills": [first_skill]},
    )

    report = doctor_runtime_catalog(bad_catalog)

    assert report["status"] == "WARN"
    assert any(
        finding["target"] == first_skill["skill_id"]
        and "unknown allowed tool: missing.tool" in finding["message"]
        for finding in report["findings"]
    )


def test_invalid_decision_cannot_execute():
    catalog = load_runtime_catalog(Path("."))

    try:
        execute_agent_decision({"schema": "bad"}, catalog, root=Path("."))
    except DeclarativeRuntimeError as exc:
        assert "missing required decision key" in str(exc)
    else:
        raise AssertionError("invalid decision executed")


def test_invalid_schema_value_cannot_execute():
    catalog = load_runtime_catalog(Path("."))

    try:
        execute_agent_decision(
            {
                "schema": "not.the.schema",
                "intent": {"kind": "audit", "confidence": 0.5, "target": "remote"},
                "selected_skills": [],
                "selected_tools": [],
                "execution_mode": "nonsense",
                "requires_approval": False,
                "approval_reason": "",
                "plan": [],
                "verification": [],
            },
            catalog,
            root=Path("."),
        )
    except DeclarativeRuntimeError as exc:
        assert "invalid decision schema" in str(exc)
    else:
        raise AssertionError("invalid schema executed")


def test_unknown_selected_tool_cannot_execute():
    catalog = load_runtime_catalog(Path("."))

    try:
        execute_agent_decision(
            {
                "schema": "hydra.agent_decision.v1",
                "intent": {"kind": "audit", "confidence": 0.5, "target": "remote"},
                "selected_skills": [],
                "selected_tools": [{"tool_id": "missing.tool", "reason": "test"}],
                "execution_mode": "direct",
                "requires_approval": False,
                "approval_reason": "",
                "plan": [{"id": "run", "action": "run", "tool_id": "missing.tool"}],
                "verification": [{"check": "none", "command": "none", "required": True}],
            },
            catalog,
            root=Path("."),
        )
    except DeclarativeRuntimeError as exc:
        assert "unknown selected tool" in str(exc)
    else:
        raise AssertionError("unknown selected tool executed")


def test_declarative_skill_show_connector_executes():
    catalog = load_runtime_catalog(Path("."))
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "design", "confidence": 0.95, "target": "system architecture"},
            "selected_skills": [{"skill_id": "task_planner", "reason": "route skill library"}],
            "selected_tools": [{"tool_id": "skill_show", "reason": "load selected skill contract"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "show",
                    "action": "show selected skill",
                    "tool_id": "skill_show",
                    "arguments": {"name": "system-architecture-design", "max_chars": 1000},
                    "expected_evidence": "skill content",
                }
            ],
            "verification": [{"check": "content returned", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    report = result["results"][0]
    assert report["schema"] == "hydra.skills.show.v1"
    assert report["ok"] is True
    assert report["name"] == "system-architecture-design"


def test_declarative_spawn_subagent_executes_when_contract_delegates_gate(monkeypatch):
    catalog = load_runtime_catalog(Path("."))
    calls = []

    def fake_spawn_one(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "schema": "hydra.subagent.spawn.v1", "status": "completed"}

    monkeypatch.setattr("hydra.skill_connectors.spawn_one", fake_spawn_one)

    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "code_change", "confidence": 0.95, "target": "TUI"},
            "selected_skills": [{"skill_id": "subagent-driven-development", "reason": "parallel review"}],
            "selected_tools": [{"tool_id": "spawn_subagent", "reason": "run a real worker"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "worker",
                    "action": "spawn worker",
                    "tool_id": "spawn_subagent",
                    "arguments": {"task": "audit the TUI", "runtime_only": True},
                    "expected_evidence": "worker report",
                }
            ],
            "verification": [{"check": "worker completed", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    assert result["results"] == [{"ok": True, "schema": "hydra.subagent.spawn.v1", "status": "completed"}]
    assert calls[0]["task"] == "audit the TUI"
    assert calls[0]["root"] == Path(".")
    assert calls[0]["runtime_only"] is True


def test_declarative_safe_shell_executes_without_approval_queue_or_telegram(tmp_path, monkeypatch):
    catalog = load_runtime_catalog(Path("."))
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
        notify_telegram=False,
    )
    monkeypatch.setattr(
        "gateways.telegram.live.notify_approval",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("telegram approval was called")),
    )

    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.95, "target": "shell"},
            "selected_skills": [{"skill_id": "systematic-debugging", "reason": "diagnostic command"}],
            "selected_tools": [{"tool_id": "shell", "reason": "single policy path"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "shell",
                    "action": "run shell",
                    "tool_id": "shell",
                    "arguments": {"command": "printf ok"},
                    "expected_evidence": "stdout",
                }
            ],
            "verification": [{"check": "stdout", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
        approval_policy=policy,
    )

    assert result["results"][0]["ok"] is True
    assert result["results"][0]["stdout"] == "ok"
    assert not (tmp_path / "approvals.jsonl").exists()


def test_declarative_destructive_shell_uses_single_policy_approval_path(tmp_path):
    catalog = load_runtime_catalog(Path("."))
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
        notify_telegram=False,
    )

    try:
        execute_agent_decision(
            {
                "schema": "hydra.agent_decision.v1",
                "intent": {"kind": "operate", "confidence": 0.95, "target": "shell"},
                "selected_skills": [{"skill_id": "systematic-debugging", "reason": "diagnostic command"}],
                "selected_tools": [{"tool_id": "shell", "reason": "single policy path"}],
                "execution_mode": "direct",
                "requires_approval": False,
                "approval_reason": "",
                "plan": [
                    {
                        "id": "shell",
                        "action": "run shell",
                        "tool_id": "shell",
                        "arguments": {"command": "echo changed > file.txt"},
                        "expected_evidence": "stdout",
                    }
                ],
                "verification": [{"check": "stdout", "command": "none", "required": True}],
            },
            catalog,
            root=Path("."),
            approval_policy=policy,
        )
    except ApprovalDenied as exc:
        assert "approval queued" in str(exc)
    else:
        raise AssertionError("destructive shell did not use policy approval path")

    approvals = load_records(tmp_path / "approvals.jsonl")
    assert len(approvals) == 1
    assert approvals[0].tool_name == "bash"
    assert approvals[0].arguments_preview["command"] == "echo changed > file.txt"


def test_declarative_planner_approval_flag_is_not_a_second_gate(tmp_path):
    catalog = load_runtime_catalog(Path("."))
    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
        notify_telegram=False,
    )

    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.95, "target": "shell"},
            "selected_skills": [{"skill_id": "systematic-debugging", "reason": "diagnostic command"}],
            "selected_tools": [{"tool_id": "shell", "reason": "single policy path"}],
            "execution_mode": "direct",
            "requires_approval": True,
            "approval_reason": "planner flag should not gate safe commands",
            "plan": [
                {
                    "id": "shell",
                    "action": "run shell",
                    "tool_id": "shell",
                    "arguments": {"command": "printf safe"},
                    "expected_evidence": "stdout",
                }
            ],
            "verification": [{"check": "stdout", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
        approval_policy=policy,
    )

    assert result["results"][0]["stdout"] == "safe"
    assert not (tmp_path / "approvals.jsonl").exists()


def test_yaml_declared_list_directory_executes():
    catalog = load_runtime_catalog(Path("."))
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.8, "target": "."},
            "selected_skills": [],
            "selected_tools": [{"tool_id": "list_directory", "reason": "test"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "list",
                    "action": "list",
                    "tool_id": "list_directory",
                    "arguments": {"path": "."},
                }
            ],
            "verification": [{"check": "listed", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    assert result["results"][0]["ok"] is True
    assert any(entry["name"] == "hydra" for entry in result["results"][0]["entries"])


def test_yaml_declared_grep_executes():
    catalog = load_runtime_catalog(Path("."))
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.8, "target": "."},
            "selected_skills": [],
            "selected_tools": [{"tool_id": "grep", "reason": "test"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "grep",
                    "action": "grep",
                    "tool_id": "grep",
                    "arguments": {"pattern": "HydraAgent", "path_pattern": "README.md"},
                }
            ],
            "verification": [{"check": "searched", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    assert result["results"][0]["ok"] is True
    assert result["results"][0]["pattern"] == "HydraAgent"


def test_yaml_declared_glob_executes_with_workspace_root():
    catalog = load_runtime_catalog(Path("."))
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.8, "target": "."},
            "selected_skills": [],
            "selected_tools": [{"tool_id": "glob", "reason": "test"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "glob",
                    "action": "glob",
                    "tool_id": "glob",
                    "arguments": {"pattern": "hydra/test_declarative_runtime.py"},
                }
            ],
            "verification": [{"check": "matched", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    assert result["results"][0]["ok"] is True
    assert result["results"][0]["matches"] == ["hydra/test_declarative_runtime.py"]
    assert result["validation"]["passed"] is True


def test_yaml_declared_git_diff_executes_with_workspace_root():
    catalog = load_runtime_catalog(Path("."))
    result = execute_agent_decision(
        {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.8, "target": "."},
            "selected_skills": [],
            "selected_tools": [{"tool_id": "git_diff", "reason": "test"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "git-diff",
                    "action": "git diff",
                    "tool_id": "git_diff",
                    "arguments": {},
                }
            ],
            "verification": [{"check": "stat", "command": "none", "required": True}],
        },
        catalog,
        root=Path("."),
    )

    assert result["results"][0]["ok"] is True
    assert "files_changed" in result["results"][0]
    assert result["validation"]["passed"] is True


def test_parse_agent_decision_from_yaml():
    decision = parse_agent_decision(
        """
schema: hydra.agent_decision.v1
intent:
  kind: audit
  confidence: 0.96
  target: remote
selected_skills:
  - skill_id: systematic-debugging
    reason: root-cause audit requested
selected_tools:
  - tool_id: remote.runtime_audit
    reason: remote runtime audit contract covers target
execution_mode: direct
requires_approval: false
approval_reason: ""
plan:
  - id: run_remote_audit
    action: run read-only audit
    tool_id: remote.runtime_audit
    arguments:
      target: remote
    expected_evidence: summary.md
verification:
  - check: report schema returned
    command: none
    required: true
"""
    )

    assert decision["intent"]["kind"] == "audit"
    assert decision["plan"][0]["tool_id"] == "remote.runtime_audit"


def test_run_declarative_turn_executes_fake_read_only_decision(tmp_path, monkeypatch):
    """run_declarative_turn executes a planned read-only tool and returns handled=True."""
    catalog = load_runtime_catalog(Path("."))
    monkeypatch.setattr(
        "hydra.skill_connectors.spawn_many",
        lambda **kwargs: {"ok": True, "schema": "hydra.subagents.spawn.v1", "count": len(kwargs["tasks"]), "results": []},
    )

    def fake_planner(brief):
        assert brief["operator_input"] == "list workspace files"
        return {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.99, "target": "."},
            "selected_skills": [{"skill_id": "systematic-debugging", "reason": "read-only diagnostic"}],
            "selected_tools": [{"tool_id": "list_directory", "reason": "read-only contract"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "list",
                    "action": "list workspace root",
                    "tool_id": "list_directory",
                    "arguments": {"path": "."},
                    "expected_evidence": "entries",
                }
            ],
            "verification": [{"check": "entries present", "command": "none", "required": True}],
        }

    result = run_declarative_turn("list workspace files", catalog, root=Path("."), planner=fake_planner, memory_root=tmp_path)

    assert result.handled is True
    assert result.execution is not None
    assert result.execution["results"][0]["ok"] is True


def test_run_declarative_turn_appends_episodic_memory(tmp_path, monkeypatch):
    """run_declarative_turn appends a well-formed episodic memory record after executing a turn."""
    memory_dir = tmp_path / ".hydraAgent" / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "episodic.jsonl").write_text("", encoding="utf-8")
    catalog = load_runtime_catalog(Path("."))

    monkeypatch.setattr(
        "hydra.skill_connectors.spawn_many",
        lambda **kwargs: {"ok": True, "schema": "hydra.subagents.spawn.v1", "count": len(kwargs["tasks"]), "results": []},
    )

    def fake_planner(_brief):
        return {
            "schema": "hydra.agent_decision.v1",
            "intent": {"kind": "operate", "confidence": 0.99, "target": "."},
            "selected_skills": [],
            "selected_tools": [{"tool_id": "list_directory", "reason": "read-only diagnostic"}],
            "execution_mode": "direct",
            "requires_approval": False,
            "approval_reason": "",
            "plan": [
                {
                    "id": "list",
                    "action": "list workspace",
                    "tool_id": "list_directory",
                    "arguments": {"path": "."},
                    "expected_evidence": "entries",
                }
            ],
            "verification": [{"check": "entries", "command": "none", "required": True}],
        }

    result = run_declarative_turn("list workspace files", catalog, root=Path("."), planner=fake_planner, memory_root=tmp_path)
    records = [
        json.loads(line)
        for line in (memory_dir / "episodic.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result.handled is True
    assert records[-1]["schema"] == "hydra.memory.episodic.v1"
    assert records[-1]["operator_input"] == "list workspace files"
    assert records[-1]["decision"]["intent"]["kind"] == "operate"
    assert records[-1]["execution"]["schema"] == "hydra.execution_result.v1"



def test_cmd_declarative_brief_prints_runtime_brief(capsys):
    import argparse

    args = argparse.Namespace(declarative_cmd="brief", prompt="search skill library", root=".")
    assert cmd_declarative(args) == 0
    out = capsys.readouterr().out

    assert "hydra.runtime_brief.v1" in out
    assert "skill_library.search" in out


def test_cmd_declarative_doctor_prints_report(capsys):
    import argparse

    args = argparse.Namespace(declarative_cmd="doctor", root=".")
    assert cmd_declarative(args) == 0
    out = capsys.readouterr().out

    assert "hydra.declarative_doctor.v1" in out
    assert '"status": "OK"' in out


def test_runtime_parser_registers_declarative_command():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register_runtime_commands(sub)
    args = parser.parse_args(["declarative", "doctor"])

    assert args.cmd == "declarative"
    assert args.declarative_cmd == "doctor"
