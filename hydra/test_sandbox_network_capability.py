"""A usable filesystem sandbox does not imply usable network namespaces."""
from __future__ import annotations

import subprocess

import pytest

from hydra import exec_backend as backend


@pytest.fixture
def filesystem_only_bwrap(monkeypatch):
    calls = []
    backend._reset_capability_cache()
    monkeypatch.setattr(backend, "_bwrap_available", lambda: True)
    monkeypatch.setattr(backend.shutil, "which", lambda _: "/test/bwrap")

    def run(argv, **kwargs):
        calls.append(list(argv))
        blocked = "--unshare-net" in argv
        return subprocess.CompletedProcess(
            argv, 1 if blocked else 0,
            stdout="network namespace unavailable" if blocked else "verified",
        )

    monkeypatch.setattr(backend.subprocess, "run", run)
    yield calls
    backend._reset_capability_cache()


def test_no_network_payload_is_refused_before_launch(tmp_path, filesystem_only_bwrap):
    result = backend.run_sandboxed(["private-verification-payload"], workspace=tmp_path)

    assert result.returncode == 126
    assert result.sandboxed is False
    assert "not executed" in result.stdout
    assert "network" in result.stdout.lower()
    assert all("private-verification-payload" not in argv for argv in filesystem_only_bwrap)


def test_explicit_shared_network_keeps_filesystem_confinement(tmp_path, filesystem_only_bwrap):
    result = backend.run_sandboxed(["shared-network-payload"], workspace=tmp_path, network=True)

    assert result.returncode == 0
    assert result.sandboxed is True
    assert any("shared-network-payload" in argv for argv in filesystem_only_bwrap)
    assert all("--unshare-net" not in argv for argv in filesystem_only_bwrap)


def test_network_capability_is_cached_until_reset(tmp_path, filesystem_only_bwrap):
    for _ in range(2):
        result = backend.run_sandboxed(["never-launch"], workspace=tmp_path)
        assert result.returncode == 126
    assert len(filesystem_only_bwrap) == 1

    backend._reset_capability_cache()
    result = backend.run_sandboxed(["never-launch"], workspace=tmp_path)
    assert result.returncode == 126
    assert len(filesystem_only_bwrap) == 2
