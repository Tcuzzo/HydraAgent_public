"""hydra — operator CLI entrypoint.

`python3 -m hydra <subcommand>` ties the proven runtime together into
one daily-driver command:

  ask <prompt>     run the agent loop with the default tool set
  chat             interactive prompt loop around `ask`
  tools            list the default tools the agent loop is wired with
  providers        list configured LLM providers
  status           verifier verdict for the live repo
  wiki             initialize and render Hydra wiki memory
  audit <target>   deterministic read-only runtime audit for a directory
  locate <query>   deterministic filename/directory search under a root
  local-memory     import local durable memory context for chat
  remember         append a sourced durable lesson to local memory
  capability-score     score capability from promoted evidence
  competitive-score compare Hydra against current agent runtime patterns
  domain-pack      print domain playbooks for specialized agent work
  task-eval        score real operator task scenarios
  trace-bundle     write a compact evidence bundle for review
  models           list models the resolved provider exposes
                   (only meaningful for ollama; cloud hosts don't ship
                   the `/api/tags` surface)
  setup            write local/cloud/Codex config outside the repo
  roles            show planner/doer/auditor model routing
  telegram         check Telegram Bot API reachability
  execute          planner -> doer -> auditor-gated mission loop
  surgery-loop     bounded repair loop with per-attempt evidence
  ops              preview infrastructure ops plans without execution

Options for `ask`:
  --provider {ollama,ollama-cloud,...}   default: ollama
  --model <name>                 default: provider's resolved model
  --root <dir>                   filesystem scope (default: /)
  --max-iterations N             default: 200 (iterate like Claude Code/Codex)
  --approval-policy allow|ask|deny
                                  default: allow

The CLI binds the default tool set (fs_read, fs_write, bash, glob,
grep, http_fetch) with `--root` pre-bound via closure — the LLM only
sees args it should care about, never the filesystem scope.

Maturity: SCAFFOLDED. Promoted by §10.29.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))

from hydra.policy import POLICY_CHOICES  # noqa: E402
from hydra.providers import DEFAULT_ENV_DIR  # noqa: E402
from hydra.cli.cmd_ask import cmd_ask, register_ask_command  # noqa: E402
from hydra.cli.cmd_code import cmd_code, register_code_command  # noqa: E402
# Optional loop and swarm subcommands were removed in the public build.
from hydra.cli.cmd_chat import (  # noqa: E402
    DEFAULT_MEMORY_ROOT,
    LOCAL_MEMORY_MAX_CHARS,
    _handle_chat_operator_intent,
    _handle_chat_terminal_control,
    cmd_chat,
    register_chat_commands,
)
from hydra.cli.cmd_chat_support import (  # noqa: E402
    cmd_skills,
    cmd_tools,
    register_chat_support_commands,
)
# SLICE 2 CUT: cmd_input removed — it was purely a loop_runtime wrapper (loop_runtime stripped).
from hydra.cli.cmd_execution import (  # noqa: E402
    cmd_execute,
    cmd_surgery,
    cmd_surgery_loop,
    register_execution_commands,
)
from hydra.cli.cmd_memory import (  # noqa: E402
    cmd_capabilities,
    cmd_local_memory,
    cmd_remember,
    cmd_source,
    cmd_wiki,
    register_memory_commands,
)
from hydra.cli.cmd_mission import cmd_continuation, cmd_mission, register_mission_commands  # noqa: E402
from hydra.cli.cmd_ops import (  # noqa: E402
    cmd_aci,
    cmd_audit,
    cmd_autonomy,
    cmd_competitive_score,
    cmd_domain_pack,
    cmd_locate,
    cmd_ops,
    cmd_status,
    cmd_task_eval,
    cmd_trace_bundle,
    cmd_capability_score,
    register_ops_commands,
    register_ops_surface_commands,
)
from hydra.cli.cmd_runtime import cmd_declarative, cmd_models, cmd_providers, cmd_roles, register_runtime_commands  # noqa: E402
from hydra.cli.cmd_setup import cmd_setup, register_setup_commands  # noqa: E402
from hydra.cli.cmd_workbench import (  # noqa: E402
    cmd_api,
    cmd_ledger,
    cmd_telegram,
    cmd_workbench,
    register_workbench_commands,
)
from hydra.cli.cmd_undo import cmd_undo, register_undo_command  # noqa: E402
from hydra.cli.tool_binding import bind_tools as _bind_tools  # noqa: E402


# ── self-audit command ────────────────────────────────────────────────────────
def _cmd_self_audit(_args) -> int:
    """Run HYDRA's self-audit: classify->route->execute invariant checks.

    Returns exit code 0 on pass, 1 on any invariant violation.
    """
    import json as _json
    from hydra.self_audit import run_self_audit
    report = run_self_audit()
    d = report.as_dict()
    print(_json.dumps(d, indent=2))
    if report.passed:
        print("\nself-audit: PASSED — all invariants green")
        return 0
    else:
        print(f"\nself-audit: FAILED — {len(report.violations)} violation(s):")
        for v in report.violations:
            print(f"  FAIL [{v.check_id}] {v.description}")
            if v.detail:
                print(f"       {v.detail}")
        return 1

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hydra",
        description="Hydra operator CLI — run the agent loop with default tools.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    register_ask_command(
        sub,
        default_env_dir=DEFAULT_ENV_DIR,
        policy_choices=POLICY_CHOICES,
    )

    register_chat_commands(
        sub,
        default_env_dir=DEFAULT_ENV_DIR,
        default_memory_root=DEFAULT_MEMORY_ROOT,
        local_memory_max_chars=LOCAL_MEMORY_MAX_CHARS,
        policy_choices=POLICY_CHOICES,
    )

    register_chat_support_commands(sub)
    # SLICE 2 CUT: register_input_command removed (loop_runtime stripped).

    register_runtime_commands(sub)

    register_mission_commands(sub)
    register_memory_commands(sub)
    register_ops_commands(sub)

    register_ops_surface_commands(sub)

    register_setup_commands(sub, default_env_dir=DEFAULT_ENV_DIR)

    register_execution_commands(sub, policy_choices=POLICY_CHOICES)

    register_workbench_commands(sub)
    register_code_command(sub)
    # (loop + swarm command registration removed)
    # SLICE 2 CUT: register_lol_run_command and register_worker_commands removed (lol/worker sauce stripped).
    register_undo_command(sub)

    # self-audit: hydra self-audit
    _sa = sub.add_parser("self-audit", help="Run HYDRA's self-audit (classify->route->execute invariants)")
    _sa.set_defaults(cmd="self-audit")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return {
        "ask": cmd_ask,
        "chat": cmd_chat,
        "tools": cmd_tools,
        # SLICE 2 CUT: "input" command removed (loop_runtime stripped).
        "skills": cmd_skills,
        "providers": cmd_providers,
        "status": cmd_status,
        "source": cmd_source,
        "mission": cmd_mission,
        "continuation": cmd_continuation,
        "wiki": cmd_wiki,
        "capabilities": cmd_capabilities,
        "aci": cmd_aci,
        "audit": cmd_audit,
        "locate": cmd_locate,
        "local-memory": cmd_local_memory,
        "remember": cmd_remember,
        "capability-score": cmd_capability_score,
        "competitive-score": cmd_competitive_score,
        "domain-pack": cmd_domain_pack,
        "ledger": cmd_ledger,
        "api": cmd_api,
        "workbench": cmd_workbench,
        "autonomy": cmd_autonomy,
        "telegram": cmd_telegram,
        "ops": cmd_ops,
        "task-eval": cmd_task_eval,
        "trace-bundle": cmd_trace_bundle,
        "models": cmd_models,
        "setup": cmd_setup,
        "roles": cmd_roles,
        "declarative": cmd_declarative,
        "execute": cmd_execute,
        "surgery": cmd_surgery,
        "surgery-loop": cmd_surgery_loop,
        "code": cmd_code,
        # (loop + swarm commands removed)
        # SLICE 2 CUT: worker + lol-run removed (worker_orchestrator/lol_runner sauce stripped).
        "undo": cmd_undo,
        "self-audit": _cmd_self_audit,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
