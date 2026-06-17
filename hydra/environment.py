"""Hydra-native local environment lifecycle."""
from __future__ import annotations

import datetime as _dt
import difflib
import ipaddress
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import urllib.request


SESSION_SCHEMA = "hydra.environment.session.v1"
COMMAND_SCHEMA = "hydra.environment.command.v1"
FILE_READ_SCHEMA = "hydra.environment.file_read.v1"
FILE_WRITE_SCHEMA = "hydra.environment.file_write.v1"
BROWSER_FETCH_SCHEMA = "hydra.environment.browser_fetch.v1"


class EnvironmentError(Exception):
    """Operator-facing environment lifecycle failure."""


def create_session(
    *,
    source_repo: str | Path,
    env_root: str | Path,
    title: str,
) -> dict[str, Any]:
    source = Path(source_repo).expanduser().resolve()
    if not source.is_dir():
        raise EnvironmentError(f"source repo is not a directory: {source}")
    if not title.strip():
        raise EnvironmentError("title must be non-empty")
    root = Path(env_root).expanduser().resolve()
    session_id = _session_id(title)
    session_path = root / session_id
    suffix = 1
    while session_path.exists():
        suffix += 1
        session_path = root / f"{session_id}-{suffix}"
    workspace = session_path / "workspace"
    session_path.mkdir(parents=True)
    shutil.copytree(
        source,
        workspace,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "node_modules"),
        symlinks=False,
    )
    session = {
        "schema": SESSION_SCHEMA,
        "session_id": session_path.name,
        "title": " ".join(title.split()),
        "source_repo": str(source),
        "session_path": str(session_path),
        "workspace": str(workspace),
        "created_at": _now(),
        "state": "ready",
        "capabilities": ["shell", "workspace", "evidence"],
    }
    _write_json(session_path / "session.json", session)
    return session


def run_session_command(
    env_root: str | Path,
    session_id: str,
    command: list[str],
    *,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    session = session_status(env_root, session_id)
    workspace = Path(session["workspace"])
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        raise EnvironmentError("command must be a non-empty list of strings")
    started = _now()
    proc = subprocess.run(
        command,
        cwd=workspace,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    packet = {
        "schema": COMMAND_SCHEMA,
        "session_id": session_id,
        "command": command,
        "status": "ok" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "timeout_seconds": timeout_seconds,
        "started_at": started,
        "finished_at": _now(),
    }
    commands_dir = Path(session["session_path"]) / "commands"
    index = len(list(commands_dir.glob("*.json"))) + 1 if commands_dir.is_dir() else 1
    _write_json(commands_dir / f"{index:04d}.json", packet)
    return packet


def read_session_file(
    env_root: str | Path,
    session_id: str,
    relative_path: str,
    *,
    max_chars: int = 20000,
) -> dict[str, Any]:
    session = session_status(env_root, session_id)
    path = _workspace_path(session, relative_path)
    if not path.is_file():
        raise EnvironmentError(f"file not found in session workspace: {relative_path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    packet = {
        "schema": FILE_READ_SCHEMA,
        "session_id": session_id,
        "path": relative_path,
        "content": text[:max_chars],
        "truncated": len(text) > max_chars,
        "created_at": _now(),
    }
    _write_file_event(session, packet)
    return packet


def write_session_file(
    env_root: str | Path,
    session_id: str,
    relative_path: str,
    content: str,
) -> dict[str, Any]:
    session = session_status(env_root, session_id)
    path = _workspace_path(session, relative_path)
    before = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
        )
    )
    packet = {
        "schema": FILE_WRITE_SCHEMA,
        "session_id": session_id,
        "path": relative_path,
        "diff": diff,
        "created_at": _now(),
    }
    _write_file_event(session, packet)
    return packet


def _is_ip_allowed(host: str) -> tuple[bool, str | None]:
    """Check if a host resolves to allowed (public) IPs only.
    
    Returns (True, None) if all resolved IPs are public.
    Returns (False, reason) if any IP is private/internal.
    """
    try:
        addr_info = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror as e:
        return False, f"DNS resolution failed: {e}"
    
    for family, _, _, _, sockaddr in addr_info:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        
        # Check for private/internal ranges
        if ip.is_loopback:
            return False, f"loopback address {ip_str}"
        if ip.is_link_local:
            return False, f"link-local address {ip_str}"
        if ip.is_private:
            return False, f"private address {ip_str}"
        if ip.is_multicast:
            return False, f"multicast address {ip_str}"
        # Check for 169.254.0.0/16 explicitly (link-local for IPv4)
        if isinstance(ip, ipaddress.IPv4Address):
            if ip in ipaddress.ip_network("169.254.0.0/16"):
                return False, f"link-local metadata address {ip_str}"
    
    return True, None


def fetch_session_url(
    env_root: str | Path,
    session_id: str,
    url: str,
    *,
    timeout_seconds: int = 20,
    max_chars: int = 50000,
) -> dict[str, Any]:
    # SSRF guard: resolve and reject private/internal IPs BEFORE any network activity
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        raise EnvironmentError("invalid URL: missing host")
    allowed, reason = _is_ip_allowed(host)
    if not allowed:
        raise EnvironmentError(f"refusing to fetch private/internal address: {host} ({reason})")
    
    session = session_status(env_root, session_id)
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise EnvironmentError("url must start with http:// or https://")
    request = urllib.request.Request(url, headers={"User-Agent": "HydraAgent/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(max_chars + 1)
        text = raw[:max_chars].decode("utf-8", errors="replace")
        packet = {
            "schema": BROWSER_FETCH_SCHEMA,
            "session_id": session_id,
            "url": url,
            "status_code": getattr(response, "status", 0),
            "content_type": response.headers.get("Content-Type", ""),
            "text": text,
            "truncated": len(raw) > max_chars,
            "created_at": _now(),
        }
    _write_browser_event(session, packet)
    return packet


def session_status(env_root: str | Path, session_id: str) -> dict[str, Any]:
    root = Path(env_root).expanduser().resolve()
    if "/" in session_id or "\\" in session_id or ".." in session_id:
        raise EnvironmentError("session_id must be a simple path segment")
    session_path = root / session_id
    path = session_path / "session.json"
    if not path.is_file():
        raise EnvironmentError(f"session not found: {session_id}")
    try:
        session = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise EnvironmentError(f"invalid session packet: {path}") from e
    commands_dir = session_path / "commands"
    command_packets = sorted(commands_dir.glob("*.json")) if commands_dir.is_dir() else []
    files_dir = session_path / "files"
    file_events = sorted(files_dir.glob("*.json")) if files_dir.is_dir() else []
    browser_dir = session_path / "browser"
    browser_events = sorted(browser_dir.glob("*.json")) if browser_dir.is_dir() else []
    return {
        **session,
        "command_count": len(command_packets),
        "command_packets": [str(path) for path in command_packets],
        "file_event_count": len(file_events),
        "file_events": [str(path) for path in file_events],
        "browser_event_count": len(browser_events),
        "browser_events": [str(path) for path in browser_events],
    }


def _workspace_path(session: dict[str, Any], relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise EnvironmentError("relative_path must be a non-empty string")
    if Path(relative_path).is_absolute():
        raise EnvironmentError("relative_path must stay inside the workspace")
    workspace = Path(session["workspace"]).resolve()
    path = (workspace / relative_path).resolve()
    try:
        path.relative_to(workspace)
    except ValueError as e:
        raise EnvironmentError("relative_path must stay inside the workspace") from e
    return path


def _write_file_event(session: dict[str, Any], packet: dict[str, Any]) -> None:
    events_dir = Path(session["session_path"]) / "files"
    index = len(list(events_dir.glob("*.json"))) + 1 if events_dir.is_dir() else 1
    _write_json(events_dir / f"{index:04d}.json", packet)


def _write_browser_event(session: dict[str, Any], packet: dict[str, Any]) -> None:
    events_dir = Path(session["session_path"]) / "browser"
    index = len(list(events_dir.glob("*.json"))) + 1 if events_dir.is_dir() else 1
    _write_json(events_dir / f"{index:04d}.json", packet)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _session_id(title: str) -> str:
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = "".join(ch if ch.isalnum() else "-" for ch in title.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:48] or "session"
    return f"{stamp}-{slug}"
