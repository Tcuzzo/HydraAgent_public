"""hydra update — pull the latest Hydra straight from GitHub, in one command.

`hydra update` reinstalls Hydra (and its dependencies) from the public repo's
latest commit using the current interpreter's pip — so a client just runs one
command to get the newest version. Use `--check` to see the install command
without running it, or `--repo` to update from a fork.
"""
from __future__ import annotations

import argparse
import importlib.metadata as _md
import subprocess
import sys

DEFAULT_REPO_URL = "https://github.com/Tcuzzo/HydraAgent_public.git"


def build_update_command(repo_url: str) -> list[str]:
    """The pip command that force-reinstalls Hydra from the repo's latest commit.

    `--force-reinstall` is required because the package version is stable, so a
    plain `-U` would consider an unchanged version 'already satisfied' and skip a
    newer commit.
    """
    spec = repo_url if repo_url.startswith("git+") else f"git+{repo_url}"
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", spec]


def register_update_command(sub) -> None:
    p = sub.add_parser("update", help="update Hydra to the latest version from GitHub (one command)")
    p.add_argument("--repo", default=DEFAULT_REPO_URL, help="repo URL to update from (default: the public repo)")
    p.add_argument("--check", action="store_true", help="print the current version + update command without installing")


def _installed_version() -> str:
    try:
        return _md.version("hydraagent")
    except _md.PackageNotFoundError:
        return "unknown (running from a source checkout)"


def cmd_update(args: argparse.Namespace) -> int:
    cmd = build_update_command(args.repo)
    current = _installed_version()

    if args.check:
        print(f"Hydra {current} installed.")
        print("Update with:  " + " ".join(cmd))
        return 0

    print(f"Updating Hydra from {args.repo}\n(current: {current}) ...", flush=True)
    rc = subprocess.call(cmd)
    if rc != 0:
        print("hydra update: failed — run the pip command above manually, or check your network.",
              file=sys.stderr)
        return rc
    print("\nUpdated to the latest from GitHub. Run `hydra doctor` to check dependencies.", flush=True)
    return 0
