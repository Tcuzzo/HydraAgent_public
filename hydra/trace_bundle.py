"""Trace bundle builder for operator-readable evidence packets.

SLICE 2 NOTE: hydra.task_evals, hydra.build, memory.source, and verifier.check
have been stripped in the lean-core build. build_trace_bundle returns a minimal
bundle without those sections.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "hydra.trace_bundle.v1"


def build_trace_bundle(repo_root: Path, suite_id: str = "hydra", limit: int = 12) -> dict[str, Any]:
    repo_root = repo_root.expanduser().resolve()
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repo_root": str(repo_root),
        "verifier": {"note": "verifier package stripped in lean-core build"},
        "build": {"note": "build module stripped in lean-core build"},
        "task_eval": {"note": "task_evals module stripped in lean-core build"},
        "recent_promotions": [],
        "reproduce": [
            "PYTHONDONTWRITEBYTECODE=1 python3 -m hydra status",
            f"PYTHONDONTWRITEBYTECODE=1 python3 -m hydra trace-bundle --suite {suite_id} --format json",
        ],
        "secret_scan": {
            "status": "scrubbed",
            "policy": "bundle includes metadata and commands only; provider secrets and OAuth files are never read",
        },
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Hydra trace bundle",
        f"Repo: {report['repo_root']}",
        f"Generated: {report['generated_at']}",
        "Reproduce:",
    ]
    for command in report["reproduce"]:
        lines.append(f"- {command}")
    return "\n".join(lines) + "\n"
