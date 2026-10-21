"""Non-interactive ask CLI command handler."""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

from hydra.inter_agent import use_correlation_id, use_trace_id
from hydra.llm import LlmError
from hydra.loop import AgentLoop, LoopError
from hydra.codex_client import configure_native_scope
from hydra.turn_memory import finish_turn
from hydra.policy import POLICY_CHOICES
from hydra.providers import DEFAULT_ENV_DIR, ProviderError
from hydra.roles import RoleError, resolve_roles
from hydra.skill_spine import build_agent_system_prompt, build_routed_skill_context
from hydra.memory_kernel import assemble_truth_context
from hydra.local_memory import DEFAULT_MEMORY_ROOT
from hydra.semantic_recall import build_semantic_memory_context
from hydra.prompt_builder import PromptBuilderError, compose_operator_prompt
from hydra.rubric_judge import RubricJudgeError
from hydra.cli.cmd_chat import (
    DEFAULT_SYSTEM_PROMPT,
    HYDRA_CONFIG,
    HYDRA_SCHEMA,
    REPO_ROOT,
    _auto_route_role,
    _bind_tools,
    _build_ask_trace,
    _judge_ask_response,
    _local_gpu_fallback_client,
    _make_client_or_setup,
    _print_ask_identity,
    _resolve_chat_runtime,
    _root_arg,
    _with_workspace_context,
)


DEFAULT_ASK_MAX_ITERATIONS = 20
HYDRA_ASK_MAX_ITERATIONS_ENV = "HYDRA_ASK_MAX_ITERATIONS"


def _default_ask_max_iterations() -> int:
    raw = os.environ.get(HYDRA_ASK_MAX_ITERATIONS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_ASK_MAX_ITERATIONS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_ASK_MAX_ITERATIONS
    return value if value >= 1 else DEFAULT_ASK_MAX_ITERATIONS


def register_ask_command(
    sub: argparse._SubParsersAction,
    *,
    default_env_dir: str = DEFAULT_ENV_DIR,
    policy_choices: tuple[str, ...] = POLICY_CHOICES,
) -> None:
    p_ask = sub.add_parser("ask", help="run the agent loop on a prompt")
    p_ask.add_argument("prompt", help="user prompt the agent should act on")
    p_ask.add_argument(
        "--profile",
        choices=("auto", "cloud", "local"),
        default="auto",
        help="ask runtime profile: auto uses cloud chat plus local worker routing; local uses Ollama",
    )
    p_ask.add_argument("--provider", default=None, help="override provider selected by --profile")
    p_ask.add_argument("--model", default=None)
    p_ask.add_argument("--env-dir", default=None, help=f"provider env dir (default: {default_env_dir})")
    p_ask.add_argument(
        "--runtime-only",
        action="store_true",
        help="print resolved runtime/provider/model/skill route without calling an LLM",
    )
    p_ask.add_argument(
        "--setup-if-needed",
        action="store_true",
        help="prompt for local/cloud/Codex setup if provider config is missing",
    )
    p_ask.add_argument("--root", default=None, help="filesystem scope (default: current directory)")
    p_ask.add_argument(
        "--max-iterations",
        type=int,
        default=_default_ask_max_iterations(),
        help=(
            f"agent loop iteration cap (default: {DEFAULT_ASK_MAX_ITERATIONS}, "
            f"override with {HYDRA_ASK_MAX_ITERATIONS_ENV})"
        ),
    )
    p_ask.add_argument("--timeout", type=float, default=120.0)
    p_ask.add_argument(
        "--approval-policy",
        choices=policy_choices,
        default="ask",
        help="risky tool policy for bash/fs_write/fs_edit (default: ask — prompt on a terminal, block when non-interactive; use 'allow' to run unattended)",
    )
    p_ask.add_argument(
        "--with-context",
        action="store_true",
        help="prepend the §10.59 context bundle (lessons + clusters + promotions) to the system prompt",
    )
    p_ask.add_argument(
        "--truth-context",
        action="store_true",
        help="prepend provenance-backed truth memory context to the system prompt",
    )
    p_ask.add_argument(
        "--context-budget-bytes",
        type=int,
        default=4096,
        help="byte budget for the context bundle (default: 4096)",
    )
    p_ask.add_argument(
        "--memory-root",
        default=str(DEFAULT_MEMORY_ROOT),
        help="memory root for context bundle lookups",
    )
    p_ask.add_argument(
        "--no-local-memory", action="store_true",
        help="disable automatic relevant recall and execution learning for this run",
    )
    p_ask.add_argument(
        "--judge-rubric",
        default=None,
        help="path to a §10.60 JSON rubric to score the reply against",
    )
    p_ask.add_argument(
        "--judge-threshold",
        type=float,
        default=1.0,
        help="rubric pass threshold in [0.0, 1.0] (default: 1.0 — every rule)",
    )
    p_ask.add_argument(
        "--judge-out",
        default=None,
        help="optional path to write the judge report JSON",
    )
    p_ask.add_argument(
        "--judge-fail-exit",
        action="store_true",
        help="exit 1 when the judge verdict is FAIL (default: advisory only)",
    )
    p_ask.add_argument(
        "--trace-out",
        default=None,
        help="path to write a JSON trace of the turn (prompt, system prompt size, iterations, tool calls, halted reason, final response, redacted messages). Schema: hydra.ask_trace.v1.",
    )
    p_ask.add_argument(
        "--auto-route",
        action="store_true",
        help="classify the prompt via §10.89 router_classifier and pick the model from §10.34 role router (planner/doer/auditor). When set, the resolved role's model overrides --model.",
    )


def cmd_ask(args: argparse.Namespace) -> int:
    if args.max_iterations < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
        print("ask error: --max-iterations must be at least 1 and --timeout must be finite and positive", file=sys.stderr)
        return 2
    if args.context_budget_bytes < 256:
        print("ask error: --context-budget-bytes must be at least 256", file=sys.stderr)
        return 2
    root = _root_arg(args.root)
    if not root.is_dir():
        print(f"workspace root is not a directory: {root}", file=sys.stderr)
        return 2
    try:
        runtime = _resolve_chat_runtime(args)
    except (ProviderError, RoleError, ValueError) as e:
        print(f"runtime error: {e}", file=sys.stderr)
        return 2
    args.provider = runtime["provider"]
    if getattr(args, "runtime_only", False):
        _print_ask_identity(runtime, root, args.approval_policy, args.prompt)
        return 0
    try:
        client, cfg = _make_client_or_setup(args)
    except (ProviderError, LlmError) as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2
    model = args.model or cfg.model
    if not model:
        print(
            f"no model resolved for provider {args.provider!r}; "
            f"pass --model",
            file=sys.stderr,
        )
        return 2

    auto_route_report = _auto_route_role(args)
    if auto_route_report is not None:
        # Resolve §10.34 role router and pick the model for the classified role.
        try:
            roles = resolve_roles(
                config_path=HYDRA_CONFIG,
                schema_path=HYDRA_SCHEMA,
            )
        except RoleError as e:
            print(f"auto-route error: {e}", file=sys.stderr)
            return 2
        chosen_role = getattr(roles, auto_route_report["role"])
        if chosen_role.model:
            model = chosen_role.model
        print(
            f"  auto-route: role={auto_route_report['role']} "
            f"model={model} matched={auto_route_report['matched']}",
            file=sys.stderr,
        )

    system_prompt = build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT)
    context_proof_lines: list[str] = []
    memory_root = Path(args.memory_root).expanduser().resolve()
    if not getattr(args, "no_local_memory", False):
        try:
            recall = build_semantic_memory_context(
                args.prompt, root=memory_root, workspace_root=root,
                max_chars=args.context_budget_bytes,
            )
            if recall.context:
                bounded = recall.context.encode("utf-8")[:args.context_budget_bytes].decode("utf-8", errors="ignore")
                system_prompt = system_prompt.rstrip() + "\n\n" + bounded
            context_proof_lines.append(
                f"memory={recall.status} backend={recall.data.get('backend', 'none')} "
                f"chunks={recall.data.get('chunks_returned', 0)}"
            )
            if recall.data.get("fallback_reason"):
                print(f"memory recall: {recall.data['fallback_reason']}", file=sys.stderr)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"memory recall unavailable: {e}", file=sys.stderr)
    if getattr(args, "with_context", False):
        try:
            composed = compose_operator_prompt(
                "",
                repo_root=root,
                memory_root=args.memory_root,
                evidence_root=root / "evidence",
                budget_bytes=args.context_budget_bytes,
                query=args.prompt,
            )
        except PromptBuilderError as e:
            print(f"prompt builder error: {e}", file=sys.stderr)
            return 2
        system_prompt = system_prompt.rstrip() + "\n\n" + composed["prompt"]
        context_proof_lines.extend(composed["proof"])
    if getattr(args, "truth_context", False):
        try:
            truth = assemble_truth_context(
                args.prompt,
                repo_root=root,
                memory_root=Path(args.memory_root),
                budget_chars=args.context_budget_bytes,
            )
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"truth memory error: {e}", file=sys.stderr)
            return 2
        if truth["text"]:
            system_prompt = system_prompt.rstrip() + "\n\n" + truth["text"]
            context_proof_lines.extend(truth["proof"])
    routed_skill_context = build_routed_skill_context(args.prompt)
    if routed_skill_context:
        system_prompt = system_prompt.rstrip() + "\n\n" + routed_skill_context

    configure_native_scope(client, root=root, approval_policy=args.approval_policy)
    loop = AgentLoop(client, model=model, system_prompt=system_prompt)
    tools = _bind_tools(root, approval_policy=args.approval_policy, memory_root=memory_root,
                        memory_workspace_root=root)
    print(
        f"Hydra: provider={cfg.name} model={model} root={root}\n"
        f"  tools: {', '.join(t.name for t in tools)}\n"
        f"  approval_policy={args.approval_policy}",
        file=sys.stderr,
    )
    if context_proof_lines:
        print("  context: " + "; ".join(context_proof_lines[:3]), file=sys.stderr)
    attempted_local_fallback = False
    attempt_results = []
    retry_messages = None
    exit_code = 0
    while True:
        try:
            with use_trace_id(os.environ.get("HYDRA_TRACE_ID")), use_correlation_id(
                os.environ.get("HYDRA_PARENT_MESSAGE_ID")
            ):
                result = loop.run(
                    (_with_workspace_context(args.prompt, root) if retry_messages is None else
                     "Continue the same operator task using the completed tool results above. "
                     "Do not repeat completed actions."),
                    tools=tools,
                    max_iterations=max(1, args.max_iterations - sum(r.iterations for r in attempt_results)),
                    timeout=args.timeout,
                    initial_messages=retry_messages,
                )
            attempt_results.append(result)
            break
        except LoopError as e:
            print(f"ask error: {e}", file=sys.stderr)
            return 2
        except LlmError as e:
            partial = getattr(e, "partial_result", None)
            if partial is not None:
                attempt_results.append(partial)
                retry_messages = partial.messages
            if attempted_local_fallback or args.provider == "ollama":
                print(f"LLM error: {e}", file=sys.stderr)
                if partial is None:
                    return 1
                result, exit_code = partial, 1
                break
            if sum(r.iterations for r in attempt_results) >= args.max_iterations:
                print(f"LLM error: {e}; iteration budget exhausted", file=sys.stderr)
                if partial is None:
                    return 1
                result, exit_code = partial, 1
                break
            fallback = _local_gpu_fallback_client(args)
            if fallback is None:
                print(f"LLM error: {e}", file=sys.stderr)
                if partial is None:
                    return 1
                result, exit_code = partial, 1
                break
            client, cfg = fallback
            args.provider = cfg.name
            model = cfg.model
            print(
                f"API failure on the configured provider. Falling back to local GPU provider {cfg.name}/{model}.",
                file=sys.stderr,
            )
            loop = AgentLoop(client, model=model, system_prompt=system_prompt)
            attempted_local_fallback = True
            continue

    result = finish_turn(attempt_results, [{"role": "system", "content": system_prompt}], result.final_response)

    if not getattr(args, "no_local_memory", False):
        try:
            from hydra.execution_learning import record_execution_lesson
            learned = record_execution_lesson(
                repo_root=root, memory_root=memory_root, goal=args.prompt,
                result=result, provider=cfg.name, model=model,
            )
            print(f"  execution memory: {learned.get('status', 'recorded')}", file=sys.stderr)
        except (OSError, ValueError, RuntimeError) as e:
            print(f"execution memory could not be saved: {e}", file=sys.stderr)

    print(result.final_response)
    print(
        f"\n(iterations={result.iterations} "
        f"tool_calls={result.tool_calls_made} "
        f"halted={result.halted_reason})",
        file=sys.stderr,
    )

    if getattr(args, "trace_out", None):
        trace_path = Path(args.trace_out).expanduser().resolve()
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_payload = _build_ask_trace(
            prompt=args.prompt,
            provider=cfg.name,
            model=model,
            system_prompt=system_prompt,
            context_proof=context_proof_lines,
            result=result,
            approval_policy=args.approval_policy,
        )
        trace_path.write_text(
            json.dumps(trace_payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"  trace: {trace_path}", file=sys.stderr)

    judge_report: dict | None = None
    if getattr(args, "judge_rubric", None):
        try:
            judge_report = _judge_ask_response(
                result.final_response,
                args.judge_rubric,
                threshold=args.judge_threshold,
            )
        except RubricJudgeError as e:
            print(f"judge error: {e}", file=sys.stderr)
            return 2
        except (OSError, json.JSONDecodeError) as e:
            print(f"judge error: could not load rubric: {e}", file=sys.stderr)
            return 2
        if judge_report is not None:
            print(
                f"judge: {judge_report['verdict']} "
                f"score={judge_report['score']} "
                f"violations={len(judge_report['violations'])}",
                file=sys.stderr,
            )
            if args.judge_out:
                out_path = Path(args.judge_out).expanduser().resolve()
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    json.dumps(judge_report, indent=2, sort_keys=True), encoding="utf-8"
                )
            if args.judge_fail_exit and judge_report["verdict"] != "PASS":
                return 1
    return exit_code or (0 if result.halted_reason == "natural" else 1)
