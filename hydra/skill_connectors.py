"""Runtime connector functions for Hydra's local skill and subagent surfaces."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hydra.skill_library_search import search_skill_library
from hydra.skill_spine import find_skill, list_skill_records, render_route_json, render_skill


def skill_list(query: str = "", limit: int = 20) -> dict[str, Any]:
    records = list_skill_records()
    tokens = _tokens(query)
    if tokens:
        filtered = [
            record
            for record in records
            if all(token in _skill_text(record).replace("-", " ").replace("_", " ") for token in tokens)
        ]
    else:
        filtered = records
    bounded = _clamp(limit, default=20, maximum=100)
    return {
        "ok": True,
        "schema": "hydra.skills.list.v1",
        "query": query or "",
        "total_skills": len(records),
        "matched": len(filtered),
        "returned": min(len(filtered), bounded),
        "skills": [_payload(record) for record in filtered[:bounded]],
    }


def skill_search(query: str, root: str | Path, skills_root: str = "hydra/schemes", limit: int = 8) -> dict[str, Any]:
    return search_skill_library(
        query=query,
        root=Path(root),
        skills_root=skills_root,
        limit=_clamp(limit, default=8, maximum=25),
    )


def skill_show(name: str, max_chars: int = 4000) -> dict[str, Any]:
    try:
        record = find_skill(name)
    except KeyError as exc:
        return {"ok": False, "schema": "hydra.skills.show.v1", "name": name, "error": str(exc)}
    rendered = render_skill(record)
    bounded = _clamp(max_chars, default=4000, maximum=12000)
    return {
        "ok": True,
        "schema": "hydra.skills.show.v1",
        **_payload(record),
        "content": rendered[:bounded],
        "truncated": len(rendered) > bounded,
        "next_step": "Use fs_read on path for the complete local SKILL.md when full instructions are needed.",
    }


def skill_route(prompt: str) -> dict[str, Any]:
    payload = json.loads(render_route_json(prompt))
    payload["ok"] = True
    payload["total_skills"] = len(list_skill_records())
    return payload


def _ssot_planner_model() -> str:
    """Resolve the SSOT cloud-planner model at call time (avoids import-cycle at load)."""
    from hydra.model_routing import load_routing  # local import: load_routing is idempotent

    p, m = load_routing().role_pair("planner")
    return f"{p}/{m}"


def spawn_subagent(
    task: str,
    model: str,
    root: Path,
    timeout: int = 300,
    approval_policy: str = "ask",
    runtime_only: bool = False,
) -> dict[str, Any]:
    """Launch one hydra ask subagent as a subprocess and return its result dict.

    This is a module-level function so callers can monkeypatch it in tests.
    """
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "hydra", "ask"]
    if runtime_only:
        cmd.append("--runtime-only")
    provider, _, mdl = model.partition("/")
    if mdl:
        cmd.extend(["--provider", provider, "--model", mdl])
    cmd.extend(["--approval-policy", approval_policy, "--root", str(root), task])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "model": model,
            "task": task,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": -1, "stdout": "", "stderr": "timeout", "model": model, "task": task}


def spawn_one(
    task: str,
    root: str | Path,
    model: str | None = None,  # None -> resolved from SSOT planner at call time
    timeout: int = 300,
    runtime_only: bool = False,
) -> dict[str, Any]:
    resolved_model = model if model is not None else _ssot_planner_model()
    return spawn_subagent(
        task=task,
        model=resolved_model,
        root=Path(root),
        timeout=_clamp(timeout, default=300, maximum=1800),
        approval_policy="ask",
        runtime_only=runtime_only,
    )


def spawn_many(
    tasks: list[str],
    root: str | Path,
    model: str | None = None,  # None -> resolved from SSOT planner at call time
    max_workers: int = 4,
    timeout: int = 300,
    runtime_only: bool = False,
) -> dict[str, Any]:
    import concurrent.futures

    resolved_model = model if model is not None else _ssot_planner_model()
    if not isinstance(tasks, list):
        tasks = []
    clean_tasks = [str(task) for task in tasks if str(task).strip()]
    bounded_timeout = _clamp(timeout, default=300, maximum=1800)
    bounded_workers = _clamp(max_workers, default=4, maximum=12)

    def _run(task: str) -> dict[str, Any]:
        return spawn_subagent(
            task=task,
            model=resolved_model,
            root=Path(root),
            timeout=bounded_timeout,
            approval_policy="ask",
            runtime_only=runtime_only,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=bounded_workers) as pool:
        results = list(pool.map(_run, clean_tasks))
    return {"ok": True, "schema": "hydra.subagents.spawn.v1", "count": len(results), "results": results}


def _payload(record: Any) -> dict[str, Any]:
    return {
        "name": record.name,
        "description": record.description,
        "path": record.path.as_posix(),
        "heading": record.heading,
        "summary": record.summary,
    }


def _skill_text(record: Any) -> str:
    return " ".join(
        [
            str(record.name),
            str(record.description),
            str(record.heading),
            str(record.summary),
            record.path.as_posix(),
        ]
    ).lower()


def _tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(query or "").lower())


def _clamp(value: int | str | None, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))
