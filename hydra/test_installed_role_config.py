"""Role discovery must work outside a checkout without an operator config.

The command modules are imported before changing directories. Their historical
package-relative constants deliberately point at a missing installation config,
as they do in a clean wheel. Resolution must use the live workspace or the
existing routing defaults, while an operator-selected file remains authoritative.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra.cli import cmd_ask, cmd_chat, cmd_execution, cmd_runtime
from hydra.llm import ChatResponse
from hydra.roles import resolve_roles_from_dict


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    work = tmp_path / "workspace"
    work.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("HYDRA_CONFIG", raising=False)
    for module in (cmd_runtime, cmd_execution, cmd_chat, cmd_ask):
        monkeypatch.setattr(module, "HYDRA_CONFIG", tmp_path / "site-packages" / ".hydraAgent" / "hydra.yaml", raising=False)
        monkeypatch.setattr(module, "HYDRA_SCHEMA", tmp_path / "site-packages" / ".hydraAgent" / "schemas" / "hydra.schema.yaml", raising=False)
    return work


def _write_config(path: Path, route: str = "worker") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"agentic:\n  roles:\n    planner:\n      route: {route}\n", encoding="utf-8")
    return path


def _resolve(command, *, config=None, root=None):
    return command._resolved_roles_or_exit(SimpleNamespace(config=config, root=root))


@pytest.mark.parametrize("command", [cmd_runtime, cmd_execution])
def test_clean_install_uses_existing_routing_defaults(workspace, command, capsys):
    roles = _resolve(command)
    assert roles == resolve_roles_from_dict({})
    assert "role error:" not in capsys.readouterr().err


@pytest.mark.parametrize("command", [cmd_runtime, cmd_execution])
@pytest.mark.parametrize("relative", [".hydraAgent/hydra.yaml", "hydra.yaml"])
def test_workspace_config_is_discovered_after_command_import(workspace, command, relative):
    _write_config(workspace / relative)
    roles = _resolve(command)
    assert roles is not None
    assert roles.planner.family == "route:local"


def test_hidden_workspace_config_precedes_root_file_and_home(workspace):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml")
    _write_config(workspace / "hydra.yaml", "planner")
    _write_config(Path.home() / ".hydra" / "hydra.yaml", "planner")
    roles = _resolve(cmd_runtime)
    assert roles is not None and roles.planner.family == "route:local"


def test_existing_user_config_location_is_used_without_workspace_config(workspace):
    _write_config(Path.home() / ".hydra" / "hydra.yaml")
    roles = _resolve(cmd_runtime)
    assert roles is not None and roles.planner.family == "route:local"


def test_existing_environment_override_precedes_workspace(workspace, monkeypatch):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml", "planner")
    override = _write_config(workspace / "selected.yaml")
    monkeypatch.setenv("HYDRA_CONFIG", str(override))
    roles = _resolve(cmd_runtime)
    assert roles is not None and roles.planner.family == "route:local"


@pytest.mark.parametrize("command", [cmd_runtime, cmd_execution])
def test_explicit_custom_filename_precedes_environment_and_workspace(workspace, monkeypatch, command):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml", "planner")
    override = _write_config(workspace / "environment.yaml", "planner")
    monkeypatch.setenv("HYDRA_CONFIG", str(override))
    explicit = _write_config(workspace / "operator-roles.yml")
    roles = _resolve(command, config=str(explicit))
    assert roles is not None and roles.planner.family == "route:local"


@pytest.mark.parametrize("command", [cmd_runtime, cmd_execution])
@pytest.mark.parametrize("name", ["hydra.yaml", "operator-roles.yml"])
def test_missing_explicit_file_never_falls_back(workspace, command, name, capsys):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml")
    missing = workspace / "absent" / name
    assert _resolve(command, config=str(missing)) is None
    error = capsys.readouterr().err
    assert str(missing) in error and "not found" in error


def test_missing_environment_override_never_falls_back(workspace, monkeypatch, capsys):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml")
    missing = workspace / "missing-env.yaml"
    monkeypatch.setenv("HYDRA_CONFIG", str(missing))
    assert _resolve(cmd_runtime) is None
    assert str(missing) in capsys.readouterr().err


@pytest.mark.parametrize("text", ["[", "[]\n", "agentic: []\n", "agentic:\n  roles: []\n", "agentic:\n  roles:\n    planner: invalid\n"])
def test_invalid_discovered_config_is_reported_without_defaults(workspace, text, capsys):
    config = workspace / "hydra.yaml"
    config.write_text(text, encoding="utf-8")
    assert _resolve(cmd_runtime) is None
    error = capsys.readouterr().err
    assert "role error:" in error and str(config) in error


def test_execute_uses_explicit_workspace_for_config(workspace):
    _write_config(workspace / ".hydraAgent" / "hydra.yaml", "planner")
    target = workspace / "requested-root"
    _write_config(target / ".hydraAgent" / "hydra.yaml")
    roles = _resolve(cmd_execution, root=str(target))
    assert roles is not None and roles.planner.family == "route:local"


@pytest.mark.parametrize("with_config", [False, True])
def test_ask_auto_route_uses_defaults_or_explicit_workspace(workspace, monkeypatch, with_config, capsys):
    target = workspace / "requested-root"
    target.mkdir()
    if with_config:
        _write_config(target / ".hydraAgent" / "hydra.yaml", "doer")
    expected = resolve_roles_from_dict({"agentic": {"roles": {"planner": {"route": "doer"}}}} if with_config else {}).planner.model
    seen = []

    class Client:
        def chat(self, messages, *, model, **kwargs):
            seen.append(model)
            return ChatResponse("completed", model, "stop", 1, 1, {})

    parser = argparse.ArgumentParser()
    cmd_ask.register_ask_command(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["ask", "plan this task", "--auto-route", "--root", str(target), "--approval-policy", "deny"])
    monkeypatch.setattr(cmd_ask, "_resolve_chat_runtime", lambda args: {"provider": "configured"})
    monkeypatch.setattr(cmd_ask, "_make_client_or_setup", lambda args: (Client(), SimpleNamespace(name="configured", model="initial")))
    monkeypatch.setattr(cmd_ask, "_auto_route_role", lambda args: {"role": "planner", "matched": []})
    monkeypatch.setattr(cmd_ask, "build_agent_system_prompt", lambda prompt: prompt)
    monkeypatch.setattr(cmd_ask, "build_routed_skill_context", lambda prompt: "")
    monkeypatch.setattr(cmd_ask, "_bind_tools", lambda *args, **kwargs: [])

    assert cmd_ask.cmd_ask(args) == 0
    assert seen == [expected]
    assert "auto-route error:" not in capsys.readouterr().err
