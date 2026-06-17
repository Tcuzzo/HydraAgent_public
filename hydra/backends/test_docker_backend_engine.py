"""hydra/backends/test_docker_backend_engine.py

TDD tests for DockerBackend engine-awareness:
  - engine resolver: podman preferred, docker fallback, config override
  - _build_docker_command uses resolved engine as argv[0]
  - network: default build has NO --network flag; "none" adds --network none
  - health_check / kill / rm use the resolved engine (via subprocess.run mock)

No real container engine is required — all subprocess calls are mocked.
"""
from __future__ import annotations

import subprocess
from typing import List
from unittest import mock

import pytest

from hydra.backends.base import BackendConfig
from hydra.backends.docker import DockerBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend(extra: dict | None = None) -> DockerBackend:
    cfg = BackendConfig(extra_config=extra or {})
    return DockerBackend(cfg)


def _cmd_for(backend: DockerBackend, command: List[str] | None = None) -> List[str]:
    """Return the container command list built for a dummy worktree."""
    return backend._build_docker_command(
        container_name="hydra-test",
        worktree_path="/tmp/fake-wt",
        command=command or ["echo", "hi"],
        env={},
    )


# ---------------------------------------------------------------------------
# 1. Engine resolver
# ---------------------------------------------------------------------------

class TestResolveEngine:
    def test_prefers_podman_when_both_present(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            assert DockerBackend._resolve_engine() == "podman"

    def test_falls_back_to_docker_when_no_podman(self):
        def which(name):
            return "/usr/bin/docker" if name == "docker" else None

        with mock.patch("shutil.which", side_effect=which):
            assert DockerBackend._resolve_engine() == "docker"

    def test_last_resort_literal_when_neither_found(self):
        with mock.patch("shutil.which", return_value=None):
            assert DockerBackend._resolve_engine() == "docker"

    def test_config_override_wins_over_podman(self):
        """container_engine in extra_config must override auto-detection."""
        with mock.patch("shutil.which", return_value="/usr/bin/podman"):
            assert DockerBackend._resolve_engine(override="nerdctl") == "nerdctl"

    def test_config_override_applied_at_init(self):
        """DockerBackend.__init__ reads container_engine from extra_config."""
        with mock.patch("shutil.which", return_value="/usr/bin/podman"):
            b = _make_backend({"container_engine": "nerdctl"})
            assert b._engine == "nerdctl"

    def test_engine_is_podman_when_podman_installed(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()
            assert b._engine == "podman"

    def test_engine_is_docker_when_only_docker_installed(self):
        def which(name):
            return "/usr/bin/docker" if name == "docker" else None

        with mock.patch("shutil.which", side_effect=which):
            b = _make_backend()
            assert b._engine == "docker"


# ---------------------------------------------------------------------------
# 2. _build_docker_command uses engine as argv[0]
# ---------------------------------------------------------------------------

class TestBuildDockerCommand:
    def test_argv0_is_engine_podman(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()
        cmd = _cmd_for(b)
        assert cmd[0] == "podman"

    def test_argv0_is_engine_docker_fallback(self):
        def which(name):
            return "/usr/bin/docker" if name == "docker" else None

        with mock.patch("shutil.which", side_effect=which):
            b = _make_backend()
        cmd = _cmd_for(b)
        assert cmd[0] == "docker"

    def test_argv1_is_run(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()
        cmd = _cmd_for(b)
        assert cmd[1] == "run"


# ---------------------------------------------------------------------------
# 3. Network default — no --network flag when docker_network not set
# ---------------------------------------------------------------------------

class TestNetworkDefault:
    def test_default_no_network_flag(self):
        """When docker_network is not set, --network must NOT appear."""
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()  # no docker_network key
        cmd = _cmd_for(b)
        assert "--network" not in cmd, (
            f"--network should not be in default command, got: {cmd}"
        )

    def test_network_none_adds_flag(self):
        """When docker_network='none', --network none must appear."""
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend({"docker_network": "none"})
        cmd = _cmd_for(b)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_network_custom_value(self):
        """docker_network='mybridge' → --network mybridge."""
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend({"docker_network": "mybridge"})
        cmd = _cmd_for(b)
        assert "--network" in cmd
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "mybridge"


# ---------------------------------------------------------------------------
# 4. health_check uses resolved engine
# ---------------------------------------------------------------------------

class TestHealthCheckEngine:
    def test_health_check_calls_resolved_engine(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()
        assert b._engine == "podman"

        mock_result = mock.MagicMock()
        mock_result.returncode = 0

        with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
            result = b.health_check()

        assert result is True
        called_argv = mock_run.call_args[0][0]
        assert called_argv[0] == "podman"
        assert called_argv[1] == "info"

    def test_health_check_returns_false_on_nonzero(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()

        mock_result = mock.MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "some error"

        with mock.patch("subprocess.run", return_value=mock_result):
            assert b.health_check() is False

    def test_health_check_returns_false_on_file_not_found(self):
        with mock.patch("shutil.which", return_value=None):
            b = _make_backend()

        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no engine")):
            assert b.health_check() is False


# ---------------------------------------------------------------------------
# 5. _force_kill_container and _cleanup_container use resolved engine
# ---------------------------------------------------------------------------

class TestKillAndClean:
    def test_force_kill_uses_engine(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()

        with mock.patch("subprocess.run") as mock_run:
            b._force_kill_container("hydra-foo")

        called_argv = mock_run.call_args[0][0]
        assert called_argv[0] == "podman"
        assert called_argv[1] == "kill"
        assert called_argv[2] == "hydra-foo"

    def test_cleanup_container_uses_engine(self):
        with mock.patch("shutil.which", side_effect=lambda x: f"/usr/bin/{x}"):
            b = _make_backend()

        with mock.patch("subprocess.run") as mock_run:
            b._cleanup_container("hydra-bar")

        called_argv = mock_run.call_args[0][0]
        assert called_argv[0] == "podman"
        assert called_argv[1] == "rm"
        assert "-f" in called_argv
        assert "hydra-bar" in called_argv
