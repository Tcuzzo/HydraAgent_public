"""ACI v2 read-only tools for Hydra operator context."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

INSPECT_REPO_SCHEMA = "hydra.aci.inspect_repo.v1"
INSPECT_RUNTIME_SCHEMA = "hydra.aci.inspect_runtime.v1"
INSPECT_ENVIRONMENT_SCHEMA = "hydra.aci.inspect_environment.v1"
READ_EVIDENCE_SCHEMA = "hydra.aci.read_evidence.v1"
MAX_EVIDENCE_CHARS = 12_000
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASS", "CREDENTIAL")


@dataclass(frozen=True)
class AciError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def inspect_repo(root: str | Path) -> dict[str, Any]:
    """Inspect repository shape without mutating it."""
    repo_root = _resolve_root(root)
    return {
        "schema": INSPECT_REPO_SCHEMA,
        "repo_root": str(repo_root),
        "risk_tier": "T0",
        "is_git_repo": (repo_root / ".git").exists(),
        "manifests": _existing(repo_root, ("README.md", "pyproject.toml", "setup.py", "package.json", "Cargo.toml")),
        "directories": _existing_dirs(repo_root, ("hydra", "skills", ".hydraAgent", "evidence", "workbench", "docs")),
        "policy": "strict read-only filesystem introspection; no git commands or project code executed",
    }


def inspect_runtime(root: str | Path) -> dict[str, Any]:
    """Inspect local runtime facts without invoking project code."""
    repo_root = _resolve_root(root)
    return {
        "schema": INSPECT_RUNTIME_SCHEMA,
        "repo_root": str(repo_root),
        "risk_tier": "T0",
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "tools": {
            name: shutil.which(name)
            for name in ("python3", "git", "rg")
        },
        "environment": inspect_environment(repo_root),
        "policy": "read-only runtime introspection; no project code executed",
    }


def inspect_environment(root: str | Path) -> dict[str, Any]:
    """Return non-secret environment shape for operator context."""
    repo_root = _resolve_root(root)
    env_keys = sorted(os.environ)
    return {
        "schema": INSPECT_ENVIRONMENT_SCHEMA,
        "repo_root": str(repo_root),
        "risk_tier": "T0",
        "cwd": str(Path.cwd().resolve()),
        "environment_keys": env_keys,
        "redacted_keys": [key for key in env_keys if _is_secret_name(key)],
        "policy": "environment key inventory only; values are never returned",
    }


def read_evidence(root: str | Path, relative_path: str | Path) -> dict[str, Any]:
    """Read a bounded text evidence file from inside the repo root."""
    repo_root = _resolve_root(root)
    requested = Path(relative_path)
    if requested.is_absolute():
        raise AciError("evidence path must be relative to repo root")
    if any(part == ".." for part in requested.parts):
        raise AciError("evidence path must not traverse outside repo root")

    path = (repo_root / requested).resolve()
    if not _is_relative_to(path, repo_root):
        raise AciError("evidence path escapes repo root")
    evidence_root = (repo_root / "evidence").resolve()
    if not _is_relative_to(path, evidence_root):
        raise AciError("evidence path must stay under evidence/")
    if not path.is_file():
        raise AciError(f"evidence file not found: {requested.as_posix()}")

    text = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > MAX_EVIDENCE_CHARS
    return {
        "schema": READ_EVIDENCE_SCHEMA,
        "repo_root": str(repo_root),
        "relative_path": path.relative_to(repo_root).as_posix(),
        "risk_tier": "T0",
        "chars": len(text),
        "max_chars": MAX_EVIDENCE_CHARS,
        "truncated": truncated,
        "text": text[:MAX_EVIDENCE_CHARS],
        "policy": "read-only bounded evidence read",
    }


def _resolve_root(root: str | Path) -> Path:
    resolved = Path(root).expanduser().resolve()
    if not resolved.exists():
        raise AciError(f"repo root does not exist: {resolved}")
    if not resolved.is_dir():
        raise AciError(f"repo root is not a directory: {resolved}")
    return resolved


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    return any(marker in upper for marker in _SECRET_MARKERS)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _existing(root: Path, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if (root / name).is_file()]


def _existing_dirs(root: Path, names: tuple[str, ...]) -> list[str]:
    return [name for name in names if (root / name).is_dir()]
