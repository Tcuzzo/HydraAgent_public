"""Execution and surgery CLI command handlers."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Literal

from hydra.cli.tool_binding import bind_tools, root_arg
from hydra.llm import LlmError
from hydra.loop import AgentLoop
from hydra.providers import ProviderError, make_client
from hydra.roles import (
    RoleError,
    RoleSpec,
    provider_available,
    resolve_roles,
    should_unload_between_roles,
)
# SEAM CUT: hydra.surgery is stripped (private sauce); surgery/surgery-loop commands removed.


REPO_ROOT = Path(__file__).resolve().parents[2]
HYDRA_CONFIG = REPO_ROOT / ".hydraAgent" / "hydra.yaml"
HYDRA_SCHEMA = REPO_ROOT / ".hydraAgent" / "schemas" / "hydra.schema.yaml"
PLANNER_SYSTEM_PROMPT = (
    "You are Hydra Planner. Produce a concrete engineering plan for "
    "the doer. Preserve the original request. Do not edit files."
)
DOER_SYSTEM_PROMPT = (
    "You are Hydra Doer. Execute the supplied plan in the workspace "
    "using tools. Do not reduce scope. Say exactly what changed and what "
    "verification ran."
)


def register_execution_commands(sub: argparse._SubParsersAction, *, policy_choices: tuple[str, ...]) -> None:
    p_execute = sub.add_parser(
        "execute",
        help="run planner -> doer -> auditor-gated mission loop",
        description=(
            "Execute a mission through config-driven roles: planner creates "
            "the plan, doer performs tool work, and the strongest available "
            "audit lane reviews the result."
        ),
    )
    p_execute.add_argument("mission")
    p_execute.add_argument("--config", default=None)
    p_execute.add_argument("--env-dir", default=None)
    p_execute.add_argument("--root", default=None, help="filesystem scope (default: /)")
    p_execute.add_argument("--max-iterations", type=int, default=8)
    p_execute.add_argument(
        "--fallback-iterations",
        type=int,
        default=12,
        help=(
            "minimum doer iterations when Codex is unavailable and local fallback "
            "audit is used; the loop still ends early on a natural answer"
        ),
    )
    p_execute.add_argument("--timeout", type=float, default=120.0)
    p_execute.add_argument(
        "--approval-policy",
        choices=policy_choices,
        default="ask",
        help="risky tool policy for doer bash/fs_write/fs_edit",
    )
    p_execute.add_argument(
        "--no-audit",
        action="store_true",
        help="skip auditor execution for local dry-runs; never claims GREEN",
    )
    p_execute.add_argument(
        "--audit-mode",
        choices=("auto", "codex", "local", "none"),
        default="auto",
        help=(
            "audit lane: auto uses Codex when available and local fallback otherwise; "
            "codex fails closed; local uses the planner as verifier; none skips audit"
        ),
    )
    p_execute.add_argument(
        "--no-unload-between-roles",
        action="store_true",
        help=(
            "keep distinct local Ollama planner/doer models loaded together; "
            "default unloads planner before doer to conserve VRAM"
        ),
    )

    # SEAM CUT: surgery and surgery-loop subcommands removed (hydra.surgery is stripped).


def _resolved_roles_or_exit(args: argparse.Namespace):
    try:
        return resolve_roles(
            config_path=args.config or HYDRA_CONFIG,
            schema_path=HYDRA_SCHEMA,
        )
    except RoleError as e:
        print(f"role error: {e}", file=sys.stderr)
        return None


def _client_for_role(role: RoleSpec, env_dir: str | None = None):
    if role.provider == "codex":
        raise ProviderError(
            "EVALUATOR_UNAVAILABLE: Codex adapter is configured but not callable in this runtime"
        )
    client, cfg = make_client(role.provider, env_dir=env_dir)
    return client, role.model or cfg.model


def _unload_ollama_model(model: str) -> None:
    proc = subprocess.run(
        ["ollama", "stop", model],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        print(
            f"warning: could not unload Ollama model {model!r}: "
            f"{(proc.stderr or proc.stdout).strip()}",
            file=sys.stderr,
        )


AuditMode = Literal["auto", "codex", "local", "none"]


def _resolve_audit_mode(
    requested: str,
    *,
    auditor_available: bool,
) -> tuple[str, str]:
    if requested == "none":
        return "none", "audit disabled by operator"
    if requested == "local":
        return "local", "operator selected local fallback audit"
    if requested == "codex":
        if auditor_available:
            return "codex", "Codex evaluator available"
        return "blocked", "Codex evaluator required but unavailable"
    if requested == "auto":
        if auditor_available:
            return "codex", "Codex evaluator available"
        return "local", "Codex evaluator unavailable; using local fallback audit"
    return "blocked", f"unknown audit mode {requested!r}"

def cmd_execute(args: argparse.Namespace) -> int:
    roles = _resolved_roles_or_exit(args)
    if roles is None:
        return 2
    root = root_arg(args.root)
    if not root.is_dir():
        print(f"workspace root is not a directory: {root}", file=sys.stderr)
        return 2

    try:
        planner_client, planner_model = _client_for_role(roles.planner, args.env_dir)
        doer_client, doer_model = _client_for_role(roles.doer, args.env_dir)
    except ProviderError as e:
        print(f"provider error: {e}", file=sys.stderr)
        return 2

    requested_audit = "none" if args.no_audit else args.audit_mode
    auditor_ok, auditor_detail = provider_available(roles.auditor, env_dir=args.env_dir)
    audit_mode, audit_detail = _resolve_audit_mode(
        requested_audit,
        auditor_available=auditor_ok,
    )
    if audit_mode == "blocked":
        print(auditor_detail, file=sys.stderr)
        print(audit_detail, file=sys.stderr)
        return 2

    planner = AgentLoop(planner_client, model=planner_model, system_prompt=PLANNER_SYSTEM_PROMPT)
    doer = AgentLoop(doer_client, model=doer_model, system_prompt=DOER_SYSTEM_PROMPT)
    print(
        f"Hydra execute: planner={roles.planner.provider}/{planner_model} "
        f"doer={roles.doer.provider}/{doer_model} auditor={roles.auditor.provider}/{roles.auditor.model} "
        f"audit_mode={audit_mode}",
        file=sys.stderr,
    )
    if audit_mode == "local" and not auditor_ok:
        print(f"Hydra execute: {audit_detail}: {auditor_detail}", file=sys.stderr)

    try:
        plan = planner.run(
            "Create an implementation plan for this request:\n\n" + args.mission,
            tools=[],
            max_iterations=1,
            timeout=args.timeout,
        )
        if (
            not args.no_unload_between_roles
            and should_unload_between_roles(roles.planner, roles.doer)
        ):
            print(
                f"Hydra execute: unloading planner model {planner_model} "
                "before loading doer to conserve local VRAM",
                file=sys.stderr,
            )
            _unload_ollama_model(planner_model)
        prompt = (
            "Original request:\n"
            f"{args.mission}\n\n"
            "Planner output:\n"
            f"{plan.final_response}\n\n"
            "Execute this plan. Keep scope complete and verification concrete."
        )
        doer_iterations = args.max_iterations
        if audit_mode == "local":
            doer_iterations = max(args.max_iterations, args.fallback_iterations)
        result = doer.run(
            prompt,
            tools=bind_tools(root, approval_policy=args.approval_policy),
            max_iterations=doer_iterations,
            timeout=args.timeout,
        )
        local_audit = None
        if audit_mode == "local":
            if (
                not args.no_unload_between_roles
                and should_unload_between_roles(roles.doer, roles.planner)
            ):
                print(
                    f"Hydra execute: unloading doer model {doer_model} "
                    "before local audit to conserve local VRAM",
                    file=sys.stderr,
                )
                _unload_ollama_model(doer_model)
            local_audit = planner.run(
                (
                    "Audit this run as a strict local verifier. Do not claim Codex GREEN.\n\n"
                    "Original request:\n"
                    f"{args.mission}\n\n"
                    "Original plan:\n"
                    f"{plan.final_response}\n\n"
                    "Doer result:\n"
                    f"{result.final_response}\n\n"
                    "Return one paragraph with: capability confidence, missing evidence, "
                    "recommended next tests, and whether another repair iteration is needed."
                ),
                tools=[],
                max_iterations=1,
                timeout=args.timeout,
            )
    except LlmError as e:
        print(f"LLM error: {e}", file=sys.stderr)
        return 1

    print("PLAN:")
    print(plan.final_response)
    print()
    print("DOER RESULT:")
    print(result.final_response)
    if audit_mode == "none":
        print("\nAUDIT: skipped; no GREEN verdict claimed.")
    elif audit_mode == "local":
        print("\nAUDIT: local fallback verifier; no Codex GREEN verdict claimed.")
        if local_audit is not None:
            print(local_audit.final_response)
    else:
        print("\nAUDIT: Codex evaluator configured; adapter execution is not implemented here.")
    return 0


def _make_patch_doer(patch_file: str, replace: str, with_text: str):
    def _patch_doer(sandbox: Path, _plan: dict) -> str:
        target = (sandbox / patch_file).resolve(strict=False)
        try:
            target.relative_to(sandbox.resolve())
        except ValueError as e:
            raise SurgeryError(f"patch file escapes sandbox: {patch_file}") from e
        if not target.is_file():
            raise SurgeryError(f"patch file is not a file: {patch_file}")
        text = target.read_text(encoding="utf-8")
        if replace not in text:
            raise SurgeryError(f"old text not found in {patch_file}")
        target.write_text(text.replace(replace, with_text, 1), encoding="utf-8")
        return f"patched {patch_file}: replaced {replace!r} with {with_text!r}"

    return _patch_doer


# SEAM CUT: cmd_surgery and cmd_surgery_loop removed (hydra.surgery is stripped).
# These functions are exported stubs so __main__.py dispatch dict compiles cleanly.
def cmd_surgery(args: argparse.Namespace) -> int:  # pragma: no cover
    print("surgery command not available in this build", file=sys.stderr)
    return 1


def cmd_surgery_loop(args: argparse.Namespace) -> int:  # pragma: no cover
    print("surgery-loop command not available in this build", file=sys.stderr)
    return 1

