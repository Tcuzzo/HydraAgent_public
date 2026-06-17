"""hydra/cli/cmd_undo.py — 'hydra undo' CLI command.

Restores the most-recent N file snapshots recorded by hydra.edit_checkpoints.

Usage
-----
  hydra undo                   restore the last 1 edit
  hydra undo -n 3              restore the last 3 edits
  hydra undo --list            show the checkpoint stack without restoring

IMPORTANT: undo reverts FILES only — it cannot undo external side effects such
as network calls, database writes, or shell commands executed during the edit
session.  This limitation is the Claude Code safe-edit contract.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def register_undo_command(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "undo",
        help="restore the most-recent file edit snapshots (files only — no external side effects)",
    )
    p.add_argument(
        "-n",
        type=int,
        default=1,
        metavar="N",
        help="number of edits to restore (default: 1)",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="show the checkpoint stack without restoring anything",
    )
    p.add_argument(
        "--root",
        default=str(REPO_ROOT),
        help="repo root whose checkpoint stack to use (default: HydraAgent repo root)",
    )


def cmd_undo(args: argparse.Namespace) -> int:
    from hydra.edit_checkpoints import CheckpointStore, undo, list_stack

    root = Path(args.root).expanduser().resolve()
    store = CheckpointStore(root)

    if args.list:
        entries = list_stack(store)
        if not entries:
            print("Checkpoint stack is empty.")
            return 0
        print(f"Checkpoint stack ({len(entries)} entries, oldest first):")
        for e in entries:
            op_label = "create" if e["op"] == "create" else "modify"
            size_kb = e["pre_image_size"] / 1024
            print(f"  seq={e['seq']:6d}  op={op_label:6s}  size={size_kb:7.1f}KB  path={e['path']}")
        print()
        print(
            "NOTE: undo reverts files only — external side effects "
            "(network, db, shell) are NOT reversed."
        )
        return 0

    n = max(1, args.n)
    msg = undo(store, n=n)
    print(msg)
    # Return 0 if something was restored; 1 if the stack was empty / nothing done
    if "nothing" in msg.lower() and "restored" not in msg.lower():
        return 1
    return 0
