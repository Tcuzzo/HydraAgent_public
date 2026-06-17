"""Operator prompt composer: base system prompt + §10.59 context bundle.

This is the integration surface between the §10.59 context engine and any LLM
turn. Given a base system prompt, it appends a clearly-fenced context block
containing the highest-signal lessons, failure clusters, and recent
promotions, all under a hard byte budget. The fenced markers let an operator
(and downstream tools) trim or strip the context block cleanly.

Pure, read-only. No LLM calls happen here — this only assembles the *prompt
string* that callers can hand to ``AgentLoop`` (or any other LLM client).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from hydra.context_engine import (
    ContextEngineError,
    assemble_context,
)
from hydra.prompt_injection_guard import scan_for_injection


SCHEMA = "hydra.prompt_builder.v1"
CONTEXT_OPEN = "<!-- hydra-context-bundle-begin -->"
CONTEXT_CLOSE = "<!-- hydra-context-bundle-end -->"
CONTEXT_HEADER = "## HydraAgent operator context (priority: lessons → clusters → recent promotions)"
MISSION_HEADER = "## Hydra mission context"


class PromptBuilderError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def compose_operator_prompt(
    base_system_prompt: str,
    *,
    repo_root: str | Path,
    memory_root: str | Path | None = None,
    evidence_root: str | Path | None = None,
    budget_bytes: int = 4096,
    max_lessons: int = 8,
    max_clusters: int = 8,
    max_promotions: int = 8,
    include_context: bool = True,
    query: str | None = None,
    neutralize_injection: bool = True,
    mission_context: str = "",
) -> dict[str, Any]:
    """Compose an operator-facing system prompt with an embedded context bundle.

    When ``query`` is supplied, it is forwarded to §10.59 ``assemble_context``
    so candidates within each priority bucket are re-ranked by relevance to
    the query before the byte budget is applied — letting the operator's
    actual ask drive which lessons / clusters / promotions show up.

    Returns a dict with the rendered prompt plus the underlying context-engine
    report and a small set of proof rows so callers can log what they sent.
    """
    if not isinstance(base_system_prompt, str):
        raise PromptBuilderError("base_system_prompt must be a string")
    if query is not None and not isinstance(query, str):
        raise PromptBuilderError("query must be a string or None")
    if not isinstance(mission_context, str):
        raise PromptBuilderError("mission_context must be a string")
    base_prompt = _append_mission_context(base_system_prompt, mission_context)
    if not include_context:
        return {
            "schema": SCHEMA,
            "prompt": base_prompt,
            "context_included": False,
            "context_report": None,
            "proof": [
                f"base_bytes={len(base_system_prompt.encode('utf-8', errors='replace'))}",
                "context_included=False",
            ] + _mission_proof(mission_context, base_prompt),
        }

    try:
        context_report = assemble_context(
            repo_root=repo_root,
            memory_root=memory_root,
            evidence_root=evidence_root,
            budget_bytes=budget_bytes,
            max_lessons=max_lessons,
            max_clusters=max_clusters,
            max_promotions=max_promotions,
            query=query,
        )
    except ContextEngineError as e:
        raise PromptBuilderError(str(e)) from e

    rendered_context = context_report["rendered"].rstrip()
    injection_report = scan_for_injection(
        rendered_context, neutralize=neutralize_injection
    )
    safe_context = injection_report["neutralized_text"] if neutralize_injection else rendered_context
    if safe_context:
        block = "\n\n".join([
            CONTEXT_OPEN,
            CONTEXT_HEADER,
            safe_context,
            CONTEXT_CLOSE,
        ])
    else:
        # Even with no context, emit the fence so callers can verify presence
        block = "\n\n".join([
            CONTEXT_OPEN,
            CONTEXT_HEADER,
            "(no operator context available)",
            CONTEXT_CLOSE,
        ])

    full_prompt = (
        base_prompt.rstrip() + "\n\n" + block
        if base_prompt
        else block
    )
    return {
        "schema": SCHEMA,
        "prompt": full_prompt,
        "context_included": True,
        "context_report": context_report,
        "injection_scan": injection_report,
        "neutralize_injection": neutralize_injection,
        "proof": [
            f"base_bytes={len(base_system_prompt.encode('utf-8', errors='replace'))}",
            f"context_selected_bytes={context_report['selected_bytes']}",
            f"context_sources={len(context_report['sources'])}",
            f"context_truncated={context_report['truncated']}",
            f"final_bytes={len(full_prompt.encode('utf-8', errors='replace'))}",
            f"injection_is_safe={injection_report['is_safe']}",
            f"injection_patterns={injection_report['patterns_matched']}",
            f"context_open_marker={CONTEXT_OPEN!r}",
            f"context_close_marker={CONTEXT_CLOSE!r}",
        ] + _mission_proof(mission_context, full_prompt),
    }


def _append_mission_context(base_system_prompt: str, mission_context: str) -> str:
    mission_context = mission_context.strip()
    if not mission_context:
        return base_system_prompt
    injection_report = scan_for_injection(mission_context, neutralize=True)
    mission_block = "\n\n".join([MISSION_HEADER, injection_report["neutralized_text"]])
    return (
        base_system_prompt.rstrip() + "\n\n" + mission_block
        if base_system_prompt
        else mission_block
    )


def _mission_proof(mission_context: str, full_prompt: str) -> list[str]:
    if not mission_context.strip():
        return []
    return [
        "mission_context_included=True",
        f"mission_context_bytes={len(mission_context.encode('utf-8', errors='replace'))}",
        f"mission_prompt_bytes={len(full_prompt.encode('utf-8', errors='replace'))}",
    ]


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "Hydra prompt builder",
        f"context_included: {report['context_included']}",
    ]
    for p in report["proof"]:
        lines.append(f"  - {p}")
    lines.append("--- composed prompt ---")
    lines.append(report["prompt"])
    lines.append("--- end ---")
    return "\n".join(lines) + "\n"
