"""Deterministic failure / rot clustering across HydraAgent evidence.

Scans the evidence directory for repeated failure signals — missing binaries in
ops audits, non-zero command errors, command timeouts, matched rot signals,
and FAILed eval cases across every slice — and groups them into named clusters
with concrete repair targets. The output is keyed by
``hydra.failure_clusters.v1`` and is deterministic for identical filesystem
state. This module is read-only.

Use this as the offline-improvement loop: instead of fixing the same kind of
failure one audit at a time, get one prioritised list of repair targets
ranked by how often each pattern recurs.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "hydra.failure_clusters.v1"
MAX_CLUSTER_SOURCES = 25


@dataclass(frozen=True)
class FailureClusterError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


def cluster_failures(evidence_root: str | Path) -> dict[str, Any]:
    """Walk ``evidence_root`` and cluster every observable failure / rot signal."""
    root = Path(evidence_root).expanduser().resolve()
    if not root.exists():
        raise FailureClusterError(f"evidence root does not exist: {root}")
    if not root.is_dir():
        raise FailureClusterError(f"evidence root is not a directory: {root}")

    eval_results: list[Path] = sorted(root.rglob("results.tsv"))
    ops_bundles: list[Path] = sorted(
        p for p in (root / "ops").iterdir() if p.is_dir()
    ) if (root / "ops").is_dir() else []

    raw_findings: list[dict[str, Any]] = []
    raw_findings.extend(_scan_eval_failures(eval_results, root))
    for bundle in ops_bundles:
        raw_findings.extend(_scan_ops_bundle(bundle, root))

    clusters = _group(raw_findings)
    clusters_sorted = sorted(
        clusters,
        key=lambda c: (-c["count"], c["id"]),
    )

    counts_by_kind: dict[str, int] = {}
    for cluster in clusters_sorted:
        kind = cluster["kind"]
        counts_by_kind[kind] = counts_by_kind.get(kind, 0) + cluster["count"]

    proof = [
        f"evidence_root={root}",
        f"results_files={len(eval_results)}",
        f"ops_bundles={len(ops_bundles)}",
        f"raw_findings={len(raw_findings)}",
        f"clusters={len(clusters_sorted)}",
    ]

    return {
        "schema": SCHEMA,
        "scope": {
            "evidence_root": str(root),
            "results_files_scanned": len(eval_results),
            "ops_bundles_scanned": len(ops_bundles),
        },
        "totals": {
            "raw_findings": len(raw_findings),
            "clusters": len(clusters_sorted),
            "by_kind": dict(sorted(counts_by_kind.items())),
        },
        "clusters": clusters_sorted,
        "proof": proof,
        "policy": "read-only scan over evidence; never writes to source files",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"Hydra failure clusters: {report['scope']['evidence_root']}",
        f"results_files={report['scope']['results_files_scanned']}  "
        f"ops_bundles={report['scope']['ops_bundles_scanned']}  "
        f"clusters={report['totals']['clusters']}  "
        f"raw_findings={report['totals']['raw_findings']}",
    ]
    if report["totals"]["by_kind"]:
        lines.append(
            "by_kind: "
            + ", ".join(f"{k}={v}" for k, v in report["totals"]["by_kind"].items())
        )
    lines.append("clusters (highest count first):")
    if not report["clusters"]:
        lines.append("  - none")
    for cluster in report["clusters"]:
        lines.append(
            f"  - [{cluster['severity']}] {cluster['id']} x{cluster['count']}"
        )
        lines.append(f"      repair: {cluster['repair_target']}")
        for src in cluster["sources"][:3]:
            lines.append(f"      source: {src}")
        if len(cluster["sources"]) > 3:
            lines.append(f"      source: ... +{len(cluster['sources']) - 3} more")
    lines.append("proof:")
    for p in report["proof"]:
        lines.append(f"  - {p}")
    return "\n".join(lines) + "\n"


def _scan_eval_failures(results_files: list[Path], root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in results_files:
        rel = str(path.relative_to(root))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            slice_name = parts[1]
            case_name = parts[2]
            outcome = parts[3]
            detail = parts[4]
            if case_name == "SUMMARY":
                # SUMMARY rows record N/M; treat outcome=FAIL as one finding per file
                if outcome != "FAIL":
                    continue
                out.append({
                    "kind": "eval_summary_fail",
                    "key": slice_name,
                    "source": rel,
                    "detail": detail,
                    "severity": "red",
                })
                continue
            if outcome != "FAIL":
                continue
            out.append({
                "kind": "eval_case_fail",
                "key": f"{slice_name}::{case_name}",
                "source": rel,
                "detail": detail,
                "severity": "red",
            })
    return out


def _scan_ops_bundle(bundle: Path, root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    commands_path = bundle / "commands.jsonl"
    if commands_path.is_file():
        out.extend(_scan_commands(commands_path, root))
    rot_path = bundle / "rot_signals.json"
    if rot_path.is_file():
        out.extend(_scan_rot_signals(rot_path, root))
    return out


def _scan_commands(path: Path, root: Path) -> list[dict[str, Any]]:
    rel = str(path.relative_to(root))
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        outcome = row.get("outcome")
        if outcome == "missing":
            first_token = _first_token(row.get("command", ""))
            out.append({
                "kind": "missing_tool",
                "key": first_token or row.get("id", "<unknown>"),
                "source": rel,
                "detail": row.get("command", ""),
                "severity": "yellow",
            })
        elif outcome == "error":
            rc = row.get("returncode")
            out.append({
                "kind": "command_error",
                "key": f"{row.get('id', '<unknown>')}::rc={rc}",
                "source": rel,
                "detail": (row.get("stderr") or row.get("stdout") or "")[:200],
                "severity": "yellow",
            })
        elif outcome == "timeout":
            out.append({
                "kind": "command_timeout",
                "key": row.get("id", "<unknown>"),
                "source": rel,
                "detail": row.get("command", ""),
                "severity": "yellow",
            })
    return out


def _scan_rot_signals(path: Path, root: Path) -> list[dict[str, Any]]:
    rel = str(path.relative_to(root))
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for signal in data:
        if not isinstance(signal, dict):
            continue
        if not signal.get("matched"):
            continue
        out.append({
            "kind": "matched_rot_signal",
            "key": str(signal.get("id", "<unknown>")),
            "source": rel,
            "detail": f"source={signal.get('source')} match={signal.get('match')}",
            "severity": "yellow",
        })
    return out


def _first_token(command: str) -> str:
    tokens = command.strip().split()
    if not tokens:
        return ""
    # Strip env var prefixes like FOO=bar
    for token in tokens:
        if "=" in token and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token):
            continue
        return token
    return tokens[-1]


def _group(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for finding in findings:
        key = (finding["kind"], finding["key"])
        entry = by_key.setdefault(key, {
            "id": f"{finding['kind']}::{finding['key']}",
            "kind": finding["kind"],
            "key": finding["key"],
            "count": 0,
            "sources": [],
            "details": [],
            "severity": finding["severity"],
        })
        entry["count"] += 1
        if finding["source"] not in entry["sources"]:
            if len(entry["sources"]) < MAX_CLUSTER_SOURCES:
                entry["sources"].append(finding["source"])
        if finding["detail"] and finding["detail"] not in entry["details"]:
            if len(entry["details"]) < 5:
                entry["details"].append(finding["detail"])
        # Promote severity to the strictest seen
        if finding["severity"] == "red":
            entry["severity"] = "red"

    out: list[dict[str, Any]] = []
    for entry in by_key.values():
        entry["sources"].sort()
        entry["repair_target"] = _repair_target(entry)
        out.append(entry)
    return out


def _repair_target(cluster: dict[str, Any]) -> str:
    kind = cluster["kind"]
    key = cluster["key"]
    count = cluster["count"]
    if kind == "missing_tool":
        return (
            f"install or expose `{key}` on this host "
            f"(missing in {count} ops audit{'s' if count != 1 else ''})"
        )
    if kind == "command_error":
        return (
            f"investigate command `{key}` "
            f"({count} non-zero exit{'s' if count != 1 else ''} across ops audits)"
        )
    if kind == "command_timeout":
        return (
            f"raise timeout or narrow command `{key}` "
            f"({count} timeout{'s' if count != 1 else ''})"
        )
    if kind == "matched_rot_signal":
        return (
            f"address rot signal `{key}` "
            f"(matched in {count} ops audit{'s' if count != 1 else ''})"
        )
    if kind == "eval_case_fail":
        return f"fix failing eval case `{key}` ({count} occurrence{'s' if count != 1 else ''})"
    if kind == "eval_summary_fail":
        return f"re-run slice `{key}` and triage cases (suite FAILed {count}x)"
    return f"investigate `{key}` ({count} occurrences)"
