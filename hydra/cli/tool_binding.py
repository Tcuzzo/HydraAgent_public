"""Shared CLI tool binding helpers."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from hydra.lessons import remember_lesson
from hydra.loop import Tool
from hydra.local_memory import DEFAULT_MEMORY_ROOT, build_local_memory_context
# SEAM CUT: hydra.parallel_subagents stripped; spawn_subagent/parallel_spawn tools removed below.
# SEAM CUT: hydra.inter_agent stripped; agent_send_message/agent_read_messages/collab_* tools removed.
from hydra.policy import ApprovalPolicy
from hydra.skill_library_search import search_skill_library
# SEAM CUT: hydra.studio, skills.content_factory stripped; studio_stitch/studio_edit/content_factory tools removed.
from hydra.skill_spine import find_skill, list_skill_records, render_route_json, render_skill
from skills.fs_read import SkillError
from skills import bash as skill_bash
from skills import browser as skill_browser
from skills import browse_agent as skill_browse_agent
from skills import fs_edit as skill_fs_edit
# SEAM CUT: skills.minimax_media stripped; minimax_* tools removed below.
from skills import fs_read as skill_fs_read
from skills import fs_write as skill_fs_write
# (task_planner skill is available but does not add a separate orchestrate tool)
from skills import glob_files as skill_glob
from skills import grep_files as skill_grep
from skills import http_fetch as skill_http_fetch
from skills import list_directory as skill_ls
from skills import system_stats as skill_system_stats
from skills import todo as skill_todo


DEFAULT_FILESYSTEM_ROOT = Path(os.environ.get("HYDRA_DEFAULT_ROOT", "")).expanduser().resolve() if os.environ.get("HYDRA_DEFAULT_ROOT") else Path.cwd()


def guarded(policy: ApprovalPolicy, tool_name: str, fn: Callable[..., object]) -> Callable[..., object]:
    """Wrap a tool so a gated call WAITS for the operator's decision and, on
    APPROVE, actually RE-RUNS the tool.

    This is the re-execution seam (operator's #1 bug: tapping Approve in Telegram
    did nothing). policy.require() queues the approval, sends the Telegram buttons,
    and — because we run it with wait_for_approval ON — blocks until the operator
    decides. require() returns on APPROVE (we then call fn, so the gated action
    actually runs) and raises on DENY/TIMEOUT (fn never runs). When the underlying
    policy isn't a waiting policy (e.g. allow mode / non-risky tool), require()
    returns straight through and the tool runs immediately, unchanged.
    """
    def _invoke(**kwargs):
        # wait=True opts THIS call into the blocking re-execution seam without
        # cloning the policy (the policy is mutated live — plan_mode toggled per
        # turn — so a snapshot clone would go stale). require() returns on APPROVE,
        # raises on DENY/TIMEOUT (and on the immediate-raise queue path when the
        # policy isn't a waiting one — e.g. untrusted surface / deny mode).
        policy.require(tool_name, kwargs, wait=True)
        result = fn(**kwargs)
        # The gated action actually ran — mark its run record done so it doesn't
        # linger as 'queued'. Best-effort: never let bookkeeping break a real result.
        request_id = getattr(policy, "_last_approved_request_id", None)
        if request_id:
            _complete_approval_run(policy, request_id)
        return result

    return _invoke


def _complete_approval_run(policy: ApprovalPolicy, request_id: str) -> None:
    """Flip the gate's run record from 'queued' (set by the Telegram approval)
    to 'completed' now that the gated tool has actually executed. Best-effort."""
    try:
        from hydra.workbench_runs import (
            RunRecord,
            load_records as load_run_records,
            save_records as save_run_records,
        )

        run_path = Path(policy.run_path)
        records = load_run_records(run_path)
        changed = False
        out: list[RunRecord] = []
        for record in records:
            if request_id in record.approval_request_ids and record.status in {"queued", "waiting_approval"}:
                out.append(
                    RunRecord(
                        schema=record.schema,
                        run_id=record.run_id,
                        title=record.title,
                        lane=record.lane,
                        status="completed",
                        goal=record.goal,
                        evidence_paths=list(record.evidence_paths),
                        approval_request_ids=list(record.approval_request_ids),
                        command=record.command,
                        failure_reason=record.failure_reason,
                        created_at=record.created_at,
                    )
                )
                changed = True
            else:
                out.append(record)
        if changed:
            save_run_records(run_path, out)
    except Exception:  # noqa: BLE001 — run bookkeeping never breaks a real result
        return


# SEAM CUT: _factory_build_short, _factory_build_short_image_first, _studio_stitch,
# _studio_edit helpers removed (skills.content_factory and hydra.studio are stripped).


def _memory_root_arg(value: str | Path | None) -> Path:
    return Path(value).expanduser().resolve() if value is not None else DEFAULT_MEMORY_ROOT


def _recall_memory(
    memory_root: Path,
    *,
    max_chars: int = 12000,
    workspace_root: str | Path | None = None,
) -> dict[str, object]:
    result = build_local_memory_context(
        memory_root,
        max_chars=max_chars,
        workspace_root=workspace_root,
    )
    return {
        "status": result.status,
        "root": str(result.root),
        "context": result.context,
        "report": result.report,
        "data": result.data,
    }


def _glob_pattern_under(root: Path, path: str | Path, pattern: str) -> str:
    base = Path(path or ".")
    if base.is_absolute():
        root_resolved = Path(root).resolve()
        try:
            base = base.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise SkillError(f"glob path {base} escapes root {root_resolved}") from exc
    if str(base) in {"", "."}:
        return pattern
    return (base / pattern).as_posix()


def _grep_path_pattern_under(root: Path, path: str | Path, include: str | None) -> str:
    base = Path(path or ".")
    if base.is_absolute():
        root_resolved = Path(root).resolve()
        try:
            base = base.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise SkillError(f"grep path {base} escapes root {root_resolved}") from exc
    if ".." in base.parts:
        raise SkillError(f"`..` in grep path refused: {path!r}")
    base_text = base.as_posix()
    if any(mark in base_text for mark in ("*", "?", "[")):
        return base_text
    if include:
        if base_text in {"", "."}:
            return f"**/{include}"
        return (base / "**" / include).as_posix()
    if base_text in {"", "."}:
        return "**/*"
    return (base / "**/*").as_posix()


def _run_glob_tool(root: Path, *, pattern: str, path: str | Path = ".", max_results: int = 100) -> dict:
    return skill_glob.run(
        _glob_pattern_under(root, path, pattern),
        root=root,
        max_results=max_results,
    )


def _run_grep_tool(
    root: Path,
    *,
    pattern: str,
    path: str | Path = ".",
    max_matches: int = 50,
    include: str | None = None,
) -> dict:
    return skill_grep.run(
        pattern,
        root=root,
        path_pattern=_grep_path_pattern_under(root, path, include),
        max_results=max_matches,
    )


def _clamped_limit(value: int | str | None, *, default: int = 20, maximum: int = 100) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


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


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(query or "").lower())


def _skill_payload(record: Any, *, include_rendered: bool = False, max_chars: int = 4000) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": record.name,
        "description": record.description,
        "path": record.path.as_posix(),
        "heading": record.heading,
        "summary": record.summary,
    }
    if include_rendered:
        rendered = render_skill(record)
        payload["content"] = rendered[: max(1, max_chars)]
        payload["truncated"] = len(rendered) > max_chars
    return payload


def _skill_list_tool(*, query: str | None = None, limit: int = 20) -> dict[str, object]:
    records = list_skill_records()
    tokens = _query_tokens(query or "")
    if tokens:
        filtered = [
            record
            for record in records
            if all(token in _skill_text(record).replace("-", " ").replace("_", " ") for token in tokens)
        ]
    else:
        filtered = records
    bounded_limit = _clamped_limit(limit, default=20, maximum=100)
    return {
        "ok": True,
        "schema": "hydra.skills.list.v1",
        "query": query or "",
        "total_skills": len(records),
        "matched": len(filtered),
        "returned": min(len(filtered), bounded_limit),
        "skills": [_skill_payload(record) for record in filtered[:bounded_limit]],
    }


def _skill_search_tool(root: Path, *, query: str, limit: int = 8) -> dict[str, object]:
    return search_skill_library(
        query=query,
        root=root,
        limit=_clamped_limit(limit, default=8, maximum=25),
    )


def _skill_show_tool(*, name: str, max_chars: int = 4000) -> dict[str, object]:
    try:
        record = find_skill(name)
    except KeyError as exc:
        return {
            "ok": False,
            "schema": "hydra.skills.show.v1",
            "name": name,
            "error": str(exc),
        }
    return {
        "ok": True,
        "schema": "hydra.skills.show.v1",
        **_skill_payload(
            record,
            include_rendered=True,
            max_chars=_clamped_limit(max_chars, default=4000, maximum=12000),
        ),
        "next_step": "Use fs_read on path for the complete local SKILL.md when full instructions are needed.",
    }


def _skill_route_tool(*, prompt: str) -> dict[str, object]:
    try:
        payload = json.loads(render_route_json(prompt))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "schema": "hydra.skills.route.v1",
            "prompt": prompt,
            "error": str(exc),
        }
    payload["ok"] = True
    payload["total_skills"] = len(list_skill_records())
    return payload


# SEAM CUT: _agent_send_message, _agent_read_messages, collab helpers removed (multi-agent fabric stripped).
# SEAM CUT: _model_matrix_tool, _ssot_planner_model removed (model_matrix stripped).
# SEAM CUT: _backbone_ideate, _backbone_produce, _backbone_watch removed (hydra.backbone is stripped).


def _default_subagent_model() -> str:
    """Resolve the default subagent model from the SSOT planner role.

    Reads an optional spawn_model override from .hydraAgent/hydra.yaml in the
    current directory.  Falls back to the SSOT cloud-planner pair when the file
    is absent or the key is not set.  Mirrors the contract the declarative
    spawn_subagent tool uses so the caller always gets a consistent default.
    """
    import yaml as _yaml

    try:
        cfg_path = Path(".hydraAgent") / "hydra.yaml"
        if cfg_path.is_file():
            cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            override = (cfg.get("subagents") or {}).get("spawn_model")
            if override and isinstance(override, str) and override.strip():
                return override.strip()
    except Exception:  # noqa: BLE001 — config errors fall back to SSOT
        pass

    from hydra.model_routing import load_routing

    p, m = load_routing().role_pair("planner")
    return f"{p}/{m}"


def bind_tools(
    root: Path,
    approval_policy: str = "allow",
    *,
    memory_root: str | Path | None = None,
    memory_workspace_root: str | Path | None = None,
    notify_telegram: bool = False,
    surface_trusted: bool = True,  # UNLOCKED: operator controls all surfaces directly
) -> list[Tool]:
    """Wrap every skill with `root` pre-bound so the LLM only sees task args.

    `surface_trusted` carries the operator's rule-1 trust: True for the operator's own
    Telegram/CLI session (the LAW — only destructive gates), False for public/untrusted
    input (every action tool is queued for the operator's approval)."""
    # Honor the caller's notify_telegram (operator's bug #7). It was hardcoded True,
    # so passing False did nothing and EVERY caller — including tests — paged the
    # operator. The live chat/TUI/operator entrypoints pass notify_telegram=True so a
    # real approval reaches the operator's Telegram (they tap Approve to run it); tests
    # and non-live callers pass False (the default) so they never page. Quiet hours
    # (1-6am) still gate routine pings downstream in notify_approval.
    policy = ApprovalPolicy(
        approval_policy,
        notify_telegram=notify_telegram,
        surface_trusted=surface_trusted,
    )
    resolved_memory_root = _memory_root_arg(memory_root)
    resolved_memory_workspace_root = (
        Path(memory_workspace_root).expanduser().resolve()
        if memory_workspace_root is not None
        else root
    )
    _built = [
        Tool(
            name="fs_read",
            description="Read a text file from the workspace. Returns content + bytes_read. Use max_bytes to cap large files.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "relative file path"},
                    "max_bytes": {"type": "integer", "description": "maximum bytes to read (default 32768)", "default": 32768},
                    "limit": {"type": "integer", "description": "alias for max_bytes; maximum bytes to read"},
                },
                "required": ["path"],
            },
            invoke=lambda path, max_bytes=32768, limit=None, **_: skill_fs_read.run(
                path, root=root, max_bytes=limit if limit is not None else max_bytes
            ),
        ),
        Tool(
            name="fs_write",
            description=(
                "Atomically write text to a file under the workspace. "
                "Refuses to overwrite an existing file unless overwrite=True. "
                "Use fs_edit instead when changing only part of an existing file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
            invoke=guarded(
                policy,
                "fs_write",
                lambda path, content, overwrite=False: skill_fs_write.run(
                    path, content, root=root, overwrite=overwrite
                ),
            ),
        ),
        Tool(
            name="fs_edit",
            description=(
                "Replace `old_string` with `new_string` in a file. "
                "Refuses ambiguous matches (use count='all' to replace every "
                "occurrence, or include enough surrounding context to make "
                "the match unique). Pick this over fs_write when changing "
                "part of an existing file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "count": {
                        "description": 'positive integer or "all"',
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
            invoke=guarded(
                policy,
                "fs_edit",
                lambda path, old_string, new_string, count=1: skill_fs_edit.run(
                    path,
                    old_string,
                    new_string,
                    root=root,
                    count=count if count != "all" else "all",
                ),
            ),
        ),
        Tool(
            name="list_directory",
            description=(
                "List immediate children of a directory in the workspace. "
                "Returns entries with name, type (file/dir/symlink), and size."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": "."},
                    "include_hidden": {"type": "boolean", "default": True},
                },
                "required": [],
            },
            invoke=lambda path=".", include_hidden=True: skill_ls.run(
                path, root=root, include_hidden=include_hidden
            ),
        ),
        Tool(
            name="todo",
            description=(
                "Maintain a persistent per-workspace task list. Actions: "
                "'list' (no args), 'add' (text), 'done' (todo_id), "
                "'undone' (todo_id), 'remove' (todo_id), 'clear', "
                "'start' (todo_id — mark in_progress, demotes any previous "
                "in_progress to pending; single-in-progress discipline), "
                "'assert_closed' (no args — raises if any todo is still open; "
                "call before marking a run done). Always returns the full "
                "current list."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list",
                            "add",
                            "done",
                            "undone",
                            "remove",
                            "clear",
                            "start",
                            "assert_closed",
                        ],
                    },
                    "text": {"type": "string"},
                    "todo_id": {"type": "integer"},
                },
                "required": ["action"],
            },
            invoke=lambda action, text=None, todo_id=None: skill_todo.run(
                action, text=text, todo_id=todo_id, root=root
            ),
        ),
        Tool(
            name="memory_remember",
            description=(
                "Store a durable operator lesson in Hydra memory. Use this when "
                "the operator asks you to remember a stable preference, fact, or "
                "lesson for future agent turns."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lesson": {"type": "string"},
                    "source": {"type": "string", "default": "agent_tool"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["lesson"],
            },
            invoke=guarded(
                policy,
                "memory_remember",
                lambda lesson, source="agent_tool", tags=None: remember_lesson(
                    lesson,
                    source=source or "agent_tool",
                    tags=tags if isinstance(tags, list) else ["agent-tool"],
                    memory_root=resolved_memory_root,
                ),
            ),
        ),
        Tool(
            name="memory_recall",
            description=(
                "Load bounded, redacted Hydra durable memory context. Use this "
                "before answering questions that depend on operator preferences "
                "or prior Hydra lessons."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "default": 12000},
                },
                "required": [],
            },
            invoke=lambda max_chars=12000: _recall_memory(
                resolved_memory_root,
                max_chars=max_chars,
                workspace_root=resolved_memory_workspace_root,
            ),
        ),
        Tool(
            name="skill_list",
            description=(
                "List trusted local Hydra skill contracts, including the generated "
                "1400+ skill catalog. Read-only. Use query+limit to keep output bounded."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "optional text filter"},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": [],
            },
            invoke=lambda query=None, limit=20: _skill_list_tool(query=query, limit=limit),
        ),
        Tool(
            name="skill_search",
            description=(
                "Search Hydra's materialized SKILL.md library for relevant generated "
                "or bundle skills. Read-only and bounded."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 8},
                },
                "required": ["query"],
            },
            invoke=lambda query, limit=8: _skill_search_tool(root, query=query, limit=limit),
        ),
        Tool(
            name="skill_show",
            description=(
                "Show one trusted local skill by name, with source path and compact "
                "instructions. Use fs_read on the returned path for the full SKILL.md."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "max_chars": {"type": "integer", "default": 4000},
                },
                "required": ["name"],
            },
            invoke=lambda name, max_chars=4000: _skill_show_tool(name=name, max_chars=max_chars),
        ),
        Tool(
            name="skill_route",
            description=(
                "Route an operator prompt to trusted Hydra skills and native capability "
                "cards before planning or coding. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                },
                "required": ["prompt"],
            },
            invoke=lambda prompt: _skill_route_tool(prompt=prompt),
        ),
        # SEAM CUT: spawn_subagent + spawn_subagents tools removed (hydra.parallel_subagents stripped).
        # SEAM CUT: agent_send_message, agent_read_messages, collab_peers, collab_assign, collab_report, collab_read removed (multi-agent fabric stripped).
        Tool(
            name="grep",
            description="Search text files for a regex pattern under the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "max_matches": {"type": "integer", "default": 50},
                    "include": {"type": "string"},
                },
                "required": ["pattern"],
            },
            invoke=lambda pattern, path=".", max_matches=50, include=None: _run_grep_tool(
                root,
                pattern=pattern,
                path=path,
                max_matches=max_matches,
                include=include,
            ),
        ),
        Tool(
            name="glob",
            description="Find files by glob pattern under the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "max_results": {"type": "integer", "default": 100},
                },
                "required": ["pattern"],
            },
            invoke=lambda pattern, path=".", max_results=100: _run_glob_tool(
                root,
                pattern=pattern,
                path=path,
                max_results=max_results,
            ),
        ),
        Tool(
            name="bash",
            description=(
                "Run a shell command from the workspace root. Use for tests, "
                "package commands, and diagnostics."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "number", "default": 60},
                },
                "required": ["command"],
            },
            invoke=guarded(
                policy,
                "bash",
                lambda command, timeout=60: skill_bash.run(command, root=root, timeout=timeout),
            ),
        ),
        Tool(
            name="system_stats",
            description=(
                "Read local OS, disk, memory, process, and GPU statistics. "
                "This is read-only and does not require operator approval."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            invoke=lambda: skill_system_stats.run(),
        ),
        Tool(
            name="http_fetch",
            description="Fetch a URL with method/headers/body. Use for APIs or reachable docs.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "default": "GET"},
                    "headers": {"type": "object"},
                    "body": {"type": "string"},
                    "timeout": {"type": "number", "default": 20},
                    "max_bytes": {"type": "integer", "default": 200000},
                },
                "required": ["url"],
            },
            invoke=lambda url, method="GET", headers=None, body=None, timeout=20, max_bytes=200000: skill_http_fetch.run(
                url,
                method=method,
                headers=headers,
                body=body,
                timeout=timeout,
                max_bytes=max_bytes,
            ),
        ),
        # (task_planner skill does not add a standalone orchestrate tool in this build)
        # SEAM CUT: roster_count, model_matrix, skill_scout tools removed (multi-agent fabric stripped).
        # --- Browser (HydraAgent's own headless Chrome via Playwright) ---
        Tool(
            name="browser_navigate",
            description="Open a URL in HydraAgent's headless browser. Returns title + status. Call this before snapshot/click/type.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "absolute http(s) URL to open"},
                    "timeout_ms": {"type": "integer", "description": "navigation timeout (default 20000)", "default": 20000},
                },
                "required": ["url"],
            },
            invoke=lambda url, timeout_ms=20000: skill_browser.navigate(url, timeout_ms=timeout_ms),
        ),
        Tool(
            name="browser_snapshot",
            description="Return the current page's accessibility tree as text (roles, headings, links, buttons) — the best way to 'see' the page before acting.",
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "truncate the snapshot (default 8000)", "default": 8000},
                },
                "required": [],
            },
            invoke=lambda max_chars=8000: skill_browser.snapshot(max_chars=max_chars),
        ),
        Tool(
            name="browser_get_text",
            description="Return the visible body text of the current page.",
            parameters={
                "type": "object",
                "properties": {
                    "max_chars": {"type": "integer", "description": "truncate (default 8000)", "default": 8000},
                },
                "required": [],
            },
            invoke=lambda max_chars=8000: skill_browser.get_text(max_chars=max_chars),
        ),
        Tool(
            name="browser_click",
            description="Click an element on the current page by its visible text or a CSS selector.",
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "visible text (e.g. 'Sign in') or a CSS selector"},
                    "timeout_ms": {"type": "integer", "description": "default 8000", "default": 8000},
                },
                "required": ["target"],
            },
            invoke=lambda target, timeout_ms=8000: skill_browser.click(target, timeout_ms=timeout_ms),
        ),
        Tool(
            name="browser_type",
            description="Type text into a form field identified by a CSS selector.",
            parameters={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input (e.g. '#email')"},
                    "text": {"type": "string", "description": "text to type"},
                    "timeout_ms": {"type": "integer", "description": "default 8000", "default": 8000},
                },
                "required": ["selector", "text"],
            },
            invoke=lambda selector, text, timeout_ms=8000: skill_browser.type_text(selector, text, timeout_ms=timeout_ms),
        ),
        Tool(
            name="browser_screenshot",
            description="Capture a PNG screenshot of the current page (to a path, or returns byte length).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "file path to write the PNG to (optional)"},
                    "full_page": {"type": "boolean", "description": "capture the full scrollable page", "default": False},
                },
                "required": [],
            },
            invoke=lambda path=None, full_page=False: skill_browser.screenshot(path=path, full_page=full_page),
        ),
        Tool(
            name="browser_close",
            description="Close the browser session and free resources. Call when done browsing.",
            parameters={"type": "object", "properties": {}, "required": []},
            invoke=lambda: skill_browser.close(),
        ),
        Tool(
            name="browse",
            description=(
                "Agentic browsing: give a natural-language goal (e.g. 'find the current price of X on site Y') "
                "and HydraAgent autonomously drives its browser (snapshot->decide->act) to accomplish it, "
                "bounded by max_steps. Returns the answer + a step transcript. Use for multi-step web tasks; "
                "for a single navigation use browser_navigate + browser_snapshot."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "the natural-language browsing goal"},
                    "max_steps": {"type": "integer", "description": "max browser actions before stopping (default 12)", "default": 12},
                },
                "required": ["task"],
            },
            invoke=lambda task, max_steps=12: skill_browse_agent.run(task, max_steps=max_steps),
        ),
        # SEAM CUT: minimax_image/tts/music/video/web_search tools removed (skills.minimax_media stripped).
        # SEAM CUT: content_factory_build_short/_image_first tools removed (skills.content_factory stripped).
        # SEAM CUT: studio_stitch/studio_edit tools removed (hydra.studio stripped).
        # SEAM CUT: backbone_ideate/produce/watch tools removed (hydra.backbone is stripped).
    ]
    # Rule 1: the approval gate must see EVERY tool call, not just the four risky ones,
    # so the untrusted-surface escalation can stop a non-operator from running ANY action
    # tool (e.g. agent_send_message, collab_assign, spawn_subagent). require() is a no-op
    # for research tools and for non-destructive tools on a trusted surface, so wrapping
    # uniformly is cheap. Re-wrapping the already-guarded risky tools is harmless — the
    # first require() short-circuits before the inner one runs.
    for _t in _built:
        _t.invoke = guarded(policy, _t.name, _t.invoke)
    return _built


def root_arg(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else DEFAULT_FILESYSTEM_ROOT
