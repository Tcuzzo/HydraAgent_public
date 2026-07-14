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

import yaml

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
