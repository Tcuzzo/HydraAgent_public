"""Contract / characterisation test: model config SSOT reconciliation.

Verifies:
  (a) No 'deepseek-v4-flash' literal remains in any non-test runtime .py file
      under hydra/ or gateways/, nor in .hydraAgent/hydra.yaml.
  (b) skill_connectors.spawn_one resolves its default model from the SSOT
      planner pair (e.g. ollama-cloud/qwen2.5:72b) without hitting the network.
  (c) tool_binding._default_subagent_model() returns the SSOT planner pair
      when hydra.yaml contains no override (or an empty subagents block).

These tests were RED before the deepseek-v4-flash drift was fixed (slice-audit-01).
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HYDRA_DIR = REPO_ROOT / "hydra"
GATEWAYS_DIR = REPO_ROOT / "gateways"
HYDRA_YAML = REPO_ROOT / ".hydraAgent" / "hydra.yaml"

STALE_LITERAL = "deepseek-v4-flash"

TOOLS_DIR = REPO_ROOT / ".hydraAgent" / "tools"

# ── runtime-source files: .py files that are NOT test files ─────────────────

def _runtime_py_files() -> list[Path]:
    files: list[Path] = []
    for search_dir in (HYDRA_DIR, GATEWAYS_DIR):
        if search_dir.exists():
            for p in search_dir.rglob("*.py"):
                if not p.name.startswith("test_"):
                    files.append(p)
    return files


# (a) No stale literal in any runtime Python file or hydra.yaml ──────────────

def test_no_deepseek_literal_in_runtime_python_files() -> None:
    """Every deepseek-v4-flash literal must be gone from non-test Python sources."""
    hits: list[str] = []
    for path in _runtime_py_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if STALE_LITERAL in line:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.rstrip()}")
    assert not hits, (
        f"Found {len(hits)} stale '{STALE_LITERAL}' occurrence(s) in runtime source:\n"
        + "\n".join(hits)
    )


def test_no_deepseek_literal_in_hydra_yaml() -> None:
    """hydra.yaml must not reference the retired deepseek-v4-flash model."""
    if not HYDRA_YAML.exists():
        pytest.skip(".hydraAgent/hydra.yaml not present")
    text = HYDRA_YAML.read_text(encoding="utf-8")
    hits = [
        f"  line {i}: {line.rstrip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if STALE_LITERAL in line
    ]
    assert not hits, (
        f"Found {len(hits)} stale '{STALE_LITERAL}' occurrence(s) in hydra.yaml:\n"
        + "\n".join(hits)
    )


# (b) spawn_one resolves default model from SSOT planner role ─────────────────

def test_spawn_one_default_model_from_ssot(monkeypatch) -> None:
    """spawn_one's resolved default model must equal SSOT planner pair."""
    from hydra.model_routing import load_routing

    routing = load_routing()
    p, m = routing.role_pair("planner")
    expected = f"{p}/{m}"   # e.g. "ollama-cloud/qwen2.5:72b" (from SSOT)

    # Stub out spawn_subagent so no subprocess is launched.
    captured: dict[str, str] = {}

    def fake_spawn_subagent(**kwargs):
        captured["model"] = kwargs.get("model", "")
        return {"success": True, "status": "completed", "output": "stub"}

    monkeypatch.setattr("hydra.skill_connectors.spawn_subagent", fake_spawn_subagent)

    from hydra import skill_connectors

    skill_connectors.spawn_one(task="stub task", root="/tmp")

    assert captured.get("model") == expected, (
        f"spawn_one resolved model={captured.get('model')!r}, expected SSOT={expected!r}"
    )


# (c) _default_subagent_model returns SSOT value when no yaml override ────────

def test_default_subagent_model_ssot_when_no_override(tmp_path, monkeypatch) -> None:
    """_default_subagent_model must fall back to SSOT planner when no spawn_model set."""
    from hydra.model_routing import load_routing

    routing = load_routing()
    p, m = routing.role_pair("planner")
    expected = f"{p}/{m}"   # e.g. "ollama-cloud/qwen2.5:72b" (from SSOT)

    # Write a hydra.yaml with subagents block but no spawn_model key.
    cfg_dir = tmp_path / ".hydraAgent"
    cfg_dir.mkdir()
    (cfg_dir / "hydra.yaml").write_text(
        "subagents:\n  max_concurrent: 4\n", encoding="utf-8"
    )

    # Patch the config path lookup inside tool_binding.
    monkeypatch.chdir(tmp_path)

    from hydra.cli import tool_binding
    result = tool_binding._default_subagent_model()

    assert result == expected, (
        f"_default_subagent_model()={result!r}, expected SSOT={expected!r}"
    )


def test_default_subagent_model_ssot_when_yaml_missing(tmp_path, monkeypatch) -> None:
    """_default_subagent_model must return SSOT planner when hydra.yaml absent."""
    from hydra.model_routing import load_routing

    routing = load_routing()
    p, m = routing.role_pair("planner")
    expected = f"{p}/{m}"

    monkeypatch.chdir(tmp_path)  # no .hydraAgent/hydra.yaml here

    from hydra.cli import tool_binding
    result = tool_binding._default_subagent_model()

    assert result == expected, (
        f"_default_subagent_model()={result!r}, expected SSOT={expected!r}"
    )


# (d) No stale literal in any .hydraAgent/tools/*.yaml contract file ──────────

def test_no_deepseek_literal_in_tools_yaml_contracts() -> None:
    """Every .hydraAgent/tools/*.yaml contract must not reference the retired
    deepseek-v4-flash model — the declarative schema default is the source that
    feeds _default_arguments(), which bypasses the SSOT planner path in spawn_one/
    spawn_many when the default is non-None.  Zero hits required.
    """
    if not TOOLS_DIR.exists():
        pytest.skip(".hydraAgent/tools/ directory not present")

    hits: list[str] = []
    for path in sorted(TOOLS_DIR.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if STALE_LITERAL in line:
                hits.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.rstrip()}"
                )
    assert not hits, (
        f"Found {len(hits)} stale '{STALE_LITERAL}' occurrence(s) in .hydraAgent/tools/ yaml contracts:\n"
        + "\n".join(hits)
    )


# (e) Declarative spawn_subagent default-arguments path resolves SSOT model ───

def test_declarative_spawn_subagent_default_resolves_ssot(monkeypatch) -> None:
    """_default_arguments() for spawn_subagent must NOT inject a retired model.
    Concretely: either (a) no 'model' key at all in the defaults (preferred — lets
    spawn_one's None->SSOT path fire), or (b) the key is present and equals the
    SSOT planner value.  It must NOT equal deepseek-v4-flash.
    """
    from hydra.model_routing import load_routing
    from hydra.declarative_runtime import load_runtime_catalog, _default_arguments

    routing = load_routing()
    p, m = routing.role_pair("planner")
    ssot_model = f"{p}/{m}"

    catalog = load_runtime_catalog(REPO_ROOT)
    contract = catalog.tools.get("spawn_subagent")
    assert contract is not None, "spawn_subagent tool not found in runtime catalog"

    defaults = _default_arguments(contract)
    model_default = defaults.get("model")

    assert model_default != STALE_LITERAL, (
        f"_default_arguments(spawn_subagent) injects retired model {STALE_LITERAL!r}; "
        "remove the default or update it to the SSOT value"
    )
    # If a default IS present it must equal the SSOT value.
    if model_default is not None:
        assert model_default == ssot_model, (
            f"_default_arguments(spawn_subagent) model default={model_default!r} "
            f"but SSOT={ssot_model!r}"
        )
