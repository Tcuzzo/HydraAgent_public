"""Deterministic filename/path locator for operator prompts."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA = "hydra.locate.v1"
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


@dataclass(frozen=True)
class LocateResult:
    status: str
    report: str
    data: dict[str, Any]


def _resolve_root(root: str | Path) -> Path:
    return Path(root).expanduser().resolve(strict=False)


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _entry_type(path: Path) -> str:
    if path.is_symlink():
        return "symlink"
    if path.is_dir():
        return "dir"
    if path.is_file():
        return "file"
    return "other"


def _best_match(query: str, matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not matches:
        return None
    needle = query.lower()

    def _score(row: dict[str, Any]) -> tuple[int, int, int, str]:
        name = row["name"].lower()
        bare = name.lstrip(".")
        exact = 0 if bare == needle else 1
        directory = 0 if row["type"] == "dir" else 1
        return (exact, directory, len(row["path"]), row["path"])

    return sorted(matches, key=_score)[0]


def run_locate(query: str, *, root: str | Path, max_results: int = 100, max_files: int = 10000) -> LocateResult:
    query = query.strip()
    root_path = _resolve_root(root)
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "query": query,
        "root": str(root_path),
        "matches": [],
        "best_match": None,
        "count": 0,
        "truncated": False,
        "files_scanned": 0,
        "scan_errors": [],
    }
    if not query:
        data["status"] = "BLOCKED"
        data["reason"] = "query is empty"
        return LocateResult("BLOCKED", _render_report(data), data)
    if max_results <= 0 or max_files <= 0:
        data["status"] = "BLOCKED"
        data["reason"] = "limits must be positive"
        return LocateResult("BLOCKED", _render_report(data), data)
    if not root_path.is_dir():
        data["status"] = "BLOCKED"
        data["reason"] = "root is not a directory"
        return LocateResult("BLOCKED", _render_report(data), data)

    needle = query.lower()
    scanned = 0
    matches: list[dict[str, Any]] = []

    def _on_error(err: OSError) -> None:
        data["scan_errors"].append(str(err))

    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False, onerror=_on_error):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        entries = [(name, True) for name in dirnames] + [(name, False) for name in sorted(filenames)]
        current = Path(dirpath)
        for name, is_dir in entries:
            scanned += 1
            if scanned > max_files:
                data["truncated"] = True
                break
            if needle not in name.lower():
                continue
            path = current / name
            matches.append(
                {
                    "path": _rel(path, root_path),
                    "name": name,
                    "type": "dir" if is_dir else _entry_type(path),
                }
            )
            if len(matches) >= max_results:
                data["truncated"] = True
                break
        if data["truncated"]:
            break

    data["matches"] = sorted(matches, key=lambda row: row["path"])
    data["best_match"] = _best_match(query, data["matches"])
    data["count"] = len(matches)
    data["files_scanned"] = scanned
    data["status"] = "OK" if matches else "NO_MATCH"
    data["reason"] = ""
    return LocateResult(data["status"], _render_report(data), data)


def _render_report(data: dict[str, Any]) -> str:
    lines = [
        "Hydra locate:",
        f"query: {data.get('query', '')}",
        f"root: {data.get('root', '')}",
        f"status: {data.get('status', '')}",
        f"matches: {data.get('count', 0)}",
    ]
    reason = data.get("reason")
    if reason:
        lines.append(f"reason: {reason}")
    if data.get("matches"):
        lines.append("matched paths:")
        for row in data["matches"]:
            lines.append(f"  - {row['path']} ({row['type']})")
    if data.get("truncated"):
        lines.append("truncated: yes")
    if data.get("scan_errors"):
        lines.append("scan errors:")
        for err in data["scan_errors"][:10]:
            lines.append(f"  - {err}")
    return "\n".join(lines) + "\n"
