"""Locate operator role configuration independently of the installed package."""
from __future__ import annotations

import os
from pathlib import Path


def role_config_path(
    config_path: str | Path | None = None,
    *,
    workspace: str | Path | None = None,
) -> Path | None:
    """Choose an explicit override, workspace config, or existing user config.

    Explicit paths are returned even when missing so the loader can report the
    operator's error. No discovered file means the packaged routing defaults
    apply. Discovery runs at call time, including after a workspace change.
    """
    if config_path is not None:
        return Path(config_path).expanduser()
    override = os.environ.get("HYDRA_CONFIG")
    if override:
        return Path(override).expanduser()
    root = Path(workspace).expanduser() if workspace is not None else Path.cwd()
    for candidate in (
        root / ".hydraAgent" / "hydra.yaml",
        root / "hydra.yaml",
        Path.home() / ".hydra" / "hydra.yaml",
    ):
        # A directory or broken link is a bad config, not permission to ignore
        # the operator's selection and silently use a lower-priority source.
        if candidate.exists() or candidate.is_symlink():
            return candidate
    return None
