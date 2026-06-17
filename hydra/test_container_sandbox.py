"""hydra/test_container_sandbox.py — TDD tests for hydra.container_sandbox.

Written FIRST (red), then the implementation makes them green.

Coverage:
  - detect_sandbox_engine: prefers podman / falls back to docker / returns None
  - build_sandbox_run_args: all arg ordering rules, caps, network, mounts, raises
  - run_in_sandbox: no-engine degrade (never calls runner), happy-path fake runner
"""
from __future__ import annotations

import subprocess
from unittest import mock
from unittest.mock import MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_runner_ok(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Fake runner that returns a successful result with stdout 'OK'."""
    return subprocess.CompletedProcess(argv, returncode=0, stdout="OK\n", stderr="")


def _fake_runner_fail(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Fake runner that returns a non-zero exit."""
    return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="error")


# ---------------------------------------------------------------------------
# detect_sandbox_engine
# ---------------------------------------------------------------------------

class TestDetectSandboxEngine:
    def test_prefers_podman_when_both_present(self):
        from hydra.container_sandbox import detect_sandbox_engine

        which = lambda b: f"/usr/bin/{b}"  # noqa: E731
        assert detect_sandbox_engine(which=which) == "podman"

    def test_falls_back_to_docker_when_no_podman(self):
        from hydra.container_sandbox import detect_sandbox_engine

        def which(b):
            return "/usr/bin/docker" if b == "docker" else None

        assert detect_sandbox_engine(which=which) == "docker"

    def test_returns_none_when_neither_present(self):
        from hydra.container_sandbox import detect_sandbox_engine

        assert detect_sandbox_engine(which=lambda _: None) is None

    def test_podman_wins_even_if_docker_present_first(self):
        """Ordering: podman is checked first and wins; docker need not be checked."""
        from hydra.container_sandbox import detect_sandbox_engine

        calls = []

        def which(b):
            calls.append(b)
            return f"/usr/bin/{b}"  # both present

        result = detect_sandbox_engine(which=which)
        assert result == "podman"
        # podman must be the first binary checked (short-circuit is fine)
        assert calls[0] == "podman"


# ---------------------------------------------------------------------------
# build_sandbox_run_args
# ---------------------------------------------------------------------------

class TestBuildSandboxRunArgs:
    def _build(self, **kwargs):
        from hydra.container_sandbox import build_sandbox_run_args
        return build_sandbox_run_args(**kwargs)

    def test_starts_with_run_rm(self):
        args = self._build(image="img:test", command="echo hi")
        assert args[:2] == ["run", "--rm"]

    def test_network_on_by_default_no_network_flag(self):
        """When network=True (default), '--network none' must NOT appear."""
        args = self._build(image="img:test", command="echo hi")
        assert "--network" not in args

    def test_network_false_adds_network_none(self):
        args = self._build(image="img:test", command="echo hi", network=False)
        idx = args.index("--network")
        assert args[idx + 1] == "none"

    def test_network_none_flag_comes_before_mounts(self):
        args = self._build(
            image="img:test",
            command="echo hi",
            network=False,
            mounts=[{"source": "/src", "target": "/dst"}],
        )
        network_idx = args.index("--network")
        v_idx = args.index("-v")
        assert network_idx < v_idx

    def test_ro_mount_has_colon_ro(self):
        args = self._build(
            image="img:test",
            command="echo hi",
            mounts=[{"source": "/src", "target": "/dst", "ro": True}],
        )
        v_idx = args.index("-v")
        assert args[v_idx + 1] == "/src:/dst:ro"

    def test_rw_mount_no_ro_suffix(self):
        args = self._build(
            image="img:test",
            command="echo hi",
            mounts=[{"source": "/src", "target": "/dst", "ro": False}],
        )
        v_idx = args.index("-v")
        assert args[v_idx + 1] == "/src:/dst"

    def test_mount_without_ro_key_is_rw(self):
        args = self._build(
            image="img:test",
            command="echo hi",
            mounts=[{"source": "/src", "target": "/dst"}],
        )
        v_idx = args.index("-v")
        assert args[v_idx + 1] == "/src:/dst"

    def test_workdir_flag_present(self):
        args = self._build(image="img:test", command="echo hi", workdir="/mywork")
        w_idx = args.index("-w")
        assert args[w_idx + 1] == "/mywork"

    def test_cpus_present_when_given(self):
        args = self._build(image="img:test", command="echo hi", cpus=4)
        assert "--cpus" in args
        assert args[args.index("--cpus") + 1] == "4"

    def test_cpus_absent_when_not_given(self):
        args = self._build(image="img:test", command="echo hi")
        assert "--cpus" not in args

    def test_memory_present_when_given(self):
        args = self._build(image="img:test", command="echo hi", memory="512m")
        assert "--memory" in args
        assert args[args.index("--memory") + 1] == "512m"

    def test_memory_absent_when_not_given(self):
        args = self._build(image="img:test", command="echo hi")
        assert "--memory" not in args

    def test_pids_present_when_given(self):
        args = self._build(image="img:test", command="echo hi", pids=256)
        assert "--pids-limit" in args
        assert args[args.index("--pids-limit") + 1] == "256"

    def test_pids_absent_when_not_given(self):
        args = self._build(image="img:test", command="echo hi")
        assert "--pids-limit" not in args

    def test_env_vars_emitted(self):
        args = self._build(image="img:test", command="echo hi", env={"FOO": "bar"})
        e_idx = args.index("-e")
        assert args[e_idx + 1] == "FOO=bar"

    def test_user_flag_emitted(self):
        args = self._build(image="img:test", command="echo hi", user="1000")
        u_idx = args.index("--user")
        assert args[u_idx + 1] == "1000"

    def test_user_absent_when_not_given(self):
        args = self._build(image="img:test", command="echo hi")
        assert "--user" not in args

    def test_image_precedes_sh_lc(self):
        args = self._build(image="docker.io/library/python:3.11-slim", command="echo hi")
        img_idx = args.index("docker.io/library/python:3.11-slim")
        sh_idx = args.index("sh")
        assert img_idx < sh_idx

    def test_command_is_last_element(self):
        cmd = "echo hello world"
        args = self._build(image="img:test", command=cmd)
        assert args[-1] == cmd

    def test_sh_lc_precedes_command(self):
        cmd = "echo hi"
        args = self._build(image="img:test", command=cmd)
        assert args[-3:] == ["sh", "-lc", cmd]

    def test_raises_on_missing_image(self):
        from hydra.container_sandbox import build_sandbox_run_args
        with pytest.raises(ValueError, match="image"):
            build_sandbox_run_args(image="", command="echo hi")

    def test_raises_on_none_image(self):
        from hydra.container_sandbox import build_sandbox_run_args
        with pytest.raises((ValueError, TypeError)):
            build_sandbox_run_args(image=None, command="echo hi")

    def test_raises_on_missing_command(self):
        from hydra.container_sandbox import build_sandbox_run_args
        with pytest.raises(ValueError, match="command"):
            build_sandbox_run_args(image="img:test", command="")

    def test_strict_arg_ordering(self):
        """Verify the full documented ordering: run --rm [--network none] [-v] -w [caps] [-e] [--user] image sh -lc cmd."""
        args = self._build(
            image="img:test",
            command="echo hi",
            network=False,
            mounts=[{"source": "/s", "target": "/t"}],
            workdir="/work",
            cpus=2,
            memory="1g",
            pids=512,
            env={"K": "V"},
            user="nobody",
        )
        # find positions of key elements
        pos = {x: args.index(x) for x in ["run", "--rm", "--network", "-v", "-w", "--cpus", "--memory", "--pids-limit", "-e", "--user", "img:test", "sh"]}
        assert pos["run"] < pos["--rm"]
        assert pos["--rm"] < pos["--network"]
        assert pos["--network"] < pos["-v"]
        assert pos["-v"] < pos["-w"]
        assert pos["-w"] < pos["--cpus"]
        assert pos["--cpus"] < pos["--memory"]
        assert pos["--memory"] < pos["--pids-limit"]
        assert pos["--pids-limit"] < pos["-e"]
        assert pos["-e"] < pos["--user"]
        assert pos["--user"] < pos["img:test"]
        assert pos["img:test"] < pos["sh"]


# ---------------------------------------------------------------------------
# run_in_sandbox
# ---------------------------------------------------------------------------

class TestRunInSandbox:
    def test_no_engine_returns_degrade_dict_never_calls_runner(self):
        """When no container engine is detected, returns error dict, never calls runner."""
        from hydra.container_sandbox import run_in_sandbox

        runner_mock = MagicMock()

        result = run_in_sandbox(
            command="echo hi",
            detect=lambda: None,
            runner=runner_mock,
        )

        runner_mock.assert_not_called()
        assert result["ok"] is False
        assert result["reason"] == "no-container-engine"
        assert result["engine"] is None
        assert result["exit_code"] is None
        assert result["stdout"] == ""
        assert result["stderr"] == ""
        assert result["timed_out"] is False

    def test_happy_path_fake_runner_returns_ok_true(self):
        """With a fake runner returning exit 0 + stdout 'OK', result is ok=True."""
        from hydra.container_sandbox import run_in_sandbox

        result = run_in_sandbox(
            command="echo OK",
            detect=lambda: "podman",
            runner=_fake_runner_ok,
        )

        assert result["ok"] is True
        assert result["stdout"] == "OK\n"
        assert result["stderr"] == ""
        assert result["exit_code"] == 0
        assert result["engine"] == "podman"
        assert result["timed_out"] is False

    def test_happy_path_engine_in_argv(self):
        """The runner receives [engine, 'run', '--rm', ..., image, 'sh', '-lc', command]."""
        from hydra.container_sandbox import run_in_sandbox

        captured = {}

        def capturing_runner(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        run_in_sandbox(
            command="echo hi",
            detect=lambda: "podman",
            runner=capturing_runner,
        )

        assert captured["argv"][0] == "podman"
        assert captured["argv"][1] == "run"
        assert captured["argv"][2] == "--rm"
        assert "sh" in captured["argv"]
        assert captured["argv"][-1] == "echo hi"

    def test_defaults_applied_for_image_cpus_memory_pids(self):
        """SANDBOX_DEFAULTS fill in when caller omits image/cpus/memory/pids."""
        from hydra.container_sandbox import run_in_sandbox, SANDBOX_DEFAULTS

        captured = {}

        def capturing_runner(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        run_in_sandbox(
            command="echo hi",
            detect=lambda: "podman",
            runner=capturing_runner,
        )

        argv_str = " ".join(captured["argv"])
        assert SANDBOX_DEFAULTS["image"] in argv_str
        assert str(SANDBOX_DEFAULTS["cpus"]) in argv_str
        assert SANDBOX_DEFAULTS["memory"] in argv_str
        assert str(SANDBOX_DEFAULTS["pids"]) in argv_str

    def test_cwd_mount_prepended_rw(self):
        """cwd_mount prepends a rw mount so the agent can write inside the container."""
        from hydra.container_sandbox import run_in_sandbox

        captured = {}

        def capturing_runner(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        run_in_sandbox(
            command="echo hi",
            cwd_mount="/my/project",
            detect=lambda: "podman",
            runner=capturing_runner,
        )

        argv_str = " ".join(captured["argv"])
        # The mount must be present and NOT read-only
        assert "/my/project" in argv_str
        # Must NOT have ":ro" for the cwd mount
        v_idx = captured["argv"].index("-v")
        cwd_mount_spec = captured["argv"][v_idx + 1]
        assert "/my/project" in cwd_mount_spec
        assert ":ro" not in cwd_mount_spec

    def test_non_zero_exit_returns_ok_false(self):
        """A fake runner returning exit 1 gives ok=False."""
        from hydra.container_sandbox import run_in_sandbox

        result = run_in_sandbox(
            command="false",
            detect=lambda: "podman",
            runner=_fake_runner_fail,
        )

        assert result["ok"] is False
        assert result["exit_code"] == 1
        assert result["timed_out"] is False

    def test_engine_override_used_when_supplied(self):
        """Passing engine= directly bypasses detect entirely."""
        from hydra.container_sandbox import run_in_sandbox

        detect_mock = MagicMock(return_value="docker")
        captured = {}

        def capturing_runner(argv, **kwargs):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        run_in_sandbox(
            command="echo hi",
            engine="podman",
            detect=detect_mock,
            runner=capturing_runner,
        )

        detect_mock.assert_not_called()
        assert captured["argv"][0] == "podman"

    def test_timeout_triggers_timed_out_flag(self):
        """When runner raises TimeoutExpired, result has timed_out=True, ok=False."""
        from hydra.container_sandbox import run_in_sandbox

        def timeout_runner(argv, **kwargs):
            raise subprocess.TimeoutExpired(cmd=argv, timeout=0.1)

        result = run_in_sandbox(
            command="sleep 999",
            detect=lambda: "podman",
            runner=timeout_runner,
            timeout=0.1,
        )

        assert result["timed_out"] is True
        assert result["ok"] is False
        assert result["reason"] == "timeout"
