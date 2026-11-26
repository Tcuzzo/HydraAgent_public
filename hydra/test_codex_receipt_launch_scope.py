"""A native receipt describes the launched process, even after UI rebinding."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from hydra.codex_client import CodexClient
from hydra.providers import bind_client_workspace


@pytest.mark.parametrize("initial_sandbox,next_policy", [
    ("workspace-write", "deny"),
    ("read-only", "allow"),
    (None, "deny"),
])
def test_receipt_retains_launch_scope_when_client_is_rebound_during_turn(
    tmp_path, initial_sandbox, next_policy,
):
    first_root = tmp_path / "first"
    next_root = tmp_path / "next"
    first_root.mkdir()
    next_root.mkdir()
    launches = []

    def runner(argv, **kwargs):
        launches.append({
            "workspace": argv[argv.index("-C") + 1],
            "sandbox": argv[argv.index("--sandbox") + 1] if "--sandbox" in argv else None,
        })
        # A /root or policy change can run while the UI's worker is waiting for
        # the native process. Its next-turn state must not rewrite this receipt.
        bind_client_workspace(client, "codex", root=next_root, approval_policy=next_policy)
        Path(argv[argv.index("-o") + 1]).write_text("Native process completed.", encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    client = CodexClient(
        codex_bin="injected-native-transport", cd=str(first_root),
        sandbox=initial_sandbox, runner=runner,
    )
    response = client.chat([{"role": "user", "content": "Inspect this workspace."}])
    receipt = response.native_execution
    assert launches[0] == {"workspace": str(first_root), "sandbox": initial_sandbox}
    assert receipt.workspace == launches[0]["workspace"]
    assert receipt.sandbox == launches[0]["sandbox"]
    assert receipt.returncode == 0
    assert receipt.verification == "not_run"
    assert client.cd == str(next_root)

    # The following turn still uses the UI's new binding rather than freezing
    # the client itself to the previous process's launch state.
    following = client.chat([{"role": "user", "content": "Inspect the new workspace."}])
    assert launches[1]["workspace"] == str(next_root)
    assert following.native_execution.workspace == launches[1]["workspace"]
    assert following.native_execution.sandbox == launches[1]["sandbox"]
    assert following.native_execution.sandbox == (
        "workspace-write" if next_policy == "allow" else "read-only"
    )
