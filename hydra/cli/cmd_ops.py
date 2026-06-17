"""Operator inspection CLI command handlers."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from hydra.aci import (
    AciError,
    inspect_repo as aci_inspect_repo,
    inspect_runtime as aci_inspect_runtime,
    read_evidence as aci_read_evidence,
)
from hydra.audit import run_audit as run_directory_audit
from hydra.autonomy import classify_tool_call, render_autonomy_text
# SEAM CUT: hydra.remote_runtime_audit and hydra.cidr_discover are stripped.
# remote-audit and discover-live ops subcommands removed below.
from hydra.cluster_lessons import (
    ClusterLessonError,
    promote_clusters_to_lessons,
    render_text as render_cluster_lessons_text,
)
# SLICE 2 CUT: hydra.competitive removed (sauce stripped).
from hydra.context_engine import (
    ContextEngineError,
    assemble_context,
    render_text as render_context_text,
)
# SLICE 2 CUT: hydra.doctor removed (doctor/self_diagnosis sauce stripped).
from hydra.domain_packs import DomainPackError, get_domain_pack
from hydra.domain_packs import render_text as render_domain_pack_text
from hydra.environment import (
    EnvironmentError,
    create_session as create_env_session,
    fetch_session_url,
    read_session_file,
    run_session_command,
    session_status,
    write_session_file,
)
from hydra.failure_clusters import (
    FailureClusterError,
    cluster_failures,
    render_text as render_clusters_text,
)
from hydra.locate import run_locate
from hydra.network_discovery import (
    NetworkDiscoveryError,
    plan_discovery,
    render_text as render_discovery_text,
)
from hydra.ops_audit import OpsAuditError, run_audit as run_ops_audit
from hydra.ops_audit_judge import OpsAuditJudgeError, judge_audit_summary
from hydra.ops_packs import OpsPackError, load_pack, parse_target, render_plan
from hydra.orchestrate import (
    OrchestrateError,
    dispatch,
    dispatch_graph,
    render_graph_text,
    render_text as render_dispatch_text,
    task_from_dict,
)
from hydra.policy import POLICY_CHOICES
from hydra.recall import RecallError, recall as recall_search, render_text as render_recall_text
from hydra.repo_runtime import RepoRuntimeError, audit_repo, render_text as render_repo_text
from hydra.rubric_judge import RubricJudgeError, judge as rubric_judge, render_text as render_judge_text
# SLICE 2 CUT: hydra.self_diagnosis and hydra.task_evals removed (sauce stripped).
from hydra.trace_bundle import build_trace_bundle
from hydra.trace_bundle import render_text as render_trace_bundle_text
from hydra.trace_summary import (
    TraceSummaryError,
    render_text as render_trace_summary_text,
    summarize_trace,
)
# SLICE 2 CUT: hydra.build removed (build sauce stripped).
from hydra.verify_claim import VerifyClaimError, render_text as render_verify_text, verify_claim
from hydra.workflow import WorkflowError, render_text as render_workflow_text, run_workflow
# SLICE 2 CUT: verifier.check removed (verifier/ package stripped).


REPO_ROOT = Path(__file__).resolve().parents[2]
OPS_PACKS_DIR = REPO_ROOT / ".hydraAgent" / "ops-packs"


def register_ops_commands(sub: argparse._SubParsersAction) -> None:
    sub.add_parser("status", help="verifier verdict for the live repo")

    p_audit = sub.add_parser(
        "audit",
        help="run a deterministic read-only audit for a directory",
        description=(
            "Inspect a target directory without an LLM and without mutation. "
            "Reports concrete evidence, interesting filenames, command hints, "
            "and structured data without printing secret contents."
        ),
    )
    p_audit.add_argument("target", help="directory to audit; absolute paths are accepted")
    p_audit.add_argument("--root", default=None, help="base for relative targets (default: current directory)")
    p_audit.add_argument("--max-files", type=int, default=5000)
    p_audit.add_argument("--max-findings", type=int, default=40)

    p_locate = sub.add_parser(
        "locate",
        help="find named files or directories under a root",
        description="Deterministically locate file or directory names without reading file contents.",
    )
    p_locate.add_argument("query", help="case-insensitive name fragment to find")
    p_locate.add_argument("--root", default="/", help="root directory to search")
    p_locate.add_argument("--max-results", type=int, default=100)
    p_locate.add_argument("--max-files", type=int, default=10000)
    p_locate.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON summary",
    )
    p_locate.add_argument("--out", default=None, help="optional path to write JSON report")

    p_build = sub.add_parser(
        "capability-score",
        help="score Hydra build capability from promoted evidence",
        description=(
            "Measure the current agent against the build plan: evals, context, "
            "tools, verification, routing, observability, guardrails, and domain packs."
        ),
    )
    p_build.add_argument("--repo-root", default=str(REPO_ROOT), help="repo root to score")
    p_build.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_build.add_argument("--out", default=None, help="optional path to write JSON report")

    p_competitive = sub.add_parser(
        "competitive-score",
        help="compare Hydra against current agent runtime patterns",
        description=(
            "Score Hydra's promoted operator-runtime capabilities against "
            "current Codex, Claude Code, OpenAI Agents SDK, and LangGraph patterns."
        ),
    )
    p_competitive.add_argument("--repo-root", default=str(REPO_ROOT), help="repo root to score")
    p_competitive.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_competitive.add_argument("--out", default=None, help="optional path to write JSON report")

    p_domain = sub.add_parser(
        "domain-pack",
        help="print domain playbooks for specialized agent work",
        description="Emit a domain specialization pack as text or JSON.",
    )
    p_domain.add_argument("pack_id", help="domain pack id, e.g. hydra")
    p_domain.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON pack",
    )
    p_domain.add_argument("--out", default=None, help="optional path to write JSON pack")

    aci_p = sub.add_parser("aci", help="run ACI v2 read-only tools")
    aci_sub = aci_p.add_subparsers(dest="aci_cmd", required=True)
    aci_sub.add_parser("inspect-repo", help="inspect the Hydra repo without mutation")
    aci_sub.add_parser("inspect-runtime", help="inspect runtime facts without mutation")
    aci_read = aci_sub.add_parser("read-evidence", help="read a bounded evidence file")
    aci_read.add_argument("path")

    p_autonomy = sub.add_parser("autonomy", help="inspect operator autonomy policy decisions")
    autonomy_sub = p_autonomy.add_subparsers(dest="autonomy_cmd", required=True)
    p_autonomy_classify = autonomy_sub.add_parser("classify", help="classify a tool call under a policy")
    p_autonomy_classify.add_argument("tool_name")
    p_autonomy_classify.add_argument("arguments_json")
    p_autonomy_classify.add_argument("--policy", choices=POLICY_CHOICES, default="ask")
    p_autonomy_classify.add_argument("--format", choices=("text", "json"), default="text")

    p_task_eval = sub.add_parser(
        "task-eval",
        help="score real operator task scenarios",
        description="Evaluate Hydra domain playbooks against concrete operator scenarios.",
    )
    p_task_eval.add_argument("suite_id", help="task eval suite id, e.g. hydra")
    p_task_eval.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_task_eval.add_argument("--out", default=None, help="optional path to write JSON report")

    p_trace = sub.add_parser(
        "trace-bundle",
        help="write a compact evidence bundle for review",
        description=(
            "Build a scrubbed JSON/text bundle with verifier status, build score, "
            "task eval status, recent promotions, and reproducible commands."
        ),
    )
    p_trace.add_argument("--repo-root", default=str(REPO_ROOT), help="repo root to inspect")
    p_trace.add_argument("--suite", default="hydra", help="task eval suite id")
    p_trace.add_argument("--limit", type=int, default=12, help="recent promotion count")
    p_trace.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON summary",
    )
    p_trace.add_argument("--out", default=None, help="optional path to write JSON bundle")



def register_ops_surface_commands(sub: argparse._SubParsersAction) -> None:
    p_ops = sub.add_parser(
        "ops",
        help="preview ops plans and run Hydra self-checks",
        description=(
            "Preview ops pack plans and run Hydra self-checks. Pack commands "
            "are never executed by `ops plan`, and there are no hidden "
            "permission gates in the preview."
        ),
    )
    ops_sub = p_ops.add_subparsers(dest="ops_cmd", required=True)
    p_ops_plan = ops_sub.add_parser(
        "plan",
        help="preview an ops pack plan with no hidden permission gates",
        description=(
            "Preview an ops pack plan. This does not execute pack commands, "
            "and it does not add hidden permission gates."
        ),
    )
    p_ops_plan.add_argument("target", help="target to plan for, e.g. local or repo:/path")
    p_ops_plan.add_argument("--pack", required=True, help="ops pack id to load")
    p_ops_plan.add_argument("--packs-dir", default=None, help="directory containing ops pack YAML")
    p_ops_plan.add_argument("--out", default=None, help="optional path to write JSON plan")
    p_ops_plan.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON summary",
    )
    p_ops_recall = ops_sub.add_parser(
        "recall",
        help="keyword-recall over durable lessons + failure clusters + recent promotions",
        description=(
            "Search HydraAgent's own memory for past experience matching a "
            "query. Sources: durable lessons (§10.44), failure clusters "
            "(§10.56), and recent slice promotions (STATUS.md). Ranking is "
            "deterministic keyword-occurrence count."
        ),
    )
    p_ops_recall.add_argument("query", help="search query (free text)")
    p_ops_recall.add_argument(
        "--memory-root", default=None, help="memory root (default: ~/.hydra-memory)"
    )
    p_ops_recall.add_argument(
        "--evidence-root", default=None, help="evidence root (default: <repo>/evidence)"
    )
    p_ops_recall.add_argument(
        "--top-k", type=int, default=5, help="top-K hits per source / combined (default: 5)"
    )
    p_ops_recall.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_recall.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_judge = ops_sub.add_parser(
        "judge",
        help="run a deterministic rubric judge against a draft file",
        description=(
            "Score a draft file against a JSON rubric (list of rules). Each "
            "rule has id, kind (must_contain/must_not_contain/regex_required/"
            "regex_forbidden/max_length/min_length/must_cite_source), optional "
            "pattern/value, and weight. Returns a weighted score, violations, "
            "and PASS/FAIL verdict against the threshold."
        ),
    )
    p_ops_judge.add_argument("--draft", required=True, help="path to draft text file")
    p_ops_judge.add_argument("--rubric", required=True, help="path to JSON rubric file")
    p_ops_judge.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="pass threshold in [0.0, 1.0] (default: 1.0 = every rule)",
    )
    p_ops_judge.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_judge.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_context = ops_sub.add_parser(
        "context",
        help="assemble a byte-bounded context bundle (lessons + clusters + promotions)",
        description=(
            "Pick the highest-signal context — recent durable lessons, top "
            "failure clusters, recent slice promotions — under a hard byte "
            "budget, in priority order. Returns a manifest and a rendered "
            "block ready to paste into a planner/doer prompt."
        ),
    )
    p_ops_context.add_argument(
        "--memory-root", default=None, help="memory root (default: ~/.hydra-memory)"
    )
    p_ops_context.add_argument(
        "--evidence-root", default=None, help="evidence root (default: <repo>/evidence)"
    )
    p_ops_context.add_argument(
        "--budget-bytes", type=int, default=8192, help="byte budget (default: 8192)"
    )
    p_ops_context.add_argument(
        "--max-lessons", type=int, default=10, help="cap on lesson entries (default: 10)"
    )
    p_ops_context.add_argument(
        "--max-clusters", type=int, default=10, help="cap on cluster entries (default: 10)"
    )
    p_ops_context.add_argument(
        "--max-promotions", type=int, default=12, help="cap on promotion entries (default: 12)"
    )
    p_ops_context.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_context.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_tsum = ops_sub.add_parser(
        "trace-summary",
        help="summarize a saved §10.85 ask trace or §10.87 chat JSONL trace",
        description=(
            "Read a saved trace file (single JSON object for ask traces, "
            "JSONL of turn objects for chat traces). Emit per-tool counts + "
            "wall-time, error rate, slowest individual calls, halted-reason "
            "distribution, and total iterations. Deterministic and read-only."
        ),
    )
    p_ops_tsum.add_argument("path", help="path to the saved trace file")
    p_ops_tsum.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON summary",
    )
    p_ops_tsum.add_argument("--out", default=None, help="optional path to write JSON summary")

    # SEAM CUT: discover-live subparser removed (hydra.cidr_discover is stripped).

    p_ops_doctor = ops_sub.add_parser(
        "doctor",
        help="composite tune-up: self-check + failure clusters + optional lesson promotion",
        description=(
            "One-shot operator tune-up. Runs §10.52 self-check and §10.56 "
            "failure clustering against the live repo + evidence; with "
            "--promote, also runs §10.57 cluster→lesson promotion. Exits 1 "
            "when overall health is red."
        ),
    )
    p_ops_doctor.add_argument(
        "--evidence-root", default=None, help="evidence root (default: <repo>/evidence)"
    )
    p_ops_doctor.add_argument(
        "--memory-root", default=None, help="memory root for cluster→lesson promotion"
    )
    p_ops_doctor.add_argument(
        "--promote", action="store_true",
        help="also run §10.57 cluster→lesson promotion (default: read-only)",
    )
    p_ops_doctor.add_argument(
        "--min-cluster-count", type=int, default=2,
        help="minimum cluster count to promote when --promote is set (default: 2)",
    )
    p_ops_doctor.add_argument(
        "--top-clusters", type=int, default=10,
        help="top-K clusters to surface in self-check and (when promoting) write as lessons (default: 10)",
    )
    p_ops_doctor.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_doctor.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_verify = ops_sub.add_parser(
        "verify-claim",
        help="verify a claim against a source URL via deterministic token-match",
        description=(
            "Fetch a source URL via §10.74 web_extract, tokenize the claim "
            "(stop-words filtered, length ≥ 3), and check how many distinct "
            "claim tokens appear in the page's extracted text. Returns PASS "
            "when matched/total ≥ threshold (default 0.6). This is a fast "
            "deterministic first line — pair with §10.65 llm_judge for "
            "semantic verification."
        ),
    )
    p_ops_verify.add_argument("claim", help="the claim to verify (free text)")
    p_ops_verify.add_argument("--url", required=True, help="source URL")
    p_ops_verify.add_argument(
        "--allowed-host",
        action="append",
        required=True,
        help="allow-listed host (repeatable)",
    )
    p_ops_verify.add_argument(
        "--threshold", type=float, default=0.6,
        help="pass threshold in [0.0, 1.0] (default: 0.6)",
    )
    p_ops_verify.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_verify.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_workflow = ops_sub.add_parser(
        "workflow",
        help="dispatch a list of subagent tasks and judge each output (one PASS/FAIL)",
        description=(
            "Load a JSON array of task dicts (id, command, optional cwd/env/"
            "timeout_seconds/success_pattern/rubric). Dispatches them under "
            "max_concurrency via §10.58; for each task that has a rubric, "
            "judges its stdout via §10.60. Emits one unified report with "
            "`all_pass=true` iff every dispatched task succeeded AND every "
            "judged task verdict was PASS."
        ),
    )
    p_ops_workflow.add_argument("tasks_file", help="path to JSON array of task dicts (with optional rubric per task)")
    p_ops_workflow.add_argument(
        "--max-concurrency", type=int, default=4,
        help="maximum concurrent subagents (default: 4)",
    )
    p_ops_workflow.add_argument(
        "--pass-threshold", type=float, default=1.0,
        help="rubric pass threshold per task (default: 1.0)",
    )
    p_ops_workflow.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_workflow.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_dispatch = ops_sub.add_parser(
        "dispatch",
        help="run a list of subagent tasks under bounded concurrency",
        description=(
            "Load a JSON array of subagent task specs (id, command, optional "
            "cwd/env/timeout_seconds/success_pattern), run them concurrently "
            "under a max_concurrency bound, and emit a per-task report with "
            "status, returncode, bounded stdout/stderr, duration, and success "
            "pattern match."
        ),
    )
    p_ops_dispatch.add_argument("tasks_file", help="path to JSON array of task specs")
    p_ops_dispatch.add_argument(
        "--max-concurrency",
        type=int,
        default=4,
        help="maximum concurrent subagents (default: 4)",
    )
    p_ops_dispatch.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_dispatch.add_argument("--graph", action="store_true", help="respect depends_on task dependencies")
    p_ops_dispatch.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_env = ops_sub.add_parser(
        "env",
        help="create and operate local sandbox environment sessions",
    )
    env_sub = p_ops_env.add_subparsers(dest="env_cmd", required=True)
    p_ops_env_create = env_sub.add_parser("create", help="create a repo sandbox session")
    p_ops_env_create.add_argument("repo")
    p_ops_env_create.add_argument("--env-root", default="evidence/environments")
    p_ops_env_create.add_argument("--title", required=True)
    p_ops_env_create.add_argument("--format", choices=("text", "json"), default="text")
    p_ops_env_status = env_sub.add_parser("status", help="inspect a sandbox session")
    p_ops_env_status.add_argument("session_id")
    p_ops_env_status.add_argument("--env-root", default="evidence/environments")
    p_ops_env_status.add_argument("--format", choices=("text", "json"), default="text")
    p_ops_env_exec = env_sub.add_parser("exec", help="run a command inside a sandbox session")
    p_ops_env_exec.add_argument("session_id")
    p_ops_env_exec.add_argument("--env-root", default="evidence/environments")
    p_ops_env_exec.add_argument("--timeout", type=int, default=60)
    p_ops_env_exec.add_argument("--format", choices=("text", "json"), default="text")
    p_ops_env_exec.add_argument("command", nargs=argparse.REMAINDER)
    p_ops_env_read = env_sub.add_parser("read", help="read a file inside a sandbox session")
    p_ops_env_read.add_argument("session_id")
    p_ops_env_read.add_argument("path")
    p_ops_env_read.add_argument("--env-root", default="evidence/environments")
    p_ops_env_read.add_argument("--format", choices=("text", "json"), default="text")
    p_ops_env_write = env_sub.add_parser("write", help="write a file inside a sandbox session")
    p_ops_env_write.add_argument("session_id")
    p_ops_env_write.add_argument("path")
    p_ops_env_write.add_argument("--content", required=True)
    p_ops_env_write.add_argument("--env-root", default="evidence/environments")
    p_ops_env_write.add_argument("--format", choices=("text", "json"), default="text")
    p_ops_env_fetch = env_sub.add_parser("fetch", help="fetch a URL as sandbox browser evidence")
    p_ops_env_fetch.add_argument("session_id")
    p_ops_env_fetch.add_argument("url")
    p_ops_env_fetch.add_argument("--env-root", default="evidence/environments")
    p_ops_env_fetch.add_argument("--format", choices=("text", "json"), default="text")

    p_ops_cluster_lessons = ops_sub.add_parser(
        "cluster-lessons",
        help="promote top failure clusters into durable lessons (idempotent)",
        description=(
            "Cluster failures across the evidence directory, then write the "
            "top-N clusters (those with count >= min_count) as durable lessons "
            "via the existing remember_lesson API. Maintains a per-cluster "
            "count index so re-running over the same evidence does not duplicate "
            "lessons."
        ),
    )
    p_ops_cluster_lessons.add_argument(
        "--evidence-root",
        default=None,
        help="evidence directory root (default: <repo>/evidence)",
    )
    p_ops_cluster_lessons.add_argument(
        "--memory-root",
        default=None,
        help="memory root for lessons + index (default: ~/.hydra-memory)",
    )
    p_ops_cluster_lessons.add_argument(
        "--min-count",
        type=int,
        default=2,
        help="minimum cluster count to promote (default: 2)",
    )
    p_ops_cluster_lessons.add_argument(
        "--top-n",
        type=int,
        default=20,
        help="maximum clusters to promote per run (default: 20)",
    )
    p_ops_cluster_lessons.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_cluster_lessons.add_argument(
        "--out", default=None, help="optional path to write JSON report"
    )

    p_ops_clusters = ops_sub.add_parser(
        "clusters",
        help="cluster repeated failures and rot signals across the evidence",
        description=(
            "Scan the evidence directory for FAILed eval cases, missing tools, "
            "command errors, command timeouts, and matched rot signals; group "
            "them into named clusters with concrete repair targets ranked by "
            "recurrence."
        ),
    )
    p_ops_clusters.add_argument(
        "--evidence-root",
        default=None,
        help="evidence directory root (default: <repo>/evidence)",
    )
    p_ops_clusters.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_clusters.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_discover = ops_sub.add_parser(
        "discover",
        help="render a preview-only network discovery plan for a CIDR target",
        description=(
            "Parse a CIDR target, enumerate the hosts that would be probed, "
            "classify the network (private/public/loopback/etc.), and render "
            "the exact command templates a future probe would use. No live "
            "probing happens here — every command has execute: false."
        ),
    )
    p_ops_discover.add_argument(
        "target", help="discovery target, e.g. cidr:198.51.100.0/24"
    )
    p_ops_discover.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON plan",
    )
    p_ops_discover.add_argument("--out", default=None, help="optional path to write JSON plan")

    p_ops_repo = ops_sub.add_parser(
        "repo-audit",
        help="introspect any repo for manifests, tests, scripts, and verifications",
        description=(
            "Point Hydra at any repo path. Detects manifests, languages, "
            "package managers, test evidence, runtime scripts, container/CI "
            "evidence, and likely verification commands. Read-only — the target "
            "repo is never mutated."
        ),
    )
    p_ops_repo.add_argument("path", help="repo path to introspect")
    p_ops_repo.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_repo.add_argument("--out", default=None, help="optional path to write JSON report")

    p_ops_audit = ops_sub.add_parser(
        "audit",
        help="execute a read-only ops pack against a local or SSH target",
        description=(
            "Execute the read-only commands declared in an ops pack and write a "
            "structured evidence bundle. Missing commands become missing "
            "evidence; secret-like values are redacted before write. This slice "
            "supports local, router, file, and ssh targets."
        ),
    )
    p_ops_audit.add_argument("target", help="target to audit, e.g. local or ssh:remote-host")
    p_ops_audit.add_argument("--pack", required=True, help="ops pack id to load")
    p_ops_audit.add_argument(
        "--packs-dir", default=None, help="directory containing ops pack YAML"
    )
    p_ops_audit.add_argument(
        "--evidence-root",
        default=None,
        help="evidence directory root (default: <repo>/evidence)",
    )
    p_ops_audit.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON summary",
    )
    p_ops_audit.add_argument("--out", default=None, help="optional path to write JSON result")
    p_ops_audit.add_argument(
        "--judge-rubric",
        default=None,
        help="optional §10.60 JSON rubric path; the audit's summary.md is scored against it and `judge_report.json` is written to the bundle",
    )
    p_ops_audit.add_argument(
        "--judge-threshold",
        type=float,
        default=1.0,
        help="judge pass threshold in [0.0, 1.0] (default: 1.0)",
    )
    p_ops_audit.add_argument(
        "--judge-fail-exit",
        action="store_true",
        help="exit 1 when the summary judge verdict is FAIL (default: advisory only)",
    )

    # SEAM CUT: remote-audit subparser removed (hydra.remote_runtime_audit is stripped).

    p_ops_self = ops_sub.add_parser(
        "self-check",
        help="inspect HydraAgent health, rot signals, repairs, and proof",
        description=(
            "Inspect HydraAgent's own verifier, git state, evidence, provider "
            "configuration hints, and optional local runtime notes."
        ),
    )
    p_ops_self.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )
    p_ops_self.add_argument("--out", default=None, help="optional path to write JSON report")
    p_ops_self.add_argument("--env-dir", default=None, help="provider env directory to inspect")
    p_ops_self.add_argument(
        "--runtime-notes-root",
        default=None,
        help="optional local runtime notes directory to scan for rot hints",
    )



def cmd_status(_args: argparse.Namespace) -> int:
    # SLICE 2 CUT: verifier.check stripped; status command prints a note.
    print("status: verifier package removed in lean-core build.")
    return 0


def cmd_aci(args: argparse.Namespace) -> int:
    try:
        if args.aci_cmd == "inspect-repo":
            report = aci_inspect_repo(REPO_ROOT)
        elif args.aci_cmd == "inspect-runtime":
            report = aci_inspect_runtime(REPO_ROOT)
        elif args.aci_cmd == "read-evidence":
            report = aci_read_evidence(REPO_ROOT, args.path)
        else:
            return 0
    except (AciError, OSError) as e:
        print(f"aci error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0

def _print_audit_result(result) -> None:
    print(result.report, end="")
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def _print_locate_result(result) -> None:
    print(result.report, end="")
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def cmd_audit(args: argparse.Namespace) -> int:
    result = run_directory_audit(
        args.target,
        root=args.root,
        max_files=args.max_files,
        max_findings=args.max_findings,
    )
    _print_audit_result(result)
    return 0 if result.status == "OK" else 2


def cmd_locate(args: argparse.Namespace) -> int:
    result = run_locate(
        args.query,
        root=args.root,
        max_results=args.max_results,
        max_files=args.max_files,
    )
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result.data, indent=2, sort_keys=True), encoding="utf-8")
    if args.format == "json":
        print(f"Hydra locate: {result.data['count']} matches")
    else:
        _print_locate_result(result)
    if result.status == "OK":
        return 0
    if result.status == "NO_MATCH":
        return 1
    return 2


def cmd_capability_score(args: argparse.Namespace) -> int:
    # SLICE 2 CUT: hydra.build stripped.
    print("capability-score: build module removed in lean-core build.", file=sys.stderr)
    return 2


def cmd_competitive_score(args: argparse.Namespace) -> int:
    # SLICE 2 CUT: hydra.competitive stripped.
    print("competitive-score: competitive module removed in lean-core build.", file=sys.stderr)
    return 2


def cmd_domain_pack(args: argparse.Namespace) -> int:
    try:
        pack = get_domain_pack(args.pack_id)
    except DomainPackError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(pack, indent=2, sort_keys=True), encoding="utf-8")
    if args.format == "json":
        print(f"Hydra domain pack: {pack['pack_id']}")
    else:
        print(render_domain_pack_text(pack), end="")
    return 0


def cmd_task_eval(args: argparse.Namespace) -> int:
    # SLICE 2 CUT: hydra.task_evals stripped.
    print("task-eval: task_evals module removed in lean-core build.", file=sys.stderr)
    return 2


def cmd_trace_bundle(args: argparse.Namespace) -> int:
    report = build_trace_bundle(Path(args.repo_root), suite_id=args.suite, limit=args.limit)
    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if args.format == "json":
        print(f"Hydra trace bundle: {report['repo_root']}")
    else:
        print(render_trace_bundle_text(report), end="")
    return 0 if report["verifier"]["failed"] == 0 else 1


def cmd_autonomy(args: argparse.Namespace) -> int:
    if args.autonomy_cmd == "classify":
        try:
            arguments = json.loads(args.arguments_json)
        except json.JSONDecodeError as e:
            print(f"autonomy error: invalid JSON arguments: {e}", file=sys.stderr)
            return 2
        if not isinstance(arguments, dict):
            print("autonomy error: arguments must be a JSON object", file=sys.stderr)
            return 2
        decision = classify_tool_call(args.tool_name, arguments, args.policy)
        if args.format == "json":
            print(json.dumps(decision, indent=2, sort_keys=True))
        else:
            print(render_autonomy_text(decision))
        return 0
    return 0

def cmd_ops(args: argparse.Namespace) -> int:
    if args.ops_cmd == "self-check":
        # SLICE 2 CUT: hydra.self_diagnosis stripped.
        print("self-check: self_diagnosis module removed in lean-core build.", file=sys.stderr)
        return 2

    if args.ops_cmd == "recall":
        try:
            report = recall_search(
                args.query,
                repo_root=REPO_ROOT,
                memory_root=args.memory_root,
                evidence_root=args.evidence_root,
                top_k=args.top_k,
            )
        except RecallError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_recall_text(report), end="")
        return 0

    if args.ops_cmd == "judge":
        try:
            draft = Path(args.draft).expanduser().read_text(encoding="utf-8", errors="replace")
            rubric_raw = json.loads(Path(args.rubric).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"ops error: could not load draft/rubric: {e}", file=sys.stderr)
            return 2
        if not isinstance(rubric_raw, list):
            print("ops error: rubric file must be a JSON array of rule objects", file=sys.stderr)
            return 2
        try:
            report = rubric_judge(draft, rubric_raw, pass_threshold=args.threshold)
        except RubricJudgeError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_judge_text(report), end="")
        return 0 if report["verdict"] == "PASS" else 1

    if args.ops_cmd == "context":
        try:
            report = assemble_context(
                repo_root=REPO_ROOT,
                memory_root=args.memory_root,
                evidence_root=args.evidence_root,
                budget_bytes=args.budget_bytes,
                max_lessons=args.max_lessons,
                max_clusters=args.max_clusters,
                max_promotions=args.max_promotions,
            )
        except ContextEngineError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_context_text(report), end="")
        return 0

    if args.ops_cmd == "trace-summary":
        try:
            report = summarize_trace(args.path)
        except TraceSummaryError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_trace_summary_text(report), end="")
        return 0

    # SEAM CUT: discover-live handler removed (hydra.cidr_discover is stripped).

    if args.ops_cmd == "doctor":
        # SLICE 2 CUT: hydra.doctor stripped.
        print("doctor: doctor module removed in lean-core build.", file=sys.stderr)
        return 2

    if args.ops_cmd == "verify-claim":
        try:
            report = verify_claim(
                args.claim,
                args.url,
                args.allowed_host,
                pass_threshold=args.threshold,
            )
        except VerifyClaimError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_verify_text(report), end="")
        return 0 if report["verdict"] == "PASS" else 1

    if args.ops_cmd == "workflow":
        try:
            with open(args.tasks_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ops error: could not load workflow file: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw, list):
            print("ops error: workflow file must be a JSON array of task dicts", file=sys.stderr)
            return 2
        try:
            report = run_workflow(
                raw,
                max_concurrency=args.max_concurrency,
                pass_threshold=args.pass_threshold,
            )
        except WorkflowError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_workflow_text(report), end="")
        return 0 if report["all_pass"] else 1

    if args.ops_cmd == "dispatch":
        try:
            with open(args.tasks_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ops error: could not load tasks file: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw, list):
            print("ops error: tasks file must be a JSON array of task objects", file=sys.stderr)
            return 2
        try:
            tasks = [task_from_dict(row) for row in raw]
            if getattr(args, "graph", False):
                report = dispatch_graph(tasks, max_concurrency=args.max_concurrency)
            else:
                report = dispatch(tasks, max_concurrency=args.max_concurrency)
        except OrchestrateError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        elif getattr(args, "graph", False):
            print(render_graph_text(report), end="")
        else:
            print(render_dispatch_text(report), end="")
        return 0 if report["failed"] == 0 and report["timed_out"] == 0 else 1

    if args.ops_cmd == "env":
        try:
            if args.env_cmd == "create":
                report = create_env_session(
                    source_repo=args.repo,
                    env_root=args.env_root,
                    title=args.title,
                )
            elif args.env_cmd == "status":
                report = session_status(args.env_root, args.session_id)
            elif args.env_cmd == "exec":
                command = list(args.command)
                if command and command[0] == "--":
                    command = command[1:]
                report = run_session_command(
                    args.env_root,
                    args.session_id,
                    command,
                    timeout_seconds=args.timeout,
                )
            elif args.env_cmd == "read":
                report = read_session_file(args.env_root, args.session_id, args.path)
            elif args.env_cmd == "write":
                report = write_session_file(
                    args.env_root,
                    args.session_id,
                    args.path,
                    args.content,
                )
            elif args.env_cmd == "fetch":
                report = fetch_session_url(args.env_root, args.session_id, args.url)
            else:
                raise EnvironmentError(f"unknown env command: {args.env_cmd}")
        except (EnvironmentError, subprocess.TimeoutExpired, OSError) as e:
            print(f"ops env error: {e}", file=sys.stderr)
            return 2
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        elif args.env_cmd == "exec":
            print(report.get("stdout", ""), end="")
            if report.get("stderr"):
                print(report["stderr"], file=sys.stderr, end="")
        elif args.env_cmd == "read":
            print(report["content"], end="")
        elif args.env_cmd == "write":
            print(report["diff"], end="")
        elif args.env_cmd == "fetch":
            print(report["text"], end="")
        else:
            print(f"session_id={report['session_id']}")
            print(f"workspace={report['workspace']}")
            print(f"state={report.get('state', 'unknown')}")
            print(f"command_count={report.get('command_count', 0)}")
        if args.env_cmd == "exec":
            return 0 if report["status"] == "ok" else 1
        return 0

    if args.ops_cmd == "cluster-lessons":
        evidence_root = (
            Path(args.evidence_root).expanduser().resolve()
            if args.evidence_root
            else REPO_ROOT / "evidence"
        )
        try:
            report = promote_clusters_to_lessons(
                evidence_root,
                memory_root=args.memory_root,
                min_count=args.min_count,
                top_n=args.top_n,
            )
        except ClusterLessonError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_cluster_lessons_text(report), end="")
        return 0

    if args.ops_cmd == "clusters":
        evidence_root = (
            Path(args.evidence_root).expanduser().resolve()
            if args.evidence_root
            else REPO_ROOT / "evidence"
        )
        try:
            report = cluster_failures(evidence_root)
        except FailureClusterError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_clusters_text(report), end="")
        return 0

    if args.ops_cmd == "discover":
        try:
            plan = plan_discovery(args.target)
        except NetworkDiscoveryError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(plan, indent=2, sort_keys=True))
        else:
            print(render_discovery_text(plan), end="")
        return 0

    if args.ops_cmd == "repo-audit":
        try:
            report = audit_repo(args.path)
        except RepoRuntimeError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2
        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(render_repo_text(report), end="")
        return 0

    if args.ops_cmd == "audit":
        packs_dir = Path(args.packs_dir).expanduser().resolve() if args.packs_dir else OPS_PACKS_DIR
        evidence_root = (
            Path(args.evidence_root).expanduser().resolve()
            if args.evidence_root
            else REPO_ROOT / "evidence"
        )
        try:
            result = run_ops_audit(
                args.pack,
                args.target,
                evidence_root=evidence_root,
                packs_dir=packs_dir,
            )
        except OpsAuditError as e:
            print(f"ops error: {e}", file=sys.stderr)
            return 2

        if args.out:
            out_path = Path(args.out).expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

        judge_result: dict[str, Any] | None = None
        if getattr(args, "judge_rubric", None):
            try:
                judge_result = judge_audit_summary(
                    result["run_dir"],
                    args.judge_rubric,
                    pass_threshold=args.judge_threshold,
                )
            except OpsAuditJudgeError as e:
                print(f"audit judge error: {e}", file=sys.stderr)
                return 2

        if args.format == "json":
            payload = dict(result)
            if judge_result is not None:
                payload["judge"] = judge_result
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            summary = result["summary"]
            counts = summary["command_outcomes"]
            outcomes_str = (
                ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                if counts
                else "none"
            )
            print(
                f"Hydra ops audit: {summary['pack_id']} -> {summary['target']['type']}"
            )
            print(f"run_id: {result['run_id']}")
            print(f"run_dir: {result['run_dir']}")
            print(f"command outcomes: {outcomes_str}")
            print(f"rot signals matched: {result['rot_signals_matched']}")
            print(f"permission_policy: operator-selected-later")
            if judge_result is not None:
                jr = judge_result["judge_report"]
                print(
                    f"summary judge: {jr['verdict']} score={jr['score']} "
                    f"violations={len(jr['violations'])}"
                )

        if judge_result is not None and args.judge_fail_exit and judge_result["judge_report"]["verdict"] != "PASS":
            return 1
        return 0

    # SEAM CUT: remote-audit handler removed (hydra.remote_runtime_audit is stripped).

    if args.ops_cmd != "plan":
        print(f"ops error: unsupported ops command {args.ops_cmd!r}", file=sys.stderr)
        return 2
    packs_dir = Path(args.packs_dir).expanduser().resolve() if args.packs_dir else OPS_PACKS_DIR
    try:
        pack = load_pack(args.pack, packs_dir=packs_dir)
        target = parse_target(args.target)
        plan = render_plan(pack, target)
    except OpsPackError as e:
        print(f"ops error: {e}", file=sys.stderr)
        return 2

    if args.out:
        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")

    if args.format == "json":
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(f"Hydra ops plan: {plan['pack_id']} -> {plan['target']['type']}")
        print(f"permission_policy: {plan['permission_policy']}")
        print(f"executed: {plan['executed']}")
        for command in plan["commands"]:
            print(f"- {command['id']} [{command['risk_tier']}]: {command['command']}")
    return 0
