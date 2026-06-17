"""Mission and continuation CLI command handlers."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hydra.continuation import (
    ContinuationError,
    render_loop_text as render_continuation_loop_text,
    render_text as render_continuation_text,
    run_continuation,
    run_continuation_loop,
)
from hydra.mission import MissionError, create_mission, load_mission, render_mission_text
from hydra.mission_loop import MissionLoopError, render_mission_loop_text, run_mission_loop


REPO_ROOT = Path(__file__).resolve().parents[2]


def register_mission_commands(sub: argparse._SubParsersAction) -> None:
    mission_p = sub.add_parser("mission", help="manage Hydra missions")
    mission_sub = mission_p.add_subparsers(dest="mission_cmd", required=True)
    mission_start = mission_sub.add_parser("start", help="start a planned mission")
    mission_start.add_argument("operator_prompt")
    mission_start.add_argument("--intent", default="build")
    mission_start.add_argument("--next-action", default="inspect")
    mission_show = mission_sub.add_parser("show", help="show a mission")
    mission_show.add_argument("mission_id")
    mission_run = mission_sub.add_parser("run", help="run the native mission execution loop")
    mission_run.add_argument("operator_prompt")
    mission_run.add_argument("--root", default=None, help="mission workspace root (default: repo root)")
    mission_run.add_argument("--max-concurrency", type=int, default=2)
    mission_run.add_argument("--auto-continue", action="store_true", help="execute the generated continuation plan after mission verification")

    continuation_p = sub.add_parser("continuation", help="execute mission continuation plans")
    continuation_sub = continuation_p.add_subparsers(dest="continuation_cmd", required=True)
    continuation_run = continuation_sub.add_parser("run", help="run the continuation from a mission_loop.json packet")
    continuation_run.add_argument("packet", help="path to a mission_loop.json packet containing continuation")
    continuation_run.add_argument("--root", default=None, help="workspace root for verification commands")
    continuation_run.add_argument("--max-concurrency", type=int, default=2)
    continuation_run.add_argument("--format", choices=("text", "json"), default="text")
    continuation_loop = continuation_sub.add_parser("loop", help="run a bounded continuation cycle loop from a mission_loop.json packet")
    continuation_loop.add_argument("packet", help="path to a mission_loop.json packet containing continuation")
    continuation_loop.add_argument("--root", default=None, help="workspace root for verification commands")
    continuation_loop.add_argument("--cycles", type=int, default=2)
    continuation_loop.add_argument("--max-concurrency", type=int, default=2)
    continuation_loop.add_argument("--format", choices=("text", "json"), default="text")


def cmd_mission(args: argparse.Namespace) -> int:
    try:
        if args.mission_cmd == "start":
            mission = create_mission(
                root=REPO_ROOT,
                operator_prompt=args.operator_prompt,
                intent=args.intent,
                next_action=args.next_action,
            )
            write_current_mission_marker(REPO_ROOT, mission.mission_id)
            print(f"mission_id: {mission.mission_id}")
            print(f"run_dir: {mission.evidence['run_dir']}")
            return 0
        if args.mission_cmd == "show":
            print(render_mission_text(load_mission(REPO_ROOT, args.mission_id)))
            return 0
        if args.mission_cmd == "run":
            root = Path(args.root).expanduser().resolve() if args.root else REPO_ROOT
            report = run_mission_loop(
                root=root,
                operator_prompt=args.operator_prompt,
                max_concurrency=args.max_concurrency,
                auto_continue=args.auto_continue,
            )
            write_current_mission_marker(root, report["mission"]["mission_id"])
            print(render_mission_loop_text(report), end="")
            return 0 if report["summary"]["verdict"] == "GREEN" else 1
    except (MissionError, MissionLoopError, OSError, json.JSONDecodeError) as e:
        print(f"mission error: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_continuation(args: argparse.Namespace) -> int:
    try:
        packet_path = Path(args.packet).expanduser().resolve()
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        continuation = payload.get("continuation")
        if not isinstance(continuation, dict):
            raise ContinuationError("packet does not contain a continuation object")
        root = Path(args.root).expanduser().resolve() if args.root else REPO_ROOT
        if args.continuation_cmd == "loop":
            packet = run_continuation_loop(
                root=root,
                continuation=continuation,
                cycles=args.cycles,
                max_concurrency=args.max_concurrency,
            )
        else:
            packet = run_continuation(
                root=root,
                continuation=continuation,
                max_concurrency=args.max_concurrency,
            )
    except (ContinuationError, OSError, json.JSONDecodeError) as e:
        print(f"continuation error: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(packet, indent=2, sort_keys=True))
    elif args.continuation_cmd == "loop":
        print(render_continuation_loop_text(packet), end="")
    else:
        print(render_continuation_text(packet), end="")
    return 0 if packet["verdict"] == "GREEN" else 1


def write_current_mission_marker(root: Path, mission_id: str) -> None:
    marker = root / ".hydraAgent" / "current_mission"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(mission_id + "\n", encoding="utf-8")
