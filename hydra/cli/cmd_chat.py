"""Ask/chat CLI command handlers and parser registration."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from hydra.inter_agent import redact_text
from hydra.llm import LlmError
from hydra.loop import AgentLoop
from hydra.policy import POLICY_CHOICES
from hydra.providers import (
    DEFAULT_ENV_DIR,
    ProviderError,
    list_providers as list_providers_fn,
    make_client,
    resolve,
)
from hydra.identity import build_identity, render_identity_text, render_runtime_text
from hydra.roles import RoleError, resolve_roles
# SEAM CUT: a stripped remote-audit module is stripped; remote-audit intent handler removed below.
from hydra.skill_spine import (
    build_agent_system_prompt,
    build_routed_skill_context,
    build_skill_doctrine,
    find_skill,
    list_skill_records,
    render_skill_doctor,
    render_skill,
    render_skill_list,
)
from hydra.audit import run_audit as run_directory_audit
from hydra.local_memory import (
    DEFAULT_MAX_CHARS as LOCAL_MEMORY_MAX_CHARS,
    DEFAULT_MEMORY_ROOT,
    build_local_memory_context,
)
from hydra.semantic_recall import build_semantic_memory_context
from hydra.memory_kernel import assemble_truth_context
from hydra.rubric_judge import RubricJudgeError, judge as rubric_judge
from hydra.prompt_builder import PromptBuilderError, compose_operator_prompt
from hydra.runtime_route import default_runtime_route, route_string
from hydra.model_routing import load_routing
from hydra.session_memory import (
    DEFAULT_STARTUP_HISTORY_LIMIT,
    add_message,
    compact_session,
    create_session,
    get_session_messages,
    resolve_startup_history_limit,
    session_exists,
)
from hydra.mission import MissionError, create_mission, load_mission, render_mission_text
from hydra.cli.cmd_chat_support import (
    best_locate_path as _best_locate_path,
    print_audit_result as _print_audit_result,
    print_local_memory_result as _print_local_memory_result,
    print_locate_result as _print_locate_result,
)
from hydra.cli.tool_binding import bind_tools as _bind_tools, root_arg as _root_arg
from hydra.chat_intents import route_chat_intent
from hydra.locate import run_locate
from gateways.tui.elite import EliteTUI


REPO_ROOT = Path(__file__).resolve().parents[2]


def build_chat_profile_defaults() -> dict[str, tuple[str, str]]:
    """Build the chat-profile (provider, model) map from the single YAML source.

    Reads hydra/model_routing.yaml via the typed loader (hydra.model_routing).
    Editing a model name there changes the resolved chat model everywhere — no
    Python edit here. The chat/conversation brain uses the cloud-doer roster entry;
    local qwen2.5-coder stays for forced --local + bounded worker loops. If the
    YAML is missing/invalid the loader returns the frozen DEFAULT (same values),
    so this never crashes and never silently picks a different model.
    """
    routing = load_routing()
    return {
        "auto": routing.chat_default("auto"),
        "cloud": routing.chat_default("cloud"),
        "local": routing.worker_default(),
    }


# Kept as a module-level dict (same name + shape) so all existing callers/tests
# are untouched — but its values now come from the YAML, not literals.
CHAT_PROFILE_DEFAULTS = build_chat_profile_defaults()
DEFAULT_WORKBENCH_API = "http://127.0.0.1:8765"


DEFAULT_SYSTEM_PROMPT = (
    "You are Hydra — a persistent coding and ops agent with full access to the operator's machine. "
    "You can read, write, and execute anywhere the operator has granted permission. "
    "Use tools to inspect and modify files; never invent file contents or tool results. "
    "For audits, use grep, glob, and list_directory before concluding something is missing. "
    "You have a local skill library — call skill_search to find a relevant skill before "
    "planning domain work, and reuse it instead of starting from scratch. "
    "For big or multi-part jobs, spawn parallel subagents (spawn_subagents) so several run at "
    "once instead of one slow sequential pass — the operator's cloud key lets you run many. "
    "The operator drives you in plain English; you do NOT need a slash command to act — read "
    "the intent, confirm it in one line if it's ambiguous, then DO it with real tool calls. "
    "If you say you did something, you MUST have actually called the tool that did it. "
    "Know what you have: your tools are listed to you each turn and your skills are searchable "
    "via skill_search — survey them before deciding, then use them. "
    "VERIFY before you call it done: this repo's tests live in tests/ and hydra/ (run them with "
    "`python3 -m pytest -q` — add `-n auto` to run them in parallel and fast). After you change "
    "code, run the relevant tests and only report GREEN once they actually pass. Never claim "
    "success on belief alone. "
    "You do not give up. If the first approach fails, diagnose and try another. "
    "When a task is done, confirm it plainly in one or two sentences. "
    "Speak like a senior engineer who cares about getting things right — warm, direct, no filler."
)

HYDRA_CONFIG = REPO_ROOT / ".hydraAgent" / "hydra.yaml"
HYDRA_SCHEMA = REPO_ROOT / ".hydraAgent" / "schemas" / "hydra.schema.yaml"
OPS_PACKS_DIR = REPO_ROOT / ".hydraAgent" / "ops-packs"
MAX_HISTORICAL_ASSISTANT_CHARS = 4000

def _with_workspace_context(prompt: str, root: Path) -> str:
    scope_note = "entire machine (/)" if root == Path("/") else str(root)
    return (
        f"Current filesystem scope: {scope_note}\n"
        "You have full access to the filesystem. Absolute paths are valid. "
        "For audits, prefer grep/glob/list_directory "
        "before saying no files were found.\n\n"
        "Operator request:\n"
        f"{prompt}"
    )


def _llm_messages_from_session(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Project persisted session records to provider-safe LLM messages."""
    out: list[dict[str, str]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if (
            role in {"system", "user", "assistant", "tool"}
            and isinstance(content, str)
            and content.strip()
            and not _looks_like_paste_fragment(content)
        ):
            out.append({"role": role, "content": _session_content_for_llm(role, content)})
    return _drop_unanswered_session_turns(out)


def _drop_unanswered_session_turns(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for index, message in enumerate(messages):
        if message["role"] in {"user", "tool"}:
            next_role = next(
                (
                    candidate["role"]
                    for candidate in messages[index + 1 :]
                    if candidate["role"] != "system"
                ),
                None,
            )
            if next_role != "assistant":
                continue
        cleaned.append(message)
    return cleaned


def _session_content_for_llm(role: str, content: str) -> str:
    text = content.strip()
    if role in {"assistant", "tool"} and len(text) > MAX_HISTORICAL_ASSISTANT_CHARS:
        return f"[prior {role} response omitted from live context: {len(text)} chars]"
    return text


def _looks_like_paste_fragment(content: str) -> bool:
    """Drop single-line box/diagram fragments from persisted chat context."""
    text = content.strip()
    if "\n" in text or len(text) > 500:
        return False
    visible = [ch for ch in text if not ch.isspace()]
    if not visible:
        return True
    box_chars = set("│─┌┐└┘├┤┬┴┼╭╮╰╯═║╔╗╚╝╟╢╤╧▼►◄")
    box_count = sum(1 for ch in visible if ch in box_chars)
    return box_count / len(visible) >= 0.45


def _build_ask_trace(
    *,
    prompt: str,
    provider: str,
    model: str,
    system_prompt: str,
    context_proof: list[str],
    result: Any,
    approval_policy: str,
) -> dict[str, Any]:
    """Assemble the §10.85 ask trace payload from an AgentLoop result."""
    tool_steps: list[dict[str, Any]] = []
    total_tool_ms = 0
    for step in getattr(result, "steps", []) or []:
        if getattr(step, "kind", "") != "tool_result":
            continue
        duration = getattr(step, "tool_duration_ms", None)
        if isinstance(duration, int):
            total_tool_ms += duration
        tool_steps.append({
            "step": getattr(step, "step", None),
            "tool_name": getattr(step, "tool_name", None),
            "tool_call_id": getattr(step, "tool_call_id", None),
            "duration_ms": duration,
            "tool_error": getattr(step, "tool_error", None),
        })

    return {
        "schema": "hydra.ask_trace.v1",
        "prompt": prompt,
        "provider": provider,
        "model": model,
        "trace_id": getattr(result, "trace_id", ""),
        "system_prompt_bytes": len(system_prompt.encode("utf-8", errors="replace")),
        "context_proof": list(context_proof),
        "iterations": getattr(result, "iterations", 0),
        "tool_calls_made": getattr(result, "tool_calls_made", 0),
        "halted_reason": getattr(result, "halted_reason", ""),
        "final_response": getattr(result, "final_response", ""),
        "messages": [
            _redact_trace_message(msg) for msg in getattr(result, "messages", [])
        ],
        "tool_steps": tool_steps,
        "tool_wall_ms": total_tool_ms,
        "approval_policy": approval_policy,
    }


def _build_chat_trace_turn(
    *,
    turn_index: int,
    user_prompt: str,
    provider: str,
    model: str,
    system_prompt: str,
    result: Any,
    approval_policy: str,
) -> dict[str, Any]:
    """Assemble one §10.87 chat trace turn payload from an AgentLoop result."""
    tool_steps: list[dict[str, Any]] = []
    total_tool_ms = 0
    for step in getattr(result, "steps", []) or []:
        if getattr(step, "kind", "") != "tool_result":
            continue
        duration = getattr(step, "tool_duration_ms", None)
        if isinstance(duration, int):
            total_tool_ms += duration
        tool_steps.append({
            "tool_name": getattr(step, "tool_name", None),
            "tool_call_id": getattr(step, "tool_call_id", None),
            "duration_ms": duration,
            "tool_error": getattr(step, "tool_error", None),
        })

    return {
        "schema": "hydra.chat_trace_turn.v1",
        "turn_index": turn_index,
        "prompt": user_prompt,
        "provider": provider,
        "model": model,
        "trace_id": getattr(result, "trace_id", ""),
        "system_prompt_bytes": len(system_prompt.encode("utf-8", errors="replace")),
        "iterations": getattr(result, "iterations", 0),
        "tool_calls_made": getattr(result, "tool_calls_made", 0),
        "halted_reason": getattr(result, "halted_reason", ""),
        "final_response": getattr(result, "final_response", ""),
        "tool_steps": tool_steps,
        "tool_wall_ms": total_tool_ms,
        "approval_policy": approval_policy,
    }


def _redact_trace_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Project an LLM message dict to a trace-safe shape.

    Drops nothing structural but truncates each content / tool_call argument
    string at 4 KiB so a single chatty tool result can't blow the trace file.
    """
    out: dict[str, Any] = {"role": msg.get("role")}
    for key in ("name", "tool_call_id"):
        if key in msg:
            out[key] = msg[key]
    content = msg.get("content")
    if isinstance(content, str):
        safe = redact_text(content) or ""
        out["content"] = safe[:4096] + ("\n[truncated]" if len(safe) > 4096 else "")
    elif content is not None:
        safe = redact_text(str(content)) or ""
        out["content"] = safe[:4096]
    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        out["tool_calls"] = [
            {
                "id": tc.get("id"),
                "type": tc.get("type"),
                "function": {
                    "name": (tc.get("function") or {}).get("name"),
                    "arguments": (redact_text(str((tc.get("function") or {}).get("arguments", ""))) or "")[:4096],
                },
            }
            for tc in tool_calls
            if isinstance(tc, dict)
        ]
    return out


def _judge_ask_response(
    response: str, rubric_path: str | None, *, threshold: float = 1.0
) -> dict | None:
    """Run the §10.60 rubric judge against an ask reply; return None if no rubric.

    Returns the judge report dict on success. Raises RubricJudgeError or
    OSError to the caller — `cmd_ask` translates those into stderr messages.
    """
    if not rubric_path:
        return None
    rubric_raw = json.loads(Path(rubric_path).expanduser().read_text(encoding="utf-8"))
    return rubric_judge(response, rubric_raw, pass_threshold=threshold)


def _build_chat_context_message(
    args: argparse.Namespace, memory_root: Path
) -> dict | None:
    """Build the optional §10.59 context system message for `hydra chat`.

    Returns None when the operator did not pass --with-context. On error the
    context-engine refusal is surfaced on stderr and the function returns None
    rather than crashing the chat session.
    """
    if not getattr(args, "with_context", False):
        return None
    try:
        composed = compose_operator_prompt(
            "",
            repo_root=REPO_ROOT,
            memory_root=memory_root,
            evidence_root=REPO_ROOT / "evidence",
            budget_bytes=getattr(args, "context_budget_bytes", 4096),
        )
    except PromptBuilderError as e:
        print(f"chat context error: {e}", file=sys.stderr)
        return None
    print(
        "Hydra context bundle injected: "
        + "; ".join(composed["proof"][:3])
    )
    return {"role": "system", "content": composed["prompt"]}


def _truth_context_message(query: str, memory_root: Path, *, budget_chars: int) -> dict | None:
    try:
        context = assemble_truth_context(
            query or "hydra mission memory",
            repo_root=REPO_ROOT,
            memory_root=memory_root,
            budget_chars=budget_chars,
        )
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"truth memory error: {e}", file=sys.stderr)
        return None
    if not context["records"]:
        return None
    print("Hydra truth memory injected: " + "; ".join(context["proof"][:3]))
    return {"role": "system", "content": context["text"]}


def _ensure_chat_mission_context(
    root: Path,
    *,
    current_mission_id: str | None,
    operator_prompt: str,
) -> tuple[str, str]:
    if current_mission_id:
        mission = load_mission(root, current_mission_id)
    else:
        mission = create_mission(
            root=root,
            operator_prompt=operator_prompt,
            intent="build",
            next_action="inspect",
        )
        _write_current_mission_marker(root, mission.mission_id)
    return mission.mission_id, render_mission_text(mission)


def _write_current_mission_marker(root: Path, mission_id: str) -> None:
    marker = root / ".hydraAgent" / "current_mission"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(mission_id + "\n", encoding="utf-8")


def _auto_route_role(args: argparse.Namespace) -> dict | None:
    """Classify args.prompt to a §10.34 role when --auto-route is set.

    Returns the classifier report (with `role`, `score`, `matched`) or None
    if --auto-route was not set. The actual role-router resolution
    happens elsewhere; this helper is the pure classification step that's
    safe to unit-test without a live provider.
    """
    if not getattr(args, "auto_route", False):
        return None
    from hydra.router_classifier import classify

    return classify(args.prompt)


def _make_client_or_setup(args: argparse.Namespace):
    env_dir = getattr(args, "env_dir", None)
    try:
        return make_client(args.provider, env_dir=env_dir)
    except ProviderError as e:
        if not getattr(args, "setup_if_needed", False):
            print(
                f"provider error: {e}\n"
                f"Run `python3 -m hydra setup --provider {args.provider}` "
                f"or pass --setup-if-needed.",
                file=sys.stderr,
            )
            raise
        if not sys.stdin.isatty():
            print(
                f"provider error: {e}\n"
                "--setup-if-needed requires an interactive terminal.",
                file=sys.stderr,
            )
            raise
        # In-surface setup panel — built for non-programmers. Renders on the same
        # Rich canvas the chat uses, so it's one seamless surface. Returns the
        # provider the person connected (or None if they skipped).
        import getpass

        from rich.console import Console

        from hydra.setup_panel import run_setup_panel

        provider = run_setup_panel(
            Console(),
            ask=lambda prompt="": input(prompt),
            secret_ask=lambda prompt="": getpass.getpass(prompt),
            env_dir=env_dir,
        )
        if not provider:
            raise  # they skipped — let the caller report no provider is configured
        args.provider = provider
        return make_client(provider, env_dir=env_dir)


def _local_gpu_fallback_client(args: argparse.Namespace):
    """Attempt to configure a local Ollama client for fallback when API calls fail."""
    if getattr(args, "provider", None) == "ollama":
        return None
    env_dir = getattr(args, "env_dir", None)
    try:
        client, cfg = make_client("ollama", env_dir=env_dir)
    except ProviderError as e:
        print(
            f"local GPU fallback unavailable: {e}. "
            "HydraAgent will continue using the original provider.",
            file=sys.stderr,
        )
        return None
    return client, cfg


def _resolve_chat_runtime(args: argparse.Namespace) -> dict[str, str]:
    profile = getattr(args, "profile", "auto") or "auto"
    default_provider, default_model = CHAT_PROFILE_DEFAULTS[profile]
    provider = getattr(args, "provider", None) or default_provider
    model = getattr(args, "model", None) or default_model
    route = default_runtime_route()
    route["conversation_provider"] = provider
    if profile == "local":
        route["planner_provider"] = provider
    return {
        "profile": profile,
        "provider": provider,
        "model": model,
        "planner_provider": route["planner_provider"],
        "worker_provider": route["worker_provider"],
        "local_worker_provider": route["worker_provider"],
        "runtime_route": route_string(route),
        "local_gpu_policy": route["local_gpu_policy"],
        "workbench_api": os.environ.get("HYDRA_WORKBENCH_API", DEFAULT_WORKBENCH_API),
    }


def _identity_from_runtime(runtime: dict[str, str]):
    worker_provider = runtime.get("local_worker_provider") or runtime.get("worker_provider", "ollama")
    return build_identity(
        profile=runtime["profile"],
        provider=runtime["provider"],
        model=runtime["model"],
        worker_provider=worker_provider,
    )


def _chat_runtime_message(runtime: dict[str, str], root: Path, approval_policy: str) -> str:
    scope_note = "entire machine (/)" if root == Path("/") else str(root)
    return (
        render_identity_text(_identity_from_runtime(runtime))
        + "\n"
        f"runtime_route: {runtime['runtime_route']}\n"
        f"- root={scope_note}\n"
        f"- approval_policy={approval_policy}\n"
        f"- workbench_api={runtime['workbench_api']}\n"
        "Use this runtime identity when asked what model, provider, or execution "
        "mode you are using. Cloud chat is for conversation; local models are "
        "reserved for bounded loop/worker tasks unless the operator switches "
        "the chat profile to local."
    )


def _print_chat_identity(runtime: dict[str, str], root: Path, approval_policy: str) -> None:
    scope_note = "entire machine (/)" if root == Path("/") else str(root)
    print(f"Hydra chat profile: {runtime['profile']}")
    print(f"provider={runtime['provider']}")
    print(f"model={runtime['model']}")
    print(f"planner_provider={runtime.get('planner_provider', runtime['provider'])}")
    print(f"local_worker_provider={runtime.get('local_worker_provider', runtime.get('worker_provider', 'ollama'))}")
    print(f"runtime_route={runtime['runtime_route']}")
    print(f"root={scope_note}")
    print(f"approval_policy={approval_policy}")
    print(f"workbench_api={runtime['workbench_api']}")
    print("identity:")
    print(render_identity_text(_identity_from_runtime(runtime)))


def _handle_chat_terminal_control(stripped_prompt: str) -> bool:
    normalized = " ".join(stripped_prompt.lower().split())
    if normalized in {
        "/clear",
        "clear",
        "cls",
        "clear screen",
        "clear terminal",
        "clear the terminal",
        "clear it",
        "clear the screen",
        "clear this screen",
        "can you clear this screen",
        "can you clear the terminal",
        "please clear this screen",
        "wipe screen",
        "wipe the screen",
    }:
        print("\033[2J\033[H", end="", flush=True)
        return True
    return False


def _handle_chat_operator_intent(stripped_prompt: str, runtime: dict[str, str], root: Path, approval_policy: str) -> bool:
    normalized = " ".join(stripped_prompt.lower().split())
    if normalized in {"what model are you using", "what model are you", "what provider are you using"}:
        _print_chat_identity(runtime, root, approval_policy)
        return True
    if normalized in {"what is your job", "tell me about yourself", "what can you do", "who are you"}:
        print("Hydra is an operator/builder harness, not a generic filesystem helper.")
        print(f"profile={runtime['profile']} provider={runtime['provider']} model={runtime['model']}")
        print(f"local_worker_provider={runtime['local_worker_provider']}")
        print(f"root={root}")
        print("I inspect, edit, test, verify, route skills/capabilities, record evidence, and keep missions moving until a real blocker appears.")
        return True
    if normalized in {"why did you ask approval", "why approval", "why are you asking approval"}:
        print("Approval appears only for risky or unknown actions under the selected policy.")
        print("Operator Autonomy Policy V1 auto-allows exact read-only proof/status commands.")
        print("unknown bash, fs_write, and fs_edit still need approval under ask.")
        return True
    if normalized in {
        "/status",
        "/self-check",
        "status",
        "what is broken",
        "what's broken",
        "what is broke",
        "what is wrong",
        "what needs repair",
        "what is the repo status",
    }:
        print(render_self_check_text(build_self_check(REPO_ROOT)), end="")
        return True
    return False


def _print_ask_identity(runtime: dict[str, str], root: Path, approval_policy: str, prompt: str) -> None:
    scope_note = "entire machine (/)" if root == Path("/") else str(root)
    print(f"Hydra ask profile: {runtime['profile']}")
    print(f"provider={runtime['provider']}")
    print(f"model={runtime['model']}")
    print(f"planner_provider={runtime.get('planner_provider', runtime['provider'])}")
    print(f"local_worker_provider={runtime.get('local_worker_provider', runtime.get('worker_provider', 'ollama'))}")
    print(f"runtime_route={runtime['runtime_route']}")
    print(f"root={scope_note}")
    print(f"approval_policy={approval_policy}")
    print(f"workbench_api={runtime['workbench_api']}")
    print("identity:")
    print(render_identity_text(_identity_from_runtime(runtime)))
    routed_names = [
        line.removeprefix("skill: ").strip()
        for line in build_routed_skill_context(prompt).splitlines()
        if line.startswith("skill: ")
    ]
    print(f"skill_route={','.join(routed_names) if routed_names else 'none'}")


def _print_runtime_route(runtime: dict[str, str]) -> None:
    print("Hydra runtime route")
    print(f"selection={runtime['profile']}")
    print(f"conversation_provider={runtime['provider']}")
    print(f"conversation_model={runtime['model']}")
    print(f"planner_provider={runtime.get('planner_provider', runtime['provider'])}")
    print(f"worker_provider={runtime.get('local_worker_provider', runtime.get('worker_provider', 'ollama'))}")
    print(f"runtime_route={runtime['runtime_route']}")
    print("inference=one chat entrypoint; cloud speaks by default, local GPU is reserved for bounded worker/loop work")
    print("identity:")
    print(render_runtime_text(_identity_from_runtime(runtime)))


def cmd_chat(args: argparse.Namespace) -> int:
    root = _root_arg(args.root)
    if not root.is_dir():
        print(f"workspace root is not a directory: {root}", file=sys.stderr)
        return 2
    memory_root = Path(args.memory_root).expanduser().resolve()
    memory_workspace_root = Path(
        getattr(args, "memory_workspace_root", None) or root
    ).expanduser().resolve()
    tools = _bind_tools(
        root,
        approval_policy=args.approval_policy,
        memory_root=memory_root,
        memory_workspace_root=memory_workspace_root,
        notify_telegram=True,  # Enabled — callback infrastructure verified working
    )
    # Initialize persistent session
    session_id = "default_chat_session"
    chat_messages: list[dict] = []

    # Load previous session messages if they exist
    if session_exists(session_id):
        try:
            compact_report = compact_session(session_id)
            if compact_report.get("compacted"):
                print(
                    "Session history auto-compacted; full archive: "
                    f"{compact_report.get('backup_path')}"
                )
            history_limit = max(
                0,
                getattr(
                    args,
                    "session_history_limit",
                    DEFAULT_STARTUP_HISTORY_LIMIT,
                ),
            )
            previous_messages = _llm_messages_from_session(
                get_session_messages(session_id, limit=history_limit)
            )
            chat_messages.extend(previous_messages)
            print(f"Loaded {len(previous_messages)} previous messages from session history.")
        except Exception as e:
            print(f"Warning: Could not load previous session: {e}", file=sys.stderr)
    else:
        # Create new session
        try:
            create_session(session_id, "HydraAgent persistent chat session started")
        except Exception as e:
            print(f"Warning: Could not create persistent session: {e}", file=sys.stderr)
    loop: AgentLoop | None = None
    runtime = _resolve_chat_runtime(args)
    provider_name = runtime["provider"]
    model = runtime["model"]
    args.provider = provider_name
    current_mission_id: str | None = None
    mission_system_message: dict | None = None
    last_audit_target: str | None = None
    last_locate_target: str | None = None
    default_chat_prompt = build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT)
    if default_chat_prompt:
        chat_messages.append({"role": "system", "content": default_chat_prompt})
    chat_messages.append(
        {
            "role": "system",
            "content": _chat_runtime_message(runtime, root, args.approval_policy),
        }
    )
    # Chat memory is now recalled PER TURN by the user's own query inside the
    # TUI (see EliteTUI._stream_turn + recall_builder below), instead of baking
    # one static ~12 KB dump into every turn. We only probe availability here so
    # the operator still sees a startup line; the heavy lifting is per-turn.
    chat_recall_builder = None if args.no_local_memory else build_semantic_memory_context
    if not args.no_local_memory:
        # Seed the core-block rows (policy rules) into the persistent store once
        # at startup.  The call is idempotent — 0-cost NOOP after the first run,
        # so this adds no latency to subsequent startups.
        try:
            from hydra.unified_memory import UnifiedMemory
            from hydra.memory_distill import seed_core_block
            from hydra.semantic_recall import _default_store_path

            _store = UnifiedMemory(path=_default_store_path())
            try:
                n_seeded = seed_core_block(_store)
                if n_seeded:
                    print(f"Hydra memory: seeded {n_seeded} core law row(s)")
            finally:
                _store.close()
        except Exception as _exc:
            # Vector memory is optional -- recall falls back to keyword search --
            # so this stays non-fatal at startup. But the operator must be TOLD
            # the vector lane is off, and why: a silent degrade is a hidden gate.
            print(f"Hydra memory: vector lane OFF — {_exc}")

        probe = build_semantic_memory_context(
            "startup memory probe",
            root=memory_root,
            workspace_root=memory_workspace_root,
        )
        if probe.status == "OK":
            print(
                "Hydra memory recall armed (per-turn, query-aware): "
                f"{probe.data.get('chunks_scored', 0)} chunks indexed"
            )
        else:
            print(probe.report.strip())
    print(f"root={root}")
    context_message = _build_chat_context_message(args, memory_root)
    if context_message is not None:
        chat_messages.append(context_message)
    if getattr(args, "truth_context", False):
        truth_message = _truth_context_message(
            "hydra chat mission memory",
            memory_root,
            budget_chars=getattr(args, "context_budget_bytes", 4096),
        )
        if truth_message is not None:
            chat_messages.append(truth_message)
    trace_out_path: Path | None = None
    if getattr(args, "trace_out", None):
        trace_out_path = Path(args.trace_out).expanduser().resolve()
        trace_out_path.parent.mkdir(parents=True, exist_ok=True)
    # ── Elite TUI ─────────────────────────────────────────────────────────────
    # Replaces the plain `hydra>` input loop with the rich interactive console.
    # Launch command unchanged: `hydra chat`
    try:
        client, cfg = _make_client_or_setup(args)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2
    model = args.model or cfg.model or model
    if not model:
        print(f"no model resolved for provider {args.provider!r}; pass --model", file=sys.stderr)
        return 2

    def _elite_command_handler(tui: EliteTUI, stripped: str) -> bool:
        nonlocal root, memory_workspace_root, tools, runtime, provider_name, model
        nonlocal current_mission_id, last_audit_target, last_locate_target

        if _handle_chat_terminal_control(stripped):
            return True
        if _handle_chat_operator_intent(stripped, runtime, root, args.approval_policy):
            return True
        if stripped.startswith("/root"):
            _, _, requested = stripped.partition(" ")
            if not requested.strip():
                print(f"current root: {root}")
                return True
            new_root = Path(requested.strip()).expanduser().resolve()
            if not new_root.is_dir():
                print(f"workspace root is not a directory: {new_root}", file=sys.stderr)
                return True
            root = new_root
            memory_workspace_root = new_root
            tools = _bind_tools(
                root,
                approval_policy=args.approval_policy,
                memory_root=memory_root,
                memory_workspace_root=memory_workspace_root,
                notify_telegram=True,  # Enabled — callback infrastructure verified working
            )
            tui.root = root
            tui.tools = tools
            tui._chat_messages.append(
                {
                    "role": "system",
                    "content": f"Filesystem scope changed to {root}. Use this scope for subsequent turns.",
                }
            )
            tui._chat_messages.append(
                {
                    "role": "system",
                    "content": _chat_runtime_message(runtime, root, args.approval_policy),
                }
            )
            print(f"workspace root changed to: {root}")
            return True
        if stripped in {"/whoami", "/model"}:
            _print_chat_identity(runtime, root, args.approval_policy)
            return True
        if stripped == "/runtime":
            _print_runtime_route(runtime)
            return True
        if stripped == "/mission":
            if not current_mission_id:
                print("mission: none")
                return True
            try:
                print(render_mission_text(load_mission(REPO_ROOT, current_mission_id)))
            except MissionError as e:
                print(f"mission error: {e}", file=sys.stderr)
            return True
        if stripped == "/providers":
            print("Built-in providers:")
            for provider in list_providers_fn():
                print(f"  {provider}")
            print("chat profiles:")
            print("  auto -> chat:ollama-cloud planner:ollama-cloud worker:ollama")
            print("  cloud -> ollama-cloud")
            print("  local -> ollama")
            return True
        if stripped == "/skills":
            print(render_skill_list(list_skill_records()))
            return True
        if stripped == "/doctrine":
            doctrine = build_skill_doctrine()
            tui._chat_messages.append({"role": "system", "content": doctrine})
            print(doctrine)
            return True
        if stripped == "/skill-doctor":
            print(render_skill_doctor())
            return True
        if stripped.startswith("/skill "):
            _, _, requested_skill = stripped.partition(" ")
            try:
                record = find_skill(requested_skill)
            except KeyError as e:
                print(str(e), file=sys.stderr)
                return True
            rendered = render_skill(record)
            tui._chat_messages.append(
                {
                    "role": "system",
                    "content": f"Hydra trusted skill loaded:\n{rendered}",
                }
            )
            print(f"Skill loaded into chat context: {record.name}")
            print(rendered)
            return True
        if stripped.startswith("/route "):
            _, _, route_prompt = stripped.partition(" ")
            print(build_routed_skill_context(route_prompt) or "Hydra routed skill context\nNo trusted skill route matched.")
            return True
        if stripped in {"/cloud", "/local"}:
            args.profile = stripped.removeprefix("/")
            args.provider = None
            runtime = _resolve_chat_runtime(args)
            provider_name = runtime["provider"]
            args.provider = provider_name
            try:
                client, cfg = _make_client_or_setup(args)
            except ProviderError as e:
                print(f"provider error: {e}", file=sys.stderr)
                return True
            model = args.model or cfg.model or runtime["model"]
            tui.reconfigure_runtime(client=client, cfg=cfg, model=model, runtime=runtime)
            tui._chat_messages.append(
                {
                    "role": "system",
                    "content": _chat_runtime_message(runtime, root, args.approval_policy),
                }
            )
            print(f"chat profile changed to: {runtime['profile']}")
            _print_chat_identity(runtime, root, args.approval_policy)
            return True
        if stripped == "/audit" or stripped.startswith("/audit "):
            _, _, requested = stripped.partition(" ")
            audit_target = requested.strip() or last_audit_target or last_locate_target
            if not audit_target:
                print("usage: /audit PATH")
                return True
            result = run_directory_audit(audit_target, root=root)
            last_audit_target = audit_target
            _print_audit_result(result)
            return True
        if stripped.startswith("/locate "):
            parts = stripped.split(maxsplit=2)
            if len(parts) < 3:
                print("usage: /locate NAME ROOT")
                return True
            result = run_locate(parts[1], root=parts[2])
            last_locate_target = _best_locate_path(result)
            _print_locate_result(result)
            return True
        if stripped == "/memory" or stripped.startswith("/memory "):
            _, _, requested = stripped.partition(" ")
            subject = requested.strip() or "hydra"
            if subject != "hydra":
                print("usage: /memory hydra")
                return True
            memory_result = build_local_memory_context(
                memory_root,
                max_chars=args.local_memory_chars,
                workspace_root=memory_workspace_root,
            )
            if memory_result.status == "OK":
                tui._chat_messages.append(
                    {
                        "role": "system",
                        "content": memory_result.context,
                    }
                )
            _print_local_memory_result(memory_result)
            return True
        return False

    EliteTUI(
        client=client,
        model=model,
        cfg=cfg,
        root=root,
        system_prompt=default_chat_prompt,
        session_id=session_id,
        initial_messages=chat_messages,
        approval_policy=args.approval_policy,
        max_iterations=args.max_iterations,
        timeout=args.timeout,
        tools=tools,
        memory_root=memory_root,
        memory_workspace_root=memory_workspace_root,
        recall_builder=chat_recall_builder,
        local_memory_chars=args.local_memory_chars,
        runtime=runtime,
        trace_out_path=trace_out_path,
        command_handler=_elite_command_handler,
        initial_request=getattr(args, "initial_request", None),
        notify_telegram=True,  # Enabled — callback infrastructure verified working
    ).run()
    return 0
    # ── Legacy REPL (kept for reference, unreachable) ──────────────────────
    while True:  # noqa: unreachable
        try:
            prompt = input("hydra> ")
        except EOFError:
            print()
            return 0
        stripped = prompt.strip()
        if stripped.lower() in {"stop", "/exit", "/quit"}:
            return 0
        if _handle_chat_terminal_control(stripped):
            continue
        if _handle_chat_operator_intent(stripped, runtime, root, args.approval_policy):
            continue
        if stripped.startswith("/root"):
            _, _, requested = stripped.partition(" ")
            if not requested.strip():
                print(f"current root: {root}")
                continue
            new_root = Path(requested.strip()).expanduser().resolve()
            if not new_root.is_dir():
                print(f"workspace root is not a directory: {new_root}", file=sys.stderr)
                continue
            root = new_root
            memory_workspace_root = new_root
            tools = _bind_tools(
                root,
                approval_policy=args.approval_policy,
                memory_root=memory_root,
                memory_workspace_root=memory_workspace_root,
                notify_telegram=True,  # Enabled — callback infrastructure verified working
            )
            chat_messages.append(
                {
                    "role": "system",
                    "content": f"Filesystem scope changed to {root}. Use this scope for subsequent turns.",
                }
            )
            chat_messages.append(
                {
                    "role": "system",
                    "content": _chat_runtime_message(runtime, root, args.approval_policy),
                }
            )
            print(f"workspace root changed to: {root}")
            continue
        if stripped in {"/whoami", "/model"}:
            _print_chat_identity(runtime, root, args.approval_policy)
            continue
        if stripped == "/runtime":
            _print_runtime_route(runtime)
            continue
        if stripped == "/mission":
            if not current_mission_id:
                print("mission: none")
                continue
            try:
                print(render_mission_text(load_mission(REPO_ROOT, current_mission_id)))
            except MissionError as e:
                print(f"mission error: {e}", file=sys.stderr)
            continue
        if stripped == "/providers":
            print("Built-in providers:")
            for provider in list_providers_fn():
                print(f"  {provider}")
            print("chat profiles:")
            print("  auto -> chat:cloud-doer planner:cloud-planner worker:local-worker")
            print("  cloud -> cloud-planner")
            print("  local -> qwen2.5-coder:7b")
            continue
        if stripped == "/skills":
            print(render_skill_list(list_skill_records()))
            continue
        if stripped == "/doctrine":
            doctrine = build_skill_doctrine()
            chat_messages.append({"role": "system", "content": doctrine})
            print(doctrine)
            continue
        if stripped == "/skill-doctor":
            print(render_skill_doctor())
            continue
        if stripped.startswith("/skill "):
            _, _, requested_skill = stripped.partition(" ")
            try:
                record = find_skill(requested_skill)
            except KeyError as e:
                print(str(e), file=sys.stderr)
                continue
            rendered = render_skill(record)
            chat_messages.append(
                {
                    "role": "system",
                    "content": f"Hydra trusted skill loaded:\n{rendered}",
                }
            )
            print(f"Skill loaded into chat context: {record.name}")
            print(rendered)
            continue
        if stripped.startswith("/route "):
            _, _, route_prompt = stripped.partition(" ")
            print(build_routed_skill_context(route_prompt) or "Hydra routed skill context\nNo trusted skill route matched.")
            continue
        if stripped in {"/cloud", "/local"}:
            args.profile = stripped.removeprefix("/")
            args.provider = None
            runtime = _resolve_chat_runtime(args)
            provider_name = runtime["provider"]
            model = runtime["model"]
            args.provider = provider_name
            loop = None
            chat_messages.append(
                {
                    "role": "system",
                    "content": _chat_runtime_message(runtime, root, args.approval_policy),
                }
            )
            print(f"chat profile changed to: {runtime['profile']}")
            _print_chat_identity(runtime, root, args.approval_policy)
            continue
        if stripped == "/audit" or stripped.startswith("/audit "):
            _, _, requested = stripped.partition(" ")
            audit_target = requested.strip() or last_audit_target or last_locate_target
            if not audit_target:
                print("usage: /audit PATH")
                continue
            result = run_directory_audit(audit_target, root=root)
            last_audit_target = audit_target
            _print_audit_result(result)
            continue
        if stripped.startswith("/locate "):
            parts = stripped.split(maxsplit=2)
            if len(parts) < 3:
                print("usage: /locate NAME ROOT")
                continue
            result = run_locate(parts[1], root=parts[2])
            last_locate_target = _best_locate_path(result)
            _print_locate_result(result)
            continue
        if stripped == "/memory" or stripped.startswith("/memory "):
            _, _, requested = stripped.partition(" ")
            subject = requested.strip() or "hydra"
            if subject != "hydra":
                print("usage: /memory hydra")
                continue
            memory_result = build_local_memory_context(
                memory_root,
                max_chars=args.local_memory_chars,
            )
            if memory_result.status == "OK":
                chat_messages.append(
                    {
                        "role": "system",
                        "content": memory_result.context,
                    }
                )
            _print_local_memory_result(memory_result)
            continue
        if not stripped:
            continue
        try:
            current_mission_id, mission_context = _ensure_chat_mission_context(
                REPO_ROOT,
                current_mission_id=current_mission_id,
                operator_prompt=stripped,
            )
            composed_mission = compose_operator_prompt(
                "",
                repo_root=REPO_ROOT,
                memory_root=memory_root,
                evidence_root=REPO_ROOT / "evidence",
                budget_bytes=getattr(args, "context_budget_bytes", 4096),
                include_context=getattr(args, "with_context", False),
                query=stripped,
                mission_context=mission_context,
            )
        except (MissionError, PromptBuilderError) as e:
            print(f"mission context error: {e}", file=sys.stderr)
            continue
        if composed_mission["prompt"]:
            mission_system_message = {"role": "system", "content": composed_mission["prompt"]}
            chat_messages = [
                message
                for message in chat_messages
                if not (
                    message.get("role") == "system"
                    and isinstance(message.get("content"), str)
                    and "## Hydra mission context" in message["content"]
                )
            ]
            chat_messages.append(mission_system_message)
        routed_skill_context = build_routed_skill_context(stripped)
        if routed_skill_context:
            chat_messages.append({"role": "system", "content": routed_skill_context})
            routed_names = [
                line.removeprefix("skill: ").strip()
                for line in routed_skill_context.splitlines()
                if line.startswith("skill: ")
            ]
            print(f"Hydra skill route: {', '.join(routed_names)}", file=sys.stderr)
        intent = route_chat_intent(
            stripped,
            last_audit_target=last_audit_target,
            last_locate_target=last_locate_target,
        )
        if intent and intent.kind == "audit":
            result = run_directory_audit(intent.target, root=root)
            last_audit_target = intent.target
            _print_audit_result(result)
            continue
        # SEAM CUT: remote-audit intent removed (a stripped remote-audit module is stripped).
        if intent and intent.kind == "locate":
            query, _, locate_root = intent.target.partition("\n")
            result = run_locate(query, root=locate_root or root)
            last_locate_target = _best_locate_path(result)
            _print_locate_result(result)
            continue
        if loop is None:
            try:
                args.provider = provider_name
                client, cfg = _make_client_or_setup(args)
            except ProviderError as e:
                print(f"provider error: {e}", file=sys.stderr)
                return 2
            provider_name = cfg.name
            model = args.model or cfg.model
            if not model:
                print(
                    f"no model resolved for provider {args.provider!r}; pass --model",
                    file=sys.stderr,
                )
                return 2
            loop = AgentLoop(client, model=model, system_prompt=default_chat_prompt)
        attempted_local_fallback = False
        while True:
            try:
                result = loop.run(
                    _with_workspace_context(prompt, root),
                    tools=tools,
                    max_iterations=args.max_iterations,
                    timeout=args.timeout,
                    initial_messages=chat_messages,
                )
                break
            except LlmError as e:
                if attempted_local_fallback:
                    print(f"LLM error: {e}", file=sys.stderr)
                    result = None
                    break
                if args.provider == "ollama":
                    # Local model failed → fall back to the cloud reasoning model.
                    try:
                        fallback = make_client(
                            "ollama-cloud", env_dir=getattr(args, "env_dir", None)
                        )
                    except ProviderError:
                        fallback = None
                    fallback_label = "cloud provider"
                else:
                    fallback = _local_gpu_fallback_client(args)
                    fallback_label = "local GPU provider"
                if fallback is None:
                    print(f"LLM error: {e}", file=sys.stderr)
                    result = None
                    break
                client, cfg = fallback
                args.provider = cfg.name
                model = args.model or cfg.model
                print(
                    f"API failure on the configured provider. Falling back to {fallback_label} {cfg.name}/{model}.",
                    file=sys.stderr,
                )
                loop = AgentLoop(client, model=model, system_prompt=default_chat_prompt)
                attempted_local_fallback = True
                continue
        if result is None:
            continue
        chat_messages = result.messages

        # Save messages to persistent session
        try:
            if session_exists(session_id):
                # Add the latest user message and assistant response
                if prompt.strip():
                    add_message(session_id, "user", prompt.strip())
                if result.final_response.strip():
                    add_message(session_id, "assistant", result.final_response.strip())
        except Exception as e:
            # Don't let session persistence errors break the chat
            pass

        print(result.final_response)
        print(
            f"(iterations={result.iterations} "
            f"tool_calls={result.tool_calls_made} "
            f"halted={result.halted_reason})",
            file=sys.stderr,
        )
        if trace_out_path is not None:
            chat_turn_index += 1
            turn_payload = _build_chat_trace_turn(
                turn_index=chat_turn_index,
                user_prompt=prompt,
                provider=provider_name or "",
                model=model or "",
                system_prompt=loop.system_prompt or "",
                result=result,
                approval_policy=args.approval_policy,
            )
            with trace_out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(turn_payload, sort_keys=True) + "\n")


def register_chat_commands(
    sub: argparse._SubParsersAction,
    *,
    default_env_dir: str = DEFAULT_ENV_DIR,
    default_memory_root: Path = DEFAULT_MEMORY_ROOT,
    local_memory_max_chars: int = LOCAL_MEMORY_MAX_CHARS,
    policy_choices: tuple[str, ...] = POLICY_CHOICES,
) -> None:
    p_chat = sub.add_parser(
        "chat",
        help="interactive session that runs the agent loop one prompt at a time",
        description=(
            "Start an interactive Hydra session. Type stop to interrupt/exit. "
            "Use /root PATH to re-scope filesystem tools. Conversation history persists."
        ),
    )
    p_chat.add_argument(
        "--profile",
        choices=("auto", "cloud", "local"),
        default="auto",
        help="chat runtime profile: auto uses cloud chat plus local worker routing; local uses Ollama",
    )
    p_chat.add_argument("--provider", default=None, help="override provider selected by --profile")
    p_chat.add_argument("--model", default=None)
    p_chat.add_argument("--env-dir", default=None, help=f"provider env dir (default: {default_env_dir})")
    p_chat.add_argument(
        "--setup-if-needed",
        action="store_true",
        help="prompt for local/cloud/Codex setup if provider config is missing",
    )
    p_chat.add_argument("--root", default=None, help="filesystem scope (default: /)")
    p_chat.add_argument(
        "--memory-root",
        default=str(default_memory_root),
        help=f"local memory root loaded into chat (default: {default_memory_root})",
    )
    p_chat.add_argument(
        "--no-local-memory",
        action="store_true",
        help="do not auto-load local memory into chat",
    )
    p_chat.add_argument(
        "--local-memory-chars",
        type=int,
        default=local_memory_max_chars,
        help="maximum characters of local memory context to inject",
    )
    p_chat.add_argument("--max-iterations", type=int, default=200)  # iterate like Claude Code/Codex
    p_chat.add_argument("--timeout", type=float, default=120.0)
    p_chat.add_argument(
        "--session-history-limit",
        type=int,
        default=resolve_startup_history_limit(),
        help=(
            "maximum persisted chat messages to reload on startup "
            f"(default {DEFAULT_STARTUP_HISTORY_LIMIT}, override with "
            "HYDRA_SESSION_HISTORY_LIMIT); 0 disables startup history reload"
        ),
    )
    p_chat.add_argument(
        "--approval-policy",
        choices=policy_choices,
        default="ask",
        help="risky tool policy for bash/fs_write/fs_edit",
    )
    p_chat.add_argument(
        "--with-context",
        action="store_true",
        help="inject the §10.59 context bundle (lessons + clusters + promotions) as a system message",
    )
    p_chat.add_argument(
        "--truth-context",
        action="store_true",
        help="inject provenance-backed truth memory context as a system message",
    )
    p_chat.add_argument(
        "--context-budget-bytes",
        type=int,
        default=4096,
        help="byte budget for the context bundle (default: 4096)",
    )
    p_chat.add_argument(
        "--trace-out",
        default=None,
        help="path to append a JSONL trace (one line per chat turn). Schema: hydra.chat_trace_turn.v1.",
    )
