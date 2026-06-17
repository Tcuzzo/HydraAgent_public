"""Telegram operator message dispatcher for Hydra deterministic lanes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from gateways.telegram.gate import ActionRequest, Gate
from hydra.audit import run_audit
# SLICE 2 CUT: a stripped remote-audit module and hydra.loop_runtime stripped.
from hydra.chat_intents import route_chat_intent
from hydra.lessons import LessonError, remember_lesson
from hydra.locate import LocateResult, run_locate


REPO_ROOT = Path(__file__).resolve().parents[2]
OPERATOR_FILESYSTEM_ROOT = Path("/")
# One shared chat session so the operator's memory/recall is CONNECTED across
# surfaces (TUI ↔ Telegram) — "human-like recall across systems" (operator goal).
# (Group-vs-DM separation is handled at the approval-routing layer, not by
# fragmenting the session.)
SHARED_SESSION_ID = "default_chat_session"
AgentRunner = Callable[..., "OperatorReply"]


@dataclass(frozen=True)
class OperatorContext:
    chat_id: str | None = None
    message_id: int | None = None
    message_thread_id: int | None = None
    username: str | None = None
    first_name: str | None = None
    chat_type: str | None = None


@dataclass(frozen=True)
class OperatorReply:
    status: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


def _best_locate_path(result: LocateResult) -> str | None:
    best = result.data.get("best_match")
    if not best:
        return None
    return str((Path(result.data["root"]) / best["path"]).resolve(strict=False))


def _remember_parts(text: str) -> tuple[str, str] | None:
    match = re.match(r"(?is)^remember\s+(?P<lesson>.+?)\s+source\s+(?P<source>.+?)\s*$", text.strip())
    if not match:
        return None
    return match.group("lesson").strip(), match.group("source").strip()


def is_likely_work_message(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return False
    words = {
        "add", "analyze", "audit", "automate", "build", "change", "check",
        "clean", "clone", "code", "commit", "configure", "create", "debug",
        "delete", "deploy", "diagnose", "edit", "execute", "find", "fix",
        "format", "generate", "implement", "inspect", "install", "investigate",
        "list", "make", "migrate", "move", "optimize", "patch", "pull", "push",
        "refactor", "remove", "repair", "restart", "review", "route", "run",
        "scan", "search", "set", "setup", "show", "start", "stop", "test",
        "update", "build", "verify", "write",
    }
    return any(word in words for word in re.findall(r"[a-zA-Z0-9_]+", stripped.lower()))


def _reply_from_locate(result: LocateResult, state: dict[str, Any]) -> OperatorReply:
    best = _best_locate_path(result)
    if best:
        state["last_locate_target"] = best
    return OperatorReply(result.status, result.report, result.data)


def handle_operator_message(
    text: str,
    *,
    gate: Gate,
    state: dict[str, Any] | None = None,
    memory_root: str | Path | None = None,
    agent_runner: AgentRunner | None = None,
    context: OperatorContext | None = None,
) -> OperatorReply:
    state = state if state is not None else {}
    stripped = text.strip()
    if not stripped:
        return OperatorReply("IGNORED", "empty message")

    if not stripped.startswith("/"):
        clap = _injection_refusal(stripped, context)
        if clap is not None:
            return clap
        runner = agent_runner or run_agent_message
        return _invoke_agent_runner(runner, stripped, context=context)

    command = stripped[1:].strip()
    lower_command = command.lower()
    if not command:
        runner = agent_runner or run_agent_message
        return _invoke_agent_runner(runner, stripped, context=context)

    remember = _remember_parts(command)
    if remember:
        lesson, source = remember
        request = ActionRequest(
            action="remember durable lesson",
            detail=f"source: {source}\nlesson: {lesson}",
            timeout_s=60,
        )
        permitted, decision, reason = gate.authorize(request)
        if not permitted:
            return OperatorReply("DENIED", f"Approval is needed before remembering that: {reason}", {"decision": decision.value})
        try:
            report = remember_lesson(lesson, source=source, tags=["telegram"], memory_root=memory_root)
        except LessonError as e:
            return OperatorReply("BLOCKED", str(e))
        return OperatorReply("OK", f"Hydra remembered: {report['path']}", report)

    if lower_command == "status":
        return OperatorReply(
            "OK",
            (
                "Hydra is online and listening.\n"
                "Just send me a task — I'll pick up the tools and get it done. "
                "Only destructive actions pause for your approval before executing."
            ),
            {"kind": "status"},
        )
    if lower_command == "help":
        return OperatorReply("OK", _telegram_help_text(), {"kind": "help"})

    auth_reply = _handle_auth_command(command)
    if auth_reply is not None:
        return auth_reply

    session_reply = _handle_session_command(lower_command, context=context)
    if session_reply is not None:
        return session_reply

    if lower_command in {"that is the directory i want you to audit", "audit that directory", "audit it"}:
        target = state.get("last_locate_target")
        if not target:
            return OperatorReply("BLOCKED", "No prior locate target is available.")
        result = run_audit(target)
        state["last_audit_target"] = target
        return OperatorReply(result.status, result.report, result.data)

    intent = route_chat_intent(
        command,
        last_audit_target=state.get("last_audit_target"),
        last_locate_target=state.get("last_locate_target"),
    )
    if intent and intent.kind == "locate":
        query, _, root = intent.target.partition("\n")
        return _reply_from_locate(run_locate(query, root=root or "/"), state)
    if intent and intent.kind == "audit":
        result = run_audit(intent.target)
        state["last_audit_target"] = intent.target
        return OperatorReply(result.status, result.report, result.data)
    # SLICE 2 CUT: remote-audit intent stripped (remote_audit removed).

    runner = agent_runner or run_agent_message
    # Pass the full slash text so the Go harness can route /help, /loop, /run, etc.
    return _invoke_agent_runner(runner, stripped, context=context)


# --- chat budget glue (decision logic lives in hydra.chat_budget, real-tested) ---

def _chat_budget_key(context: OperatorContext | None) -> str:
    return (getattr(context, "username", None) or "operator").strip().lstrip("@").lower() or "operator"


def _chat_budget_ledger():
    import os
    from pathlib import Path

    from hydra.chat_budget import BudgetLedger

    path = os.environ.get("HYDRA_CHAT_BUDGET_PATH") or str(
        Path("~/.hydraAgent/workspace/chat_budget.json").expanduser()
    )
    return BudgetLedger(path)


def _chat_daily_budget() -> int:
    import os

    try:
        return int(os.environ.get("HYDRA_CHAT_DAILY_BUDGET", "200000"))
    except ValueError:
        return 200000


def _chat_over_budget(key: str) -> bool:
    try:
        return _chat_budget_ledger().over_budget(key, limit=_chat_daily_budget())
    except Exception:  # noqa: BLE001 — a budget hiccup must never break chat
        return False


def _record_chat_spend(key: str, prompt: str, reply: str) -> None:
    # Rough token estimate (~4 chars/token); LoopResult carries no usage. Best-effort.
    try:
        tokens = (len(prompt or "") + len(reply or "")) // 4
        _chat_budget_ledger().record(key, tokens)
    except Exception:  # noqa: BLE001
        return


# Operator = law, never gated by the injection guard. Set HYDRA_OPERATOR_USERNAME
# (comma-separated) to configure operator Telegram usernames.
_DEFAULT_OPERATOR_USERNAMES: tuple[str, ...] = ()


def _is_operator(context: OperatorContext | None) -> bool:
    import os

    username = (getattr(context, "username", None) or "").strip().lstrip("@").lower()
    if not username:
        return False
    configured = os.environ.get("HYDRA_OPERATOR_USERNAME", "")
    names = {n.strip().lstrip("@").lower() for n in configured.split(",") if n.strip()}
    names.update(_DEFAULT_OPERATOR_USERNAMES)
    return username in names


def _surface_trusted(context: OperatorContext | None) -> bool:
    """The surface is trusted only when the sender is the operator.
    A non-operator on any surface (a stranger in the group, public input)
    is untrusted, so their action tool calls are queued for the operator's approval.
    Unknown sender fails closed (untrusted)."""
    return _is_operator(context)


def _injection_refusal(text: str, context: OperatorContext | None) -> OperatorReply | None:
    """If a NON-operator sends a prompt-injection attempt, return a friendly refusal
    and skip the agent loop entirely (the injection never reaches the model).
    The operator always passes through. Returns None when nothing to do."""
    if _is_operator(context):
        return None
    from hydra.injection_guard import detect_injection, friendly_refusal

    verdict = detect_injection(text)
    if not verdict.is_injection:
        return None
    # Rotate the line by message length so it isn't a canned recording.
    line = friendly_refusal(verdict, seed_index=len(text))
    return OperatorReply(
        "OK",
        line,
        {"kind": "injection_blocked", "confidence": verdict.confidence, "patterns": verdict.patterns},
    )


def _invoke_agent_runner(
    runner: AgentRunner,
    text: str,
    *,
    context: OperatorContext | None,
) -> OperatorReply:
    if _runner_accepts_context(runner):
        return runner(text, context=context)
    return runner(text)


def _runner_accepts_context(runner: AgentRunner) -> bool:
    try:
        import inspect

        signature = inspect.signature(runner)
    except (TypeError, ValueError):
        return False
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "context":
            return True
    return False


def run_agent_message(text: str, *, context: OperatorContext | None = None) -> OperatorReply:
    """Run one bounded Hydra agent turn for a Telegram operator request."""
    try:
        return _run_agent_message(text, context=context)
    except Exception as exc:
        return OperatorReply(
            "BLOCKED",
            f"I couldn't start the Hydra Agent runtime: {type(exc).__name__}: {exc}",
            {"kind": "agent_turn", "error": type(exc).__name__},
        )


def _run_agent_message(text: str, *, context: OperatorContext | None = None) -> OperatorReply:
    import argparse

    from hydra.cli.cmd_chat import (
        DEFAULT_MEMORY_ROOT,
        DEFAULT_SYSTEM_PROMPT,
        _bind_tools,
        _local_gpu_fallback_client,
        _make_client_or_setup,
        _resolve_chat_runtime,
        _with_workspace_context,
        _llm_messages_from_session,
    )
    from hydra.providers import ProviderError, make_client
    from hydra.semantic_recall import build_semantic_memory_context
    from hydra.llm import LlmError
    from hydra.loop import AgentLoop
    from hydra.session_memory import (
        SessionMemoryError,
        add_message,
        create_session,
        get_session_messages,
        resolve_startup_history_limit,
        session_exists,
    )
    from hydra.skill_spine import build_agent_system_prompt, build_routed_skill_context

    # SLICE 2 CUT: _reply_from_loop_runtime removed (loop_runtime stripped).

    args = argparse.Namespace(
        profile="auto",
        provider=None,
        model=None,
        env_dir=None,
        setup_if_needed=False,
    )
    runtime = _resolve_chat_runtime(args)
    args.provider = runtime["provider"]
    client, cfg = _make_client_or_setup(args)
    model = args.model or cfg.model or runtime["model"]

    # Chat budget: if a NON-operator sender has used up today's real-chat budget,
    # keep talking on the cheap LOCAL model (qwen2.5-coder) instead of spending more
    # cloud. The operator is never silently downgraded, mirroring the injection-guard
    # exemption above. The decision is real-tested in hydra.chat_budget.
    budget_key = _chat_budget_key(context)
    if not _is_operator(context) and _chat_over_budget(budget_key):
        local = _local_gpu_fallback_client(args)
        if local is not None:
            client, cfg = local
            args.provider = cfg.name
            model = args.model or cfg.model
    base_prompt = build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT).rstrip()
    tools = _bind_tools(
        OPERATOR_FILESYSTEM_ROOT,
        approval_policy="allow",  # Execute freely, but notify_telegram enables buttons if approvals created elsewhere
        memory_root=DEFAULT_MEMORY_ROOT,
        memory_workspace_root=REPO_ROOT,
        notify_telegram=True,  # Enabled — callback infrastructure verified working
        # Rule 1: only the operator's own input is a trusted surface. A non-operator's
        # action tool calls are queued for the operator's approval (routed via Alpha).
        surface_trusted=_surface_trusted(context),
    )
    system_prompt = _telegram_system_prompt(
        base_prompt,
        workspace_root=OPERATOR_FILESYSTEM_ROOT,
        tool_names=_tool_names(tools),
    )
    routed_skill_context = build_routed_skill_context(text)
    if routed_skill_context:
        system_prompt += "\n\n" + routed_skill_context
    loop = AgentLoop(client, model=model, system_prompt=system_prompt)
    session_id = _telegram_session_id(context)

    # Classify with real source so peer→collab, operator→convo/steering.
    # Must happen before memory recall so the profile gates what gets appended.
    from hydra.intake import classify as _classify_intake
    from hydra.turn_profiles import load_turn_profile as _load_turn_profile

    _intake_source = "operator" if _is_operator(context) else "peer:telegram"
    _intake = _classify_intake(text, source=_intake_source)
    _profile = _load_turn_profile(_intake.kind)

    initial_messages: list[dict[str, str]] | None = [{"role": "system", "content": system_prompt}]
    # Query-aware recall: gated by profile.memory.enabled so convo turns never
    # pick up the execute-biasing memory block (the execute-bias fix).
    if _profile.memory.enabled:
        local_memory = build_semantic_memory_context(
            text,
            root=DEFAULT_MEMORY_ROOT,
            workspace_root=REPO_ROOT,
        )
        if local_memory.status == "OK" and local_memory.context:
            initial_messages.append({"role": "system", "content": local_memory.context})
    if session_id:
        try:
            if not session_exists(session_id):
                create_session(session_id, "HydraAgent persistent chat session started")
            _auto_compact_session(session_id)
            history = _llm_messages_from_session(
                get_session_messages(session_id, limit=resolve_startup_history_limit())
            )
            initial_messages.extend(history)
        except SessionMemoryError:
            pass

    attempted_provider_fallback = False
    while True:
        try:
            run_kwargs: dict[str, Any] = {
                "tools": tools if _profile.tools_enabled else [],
                "max_iterations": _profile.max_iterations,
                "max_tokens": _profile.max_tokens,
                "temperature": _profile.temperature,
                "timeout": 120.0,
            }
            if initial_messages is not None:
                run_kwargs["initial_messages"] = initial_messages
            result = loop.run(
                _with_workspace_context(text, OPERATOR_FILESYSTEM_ROOT),
                **run_kwargs,
            )
            break
        except LlmError:
            if attempted_provider_fallback:
                raise
            if args.provider == "ollama":
                # Local failed → cloud reasoning model (qwen3.5).
                try:
                    fallback = make_client("ollama-cloud", env_dir=getattr(args, "env_dir", None))
                except ProviderError:
                    fallback = None
            else:
                fallback = _local_gpu_fallback_client(args)
            if fallback is None:
                raise
            client, cfg = fallback
            args.provider = cfg.name
            model = args.model or cfg.model
            loop = AgentLoop(client, model=model, system_prompt=system_prompt)
            attempted_provider_fallback = True
    reply_text = _telegram_agent_reply_text(result.final_response)
    # Record this turn's rough token spend against the sender's daily chat budget.
    _record_chat_spend(budget_key, text, reply_text)
    if session_id:
        try:
            add_message(
                session_id,
                "user",
                text,
                {"source": "telegram", "chat_id": context.chat_id if context else None},
            )
            add_message(
                session_id,
                "assistant",
                reply_text,
                {"source": "telegram", "chat_id": context.chat_id if context else None},
            )
            _auto_compact_session(session_id)
        except SessionMemoryError:
            pass
    return OperatorReply(
        "OK",
        reply_text,
        {
            "kind": "agent_turn",
            "provider": cfg.name,
            "model": model,
            "iterations": result.iterations,
            "tool_calls": result.tool_calls_made,
            "halted": result.halted_reason,
        },
    )


def _reply_from_loop_runtime(text: str, *, context: OperatorContext | None = None) -> OperatorReply | None:
    # SLICE 2 CUT: loop_runtime stripped — this function is a no-op stub.
    return None


def _loop_session_context(context: OperatorContext | None) -> str:
    session_id = _telegram_session_id(context)
    if not session_id:
        return ""
    try:
        from hydra.session_memory import SessionMemoryError, get_session_messages, session_exists

        if not session_exists(session_id):
            return ""
        _auto_compact_session(session_id)
        history = _llm_safe_session_messages(get_session_messages(session_id, limit=12))
    except SessionMemoryError:
        return ""
    if not history:
        return ""
    lines = ["Recent Telegram context:"]
    for message in history[-12:]:
        role = message["role"]
        content = " ".join(message["content"].split())
        if len(content) > 500:
            content = content[:497].rstrip() + "..."
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _loop_context_with_recall(text: str, context: OperatorContext | None) -> str:
    """Recent session context PLUS query-aware semantic recall for the Go chat path.

    The Go runtime only sees what we hand it via ``--context``; it cannot reach the
    memory corpus. We prepend the tightened Python recall (true-cosine floor +
    lowered top_k + the no-invention/answer-from-memory header) so a "what do you
    remember" turn is answered from the REAL recalled rule, not a confabulation.
    If recall is empty/unavailable, we degrade to the session context alone — never
    a crash, never the 12 KB dump (the floor already bounds the recall).
    """
    session_context = _loop_session_context(context)
    recall_block = ""
    try:
        from hydra.cli.cmd_chat import DEFAULT_MEMORY_ROOT
        from hydra.semantic_recall import build_semantic_memory_context

        recall = build_semantic_memory_context(
            text,
            root=DEFAULT_MEMORY_ROOT,
            workspace_root=REPO_ROOT,
        )
        if recall.status == "OK" and recall.context:
            recall_block = recall.context
    except Exception:
        recall_block = ""
    parts = [part for part in (recall_block, session_context) if part]
    return "\n\n".join(parts)


def _remember_loop_runtime_turn(context: OperatorContext | None, user_text: str, reply_text: str) -> None:
    session_id = _telegram_session_id(context)
    if not session_id:
        return
    try:
        from hydra.session_memory import SessionMemoryError, add_message, create_session, session_exists

        if not session_exists(session_id):
            create_session(session_id)
        add_message(
            session_id,
            "user",
            user_text,
            {"source": "telegram", "chat_id": context.chat_id if context else None, "runtime": "hydra"},
        )
        add_message(
            session_id,
            "assistant",
            reply_text,
            {"source": "telegram", "chat_id": context.chat_id if context else None, "runtime": "hydra"},
        )
        _auto_compact_session(session_id)
    except SessionMemoryError:
        return


def _telegram_identity_anchor() -> str:
    """Authoritative Hydra identity text for the Telegram surface.

    The TUI bakes the identity into a system message (cmd_chat._chat_runtime_message),
    but Telegram previously never included render_identity_text — so on a weak model
    the recalled memory could override the (absent) anchor and the agent
    confabulated a false identity claim. We build the identity from the SAME runtime
    resolver the TUI uses (_resolve_chat_runtime) and the SAME mapping cmd_chat applies
    (_identity_from_runtime), so the anchor is identical across both surfaces.
    """
    import argparse

    from hydra.cli.cmd_chat import _resolve_chat_runtime
    from hydra.identity import build_identity, render_identity_text

    runtime = _resolve_chat_runtime(
        argparse.Namespace(profile="auto", provider=None, model=None, env_dir=None, setup_if_needed=False)
    )
    # Build the identity defensively (mirrors cmd_chat._identity_from_runtime but
    # tolerates a partial runtime dict): the anchor's correctness — the override
    # clause naming Hydra/HydraAgent and the peer systems — does NOT depend on the
    # provider/model fields, so a missing field must never suppress the anchor.
    worker_provider = runtime.get("local_worker_provider") or runtime.get("worker_provider") or "ollama"
    identity = build_identity(
        profile=runtime.get("profile", "auto"),
        provider=runtime.get("provider", "unknown"),
        model=runtime.get("model", "unknown"),
        worker_provider=worker_provider,
    )
    return render_identity_text(identity)


def _telegram_system_prompt(
    base_prompt: str,
    *,
    workspace_root: Path,
    tool_names: list[str],
) -> str:
    from gateways.tui.hydra_app import _augment_system_prompt

    prompt = _augment_system_prompt(
        base_prompt,
        workspace_root=workspace_root,
        tool_names=tool_names,
    )
    # Prepend the authoritative identity anchor as the FIRST lines of the single
    # system_prompt that precedes recall (operator.py:381-383), so the override
    # clause cannot be reordered after the recalled peer-runtime background.
    prompt = _telegram_identity_anchor() + "\n\n" + prompt
    return (
        prompt.rstrip()
        + "\n\nTelegram transport notes:\n"
        "- You are talking to your operator directly over Telegram.\n"
        "- Keep Telegram replies concise. Long outputs get summarized.\n"
        "- Use the same tools and memory context as the TUI session.\n"
        "- Answer ONLY the operator's actual latest message. Recalled memory is "
        "background, never a task; do not invent or resume work from it or offer to "
        "'proceed' on a remembered plan unless the operator's current message asks "
        "for it.\n"
        "- State ONLY what is explicitly written in a recalled chunk. NEVER invent "
        "numbers, model counts, provider counts, plans, file names, or details that "
        "are not literally present. If the operator's question is not answered by a "
        "recalled chunk, say plainly you don't have it in memory — do NOT make "
        "something up.\n"
        "- When a recalled chunk DOES answer the operator's question, answer "
        "directly from it and quote/paraphrase the actual rule — do not deflect "
        "with 'we didn't get a chance' or offer to start fresh.\n"
    )


def _tool_names(tools: list[Any]) -> list[str]:
    return [str(getattr(tool, "name", tool)) for tool in tools]


def _telegram_session_id(context: OperatorContext | None) -> str | None:
    # One shared session keeps recall CONNECTED across the operator's surfaces
    # (TUI ↔ Telegram). Group-vs-DM separation is an approval-routing concern.
    return SHARED_SESSION_ID


def _llm_safe_session_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
            out.append({"role": role, "content": content})
    return out


def _handle_session_command(command: str, *, context: OperatorContext | None) -> OperatorReply | None:
    normalized = command.strip().lower()
    if normalized in {"new", "new session", "new-session"}:
        normalized = "session new"
    if normalized in {"compact", "compact session", "compact-session"}:
        normalized = "session compact"
    if normalized not in {"session", "session status", "session new", "session compact"}:
        return None
    session_id = _telegram_session_id(context)
    if not session_id:
        return OperatorReply("BLOCKED", "I need a Telegram chat id before I can manage that session.")
    try:
        from hydra.session_memory import (
            SessionMemoryError,
            compact_session,
            create_session,
            get_session_messages,
            rotate_session,
            session_exists,
        )

        if normalized in {"session", "session status"}:
            if not session_exists(session_id):
                create_session(session_id, "HydraAgent Telegram session started")
            count = len(get_session_messages(session_id))
            return OperatorReply("OK", f"Telegram session `{session_id}` is active with {count} stored messages.", {"kind": "session_status", "session_id": session_id, "messages": count})
        if normalized == "session new":
            rotate_session(session_id, "HydraAgent new Telegram session started")
            return OperatorReply("OK", "New Telegram session started. Old context was archived locally.", {"kind": "session_new", "session_id": session_id})
        report = compact_session(session_id, max_entries=1) if session_exists(session_id) else {"compacted": False}
        state = "compacted" if report.get("compacted") else "already compact"
        return OperatorReply("OK", f"Telegram session {state}.", {"kind": "session_compact", "session_id": session_id, "report": report})
    except SessionMemoryError as exc:
        return OperatorReply("BLOCKED", str(exc), {"kind": "session_error", "session_id": session_id})


def _telegram_help_text() -> str:
    return (
        "Hydra is online. Talk normally for chat or tasks.\n"
        "Useful commands: /status, /mode, /mode iteration, /mode yolo CODE, /lock, /mfa setup, /session status, /session new, /session compact.\n"
        "Read-only inspection runs directly. Destructive actions use the one approval path."
    )


def _handle_auth_command(command: str) -> OperatorReply | None:
    normalized = command.strip()
    lower = normalized.lower()
    if lower not in {"mode", "mode operator", "mode iteration", "lock", "mfa setup"} and not (
        lower.startswith("mode yolo ") or lower.startswith("yolo ")
    ):
        return None
    from hydra.operator_auth import OperatorAuth, OperatorAuthError

    auth = OperatorAuth()
    if lower == "mode":
        status = auth.status()
        if status.yolo_active:
            minutes = max(1, int((status.expires_in_seconds or 0) / 60))
            return OperatorReply(
                "OK",
                f"Mode: yolo. Local and network authority are unlocked for about {minutes} minutes.",
                {"kind": "auth_status", "status": status.to_dict()},
            )
        return OperatorReply("OK", f"Mode: {status.mode}.", {"kind": "auth_status", "status": status.to_dict()})
    if lower in {"mode operator", "lock"}:
        status = auth.lock()
        return OperatorReply("OK", "Mode set to operator. Yolo authority is locked.", {"kind": "auth_mode", "status": status.to_dict()})
    if lower == "mode iteration":
        status = auth.set_mode("iteration")
        return OperatorReply("OK", "Mode set to iteration.", {"kind": "auth_mode", "status": status.to_dict()})
    if lower == "mfa setup":
        setup = auth.setup_totp(force=False)
        return OperatorReply(
            "OK",
            (
                "Google Authenticator setup:\n"
                f"{setup.provisioning_uri}\n\n"
                "Scan this from the local TUI if you want a QR, or paste the URI into Authenticator."
            ),
            {"kind": "auth_setup", "secret_path": str(setup.secret_path)},
        )
    code = normalized.split(maxsplit=2)[-1].strip()
    try:
        status = auth.unlock_yolo(code)
    except OperatorAuthError as exc:
        return OperatorReply("DENIED", f"Yolo unlock failed: {exc}", {"kind": "auth_unlock_failed"})
    minutes = max(1, int((status.expires_in_seconds or 0) / 60))
    return OperatorReply(
        "OK",
        f"Yolo unlocked. Hydra has local and network authority for about {minutes} minutes.",
        {"kind": "auth_yolo", "status": status.to_dict()},
    )


def _auto_compact_session(session_id: str) -> None:
    try:
        from hydra.session_memory import SessionMemoryError, compact_session

        compact_session(session_id)
    except SessionMemoryError:
        return


def _telegram_agent_reply_text(text: str, *, limit: int = 3600) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
    if not normalized:
        normalized = "I ran the agent turn, but the model returned an empty final response."
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 80)].rstrip() + "\n\n[truncated for Telegram]"
