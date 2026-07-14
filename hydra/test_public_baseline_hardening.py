"""Public-baseline hardening contract (red-first).

Pins the supply-chain and safe-default posture of the public edition:
  1. constraints.txt exists at the repo root and pins EVERY pyproject runtime
     dependency to an exact version (==) — CI installs with `-c constraints.txt`
     so a compromised upstream release cannot silently ride into a build.
  2. The shipped shell tool contract defaults non_destructive_auto_allow to
     false — a fresh public install asks before running shell commands until
     its operator explicitly loosens the contract.
  3. Dependabot watches pip + github-actions weekly.
  4. SECURITY.md documents private disclosure via GitHub Security Advisories.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml

from hydra.declarative_runtime import execute_agent_decision, load_runtime_catalog
from hydra.policy import ApprovalDenied, ApprovalPolicy
from hydra.workbench_approvals import load_records

REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical(name: str) -> str:
    """PEP 503 canonical package name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_dependency_names() -> list[str]:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]
    names = []
    for spec in deps:
        match = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        assert match, f"unparseable dependency spec: {spec!r}"
        names.append(_canonical(match.group(1)))
    assert names, "pyproject must declare runtime dependencies"
    return names


def test_constraints_file_pins_every_runtime_dependency() -> None:
    constraints_path = REPO_ROOT / "constraints.txt"
    assert constraints_path.exists(), (
        "constraints.txt must ship at the repo root (pip install -e . -c constraints.txt)"
    )
    pinned: dict[str, str] = {}
    for line in constraints_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$", line)
        assert match, f"constraints.txt line is not an exact pin: {line!r}"
        pinned[_canonical(match.group(1))] = match.group(2)
    for name in _runtime_dependency_names():
        assert name in pinned, f"runtime dependency {name!r} is not pinned in constraints.txt"


def test_ci_installs_with_constraints() -> None:
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "-c constraints.txt" in ci, (
        "ci.yml install steps must use `pip install -e . -c constraints.txt`"
    )


def test_shell_contract_ships_safe_default() -> None:
    contract = yaml.safe_load(
        (REPO_ROOT / ".hydraAgent" / "tools" / "shell.yaml").read_text(encoding="utf-8")
    )
    policy = contract.get("policy") or {}
    assert policy.get("non_destructive_auto_allow") is False, (
        "the PUBLIC edition must ship shell non_destructive_auto_allow: false — "
        "a fresh install asks first; loosening is an explicit operator choice"
    )


def test_dependabot_watches_pip_and_actions_weekly() -> None:
    config = yaml.safe_load(
        (REPO_ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )
    updates = {entry["package-ecosystem"]: entry for entry in config.get("updates", [])}
    assert "pip" in updates, "dependabot must watch the pip ecosystem"
    assert "github-actions" in updates, "dependabot must watch github-actions"
    for ecosystem in ("pip", "github-actions"):
        assert updates[ecosystem]["schedule"]["interval"] == "weekly"


def test_security_policy_documents_private_disclosure() -> None:
    text = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "Security Advisories" in text, "SECURITY.md must route reports to GitHub Security Advisories"
    assert "advisories/new" in text or "security/advisories" in text


def test_ci_test_toolchain_ships_pytest_asyncio() -> None:
    """The suite uses asyncio_mode='auto' and ships async tests, so the CI test
    toolchain MUST provide pytest-asyncio — pinned in constraints.txt AND
    installed by ci.yml. Without it 4 async tests fail on every push with
    'async def functions are not natively supported'."""
    pinned: dict[str, str] = {}
    for line in (REPO_ROOT / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$", line)
        if match:
            pinned[_canonical(match.group(1))] = match.group(2)
    assert "pytest-asyncio" in pinned, (
        "constraints.txt must pin pytest-asyncio (==) — the CI async tests need it"
    )
    assert "pytest" in pinned, "constraints.txt must pin pytest (==) for reproducible CI test installs"
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pytest-asyncio" in ci, "ci.yml's test-install step must install pytest-asyncio"


def _shell_decision(command: str) -> dict:
    return {
        "schema": "hydra.agent_decision.v1",
        "intent": {"kind": "operate", "confidence": 0.9, "target": "shell"},
        "selected_skills": [],
        "selected_tools": [{"tool_id": "shell", "reason": "run a command"}],
        "execution_mode": "direct",
        "requires_approval": False,
        "approval_reason": "",
        "plan": [
            {
                "id": "sh",
                "action": "run shell",
                "tool_id": "shell",
                "arguments": {"command": command},
                "expected_evidence": "stdout",
            }
        ],
        "verification": [{"check": "ran", "command": "none", "required": True}],
    }


def test_shell_flag_false_gates_non_destructive_bash_end_to_end(tmp_path) -> None:
    """The headline hardening flip must be WIRED, not advisory. With the shipped
    shell contract's ``non_destructive_auto_allow: false``, a benign,
    non-destructive command (``echo hi``) must REQUIRE approval through the real
    runtime path: contract -> execute_agent_decision -> policy.require."""
    catalog = load_runtime_catalog(REPO_ROOT)
    shell_policy = catalog.tools["shell"].get("policy") or {}
    assert shell_policy.get("non_destructive_auto_allow") is False  # shipped safe default

    policy = ApprovalPolicy(
        "ask",
        stdin_is_tty=lambda: False,
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )
    with pytest.raises(ApprovalDenied):
        execute_agent_decision(
            _shell_decision("echo hi"), catalog, root=tmp_path, approval_policy=policy
        )
    approvals = load_records(tmp_path / "approvals.jsonl")
    assert approvals and approvals[0].tool_name == "bash"


def test_approval_policy_respects_non_destructive_auto_allow_flag(tmp_path) -> None:
    """The policy honours the threaded flag. TRUE (private-edition default) keeps
    EXACTLY today's behavior — a non-destructive command auto-allows. FALSE (the
    public safe default) makes even a benign command require approval."""

    def _policy() -> ApprovalPolicy:
        return ApprovalPolicy(
            "ask",
            stdin_is_tty=lambda: False,
            approval_path=tmp_path / "approvals.jsonl",
            run_path=tmp_path / "runs.jsonl",
        )

    # TRUE — auto-allow preserved; nothing is queued.
    _policy().require("bash", {"command": "echo hi"}, non_destructive_auto_allow=True)
    assert not (tmp_path / "approvals.jsonl").exists()

    # FALSE — even a benign, non-destructive command requires approval.
    with pytest.raises(ApprovalDenied):
        _policy().require("bash", {"command": "echo hi"}, non_destructive_auto_allow=False)
    assert (tmp_path / "approvals.jsonl").exists()
