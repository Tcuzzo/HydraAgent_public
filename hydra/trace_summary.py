"""Summarize §10.85 ask traces and §10.87 chat JSONL traces.

Reads a saved trace file (either `hydra.ask_trace.v1` JSON or a `.jsonl`
of `hydra.chat_trace_turn.v1` lines), aggregates tool-call counts and
wall-time, surfaces the slowest tools, error rate, and per-tool
percentile-style summary, and emits both a human-readable text report
and a structured JSON report.

Pure read-only; deterministic for identical input. No LLM calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCHEMA = "hydra.trace_summary.v1"
ASK_TRACE_SCHEMA = "hydra.ask_trace.v1"
CHAT_TURN_SCHEMA = "hydra.chat_trace_turn.v1"


class TraceSummaryError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def summarize_trace(path: str | Path) -> dict[str, Any]:
    """Detect ask or chat trace format and return one structured summary."""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise TraceSummaryError(f"trace file not found: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        raise TraceSummaryError(f"trace file is empty: {p}")

    # Detect format: ask = single JSON object; chat = JSONL of turn objects.
    turns: list[dict[str, Any]]
    trace_kind: str
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, dict) and parsed.get("schema") == ASK_TRACE_SCHEMA:
        turns = [parsed]
        trace_kind = "ask"
    else:
        turns = []
        for line_num, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise TraceSummaryError(
                    f"line {line_num} is not valid JSON: {e}"
                ) from e
            if obj.get("schema") != CHAT_TURN_SCHEMA:
                raise TraceSummaryError(
                    f"line {line_num} has unexpected schema {obj.get('schema')!r}; "
                    f"expected {CHAT_TURN_SCHEMA!r}"
                )
            turns.append(obj)
        if not turns:
            raise TraceSummaryError(f"no recognised trace records in {p}")
        trace_kind = "chat"

    return _build_summary(turns, trace_kind, p)


def render_text(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"Hydra trace summary: {report['trace_kind']} from {report['source']}",
        f"turns={report['turn_count']}  "
        f"total tool calls={s['total_tool_calls']}  "
        f"tool errors={s['tool_errors']}  "
        f"total tool wall_ms={s['total_tool_wall_ms']}",
        f"halted reasons: " + ", ".join(
            f"{k}={v}" for k, v in sorted(s["halted_reasons"].items())
        ) if s["halted_reasons"] else "halted reasons: (none recorded)",
    ]
    if report["per_tool"]:
        lines.append("per-tool (count, total_ms, errors):")
        for row in report["per_tool"]:
            lines.append(
                f"  - {row['tool_name']}  "
                f"count={row['count']}  "
                f"total_ms={row['total_ms']}  "
                f"errors={row['errors']}"
            )
    if report["slowest_calls"]:
        lines.append("slowest individual calls:")
        for row in report["slowest_calls"]:
            lines.append(
                f"  - turn={row['turn']} {row['tool_name']} "
                f"duration_ms={row['duration_ms']} "
                f"error={row['tool_error']!r}"
            )
    return "\n".join(lines) + "\n"


def _build_summary(
    turns: list[dict[str, Any]], trace_kind: str, source: Path
) -> dict[str, Any]:
    per_tool: dict[str, dict[str, int]] = {}
    halted_reasons: dict[str, int] = {}
    total_tool_calls = 0
    total_tool_wall_ms = 0
    tool_errors = 0
    all_calls: list[dict[str, Any]] = []
    iterations_total = 0

    for idx, turn in enumerate(turns, start=1):
        halt = turn.get("halted_reason") or ""
        if halt:
            halted_reasons[halt] = halted_reasons.get(halt, 0) + 1
        iterations_total += int(turn.get("iterations") or 0)
        total_tool_calls += int(turn.get("tool_calls_made") or 0)
        total_tool_wall_ms += int(turn.get("tool_wall_ms") or 0)
        turn_index = turn.get("turn_index", idx) if trace_kind == "chat" else idx
        for step in turn.get("tool_steps") or []:
            name = step.get("tool_name") or "<unknown>"
            duration = step.get("duration_ms")
            entry = per_tool.setdefault(
                name,
                {"tool_name": name, "count": 0, "total_ms": 0, "errors": 0},
            )
            entry["count"] += 1
            if isinstance(duration, int):
                entry["total_ms"] += duration
            if step.get("tool_error"):
                entry["errors"] += 1
                tool_errors += 1
            all_calls.append({
                "turn": turn_index,
                "tool_name": name,
                "duration_ms": duration if isinstance(duration, int) else 0,
                "tool_error": step.get("tool_error"),
            })

    per_tool_sorted = sorted(
        per_tool.values(),
        key=lambda r: (-r["total_ms"], -r["count"], r["tool_name"]),
    )
    slowest = sorted(
        all_calls,
        key=lambda r: (-r["duration_ms"], r["tool_name"]),
    )[:5]

    return {
        "schema": SCHEMA,
        "trace_kind": trace_kind,
        "source": str(source),
        "turn_count": len(turns),
        "summary": {
            "total_tool_calls": total_tool_calls,
            "total_tool_wall_ms": total_tool_wall_ms,
            "tool_errors": tool_errors,
            "halted_reasons": dict(sorted(halted_reasons.items())),
            "iterations_total": iterations_total,
        },
        "per_tool": per_tool_sorted,
        "slowest_calls": slowest,
        "policy": "deterministic aggregation over saved trace; no LLM",
    }
