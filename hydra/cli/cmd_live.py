"""hydra live — web search with citations. Does not call a paid model."""
from __future__ import annotations

import argparse
import json

from hydra.live_research import LiveResearchError, research


def register_live_command(sub) -> None:
    parser = sub.add_parser(
        "live",
        help="search the web and print citations (no paid model call)",
    )
    parser.add_argument("query", nargs="+", help="what to look up")
    parser.add_argument("--json", action="store_true", help="print the citation packet as JSON")
    parser.add_argument("--no-fetch", action="store_true", help="print search hits without fetching pages")
    parser.add_argument("--limit", type=int, default=5, help="maximum sources (default 5)")


def cmd_live(args: argparse.Namespace) -> int:
    query = " ".join(getattr(args, "query", []) or []).strip()
    try:
        packet = research(
            query,
            limit=getattr(args, "limit", 5) or 5,
            no_fetch=bool(getattr(args, "no_fetch", False)),
        )
    except LiveResearchError as exc:
        print(f"hydra live: {exc}")
        return 1
    if getattr(args, "json", False):
        print(json.dumps(packet, indent=2, ensure_ascii=False))
    else:
        print(f"Query: {packet['query']}")
        print(packet["reasoning_context"])
    return 0
