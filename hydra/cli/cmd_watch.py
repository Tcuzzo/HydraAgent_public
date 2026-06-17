"""hydra watch — run an agent task on a timer and/or when files change.

Thin CLI shell over hydra.watch.WatchLoop: validates args, wires the real
clock / filesystem snapshot / agent run, and prints a per-cycle summary. The
firing logic lives in the (unit-tested) engine; the agent run reuses cmd_ask.

Safety: default approval policy is `deny` (read-only — no bash/fs_write/fs_edit);
pass --yolo (or --approval-policy allow) to let the agent act unattended.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from hydra.cli.cmd_ask import _default_ask_max_iterations, cmd_ask
from hydra.watch import WatchConfig, WatchLoop, parse_duration


class WatchArgsError(Exception):
    """Raised on invalid `hydra watch` argument combinations."""


# ── pure helpers (unit-tested) ──────────────────────────────────────────────


def file_snapshot(paths) -> dict[str, float]:
    """Map every existing file under `paths` to its mtime (dirs walked recursively)."""
    snap: dict[str, float] = {}
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_file():
                snap[str(p)] = p.stat().st_mtime
            elif p.is_dir():
                for f in sorted(p.rglob("*")):
                    if f.is_file():
                        snap[str(f)] = f.stat().st_mtime
        except OSError:
            continue
    return snap


def resolve_task(prompt: str | None, task_file) -> str:
    """The task text for a cycle: a fresh read of task_file, else the inline prompt."""
    if task_file is not None:
        return Path(task_file).read_text(encoding="utf-8").strip()
    return (prompt or "").strip()


def resolve_watch_policy(yolo: bool, approval_policy: str | None) -> str:
    """Read-only by default; --yolo means allow; an explicit policy passes through."""
    if yolo and approval_policy:
        raise WatchArgsError("use either --yolo or --approval-policy, not both")
    if yolo:
        return "allow"
    return approval_policy or "deny"


def validate_task(prompt, task_file) -> None:
    if bool(prompt) == bool(task_file):
        raise WatchArgsError("provide exactly one of: a prompt, or --task-file")


def validate_triggers(every, watch) -> None:
    if not every and not watch:
        raise WatchArgsError("provide at least one trigger: --every and/or --watch")


# ── CLI ─────────────────────────────────────────────────────────────────────


def register_watch_command(sub) -> None:
    p = sub.add_parser(
        "watch",
        help="run an agent task on a timer and/or when files change (read-only unless --yolo)",
    )
    p.add_argument("prompt", nargs="?", default=None, help="task to run each cycle (or use --task-file)")
    p.add_argument("--task-file", default=None, help="read the task from this file fresh each cycle")
    p.add_argument("--every", default=None, help="run on a timer, e.g. 30s, 10m, 2h")
    p.add_argument("--watch", action="append", default=[], metavar="PATH",
                   help="watch a file or directory; repeatable; runs a cycle on change")
    p.add_argument("--poll", type=float, default=2.0, help="seconds between checks (default: 2)")
    p.add_argument("--debounce", type=float, default=1.0,
                   help="quiet seconds after a change before running (default: 1)")
    p.add_argument("--max-cycles", type=int, default=None, help="stop after N cycles (default: unlimited)")
    p.add_argument("--stop-file", default=None, help="stop cleanly when this file appears")
    p.add_argument("--yolo", action="store_true",
                   help="let the agent ACT (bash/fs_write/fs_edit); default is read-only")
    p.add_argument("--approval-policy", choices=("allow", "ask", "deny"), default=None,
                   help="explicit policy (mutually exclusive with --yolo; default: deny = read-only)")
    p.add_argument("--provider", default=None, help="model provider override")
    p.add_argument("--model", default=None, help="model override")
    p.add_argument("--root", default=None, help="filesystem scope (default: current directory)")
    p.add_argument("--max-iterations", type=int, default=None, help="agent loop cap per cycle")
    p.add_argument("--timeout", type=float, default=120.0)


def _ask_namespace(task: str, policy: str, args) -> argparse.Namespace:
    """A complete cmd_ask namespace for one cycle (mirrors cmd_ask's arg defaults)."""
    return argparse.Namespace(
        prompt=task,
        provider=args.provider,
        model=args.model,
        root=args.root,
        approval_policy=policy,
        max_iterations=(
            args.max_iterations if args.max_iterations is not None else _default_ask_max_iterations()
        ),
        timeout=args.timeout,
        context_budget_bytes=4096,
        memory_root=str(Path.home() / ".hydra-memory"),
        judge_rubric=None,
        judge_threshold=1.0,
        judge_out=None,
        judge_fail_exit=False,
        trace_out=None,
    )


def cmd_watch(args: argparse.Namespace) -> int:
    try:
        validate_task(getattr(args, "prompt", None), getattr(args, "task_file", None))
        validate_triggers(args.every, args.watch)
        policy = resolve_watch_policy(args.yolo, args.approval_policy)
        interval = parse_duration(args.every) if args.every else None
    except (WatchArgsError, ValueError) as exc:
        print(f"hydra watch: {exc}", file=sys.stderr)
        return 2

    watch_paths = tuple(Path(p) for p in (args.watch or []))
    stop_file = Path(args.stop_file) if args.stop_file else None
    config = WatchConfig(
        interval_seconds=interval,
        watch_paths=watch_paths,
        poll_seconds=args.poll,
        debounce_seconds=args.debounce,
        max_cycles=args.max_cycles,
        stop_file=stop_file,
    )

    def stop_check() -> bool:
        return stop_file is not None and stop_file.exists()

    def run_cycle(reason: str) -> None:
        task = resolve_task(getattr(args, "prompt", None), getattr(args, "task_file", None))
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n── hydra watch · cycle ({reason}) · {stamp} ──", flush=True)
        try:
            cmd_ask(_ask_namespace(task, policy, args))
        except Exception as exc:  # one bad cycle must not kill the watcher
            print(f"hydra watch: cycle error: {exc}", file=sys.stderr, flush=True)

    triggers = []
    if interval is not None:
        triggers.append(f"every {args.every}")
    if watch_paths:
        triggers.append("on change in " + ", ".join(str(p) for p in watch_paths))
    mode = "read-only" if policy == "deny" else f"policy={policy}"
    print(
        f"hydra watch: {' + '.join(triggers)} · {mode} · Ctrl-C to stop"
        + (f" · stop-file {stop_file}" if stop_file else ""),
        flush=True,
    )

    loop = WatchLoop(
        config,
        clock=time.monotonic,
        sleep=time.sleep,
        snapshot=file_snapshot,
        run_cycle=run_cycle,
        stop_check=stop_check,
    )
    cycles = loop.run()
    print(f"\nhydra watch: stopped after {cycles} cycle(s)", flush=True)
    return 0
