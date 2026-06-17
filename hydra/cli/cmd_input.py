"""SLICE 2 CUT: cmd_input was purely a loop_runtime wrapper. loop_runtime has been
stripped in the lean-core build. This module is a stub so existing imports don't break."""
from __future__ import annotations

import argparse


def register_input_command(sub: argparse._SubParsersAction) -> None:
    # Command removed — loop_runtime stripped.
    pass


def cmd_input(args: argparse.Namespace) -> int:
    print("input: loop_runtime removed in lean-core build.", flush=True)
    return 2
