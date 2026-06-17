"""Chat-adjacent CLI command handlers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hydra.cli.tool_binding import bind_tools, root_arg
from hydra.skill_library_audit import audit_skill_library, render_text as render_skill_audit_text, to_json as skill_audit_json
from hydra.skill_library_materializer import (
    discover_catalog_entries,
    materialize_skill_library,
    render_text as render_skill_materializer_text,
    to_json as skill_materializer_json,
)
from hydra.skill_library_search import search_skill_library
from hydra.skill_spine import (
    build_routed_skill_context,
    build_skill_doctrine,
    find_skill,
    list_skill_records,
    render_doctrine_json,
    render_route_json,
    render_skill,
    render_skill_doctor,
    render_skill_list,
    skill_doctor_report,
)


def register_chat_support_commands(sub: argparse._SubParsersAction) -> None:
    p_tools = sub.add_parser("tools", help="list the default tool set")
    p_tools.add_argument("--root", default=None)
    p_tools.add_argument(
        "--approval-policy",
        choices=("allow", "ask", "deny"),
        default="ask",
        help="show tools as bound under this approval policy",
    )

    p_skills = sub.add_parser("skills", help="inspect trusted local Hydra skill playbooks")
    p_skills.add_argument(
        "--skills-root",
        default=None,
        help=(
            "trusted local skills root; default scans HYDRA_SKILLS_ROOT when set, "
            "otherwise ~/.codex/superpowers/skills plus repo skills/"
        ),
    )
    skills_sub = p_skills.add_subparsers(dest="skills_cmd", required=True)
    p_skills_list = skills_sub.add_parser("list", help="list trusted local skills")
    p_skills_list.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_show = skills_sub.add_parser("show", help="show one trusted local skill summary")
    p_skills_show.add_argument("name")
    p_skills_show.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_doctrine = skills_sub.add_parser("doctrine", help="print Hydra's compact default skill doctrine")
    p_skills_doctrine.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_route = skills_sub.add_parser("route", help="route a prompt to trusted local skills")
    p_skills_route.add_argument("prompt")
    p_skills_route.add_argument(
        "--skills-root",
        default=argparse.SUPPRESS,
        help="trusted local skills root (also accepted after the route subcommand)",
    )
    p_skills_route.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_search = skills_sub.add_parser("search", help="search materialized Hydra SKILL.md docs")
    p_skills_search.add_argument("query")
    p_skills_search.add_argument("--library-root", default="hydra/schemes")
    p_skills_search.add_argument("--limit", type=int, default=8)
    p_skills_search.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_audit = skills_sub.add_parser("audit", help="audit skill library truth claims")
    p_skills_audit.add_argument(
        "--skills-root",
        default=argparse.SUPPRESS,
        help="skill catalog root (also accepted after the audit subcommand)",
    )
    p_skills_audit.add_argument("--runtime-skills-root", default="skills")
    p_skills_audit.add_argument("--claimed-production-count", type=int, default=None)
    p_skills_audit.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_materialize = skills_sub.add_parser(
        "materialize",
        help="materialize scaffolded bundle catalog entries into concrete SKILL.md docs",
    )
    p_skills_materialize.add_argument("--bundles-root", default="hydra/schemes/bundles")
    p_skills_materialize.add_argument("--output-root", default="hydra/schemes/generated")
    p_skills_materialize.add_argument("--min-count", type=int, default=1200)
    p_skills_materialize.add_argument("--dry-run", action="store_true")
    p_skills_materialize.add_argument("--format", choices=("text", "json"), default="text")
    p_skills_doctor = skills_sub.add_parser("doctor", help="check trusted skill spine safety state")
    p_skills_doctor.add_argument("--format", choices=("text", "json"), default="text")


def cmd_tools(args: argparse.Namespace) -> int:
    root = root_arg(args.root)
    tools = bind_tools(root, approval_policy=args.approval_policy)
    print("Default tool set wired into `hydra ask`:")
    for tool in tools:
        params = ", ".join(tool.parameters.get("required", []))
        print(f"  {tool.name}({params}) — {tool.description}")
    return 0


def print_audit_result(result) -> None:
    print(result.report, end="")
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def print_locate_result(result) -> None:
    print(result.report, end="")
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def best_locate_path(result) -> str | None:
    best = result.data.get("best_match")
    if not best:
        return None
    return str((Path(result.data["root"]) / best["path"]).resolve(strict=False))


def print_local_memory_result(result, *, context_only: bool = False) -> None:
    if context_only:
        print(result.context)
        return
    print(result.report, end="")
    if result.context:
        print("context:")
        print(result.context)
    print("structured data:")
    print(json.dumps(result.data, indent=2, sort_keys=True))


def cmd_skills(args: argparse.Namespace) -> int:
    if args.skills_cmd == "list":
        records = list_skill_records(args.skills_root)
        if args.format == "json":
            print(
                json.dumps(
                    {
                        "schema": "hydra.skills.list.v1",
                        "records": [record.to_dict() for record in records],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(render_skill_list(records))
        return 0
    if args.skills_cmd == "show":
        try:
            record = find_skill(args.name, args.skills_root)
        except KeyError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.format == "json":
            print(
                json.dumps(
                    {"schema": "hydra.skills.show.v1", "record": record.to_dict()},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(render_skill(record))
        return 0
    if args.skills_cmd == "doctrine":
        if args.format == "json":
            print(render_doctrine_json(args.skills_root))
        else:
            print(build_skill_doctrine(args.skills_root))
        return 0
    if args.skills_cmd == "route":
        context = build_routed_skill_context(args.prompt, args.skills_root)
        if args.format == "json":
            print(render_route_json(args.prompt, args.skills_root))
        else:
            print(context or "Hydra routed skill context\nNo trusted skill route matched.")
        return 0
    if args.skills_cmd == "search":
        repo_root = Path.cwd()
        report = search_skill_library(
            query=args.query,
            root=repo_root,
            skills_root=args.library_root,
            limit=args.limit,
        )
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"Hydra skill library search: {report['query']}")
            print(f"scanned: {report['total_scanned']} returned: {report['returned']}")
            for hit in report["hits"]:
                print(f"- {hit['skill_id']} [{hit['bundle']}] {hit['path']}")
                if hit["description"]:
                    print(f"  {hit['description']}")
        return 0
    if args.skills_cmd == "audit":
        repo_root = Path.cwd()
        report = audit_skill_library(
            repo_root=repo_root,
            catalog_root=repo_root / args.skills_root if args.skills_root else repo_root / "hydra" / "skills",
            runtime_root=repo_root / args.runtime_skills_root,
            claimed_production_count=args.claimed_production_count,
        )
        if args.format == "json":
            print(skill_audit_json(report))
        else:
            print(render_skill_audit_text(report), end="")
        return 0
    if args.skills_cmd == "materialize":
        repo_root = Path.cwd()
        entries = discover_catalog_entries(repo_root / args.bundles_root)
        report = materialize_skill_library(
            entries,
            output_root=repo_root / args.output_root,
            min_count=args.min_count,
            dry_run=args.dry_run,
        )
        if not report["meets_min_count"]:
            print(
                f"skills error: catalog has {report['entries_total']} entries, below min-count {args.min_count}",
                file=sys.stderr,
            )
            return 1
        if args.format == "json":
            print(skill_materializer_json(report))
        else:
            print(render_skill_materializer_text(report), end="")
        return 0
    if args.skills_cmd == "doctor":
        if args.format == "json":
            print(json.dumps(skill_doctor_report(args.skills_root), indent=2, sort_keys=True))
        else:
            print(render_skill_doctor(args.skills_root))
        return 0
    print(f"skills error: unsupported command {args.skills_cmd!r}", file=sys.stderr)
    return 2
