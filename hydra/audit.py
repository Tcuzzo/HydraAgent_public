"""Deterministic runtime audit for local directories.

This module is intentionally boring: no LLM, no mutation, no secret
contents. It gathers filesystem evidence that an operator can trust as a
starting point before deciding what deeper audit work is needed.
"""
from __future__ import annotations

import datetime as _dt
import os
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MAX_FILES = 5000
DEFAULT_MAX_FINDINGS = 40

SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
}

INTERESTING_NAMES = {
    "readme",
    "readme.md",
    "readme.rst",
    "readme.txt",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-test.txt",
    "poetry.lock",
    "uv.lock",
    "pipfile",
    "pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
}

LOCKFILE_NAMES = {
    "poetry.lock",
    "uv.lock",
    "pipfile.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "go.sum",
}

SECRET_NAME_MARKERS = (
    "secret",
    "token",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "apikey",
    "api_key",
    "access_key",
    "id_rsa",
)

SECRET_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
LOG_SUFFIXES = (".log", ".out", ".err", ".trace")


@dataclass(frozen=True)
class AuditResult:
    """Plain report plus structured evidence."""

    status: str
    report: str
    data: dict[str, Any]


def _resolve_target(target: str | Path, root: str | Path | None) -> Path:
    requested = Path(target).expanduser()
    if requested.is_absolute():
        return requested.resolve(strict=False)
    base = Path(root).expanduser().resolve(strict=False) if root else Path.cwd().resolve()
    return (base / requested).resolve(strict=False)


def _contains(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def _entry_type(path: Path) -> str:
    try:
        if path.is_symlink():
            return "symlink"
        if path.is_dir():
            return "dir"
        if path.is_file():
            return "file"
    except OSError:
        return "unknown"
    return "other"


def _safe_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except OSError:
        return None


def _extension(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix else "[no extension]"


def _is_env_like(name: str) -> bool:
    lower = name.lower()
    return lower == ".env" or lower.startswith(".env.") or lower.endswith(".env")


def _is_interesting(path: Path) -> bool:
    lower = path.name.lower()
    return (
        lower in INTERESTING_NAMES
        or _is_env_like(path.name)
    )


def _is_secret_filename(path: Path) -> bool:
    lower = path.name.lower()
    return (
        _is_env_like(path.name)
        or any(marker in lower for marker in SECRET_NAME_MARKERS)
        or lower.endswith(SECRET_SUFFIXES)
    )


def _is_logish(path: Path) -> bool:
    lower = path.name.lower()
    return lower.endswith(LOG_SUFFIXES) or "log" in [part.lower() for part in path.parts]


def _top_level_entries(target: Path, max_findings: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: p.name.lower())
    except OSError as e:
        return [{"name": "<blocked>", "type": "error", "error": str(e)}]
    for child in children[:max_findings]:
        st = _safe_stat(child)
        entries.append(
            {
                "name": child.name,
                "type": _entry_type(child),
                "size": st.st_size if st else None,
            }
        )
    if len(children) > max_findings:
        entries.append(
            {
                "name": f"... {len(children) - max_findings} more",
                "type": "truncated",
                "size": None,
            }
        )
    return entries


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _scan_files(target: Path, max_files: int, max_findings: int) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    interesting: list[str] = []
    secret_names: list[str] = []
    logs: list[dict[str, Any]] = []
    command_hint_markers: set[str] = set()
    errors: list[str] = []
    scanned = 0
    truncated = False

    def _on_walk_error(err: OSError) -> None:
        errors.append(str(err))

    for dirpath, dirnames, filenames in os.walk(
        target,
        followlinks=False,
        onerror=_on_walk_error,
    ):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        filenames = sorted(filenames)
        current = Path(dirpath)
        for filename in filenames:
            path = current / filename
            scanned += 1
            if scanned > max_files:
                truncated = True
                break
            rel = _rel(path, target)
            lower_name = path.name.lower()
            counts[_extension(path)] += 1
            if lower_name in {"package.json", "pyproject.toml", "requirements.txt", "makefile", "go.mod", "cargo.toml"}:
                command_hint_markers.add(lower_name)
            if path.suffix.lower() == ".py":
                command_hint_markers.add("*.py")
            if _is_interesting(path) and len(interesting) < max_findings:
                interesting.append(rel)
            if _is_secret_filename(path) and len(secret_names) < max_findings:
                secret_names.append(rel)
            if _is_logish(path) and len(logs) < max_findings:
                st = _safe_stat(path)
                logs.append(
                    {
                        "path": rel,
                        "size": st.st_size if st else None,
                        "mtime": (
                            _dt.datetime.fromtimestamp(st.st_mtime, _dt.timezone.utc)
                            .isoformat(timespec="seconds")
                            if st
                            else None
                        ),
                    }
                )
        if truncated:
            break
    logs.sort(key=lambda row: row.get("mtime") or "", reverse=True)
    return {
        "total_files_seen": scanned - 1 if truncated else scanned,
        "file_counts_by_extension": dict(sorted(counts.items())),
        "interesting_files": interesting,
        "suspicious_secret_filenames": secret_names,
        "recent_log_files": logs[:max_findings],
        "command_hint_markers": sorted(command_hint_markers),
        "scan_truncated": truncated,
        "scan_errors": errors,
    }


def _git_status(target: Path, max_findings: int) -> dict[str, Any]:
    if not (target / ".git").exists():
        return {"is_repo": False, "status": []}
    try:
        proc = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(target),
                "status",
                "--short",
                "--branch",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8,
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"is_repo": True, "status": [], "error": str(e)}
    lines = (proc.stdout or proc.stderr).splitlines()
    return {
        "is_repo": True,
        "status": lines[:max_findings],
        "exit_code": proc.returncode,
        "truncated": len(lines) > max_findings,
    }


def _command_hints(files: list[str]) -> list[str]:
    names = {Path(f).name.lower() for f in files}
    hints: list[str] = []
    if "package.json" in names:
        hints.extend(["npm test", "npm run build"])
    if "pyproject.toml" in names or "*.py" in names or any(f.endswith(".py") for f in files):
        hints.append("python3 -m pytest -q")
    if "requirements.txt" in names:
        hints.append("python3 -m pip install -r requirements.txt")
    if "makefile" in names:
        hints.append("make test")
    if "go.mod" in names:
        hints.append("go test ./...")
    if "cargo.toml" in names:
        hints.append("cargo test")
    return list(dict.fromkeys(hints))


def _render_list(title: str, rows: list[str], none_text: str = "none found") -> list[str]:
    out = [f"{title}:"]
    if not rows:
        out.append(f"  - {none_text}")
        return out
    out.extend(f"  - {row}" for row in rows)
    return out


def _render_report(data: dict[str, Any]) -> str:
    lines = [
        "Hydra deterministic audit",
        f"target: {data['target']}",
        f"status: {data['status']}",
        f"exists: {'yes' if data['exists'] else 'no'}",
        f"is_dir: {'yes' if data['is_dir'] else 'no'}",
    ]
    if data.get("reason"):
        lines.append(f"reason: {data['reason']}")
        return "\n".join(lines) + "\n"

    lines.append(
        f"files scanned: {data['total_files_seen']}"
        + (" (truncated)" if data.get("scan_truncated") else "")
    )
    lines.append("top-level entries:")
    for entry in data["top_level_entries"]:
        size = "" if entry.get("size") is None else f", {entry['size']} bytes"
        if entry["type"] == "error":
            lines.append(f"  - {entry['name']} ({entry['error']})")
        else:
            lines.append(f"  - {entry['name']} ({entry['type']}{size})")

    lines.append("file counts by extension:")
    counts = data["file_counts_by_extension"]
    if counts:
        for ext, count in counts.items():
            lines.append(f"  - {ext}: {count}")
    else:
        lines.append("  - none")

    lines.extend(_render_list("interesting files", data["interesting_files"]))
    lines.extend(
        _render_list(
            "suspicious secret-bearing filenames",
            data["suspicious_secret_filenames"],
        )
    )
    if data["suspicious_secret_filenames"]:
        lines.append("secret note: only filenames were reported; secret contents were not read or printed.")

    recent_logs = [
        f"{row['path']} ({row['size']} bytes, mtime {row['mtime']})"
        for row in data["recent_log_files"]
    ]
    lines.extend(_render_list("recent log-ish files", recent_logs))
    lines.extend(_render_list("test/build command hints", data["test_build_hints"]))

    git = data["git"]
    lines.append("git status:")
    if not git["is_repo"]:
        lines.append("  - not a git repo")
    elif git.get("error"):
        lines.append(f"  - blocked: {git['error']}")
    elif git["status"]:
        lines.extend(f"  - {line}" for line in git["status"])
    else:
        lines.append("  - clean or no status output")
    return "\n".join(lines) + "\n"


def run_audit(
    target: str | Path,
    *,
    root: str | Path | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_findings: int = DEFAULT_MAX_FINDINGS,
) -> AuditResult:
    """Audit a directory without mutating it or reading secret contents."""

    resolved = _resolve_target(target, root)
    root_resolved = Path(root).expanduser().resolve(strict=False) if root else None
    exists = resolved.exists()
    is_dir = resolved.is_dir()
    data: dict[str, Any] = {
        "requested_target": str(target),
        "target": str(resolved),
        "root": str(root_resolved) if root_resolved else None,
        "status": "OK",
        "exists": exists,
        "is_dir": is_dir,
        "reason": "",
        "max_files": max_files,
        "max_findings": max_findings,
    }
    if max_files < 1:
        data.update({"status": "BLOCKED", "reason": "max_files must be >= 1"})
        return AuditResult(status=data["status"], report=_render_report(data), data=data)
    if max_findings < 1:
        data.update({"status": "BLOCKED", "reason": "max_findings must be >= 1"})
        return AuditResult(status=data["status"], report=_render_report(data), data=data)
    if root_resolved and not _contains(root_resolved, resolved):
        data.update({"status": "BLOCKED", "reason": "target escapes audit root"})
        return AuditResult(status=data["status"], report=_render_report(data), data=data)
    if not exists:
        data.update({"status": "BLOCKED", "reason": "target does not exist"})
        return AuditResult(status=data["status"], report=_render_report(data), data=data)
    if not is_dir:
        data.update({"status": "BLOCKED", "reason": "target is not a directory"})
        return AuditResult(status=data["status"], report=_render_report(data), data=data)

    top = _top_level_entries(resolved, max_findings)
    scan = _scan_files(resolved, max_files, max_findings)
    hints = _command_hints(scan["command_hint_markers"])
    data.update(scan)
    data.update(
        {
            "top_level_entries": top,
            "git": _git_status(resolved, max_findings),
            "test_build_hints": hints,
        }
    )
    return AuditResult(status=data["status"], report=_render_report(data), data=data)
