"""Non-interactive ask CLI command handler."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

from hydra.answer_budget import AnswerBudgetClient, Rung
from hydra.inter_agent import use_correlation_id, use_trace_id
from hydra.llm import LlmError
from hydra.loop import AgentLoop, LoopError
from hydra.policy import POLICY_CHOICES
from hydra.providers import DEFAULT_ENV_DIR, ProviderError, is_local_provider, make_client
from hydra.model_routing import RoutingConfigError, load_routing
from hydra.roles import RoleError, resolve_roles
from hydra.skill_spine import build_agent_system_prompt, build_routed_skill_context
from hydra.memory_kernel import assemble_truth_context
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


def _rejection_summary(text: str, status: int | None) -> str:
    """One readable line: the provider's own "message" when the body is JSON."""
    from hydra.provider_health import safe_message

    found = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if found:
        message = " ".join(found.group(1).replace('\\"', '"').split())
        line = f"HTTP {status}: {message}" if status else message
    else:
        line = " ".join(text.split())
    return safe_message(line)[:240]


def _auth_rejection_message(provider: str, error: LlmError) -> str | None:
    """A bad or missing key is not a reason to try another model."""
    kind = getattr(error, "kind", "")
    status = getattr(error, "status_code", None)
    text = str(error)
    lowered = text.lower()
    # A BACKS rung walk uses 401/402/403 as "try the next rung", not as a key refusal.
    if "no rung answered" in lowered or re.search(r"rung[- ][\w-]+:\d{3}", lowered):
        return None
    key_words = ("api key", "api_key", "authorization", "unauthor", "invalid key")
    talks_about_a_key = any(word in lowered for word in key_words)
    auth = kind == "authentication" or (status in {400, 401, 403} and talks_about_a_key)
    if not auth:
        return None
    key_var = re.sub(r"[^A-Za-z0-9]", "_", str(provider or "provider")).upper() + "_API_KEY"
    summary = _rejection_summary(text, status)
    return f"{provider} rejected the API key ({summary}). Check {key_var}."


def _budgeted(client, provider: str, model: str, prompt: str) -> AnswerBudgetClient:
    """Never print a reply cut off at its output budget: retry bigger, learn each rung's allowance."""
    return AnswerBudgetClient([Rung(str(provider), str(model), client=client)], question=prompt)


def _one_line(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    return text[:400] or type(exc).__name__


def _fallback_rungs(args, role: str, tried: set, failures: list[str]):
    """Next rungs for a failed ask, in order: the role's fallbacks from model_routing.yaml,
    then the local GPU rung -- each only when it resolves to a real model. A rung that
    can't be built is recorded in ``failures`` (it lands in the final error line)."""
    env_dir = getattr(args, "env_dir", None)
    try:
        pairs = load_routing().fallback_pairs(role)
    except RoutingConfigError as exc:
        failures.append(f"{role} fallbacks [{_one_line(exc)}]")
        pairs = ()
    for provider, route in pairs:
        if (str(provider), str(route)) in tried or not route:
            continue
        tried.add((str(provider), str(route)))
        try:
            client, cfg = make_client(provider, env_dir=env_dir)
        except (ProviderError, LlmError) as exc:
            failures.append(f"{provider}/{route} [{_one_line(exc)}]")
            continue
        yield client, cfg, route
    if is_local_provider(getattr(args, "provider", None)):
        return
    local = _local_gpu_fallback_client(args)
    if local is None:
        failures.append("local GPU rung [not available]")
        return
    client, cfg = local
    if not cfg.model:
        failures.append(f"local GPU rung {cfg.name} [no model configured]")
        return
    if (str(cfg.name), str(cfg.model)) in tried:
        return
    tried.add((str(cfg.name), str(cfg.model)))
    yield client, cfg, cfg.model


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
        help="ask runtime profile: auto uses cloud chat plus local worker routing; local uses the local GPU rung",
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
        default=str(Path.home() / ".hydra-memory"),
        help="memory root for context bundle lookups",
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
    root = _root_arg(args.root)
    if not root.is_dir():
        print(f"workspace root is not a directory: {root}", file=sys.stderr)
        return 2
    try:
        runtime = _resolve_chat_runtime(args)
    except (ProviderError, RoleError, ValueError, LlmError) as e:  # LlmError: no rung reachable
        print(f"runtime error: {e}", file=sys.stderr)
        return 2
    args.provider = runtime["provider"]
    if getattr(args, "runtime_only", False):
        _print_ask_identity(runtime, root, args.approval_policy, args.prompt)
        return 0
    try:
        client, cfg = _make_client_or_setup(args)
    except (ProviderError, LlmError) as e:
        if not getattr(e, "hydra_reported", False):  # the helper already said it
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
    if getattr(args, "with_context", False):
        try:
            composed = compose_operator_prompt(
                build_agent_system_prompt(DEFAULT_SYSTEM_PROMPT),
                repo_root=REPO_ROOT,
                memory_root=args.memory_root,
                evidence_root=REPO_ROOT / "evidence",
                budget_bytes=args.context_budget_bytes,
                query=args.prompt,
            )
        except PromptBuilderError as e:
            print(f"prompt builder error: {e}", file=sys.stderr)
            return 2
        system_prompt = composed["prompt"]
        context_proof_lines = composed["proof"]
    if getattr(args, "truth_context", False):
        try:
            truth = assemble_truth_context(
                args.prompt,
                repo_root=REPO_ROOT,
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

    loop = AgentLoop(_budgeted(client, cfg.name, model, args.prompt), model=model, system_prompt=system_prompt)
    memory_root = Path(args.memory_root).expanduser().resolve()
    tools = _bind_tools(root, approval_policy=args.approval_policy, memory_root=memory_root)
    print(
        f"Hydra: provider={cfg.name} model={model} root={root}\n"
        f"  tools: {', '.join(t.name for t in tools)}\n"
        f"  approval_policy={args.approval_policy}",
        file=sys.stderr,
    )
    if context_proof_lines:
        print("  context: " + "; ".join(context_proof_lines[:3]), file=sys.stderr)
    fallback_role = auto_route_report["role"] if auto_route_report is not None else "chat"
    tried = {(str(cfg.name), str(model))}
    failures: list[str] = []
    next_rungs = _fallback_rungs(args, fallback_role, tried, failures)
    while True:
        try:
            with use_trace_id(os.environ.get("HYDRA_TRACE_ID")), use_correlation_id(
                os.environ.get("HYDRA_PARENT_MESSAGE_ID")
            ):
                result = loop.run(
                    _with_workspace_context(args.prompt, root),
                    tools=tools,
                    max_iterations=args.max_iterations,
                    timeout=args.timeout,
                )
            break
        except LoopError as e:
            print(f"ask error: {e}", file=sys.stderr)
            return 2
        except LlmError as e:
            auth_message = _auth_rejection_message(args.provider, e)
            if auth_message:
                print(auth_message, file=sys.stderr)
                return 1
            failures.append(f"{cfg.name}/{model} [{_one_line(e)}]")
            if is_local_provider(args.provider):
                print(f"ask error: no rung answered: {'; '.join(failures)}", file=sys.stderr)
                return 1
            nxt = next(next_rungs, None)
            if nxt is None:
                print(f"ask error: no rung answered: {'; '.join(failures)}", file=sys.stderr)
                return 1
            failed_route = f"{cfg.name}/{model}"
            client, cfg, model = nxt
            args.provider, args.model = cfg.name, model
            print(f"hydra: {failed_route} failed ({_one_line(e)[:160]}); "
                  f"trying {fallback_role} fallback rung {cfg.name}/{model}", file=sys.stderr)
            loop = AgentLoop(_budgeted(client, cfg.name, model, args.prompt), model=model,
                             system_prompt=system_prompt)
            continue

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
    return 0 if result.halted_reason == "natural" else 1
