"""Start the Discord approval listener for Hydra launches."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


START_SCHEMA = "hydra.discord.listener_start.v1"


def start_discord_listener_if_configured(
    *,
    repo_root: Path,
    process_env: dict[str, str] | None = None,
    interval: float = 3.0,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    """Start Discord listener if configured.
    
    Mirrors Telegram listener startup.
    """
    source = discord_env(repo_root=repo_root, process_env=process_env)
    if not source.get("HYDRA_DISCORD_BOT_TOKEN", "").strip():
        return {
            "schema": START_SCHEMA,
            "status": "skipped",
            "reason": "HYDRA_DISCORD_BOT_TOKEN is not set",
        }
    
    pid_path = repo_root / "evidence" / "discord" / "listener.pid"
    log_path = repo_root / "evidence" / "discord" / "listener.log"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    
    command = [sys.executable, "-m", "hydra", "discord", "listen", "--interval", str(float(interval))]
    log_handle = log_path.open("a", encoding="utf-8")
    env = dict(os.environ)
    env.update(source)
    
    proc = popen(
        command,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=str(repo_root),
    )
    
    pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    
    return {
        "schema": START_SCHEMA,
        "status": "started",
        "pid": proc.pid,
        "pid_path": str(pid_path),
        "log_path": str(log_path),
    }


def discord_env(repo_root: Path, process_env: dict[str, str] | None = None) -> dict[str, str]:
    """Load Discord config from .env.discord.
    
    Mirrors telegram_env implementation.
    """
    env_file = repo_root / ".env.discord"
    if not env_file.exists():
        return {}
    
    source: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        source[key.strip()] = value.strip()
    
    if process_env:
        source.update(process_env)
    
    return source
