"""Tests for hydra.cli.cmd_update — the one-command self-updater."""
from __future__ import annotations

import sys

from hydra.cli.cmd_update import DEFAULT_REPO_URL, build_update_command


def test_build_update_command_forces_latest_from_git():
    cmd = build_update_command("https://github.com/X/Y.git")
    assert cmd == [
        sys.executable, "-m", "pip", "install",
        "--upgrade", "--force-reinstall",
        "git+https://github.com/X/Y.git",
    ]


def test_build_update_command_does_not_double_prefix_git():
    cmd = build_update_command("git+https://github.com/X/Y.git")
    assert cmd[-1] == "git+https://github.com/X/Y.git"


def test_default_repo_points_at_the_public_repo():
    assert DEFAULT_REPO_URL.startswith("https://github.com/")
    assert DEFAULT_REPO_URL.endswith(".git")
    assert "HydraAgent_public" in DEFAULT_REPO_URL
