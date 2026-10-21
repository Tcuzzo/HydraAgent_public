"""Sibling scope surfaces obey the same project and native-tool boundaries."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_truth_memory_cannot_follow_workspace_wiki_outside_project(tmp_path):
    from hydra.memory_kernel import assemble_truth_context
    selected, outside = tmp_path / "selected", tmp_path / "other-project"
    (selected / ".hydraAgent").mkdir(parents=True)
    outside.mkdir()
    (outside / "business.md").write_text("# Borealis acquisition\nConfidential outside-project borealis_marker.\n")
    try:
        (selected / ".hydraAgent/wiki").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    result = assemble_truth_context("Borealis acquisition", repo_root=selected,
                                    memory_root=tmp_path / "memory", budget_chars=4000)
    assert "borealis_marker" not in result["text"]


def test_native_live_chat_rebinds_selected_scope_and_read_only_policy(tmp_path, monkeypatch):
    from hydra.test_review_turn_continuity import make_live_chat
    from hydra import providers
    import hydra.codex_client as native
    command, args, first, apps, shells, _, _ = make_live_chat(tmp_path, monkeypatch)
    args.provider, args.model = "codex", "codex"
    monkeypatch.setattr(command, "make_client", providers.make_client)
    monkeypatch.setenv("HYDRA_CODEX_BIN", "offline-native-transport")
    requests = []
    def transport(argv, **kwargs):
        requests.append(argv)
        Path(argv[argv.index("-o") + 1]).write_text("Hello operator.")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(native, "_default_runner", transport)
    second = tmp_path / "second"
    second.mkdir()
    assert command.cmd_chat(args) == 0
    app = apps[0]
    async def run():
        async with app.run_test(size=(100, 30)) as pilot:
            for text in ("Hello", f"/root {second}", "Hello again"):
                app.query_one("#operator-input").value = text
                await pilot.press("enter")
                await asyncio.wait_for(app.workers.wait_for_complete(), 10)
                await pilot.pause()
    asyncio.run(run())
    assert len(requests) == 2
    for argv, selected in zip(requests, (first, second)):
        assert Path(argv[argv.index("-C") + 1]) == selected
        assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
