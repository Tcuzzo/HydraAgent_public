"""Actual recall, public command and native transport boundaries from review."""
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("linked_part", ["wiki", "parent"])
def test_automatic_wiki_recall_cannot_follow_links_outside_selected_workspace(tmp_path, linked_part):
    from hydra.semantic_recall import build_semantic_memory_context
    selected, outside = tmp_path / "selected", tmp_path / "other-project"
    selected.mkdir()
    outside.mkdir()
    destination = outside if linked_part == "wiki" else outside / "wiki"
    destination.mkdir(exist_ok=True)
    (destination / "business.md").write_text("# Borealis acquisition\nBorealis confidential outside-project marker.\n")
    link = selected / ".hydraAgent" / "wiki" if linked_part == "wiki" else selected / ".hydraAgent"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    def offline(text):
        raise RuntimeError("offline embedding transport")
    result = build_semantic_memory_context("Borealis acquisition", root=tmp_path / "memory",
        workspace_root=selected, embedder=offline)
    assert "confidential outside-project marker" not in result.context
    assert str(outside) not in result.context


@pytest.mark.parametrize("policy,expected", [("deny", "read-only"), ("ask", "read-only"), ("allow", "workspace-write")])
def test_public_ask_propagates_scope_and_policy_to_native_provider(tmp_path, monkeypatch, policy, expected):
    from hydra.__main__ import _build_parser
    from hydra.cli import cmd_ask
    import hydra.codex_client as native
    selected = tmp_path / "selected"
    selected.mkdir()
    argv_seen = []
    def transport(argv, **kwargs):
        argv_seen.append(argv)
        Path(argv[argv.index("-o") + 1]).write_text("Native transport completed.")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setenv("HYDRA_CODEX_BIN", "offline-native-transport")
    monkeypatch.setattr(native, "_default_runner", transport)
    args = _build_parser().parse_args(["ask", "Inspect selected workspace", "--provider", "codex",
        "--model", "codex", "--root", str(selected), "--approval-policy", policy,
        "--env-dir", str(tmp_path / "empty-environment"), "--no-local-memory"])
    assert cmd_ask.cmd_ask(args) == 0
    assert len(argv_seen) == 1
    argv = argv_seen[0]
    assert Path(argv[argv.index("-C") + 1]) == selected
    assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == expected


@pytest.mark.parametrize("command", ["/status", "/self-check"])
def test_chat_status_reports_runtime_without_invented_verification(tmp_path, capsys, command):
    from hydra.cli.cmd_chat import _handle_chat_operator_intent
    runtime = {"profile": "local", "provider": "offline", "model": "test-model"}
    assert _handle_chat_operator_intent(command, runtime, tmp_path, "deny")
    output = capsys.readouterr().out
    assert str(tmp_path) in output and "offline" in output and "deny" in output
    assert "verification=not_run" in output
