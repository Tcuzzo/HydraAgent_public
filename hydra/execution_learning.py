"""Learn local, sourced execution observations without trusting model claims.

Episodes live beside the existing durable Markdown corpus. Retrieval reads the
same records, and the vector cache indexes them like any other memory source.
The graph contains observed task/tool/artifact relationships, never inferred
facts about whether a program is correct. This is experience retrieval, not
neural weight training.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterator
import uuid

from hydra.atomic_write import atomic_write_bytes
from hydra.local_memory import DEFAULT_MEMORY_ROOT, _redact_text
from hydra.tool_outcome import failed

SCHEMA = "hydra.execution_learning.v1"
MAX_EPISODES = 256
MAX_RECORD_BYTES = 131072
MAX_OBSERVATIONS = 32
_SECRET_KEY = re.compile(r"(?i)(api.?key|authorization|password|credential|secret|token)")
_TOKEN = re.compile(r"(?i)\b(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9_]{12,}|bearer\s+\S+)")


def workspace_key(repo_root: Path) -> str:
    return hashlib.sha256(str(Path(repo_root).expanduser().resolve()).encode()).hexdigest()[:24]


def _root(memory_root: Path | None) -> Path:
    return Path(memory_root).expanduser().resolve() if memory_root is not None else DEFAULT_MEMORY_ROOT


def _clean(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[depth limit]"
    if isinstance(value, dict):
        return {str(k)[:80]: "[redacted]" if _SECRET_KEY.search(str(k)) else _clean(v, depth=depth + 1)
                for k, v in list(value.items())[:16]}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth=depth + 1) for v in value[:16]]
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
        return _TOKEN.sub("[redacted]", _redact_text(text[:2000]))[:600]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _clean(str(value), depth=depth + 1)


def _artifact(arguments: dict, repo: Path) -> dict | None:
    raw = arguments.get("path") or arguments.get("file_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = (repo / raw).resolve()
    if not path.is_relative_to(repo):
        return None
    rel = path.relative_to(repo).as_posix()
    from hydra.local_memory import _is_sensitive
    if _is_sensitive(path, repo):
        return None
    node = {"id": "artifact:" + hashlib.sha256(rel.encode()).hexdigest()[:16],
            "kind": "artifact", "path": rel}
    if path.is_file():
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) <= 1024 * 1024:
            node["sha256_at_recording"] = hashlib.sha256(data).hexdigest()
    return node


def record_execution_lesson(
    *, repo_root: Path, memory_root: Path | None = None, goal: str,
    result: Any, provider: str, model: str,
) -> dict[str, Any]:
    """Persist real tool outcomes, including failures, for the next task.

    ``result`` is the actual AgentLoop LoopResult. A natural model stop is not
    verification, and final_response is deliberately excluded from knowledge.
    No-tool runs produce a skipped receipt. Persistence errors propagate.
    """
    repo = Path(repo_root).expanduser().resolve()
    if not repo.is_dir():
        raise ValueError("execution memory requires an existing workspace")
    steps = [s for s in result.steps if s.kind == "tool_result"]
    if not steps:
        return {"schema": SCHEMA, "status": "skipped", "reason": "no observed tool execution"}
    if not isinstance(goal, str) or not goal.strip():
        raise ValueError("execution memory requires a non-empty task")
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    nodes = [{"id": "task:" + identifier, "kind": "task", "goal": _clean(goal)}]
    edges = []
    observations = []
    any_failure = False
    for index, step in enumerate(steps):
        try:
            payload = json.loads(step.content)
        except (ValueError, TypeError):
            payload = {"content": str(step.content)}
        is_failure = bool(step.tool_error) or failed(payload)
        any_failure = any_failure or is_failure
        if index >= MAX_OBSERVATIONS:
            continue
        arguments = step.tool_arguments if isinstance(step.tool_arguments, dict) else {}
        observable = {k: payload[k] for k in ("ok", "success", "isError", "exit_code", "returncode", "timed_out", "error", "message")
                      if isinstance(payload, dict) and k in payload}
        action_id = f"action:{index}"
        row = {"id": action_id, "tool": str(step.tool_name or "unknown")[:100],
               "arguments": _clean(arguments), "outcome": "failed" if is_failure else "executed",
               "status_fields": _clean(observable),
               "result_sha256": hashlib.sha256(str(step.content).encode()).hexdigest()}
        if step.tool_error:
            row["error"] = _clean(str(step.tool_error))
        observations.append(row)
        nodes.append({"id": action_id, "kind": "tool", "tool": row["tool"], "outcome": row["outcome"]})
        edges.append({"from": nodes[0]["id"], "to": action_id, "relation": "observed_action"})
        artifact = _artifact(arguments, repo)
        if artifact is not None:
            if not any(n["id"] == artifact["id"] for n in nodes):
                nodes.append(artifact)
            edges.append({"from": action_id, "to": artifact["id"], "relation": "referenced_artifact"})
    record = {"schema": SCHEMA, "id": identifier, "created_at": datetime.now(timezone.utc).isoformat(),
              "workspace_key": workspace_key(repo), "goal": _clean(goal),
              "provider": _clean(provider), "model": _clean(model),
              "halted_reason": str(result.halted_reason)[:80],
              "outcome": "failed" if any_failure else "executed" if result.halted_reason == "natural" else "incomplete",
              "quality_verified": False, "observations": observations,
              "observations_total": len(steps), "observations_truncated": len(steps) > MAX_OBSERVATIONS,
              "nodes": nodes, "edges": edges,
              "interpretation": "Observed tool execution only; recheck source and verify the new task independently."}
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_RECORD_BYTES:
        raise ValueError("execution memory record exceeds its bounded payload size")
    directory = _root(memory_root) / "workspace" / "episodes" / workspace_key(repo)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (identifier + ".json")
    atomic_write_bytes(path, payload, overwrite=False)
    return {"schema": SCHEMA, "status": "recorded", "id": identifier, "path": str(path),
            "outcome": record["outcome"], "quality_verified": False,
            "observations": len(observations), "nodes": len(nodes), "edges": len(edges)}


def execution_records(memory_root: Path, repo_root: Path) -> list[tuple[Path, dict]]:
    """Read only bounded, current-workspace episodes; damaged evidence is loud."""
    root = _root(memory_root)
    directory = root / "workspace" / "episodes" / workspace_key(repo_root)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json"), reverse=True)[:MAX_EPISODES]:
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("execution memory source escapes its configured root")
        with path.open("rb") as stream:
            payload = stream.read(MAX_RECORD_BYTES + 1)
        if len(payload) > MAX_RECORD_BYTES:
            raise ValueError(f"execution memory record exceeds size limit: {path.name}")
        record = json.loads(payload)
        if (not isinstance(record, dict) or record.get("schema") != SCHEMA
                or record.get("workspace_key") != workspace_key(repo_root)
                or record.get("quality_verified") is not False):
            raise ValueError(f"invalid execution memory record: {path.name}")
        records.append((path, record))
    return records


def execution_chunks(memory_root: Path, repo_root: Path) -> Iterator[tuple[str, str]]:
    """Project episodes into the existing retrieval corpus with provenance."""
    for path, record in execution_records(memory_root, repo_root):
        goal = str(_clean(record.get("goal", "")))
        yield str(path), (f"Execution observation: {goal}\n"
                          f"outcome={record['outcome']}; quality_verified=False; source={path.name}\n"
                          "Past execution is evidence of an attempt, not proof of correctness.")
        for row in record.get("observations", [])[:MAX_OBSERVATIONS]:
            yield str(path), (f"Execution for {goal[:240]}\n"
                              f"tool={row.get('tool')}; outcome={row.get('outcome')}; quality_verified=False\n"
                              f"arguments={json.dumps(_clean(row.get('arguments', {})), ensure_ascii=False)}\n"
                              f"status={json.dumps(_clean(row.get('status_fields', {})), ensure_ascii=False)}")


def query_execution_knowledge(query: str, *, repo_root: Path, memory_root: Path | None = None,
                              limit: int = 5) -> dict:
    """Retrieve matching episodes plus at most one hop through shared artifacts.

    The links describe shared source paths, not causal or semantic conclusions.
    This function powers the inspectable graph view and uses the same files as
    chat recall. It does not call a model or execute remembered actions.
    """
    limit = max(0, min(int(limit), 20))
    tokens = set(re.findall(r"[\w.-]{3,}", query.lower()))
    rows = execution_records(_root(memory_root), repo_root)
    scored = []
    for path, record in rows:
        text = json.dumps({k: record.get(k) for k in ("goal", "observations", "nodes")}).lower()
        score = sum(text.count(token) for token in tokens)
        if score:
            scored.append((score, path, record))
    scored.sort(key=lambda item: (-item[0], item[2]["id"]))
    chosen = scored[:limit]
    chosen_ids = {r["id"] for _, _, r in chosen}
    artifacts = {n.get("path") for _, _, r in chosen for n in r.get("nodes", []) if n.get("kind") == "artifact"}
    related = []
    for path, record in rows:
        if record["id"] in chosen_ids:
            continue
        overlap = {n.get("path") for n in record.get("nodes", []) if n.get("kind") == "artifact"} & artifacts
        if overlap:
            related.append({"id": record["id"], "path": str(path), "relation": "shared_artifact",
                            "artifacts": sorted(overlap), "outcome": record["outcome"]})
        if len(related) >= limit:
            break
    return {"schema": "hydra.execution_knowledge.v1", "query": query,
            "matches": [{"score": s, "path": str(p), **r} for s, p, r in chosen],
            "related": related, "records_considered": len(rows), "quality_verified": False}
