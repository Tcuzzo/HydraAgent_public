"""Read-only local system and runtime statistics for HydraAgent."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def run() -> dict[str, Any]:
    """Return bounded, read-only local machine statistics."""
    return {
        "ok": True,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "load_average": _load_average(),
        "memory": _meminfo(),
        "disk": _disk_usage("/"),
        "processes": _process_count(),
        "gpu": _nvidia_smi(),
    }


def _load_average() -> dict[str, float] | None:
    if not hasattr(os, "getloadavg"):
        return None
    one, five, fifteen = os.getloadavg()
    return {"one_minute": one, "five_minutes": five, "fifteen_minutes": fifteen}


def _meminfo() -> dict[str, int]:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return {}
    out: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, _, value = raw.partition(":")
        if key not in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            continue
        number = value.strip().split()[0]
        try:
            out[key] = int(number) * 1024
        except ValueError:
            continue
    return out


def _disk_usage(path: str) -> dict[str, int]:
    usage = shutil.disk_usage(path)
    return {"total": usage.total, "used": usage.used, "free": usage.free}


def _process_count() -> int | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    return sum(1 for child in proc.iterdir() if child.name.isdigit())


def _nvidia_smi() -> list[dict[str, str]]:
    binary = shutil.which("nvidia-smi")
    if not binary:
        return []
    command = [
        binary,
        "--query-gpu=name,memory.used,memory.total,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    out: list[dict[str, str]] = []
    for raw in completed.stdout.splitlines():
        parts = [part.strip() for part in raw.split(",")]
        if len(parts) != 4:
            continue
        out.append(
            {
                "name": parts[0],
                "memory_used_mb": parts[1],
                "memory_total_mb": parts[2],
                "utilization_percent": parts[3],
            }
        )
    return out
