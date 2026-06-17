"""Promote §10.56 failure clusters into §10.44 durable lessons.

Closes the offline-improvement loop: cluster repeated failures, then write the
top clusters into operator-visible durable lessons so the next chat / planner
turn can see them. Idempotent — re-running over the same evidence does not
duplicate lessons; a sidecar index records the last promoted count per cluster.
Lesson writes go through the existing :func:`hydra.lessons.remember_lesson`
API, so the same secret-redaction and source-required guarantees apply.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hydra.failure_clusters import FailureClusterError, cluster_failures
from hydra.lessons import LessonError, remember_lesson
from hydra.local_memory import DEFAULT_MEMORY_ROOT


SCHEMA = "hydra.cluster_lessons.v1"
INDEX_RELATIVE_PATH = Path("workspace") / "memory" / "cluster-lessons-index.json"
DEFAULT_MIN_COUNT = 2
DEFAULT_TOP_N = 20


class ClusterLessonError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def promote_clusters_to_lessons(
    evidence_root: str | Path,
    *,
    memory_root: str | Path | None = None,
    min_count: int = DEFAULT_MIN_COUNT,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Cluster failures under ``evidence_root`` and promote the top-N to lessons.

    Returns a structured report listing what was promoted, what was skipped
    because it had already been promoted at the same count, and what was
    skipped for being below ``min_count``.
    """
    if min_count <= 0:
        raise ClusterLessonError("min_count must be a positive integer")
    if top_n <= 0:
        raise ClusterLessonError("top_n must be a positive integer")

    try:
        cluster_report = cluster_failures(evidence_root)
    except FailureClusterError as e:
        raise ClusterLessonError(str(e)) from e

    root = Path(memory_root).expanduser().resolve() if memory_root else DEFAULT_MEMORY_ROOT
    index_path = root / INDEX_RELATIVE_PATH
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = _load_index(index_path)

    eligible = [c for c in cluster_report["clusters"] if c["count"] >= min_count]
    considered = eligible[:top_n]
    skipped_low_count = [
        {"id": c["id"], "count": c["count"]}
        for c in cluster_report["clusters"]
        if c["count"] < min_count
    ]
    skipped_overflow = [
        {"id": c["id"], "count": c["count"]} for c in eligible[top_n:]
    ]

    promoted: list[dict[str, Any]] = []
    skipped_already: list[dict[str, Any]] = []
    for cluster in considered:
        cid = cluster["id"]
        if index.get(cid) == cluster["count"]:
            skipped_already.append({"id": cid, "count": cluster["count"]})
            continue
        lesson_text = _cluster_to_lesson_text(cluster)
        source_text = _cluster_to_source(cluster, Path(evidence_root))
        try:
            result = remember_lesson(
                lesson_text,
                source=source_text,
                tags=["failure-cluster", cluster["kind"], cluster["severity"]],
                memory_root=memory_root,
            )
        except LessonError as e:
            raise ClusterLessonError(f"failed to write lesson for {cid}: {e}") from e
        promoted.append({
            "cluster_id": cid,
            "kind": cluster["kind"],
            "count": cluster["count"],
            "severity": cluster["severity"],
            "lesson_path": result["path"],
        })
        index[cid] = cluster["count"]

    _save_index(index_path, index)

    return {
        "schema": SCHEMA,
        "evidence_root": cluster_report["scope"]["evidence_root"],
        "memory_root": str(root),
        "min_count": min_count,
        "top_n": top_n,
        "promoted": promoted,
        "skipped_already_promoted": skipped_already,
        "skipped_low_count": skipped_low_count,
        "skipped_overflow_above_top_n": skipped_overflow,
        "index_path": str(index_path),
        "policy": "lessons are append-only via remember_lesson; idempotent via per-cluster count index",
    }


def render_text(report: dict[str, Any]) -> str:
    promoted = report["promoted"]
    lines = [
        f"Hydra cluster lessons: {len(promoted)} promoted",
        f"evidence_root: {report['evidence_root']}",
        f"memory_root: {report['memory_root']}",
        f"min_count: {report['min_count']}  top_n: {report['top_n']}",
        f"index_path: {report['index_path']}",
        "promoted:",
    ]
    if not promoted:
        lines.append("  - none")
    for p in promoted:
        lines.append(
            f"  - [{p['severity']}] {p['cluster_id']} x{p['count']} -> {p['lesson_path']}"
        )
    if report["skipped_already_promoted"]:
        lines.append(
            f"skipped (already promoted at same count): "
            f"{len(report['skipped_already_promoted'])}"
        )
    if report["skipped_low_count"]:
        lines.append(
            f"skipped (count < {report['min_count']}): {len(report['skipped_low_count'])}"
        )
    if report["skipped_overflow_above_top_n"]:
        lines.append(
            f"skipped (beyond top_n={report['top_n']}): "
            f"{len(report['skipped_overflow_above_top_n'])}"
        )
    return "\n".join(lines) + "\n"


def _cluster_to_lesson_text(cluster: dict[str, Any]) -> str:
    severity = cluster["severity"].upper()
    detail_extra = ""
    details = cluster.get("details") or []
    if details:
        sample = details[0]
        # remove tabs and bound the inline sample
        sample = " ".join(sample.split())[:160]
        if sample:
            detail_extra = f" — sample: {sample}"
    return (
        f"[{severity}] {cluster['repair_target']} "
        f"(cluster={cluster['id']}, kind={cluster['kind']}, count={cluster['count']})"
        f"{detail_extra}"
    )


def _cluster_to_source(cluster: dict[str, Any], evidence_root: Path) -> str:
    sources = cluster.get("sources") or []
    first_source = sources[0] if sources else "evidence/"
    return (
        f"hydra ops clusters (evidence_root={evidence_root}); "
        f"cluster={cluster['id']}; first-seen={first_source}"
    )


def _load_index(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): int(v) for k, v in data.items() if isinstance(v, int)}


def _save_index(path: Path, index: dict[str, int]) -> None:
    path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
