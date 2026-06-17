"""Start the Telegram approval listener for simple Hydra launches."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from gateways.telegram.live import HYDRA_CHAT_ID_ENV_VAR, HYDRA_TOKEN_ENV_VAR, telegram_env


START_SCHEMA = "hydra.telegram.listener_start.v1"


def start_telegram_listener_if_configured(
    *,
    repo_root: Path,
    process_env: dict[str, str] | None = None,
    interval: float = 3.0,
    popen: Callable[..., Any] = subprocess.Popen,
    is_pid_alive: Callable[[int], bool] | None = None,
    pid_cmdline: Callable[[int], str] | None = None,
    listener_pids: Callable[[], list[int]] | None = None,
    systemd_active: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    source = telegram_env(repo_root=repo_root, process_env=process_env)
    if not source.get(HYDRA_TOKEN_ENV_VAR, "").strip() or not source.get(HYDRA_CHAT_ID_ENV_VAR, "").strip():
        return {
            "schema": START_SCHEMA,
            "status": "skipped",
            "reason": f"{HYDRA_TOKEN_ENV_VAR} or {HYDRA_CHAT_ID_ENV_VAR} is not set",
        }
    # DEFER TO SYSTEMD (operator: "telegram gateway shouldn't break on TUI restart").
    # If the hydra-telegram.service is already running it IS the
    # robust gateway (Restart=always, survives TUI restarts) — do NOT spawn a second
    # subprocess listener: two getUpdates consumers on one bot token => Telegram 409
    # conflict, which kills approval-button polling. One owner only.
    if (systemd_active or _systemd_listener_active)():
        return {
            "schema": START_SCHEMA,
            "status": "already_running",
            "source": "systemd",
            "unit": "hydra-telegram.service",
        }
    pid_path = repo_root / "evidence" / "telegram" / "listener.pid"
    log_path = repo_root / "evidence" / "telegram" / "listener.log"
    lock_path = repo_root / "evidence" / "telegram" / "listener.lock"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_lock(lock_path)
    if lock_fd is None:
        return {
            "schema": START_SCHEMA,
            "status": "already_starting",
            "pid_path": str(pid_path),
            "log_path": str(log_path),
        }
    try:
        return _start_listener_locked(
            repo_root=repo_root,
            source=source,
            interval=interval,
            popen=popen,
            is_pid_alive=is_pid_alive or _pid_alive,
            pid_cmdline=pid_cmdline or _pid_cmdline,
            listener_pids=listener_pids or _listener_pids,
            pid_path=pid_path,
            log_path=log_path,
        )
    finally:
        _release_lock(lock_fd, lock_path)


def _start_listener_locked(
    *,
    repo_root: Path,
    source: dict[str, str],
    interval: float,
    popen: Callable[..., Any],
    is_pid_alive: Callable[[int], bool],
    pid_cmdline: Callable[[int], str],
    listener_pids: Callable[[], list[int]],
    pid_path: Path,
    log_path: Path,
) -> dict[str, Any]:
    existing_pid = _read_pid(pid_path)
    if existing_pid is not None and is_pid_alive(existing_pid) and _looks_like_listener(pid_cmdline(existing_pid)):
        return {
            "schema": START_SCHEMA,
            "status": "already_running",
            "pid": existing_pid,
            "pid_path": str(pid_path),
            "log_path": str(log_path),
        }
    for pid in listener_pids():
        if (
            pid != os.getpid()
            and is_pid_alive(pid)
            and _looks_like_listener(pid_cmdline(pid))
        ):
            pid_path.write_text(f"{pid}\n", encoding="utf-8")
            return {
                "schema": START_SCHEMA,
                "status": "already_running",
                "pid": pid,
                "pid_path": str(pid_path),
                "log_path": str(log_path),
                "source": "process_scan",
            }
    command = [sys.executable, "-m", "hydra", "telegram", "listen", "--interval", str(float(interval))]
    log_handle = log_path.open("a", encoding="utf-8")
    env = dict(os.environ)
    env.update(source)
    try:
        process = popen(
            command,
            cwd=repo_root,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    log_handle.close()
    pid_path.write_text(f"{process.pid}\n", encoding="utf-8")
    return {
        "schema": START_SCHEMA,
        "status": "started",
        "pid": process.pid,
        "proc": process,
        "pid_path": str(pid_path),
        "log_path": str(log_path),
    }


def _systemd_listener_active(
    run: Callable[..., Any] = subprocess.run,
) -> bool:
    """True if the hydra-telegram.service systemd user unit is active.

    Best-effort: on machines without systemd (or where the unit is absent) this
    returns False and the caller falls back to the subprocess listener. Never raises.
    """
    try:
        result = run(
            ["systemctl", "--user", "is-active", "hydra-telegram.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return (getattr(result, "stdout", "") or "").strip() == "active"


def _acquire_lock(path: Path) -> int | None:
    try:
        return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None


def _release_lock(fd: int, path: Path) -> None:
    os.close(fd)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _looks_like_listener(cmdline: str) -> bool:
    parts = cmdline.split()
    if len(parts) < 5:
        return False
    executable = Path(parts[0]).name
    return (
        executable.startswith("python")
        and parts[1:5] == ["-m", "hydra", "telegram", "listen"]
    )


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()


def _listener_pids() -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if _looks_like_listener(_pid_cmdline(pid)):
            pids.append(pid)
    return pids
