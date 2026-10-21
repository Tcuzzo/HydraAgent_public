#!/usr/bin/env python3
"""Install Hydra, Needle and official Kev into an owned, isolated CPU environment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


SCHEMA = "hydra.local-install.v1"


def publish(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".hydra-install-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.home() / ".hydraAgent" / "local-runtime")
    parser.add_argument("--skip-download", action="store_true", help="install dependencies only; explicitly leave model readiness unverified")
    args = parser.parse_args(argv)
    if not (3, 12) <= sys.version_info[:2] < (3, 14):
        parser.error("run this installer with Python 3.12 or 3.13 (the official Kev SDK requires it)")
    source = Path(__file__).resolve().parents[1]
    target = args.directory.expanduser().resolve()
    if target == source or source in target.parents:
        parser.error("choose an installation directory outside the source checkout")
    marker = target / "install.json"
    try:
        if target.exists() and any(target.iterdir()):
            if not marker.is_file() or json.loads(marker.read_text()).get("schema") != SCHEMA:
                parser.error(f"{target} already contains unrelated files; select a new directory")
        target.mkdir(parents=True, exist_ok=True)
        receipt = {"schema": SCHEMA, "status": "installing", "python": sys.version.split()[0],
                   "source": "https://github.com/Tcuzzo/HydraAgent_public",
                   "started_at": datetime.now(timezone.utc).isoformat(), "completed_steps": []}
        publish(marker, receipt)
        environment = target / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        commands = [
            ("CPU PyTorch", [str(python), "-m", "pip", "install", "torch==2.8.0", "--index-url", "https://download.pytorch.org/whl/cpu"]),
            ("Hydra, Needle, Kev and vector memory", [str(python), "-m", "pip", "install", f"{source}[intelligence]", "-c", str(source / "constraints.txt")]),
        ]
        for title, command in commands:
            print(f"Installing {title}...", flush=True)
            subprocess.run(command, check=True, timeout=1800)
            receipt["completed_steps"].append(title)
            publish(marker, receipt)
        command = [str(python), "-m", "hydra", "init", "--state-dir", str(target / "state")]
        if args.skip_download:
            command.append("--check")
        subprocess.run(command, check=True, timeout=1500)
        receipt["status"] = "dependencies_installed" if args.skip_download else "ready"
        receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
        publish(marker, receipt)
        executable = environment / ("Scripts/hydra.exe" if os.name == "nt" else "bin/hydra")
        print(f"\nHydra command: {executable}\nBot catalog: {executable} team list")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if target.is_dir() and marker.is_file():
            try:
                current = json.loads(marker.read_text())
                if current.get("schema") == SCHEMA:
                    current.update(status="incomplete", error=type(exc).__name__)
                    publish(marker, current)
            except (OSError, ValueError):
                pass
        print(f"Installation incomplete: {exc}. Re-run the same command to resume this owned environment.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
