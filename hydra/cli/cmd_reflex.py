"""hydra reflex-bench — stub loop-count receipt for the reflex tier."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from hydra.reflex_bench import run_bench


def register_reflex_commands(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "reflex-bench",
        help="replay scripted tasks and count stub big-model loops",
    )
    parser.add_argument("--out", type=Path, help="write the JSON receipt to this path")


def cmd_reflex_bench(args: argparse.Namespace) -> int:
    receipt = run_bench(out=getattr(args, "out", None))
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0
