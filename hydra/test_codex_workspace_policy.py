"""Native CLI tools obey the workspace and approval policy Hydra advertises."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from hydra.test_codex_setup_e2e import _fake_codex


@pytest.mark.parametrize("policy,sandbox", [
    ("deny", "read-only"),
    ("ask", "read-only"),
    ("allow", "workspace-write"),
])
def test_ask_native_cli_receives_the_requested_workspace_and_policy(tmp_path, policy, sandbox):
    launch = tmp_path / "launch"
    workspace = tmp_path / "workspace"
    launch.mkdir()
    workspace.mkdir()
    env = os.environ.copy()
    for name in list(env):
        if name.startswith(("HYDRA_", "BACKS_", "CODEX_", "OLLAMA_")):
            env.pop(name)
    env.update(
        HYDRA_ENV_DIR=str(tmp_path / "providers"),
        HYDRA_HOME=str(tmp_path / "state"),
        HYDRA_MODEL_HEALTH_PATH=str(tmp_path / "model-health.json"),
        HYDRA_CODEX_BIN=str(_fake_codex(tmp_path)),
        CODEX_TEST_RECORD=str(tmp_path / "invocation.json"),
        OLLAMA_ENDPOINT="http://127.0.0.1:1",
    )
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(Path(__file__).resolve().parents[1]), env.get("PYTHONPATH")))
    )
    result = subprocess.run(
        [sys.executable, "-m", "hydra", "ask", "Inspect the requested workspace.",
         "--provider", "codex", "--root", str(workspace),
         "--approval-policy", policy, "--memory-root", str(tmp_path / "memory")],
        cwd=launch, env=env, text=True, capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    invocation = json.loads((tmp_path / "invocation.json").read_text(encoding="utf-8"))
    argv = invocation["args"]
    actual_scope = (
        Path(argv[argv.index("-C") + 1]).resolve(),
        argv[argv.index("--sandbox") + 1] if "--sandbox" in argv else None,
    )
    assert actual_scope == (workspace.resolve(), sandbox)


@pytest.mark.parametrize("surface", ["legacy", "textual"])
@pytest.mark.parametrize("policy,sandbox", [("deny", "read-only"), ("allow", "workspace-write")])
def test_chat_native_scope_survives_root_and_profile_switches(
    tmp_path, monkeypatch, surface, policy, sandbox,
):
    from hydra.__main__ import main
    from hydra.cli import cmd_chat
    from hydra import session_memory

    launch = tmp_path / "launch"
    workspaces = [tmp_path / name for name in ("initial", "second", "third")]
    launch.mkdir()
    for workspace in workspaces:
        workspace.mkdir()
    monkeypatch.chdir(launch)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.setenv("HYDRA_CODEX_BIN", str(_fake_codex(tmp_path)))
    monkeypatch.setenv("HYDRA_MODEL_HEALTH_PATH", str(tmp_path / "model-health.json"))
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path / "sessions")
    # Select a native client for each profile without touching a live account.
    monkeypatch.setattr(cmd_chat, "CHAT_PROFILE_DEFAULTS", {
        "auto": ("codex", ""), "local": ("codex", ""), "cloud": ("codex", ""),
    })
    observed = []

    def record(client):
        observed.append((Path(client.cd).resolve(), client.sandbox))

    class SurfaceProbe:
        """Only terminal rendering is replaced; runtime callbacks are production."""

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self._chat_messages = list(kwargs["initial_messages"])

        def reconfigure_runtime(self, *, client, cfg, model, runtime=None):
            self.client, self.cfg, self.model = client, cfg, model

        def run(self):
            record(self.client)
            if surface == "legacy":
                assert self.command_handler(self, f"/root {workspaces[1]}")
                record(self.client)
                assert self.command_handler(self, "/local")
                record(self.client)
                assert self.command_handler(self, f"/root {workspaces[2]}")
                record(self.client)
            else:
                self._rebind_for_app(workspaces[1])
                record(self.client)
                switched_client, _, _, _ = self._switch_profile_for_app("local")
                record(switched_client)
                self._rebind_for_app(workspaces[2])
                record(switched_client)

    monkeypatch.setattr(cmd_chat, "EliteTUI", SurfaceProbe)
    assert main([
        "chat", "--provider", "codex", "--root", str(workspaces[0]),
        "--approval-policy", policy, "--no-local-memory",
        "--memory-root", str(tmp_path / "memory"),
    ]) == 0
    assert observed == [
        (workspaces[0].resolve(), sandbox),
        (workspaces[1].resolve(), sandbox),
        (workspaces[1].resolve(), sandbox),
        (workspaces[2].resolve(), sandbox),
    ]
