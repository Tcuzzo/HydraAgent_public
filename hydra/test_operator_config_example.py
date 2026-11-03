"""Operator config stays out of git, and the shipped example parses.

The live file is ``.hydraAgent/hydra.yaml`` (also a repo-root ``hydra.yaml``).
The shipped catalog under ``.hydraAgent/{tools,policies,skills,ux,playbooks}``
stays tracked. ``hydra/model_router.py``, ``hydra/roles.py``, and ``hydra/cli/*``
do not read ``hydra.local.yaml``, so that name is not an ignore rule.

``hydra.example.yaml`` is a template. Loaders look up ``hydra.yaml`` by name,
so the example must not become the live config and must not unskip
``test_model_config_ssot``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from hydra.cli import cmd_chat, cmd_execution, cmd_runtime
from hydra.model_router import ModelRouter
from hydra.roles import resolve_roles
from hydra.test_no_model_provider_secret_literals import HOSTED_URL, MODEL_TAG, SECRET_NAME

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / ".hydraAgent" / "hydra.example.yaml"
LIVE = REPO / ".hydraAgent" / "hydra.yaml"

PROVIDER = "<provider>"
MODEL = "<model>"
FAMILY = "<family>"
BASE_URL = "<base-url>"
KEY_ENV = "<key-env>"
SPAWN_MODEL = "<provider>/<model>"

SHIPPED_CATALOG = (
    ".hydraAgent/playbooks/dev-mode-elite-build.yaml",
    ".hydraAgent/policies/anti-mock-theater.yaml",
    ".hydraAgent/policies/backs-invariants.yaml",
    ".hydraAgent/policies/danger-gates.yaml",
    ".hydraAgent/policies/sniper-testing.yaml",
    ".hydraAgent/policies/trust-tiers.yaml",
    ".hydraAgent/skills/index.yaml",
    ".hydraAgent/tools/filesystem.yaml",
    ".hydraAgent/tools/registry.yaml",
    ".hydraAgent/tools/shell.yaml",
    ".hydraAgent/tools/skill-library.yaml",
    ".hydraAgent/tools/subagents.yaml",
    ".hydraAgent/tools/system.yaml",
    ".hydraAgent/tools/web.yaml",
    ".hydraAgent/ux/response-contracts.yaml",
)

_IPV4 = re.compile(r"([0-9]{1,3}\.){3}[0-9]{1,3}")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )


def _ignored(*paths: str) -> dict[str, str]:
    """Map a repo-relative path to the gitignore pattern that excludes it.

    ``--no-index`` reports a match even when the path is already tracked, so a
    pattern that would hide the shipped catalog fails this test immediately.
    """
    result = _git("check-ignore", "-v", "--no-index", "--", *paths)
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        meta, path = line.split("\t", 1)
        pattern = meta.rsplit(":", 1)[-1]
        found[path] = pattern
    return found


def test_gitignore_covers_operator_config_and_keeps_the_shipped_catalog() -> None:
    tracked = _git("ls-files", "--", ".hydraAgent").stdout.splitlines()
    missing = [path for path in SHIPPED_CATALOG if path not in tracked]
    assert not missing, f"shipped catalog dropped out of git ls-files: {missing}"

    probed = _ignored(
        ".hydraAgent/hydra.yaml",
        "hydra.yaml",
        ".hydraAgent/hydra.example.yaml",
        *SHIPPED_CATALOG,
    )
    assert probed.get(".hydraAgent/hydra.yaml"), ".hydraAgent/hydra.yaml must be gitignored"
    assert probed.get("hydra.yaml"), "repo-root hydra.yaml must be gitignored"
    assert ".hydraAgent/hydra.example.yaml" not in probed
    leaked = [path for path in SHIPPED_CATALOG if path in probed]
    assert not leaked, f"shipped catalog must stay visible to git: {leaked}"


def test_example_config_parses_with_the_real_loaders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert EXAMPLE.is_file(), "ship .hydraAgent/hydra.example.yaml next to the live hydra.yaml path"
    text = EXAMPLE.read_text(encoding="utf-8")
    assert not MODEL_TAG.search(text)
    assert not SECRET_NAME.search(text)
    assert not HOSTED_URL.search(text)
    assert not _IPV4.search(text)

    data = yaml.safe_load(text)
    assert isinstance(data, dict)

    for module in (cmd_runtime, cmd_chat, cmd_execution):
        assert module.HYDRA_CONFIG == LIVE
        assert module.HYDRA_CONFIG != EXAMPLE
    assert not LIVE.exists(), "the example must not be installed as the live config"

    copied = tmp_path / "hydra.yaml"
    copied.write_text(text, encoding="utf-8")
    roles = resolve_roles(config_path=copied, schema_path=tmp_path / "hydra.schema.yaml")
    assert roles.planner.provider == PROVIDER
    assert roles.planner.model == MODEL
    assert roles.planner.family == FAMILY
    assert roles.doer.provider == PROVIDER
    assert roles.auditor.provider == PROVIDER
    assert roles.auditor.model == MODEL
    assert roles.auditor.family == FAMILY

    router = ModelRouter(config_path=EXAMPLE)
    for name in ("planner", "doer", "worker", "auditor"):
        cfg = router.models[name]
        assert cfg.provider == PROVIDER
        assert cfg.model == MODEL
        assert cfg.family == FAMILY
    assert router.models["auditor"].base_url == BASE_URL
    assert router.models["auditor"].api_key_env == KEY_ENV
    assert router.models["doer"].fallbacks == [
        {"provider": PROVIDER, "model": MODEL, "family": FAMILY}
    ]

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".hydraAgent").mkdir()
    (tmp_path / ".hydraAgent" / "hydra.yaml").write_text(text, encoding="utf-8")
    from hydra.cli import tool_binding

    assert tool_binding._default_subagent_model() == SPAWN_MODEL

    monkeypatch.chdir(REPO)
    from hydra.model_routing import load_routing

    provider, model = load_routing().role_pair("planner")
    assert tool_binding._default_subagent_model() == f"{provider}/{model}"
