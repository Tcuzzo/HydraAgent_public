"""Shared local GPU lease for HydraAgent local Ollama users."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, MutableMapping


DEFAULT_GPU_LEASE_PATH = Path.home() / ".cache" / "gpu.lease"


@dataclass(frozen=True)
class GpuLease:
    acquired: bool
    lease_path: Path
    holder: Mapping[str, object] | None
    reason: str | None = None


def acquire_gpu_lease(
    *,
    lease_path: str | Path | None = None,
    runtime_id: str = "hydra",
    ttl_seconds: int = 15 * 60,
    now: Callable[[], datetime] | None = None,
) -> GpuLease:
    resolved = Path(lease_path or os.environ.get("GPU_LEASE_PATH") or DEFAULT_GPU_LEASE_PATH).expanduser()
    current = (now or (lambda: datetime.now(UTC)))()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    payload: MutableMapping[str, object] = {
        "runtimeId": runtime_id or "hydra",
        "pid": os.getpid(),
        "createdAt": _iso(current),
        "expiresAt": _iso(current + timedelta(seconds=ttl_seconds)),
    }
    resolved.parent.mkdir(parents=True, exist_ok=True)

    for _attempt in range(2):
        try:
            fd = os.open(resolved, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            holder = read_gpu_lease(resolved)
            if _lease_expired(holder, current):
                try:
                    resolved.unlink()
                    continue
                except FileNotFoundError:
                    continue
            return GpuLease(False, resolved, holder, "held")
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            return GpuLease(True, resolved, dict(payload))

    holder = read_gpu_lease(resolved)
    return GpuLease(False, resolved, holder, "held")


def release_gpu_lease(lease: GpuLease) -> bool:
    if not lease.acquired or not lease.holder:
        return False
    holder = read_gpu_lease(lease.lease_path)
    if holder.get("pid") != lease.holder.get("pid") or holder.get("runtimeId") != lease.holder.get("runtimeId"):
        return False
    try:
        lease.lease_path.unlink()
        return True
    except FileNotFoundError:
        return False


def read_gpu_lease(lease_path: str | Path | None = None) -> dict[str, object]:
    resolved = Path(lease_path or os.environ.get("GPU_LEASE_PATH") or DEFAULT_GPU_LEASE_PATH).expanduser()
    try:
        return json.loads(resolved.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _lease_expired(holder: Mapping[str, object], now: datetime) -> bool:
    expires_at = holder.get("expiresAt")
    if not isinstance(expires_at, str):
        return True
    try:
        expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expires <= now


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
