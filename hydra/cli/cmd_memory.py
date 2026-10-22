"""Memory-adjacent CLI command handlers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hydra.capability_cards import CapabilityCardError, load_cards, render_card_list, render_route
from hydra.capability_truth import capability_truth_report, render_text as render_capability_truth_text, to_json as capability_truth_json
from hydra.lessons import LessonError, remember_lesson
from hydra.local_memory import (
    DEFAULT_MAX_CHARS as LOCAL_MEMORY_MAX_CHARS,
    DEFAULT_MEMORY_ROOT,
    build_local_memory_context,
)
from hydra.memory_kernel import (
    assemble_memory_briefing,
    assemble_truth_context,
    build_memory_index,
    truth_memory_quality_report,
)
from hydra.source_reengineering import (
    SourceReengineeringError,
    list_programs,
    load_program,
    render_program_text,
)
from hydra.wiki_memory import WikiMemoryError, render_wiki_index, scaffold_wiki


REPO_ROOT = Path(__file__).resolve().parents[2]


def register_memory_commands(sub: argparse._SubParsersAction) -> None:
    source_p = sub.add_parser("source", help="manage source re-engineering programs")
    source_sub = source_p.add_subparsers(dest="source_cmd", required=True)
    source_sub.add_parser("list", help="list source re-engineering programs")
    source_show = source_sub.add_parser("show")
    source_show.add_argument("system_id")

    wiki_p = sub.add_parser("wiki", help="manage Hydra wiki memory")
    wiki_sub = wiki_p.add_subparsers(dest="wiki_cmd", required=True)
    wiki_sub.add_parser("init", help="initialize the deterministic wiki scaffold")
    wiki_sub.add_parser("index", help="render and rewrite the deterministic wiki index")

    capabilities_p = sub.add_parser("capabilities", help="manage native capability cards")
    capabilities_sub = capabilities_p.add_subparsers(dest="capabilities_cmd", required=True)
    capabilities_sub.add_parser("list", help="list native capability cards")
    capabilities_route = capabilities_sub.add_parser("route", help="route a prompt to native capability cards")
    capabilities_route.add_argument("prompt")
    capabilities_truth = capabilities_sub.add_parser("truth", help="prove visible capability claims")
    capabilities_truth.add_argument("--claimed-production-count", type=int, default=1200)
    capabilities_truth.add_argument("--format", choices=("text", "json"), default="text")

    p_memory = sub.add_parser(
        "local-memory",
        help="import local durable memory context",
        description=(
            "Read bounded durable-memory artifacts from a local tree, skip "
            "credential lanes, and print the context/evidence Hydra chat uses."
        ),
    )
    p_memory.add_argument(
        "root",
        nargs="?",
        default=str(DEFAULT_MEMORY_ROOT),
        help=f"local memory root (default: {DEFAULT_MEMORY_ROOT})",
    )
    p_memory.add_argument("--max-chars", type=int, default=LOCAL_MEMORY_MAX_CHARS)
    p_memory.add_argument("--truth", action="store_true", help="use the provenance-backed truth memory kernel")
    p_memory.add_argument("--quality", action="store_true", help="run the truth memory quality gate")
    p_memory.add_argument("--brief", action="store_true", help="emit an operator memory briefing packet")
    p_memory.add_argument("--query", default="", help="query for truth-context assembly")
    p_memory.add_argument("--out", default=None, help="write truth query packet JSON to this path")
    p_memory.add_argument("--require-records", action="store_true", help="exit 1 when a truth query selects no records")
    p_memory.add_argument(
        "--context-only",
        action="store_true",
        help="print only the system-context block",
    )

    p_remember = sub.add_parser(
        "remember",
        help="append a sourced durable lesson to local memory",
        description=(
            "Write a redacted, sourced lesson into workspace/memory/"
            "hydra-lessons.md under the selected memory root."
        ),
    )
    p_remember.add_argument("lesson")
    p_remember.add_argument("--source", required=True, help="evidence source for the lesson")
    p_remember.add_argument("--memory-root", default=str(DEFAULT_MEMORY_ROOT))
    p_remember.add_argument("--tag", action="append", default=[], help="lesson tag; may be passed more than once")
    p_remember.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="operator-readable text or JSON report",
    )


def cmd_source(args: argparse.Namespace) -> int:
    if args.source_cmd == "list":
        try:
            for program in list_programs(REPO_ROOT):
                print(f"{program.system_id}\t{len(program.capabilities)} capabilities\t{len(program.sources)} sources")
            return 0
        except (SourceReengineeringError, OSError, json.JSONDecodeError) as e:
            print(f"source error: {e}", file=sys.stderr)
            return 2
    if args.source_cmd == "show":
        try:
            print(render_program_text(load_program(REPO_ROOT, args.system_id)))
            return 0
        except (SourceReengineeringError, OSError, json.JSONDecodeError) as e:
            print(f"source error: {e}", file=sys.stderr)
            return 2
    return 0


def cmd_capabilities(args: argparse.Namespace) -> int:
    if args.capabilities_cmd == "truth":
        report = capability_truth_report(
            REPO_ROOT,
        )
        if args.format == "json":
            print(capability_truth_json(report))
        else:
            print(render_capability_truth_text(report), end="")
        return 0 if report["status"] == "PROVEN" else 1
    try:
        cards = load_cards(REPO_ROOT / ".hydraAgent/capabilities")
        if args.capabilities_cmd == "list":
            print(render_card_list(cards))
            return 0
        if args.capabilities_cmd == "route":
            print(render_route(args.prompt, cards))
            return 0
    except CapabilityCardError as e:
        print(f"capabilities error: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_wiki(args: argparse.Namespace) -> int:
    try:
        if args.wiki_cmd == "init":
            wiki_root = scaffold_wiki(REPO_ROOT)
            print(f"wiki_root: {wiki_root.relative_to(REPO_ROOT).as_posix()}")
            return 0
        if args.wiki_cmd == "index":
            scaffold_wiki(REPO_ROOT)
            text = render_wiki_index(REPO_ROOT)
            (REPO_ROOT / ".hydraAgent/wiki/index.md").write_text(text, encoding="utf-8")
            print(text, end="")
            return 0
    except (WikiMemoryError, OSError) as e:
        print(f"wiki error: {e}", file=sys.stderr)
        return 2
    return 0


def _print_local_memory_result(result, *, context_only: bool = False) -> None:
    if context_only:
        print(result.context)
        return
    print(result.report, end="")
    if result.context:
        print("context:")
        print(result.context)
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def cmd_local_memory(args: argparse.Namespace) -> int:
    if getattr(args, "truth", False):
        try:
            if getattr(args, "quality", False):
                report = truth_memory_quality_report(
                    repo_root=REPO_ROOT,
                    memory_root=Path(args.root),
                )
                print(json.dumps(report, indent=2, sort_keys=True))
                return 0 if report["verdict"] == "GREEN" else 1
            if getattr(args, "brief", False):
                briefing = assemble_memory_briefing(
                    args.query or "hydra mission memory",
                    repo_root=REPO_ROOT,
                    memory_root=Path(args.root),
                    budget_chars=args.max_chars,
                )
                print(json.dumps(briefing, indent=2, sort_keys=True))
                return 0 if not briefing["gaps"] else 1
            if args.query:
                context = assemble_truth_context(
                    args.query,
                    repo_root=REPO_ROOT,
                    memory_root=Path(args.root),
                    budget_chars=args.max_chars,
                )
                if getattr(args, "out", None):
                    out_path = Path(args.out).expanduser().resolve()
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(
                        json.dumps(context, indent=2, sort_keys=True),
                        encoding="utf-8",
                    )
                    print(f"truth context packet: {out_path}", file=sys.stderr)
                if getattr(args, "require_records", False) and not context["records"]:
                    print("no truth memory records selected", file=sys.stderr)
                    return 1
                if args.context_only:
                    print(context["text"], end="")
                else:
                    print(json.dumps(context, indent=2, sort_keys=True))
            else:
                index = build_memory_index(repo_root=REPO_ROOT, memory_root=Path(args.root))
                print(json.dumps(index, indent=2, sort_keys=True))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"memory error: {e}", file=sys.stderr)
            return 2
        return 0
    result = build_local_memory_context(
        args.root,
        max_chars=args.max_chars,
    )
    _print_local_memory_result(result, context_only=args.context_only)
    return 0 if result.status == "OK" else 2


def cmd_remember(args: argparse.Namespace) -> int:
    try:
        report = remember_lesson(
            args.lesson,
            source=args.source,
            tags=args.tag,
            memory_root=args.memory_root,
        )
    except LessonError as e:
        print(str(e), file=sys.stderr)
        return 2
    if args.format == "json":
        print("Hydra remembered:")
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Hydra remembered: {report['path']}")
        print(f"source: {report['source']}")
        print(f"tags: {', '.join(report['tags']) if report['tags'] else 'untagged'}")
    return 0
