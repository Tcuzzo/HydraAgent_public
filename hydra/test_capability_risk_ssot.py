"""Ball 1D (RED contract) - one risk authority; the contracts agree with it.

``hydra.policy.TOOL_RISK`` is the ONE runtime risk table, keyed by the RUNTIME tool
name (``bash``, not ``shell``). ``RISKY_TOOLS`` / ``MUTATING_TOOLS`` are derived from
it, the guardrails and mission-gate ladders read it, the trust-tier and danger-gate
YAMLs are generated from it, and the doctor reports ids that name no real tool.

No mocks: every test reads the real contract files, the real policy module and the
real CLI.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from hydra import policy
from hydra.declarative_runtime import doctor_runtime_catalog, load_runtime_catalog

REPO = Path(__file__).resolve().parents[1]
POLICIES = REPO / ".hydraAgent" / "policies"
RUNTIME_NAME = {"shell": "bash"}  # contract id -> runtime tool name


def _table() -> dict:
    table = getattr(policy, "TOOL_RISK", None)
    assert table, "hydra.policy.TOOL_RISK (the one risk table) does not exist"
    return table


def _catalog():
    return load_runtime_catalog(REPO)


def _runtime(tool_id: str) -> str:
    return RUNTIME_NAME.get(tool_id, tool_id)


def _trust_tiers() -> dict:
    return yaml.safe_load((POLICIES / "trust-tiers.yaml").read_text(encoding="utf-8"))


def test_every_runtime_tool_has_exactly_one_risk() -> None:
    table = _table()
    catalog = _catalog()
    declared = _trust_tiers()["tool_risks"]
    for tool_id, contract in catalog.tools.items():
        name = _runtime(tool_id)
        assert name in table, f"contract tool {tool_id!r} (runtime {name!r}) has no TOOL_RISK entry"
        assert contract["risk"] == table[name].tier, f"{tool_id}: contract risk != TOOL_RISK tier"
        assert declared.get(tool_id) == table[name].tier, f"{tool_id}: trust-tiers disagrees with TOOL_RISK"
    for name in policy.RISKY_TOOLS | policy.MUTATING_TOOLS:
        assert name in table, f"runtime set names {name!r} but TOOL_RISK does not"


def test_tool_risk_is_keyed_by_runtime_name_and_names_todo_and_apply_patch() -> None:
    table = _table()
    assert "bash" in table and "shell" not in table
    assert "todo" in table and "apply_patch" in table
    assert table["apply_patch"].mutating is True


def test_risky_and_mutating_sets_are_derived_not_hand_written() -> None:
    table = _table()
    assert policy.RISKY_TOOLS == frozenset(n for n, r in table.items() if r.gated)
    assert policy.MUTATING_TOOLS == frozenset(n for n, r in table.items() if r.mutating)
    tree = ast.parse((REPO / "hydra" / "policy.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"RISKY_TOOLS", "MUTATING_TOOLS"} for t in node.targets
        ):
            literals = [c for c in ast.walk(node.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
            assert not literals, "RISKY_TOOLS / MUTATING_TOOLS must be derived, not hand-listed"


def test_no_alias_declares_a_nonexistent_tool() -> None:
    table = _table()
    catalog = _catalog()
    declared = set(_trust_tiers()["tool_risks"])
    assert declared <= set(catalog.tools), f"orphan ids in trust-tiers: {sorted(declared - set(catalog.tools))}"
    runtime_only = {n for n in table if n not in {_runtime(t) for t in catalog.tools}}
    source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (REPO / "hydra").rglob("*.py")
        if not p.name.startswith("test_") and p.name != "policy.py"
    )
    for name in sorted(runtime_only):
        assert f"'{name}'" in source or f'"{name}"' in source, f"{name!r} is in TOOL_RISK but is no real runtime tool"


def test_doctor_reports_orphan_alias_ids(tmp_path: Path) -> None:
    """A stale user override that declares a risk for a non-existent tool is visible."""
    override = tmp_path / ".hydraAgent" / "policies"
    override.mkdir(parents=True)
    data = _trust_tiers()
    data["tool_risks"]["hydra.ghost_tool"] = "read_only"
    (override / "trust-tiers.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    report = doctor_runtime_catalog(load_runtime_catalog(tmp_path))
    targets = {f["target"] for f in report["findings"]}
    assert "hydra.ghost_tool" in targets, report["findings"]


def test_trust_tier_schema_is_bumped() -> None:
    assert _trust_tiers()["schema"] == "hydra.policies.trust_tiers.v2"


def test_destructive_tier_is_assigned_to_a_gated_tool() -> None:
    table = _table()
    destructive = [n for n, r in table.items() if r.tier == "destructive"]
    assert destructive, "the only tier that requires approval by default is assigned to zero tools"
    assert all(table[n].gated for n in destructive)


@pytest.mark.parametrize("tool_id", ["fs_write", "fs_edit", "todo", "shell"])
def test_contract_approval_matches_runtime_approval(tool_id: str) -> None:
    contract = _catalog().tools[tool_id]
    contract_requires = bool((contract.get("approval") or {}).get("required", True))
    assert contract_requires == policy.is_risky_tool(_runtime(tool_id)), (
        f"{tool_id}: contract approval.required={contract_requires} but the runtime "
        f"gates it={policy.is_risky_tool(_runtime(tool_id))}"
    )


def test_every_contract_approval_matches_the_table() -> None:
    table = _table()
    for tool_id, contract in _catalog().tools.items():
        requires = bool((contract.get("approval") or {}).get("required", True))
        assert requires == table[_runtime(tool_id)].gated, tool_id


def test_danger_gates_and_trust_tiers_are_generated_not_handwritten() -> None:
    from hydra.capability_truth import render_danger_gates_yaml, render_trust_tiers_yaml

    assert (POLICIES / "trust-tiers.yaml").read_text(encoding="utf-8") == render_trust_tiers_yaml()
    gates_text = (POLICIES / "danger-gates.yaml").read_text(encoding="utf-8")
    assert gates_text == render_danger_gates_yaml()
    assert "only approval gate" not in gates_text
    assert "single ApprovalPolicy gate" not in (POLICIES / "trust-tiers.yaml").read_text(encoding="utf-8")
    gates = yaml.safe_load(gates_text)["gates"]
    assert len(gates) == 1 and "Destructive or authority-class actions" in gates[0]["description"]


def test_guardrails_tiers_follow_the_one_table() -> None:
    from hydra.guardrails import ActionTier, Guardrails

    g = Guardrails(repo_root=REPO)
    assert g.classify_action_tier("read_file", {}) is ActionTier.READ_ONLY
    assert g.classify_action_tier("run_shell", {}) is ActionTier.DESTRUCTIVE
    assert g.classify_action_tier("write_file", {"path": "x.txt"}) is ActionTier.BOUNDED_WRITE
    assert g.classify_action_tier("brand_new_unknown_action", {}) is ActionTier.DESTRUCTIVE
    table = _table()
    assert table["bash"].tier == "destructive" and table["fs_write"].tier == "guarded_write"
    assert getattr(policy, "tool_risk", None) is not None and policy.tool_risk("bash") is table["bash"]


def test_mission_gate_ladder_is_derived_from_policy() -> None:
    from hydra import mission_gate

    ladder = policy.mission_class_order()
    assert ladder == mission_gate._ORDER
    assert ladder == ("dangerous", "destructive_or_off_lan", "huge_batch", "normal")


def test_capabilities_risk_report_prints_every_column() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "hydra", "capabilities", "risk", "--format", "json"],
        cwd=REPO, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    rows = {row["tool"]: row for row in report["tools"]}
    for name in ("bash", "fs_write", "fs_edit", "todo", "apply_patch"):
        assert name in rows, name
    for key in ("declared", "bound", "enabled", "surface_exposed", "approval", "runtime_tested", "unsupported_reason"):
        assert key in rows["bash"], key
    assert rows["apply_patch"]["unsupported_reason"]  # runtime-only: no declarative contract
    assert rows["bash"]["approval"] == "required" and rows["todo"]["approval"] == "none"
    assert report["disagreements"] == []
