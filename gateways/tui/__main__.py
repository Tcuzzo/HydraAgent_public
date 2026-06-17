"""gateways.tui.__main__ — delegates to the original tui.py CLI.

Allows `python3 -m gateways.tui <command>` to keep working now that
gateways/tui/ is a package (which shadows gateways/tui.py for imports
but requires __main__.py for -m execution).
"""
from __future__ import annotations

import sys
import runpy
from pathlib import Path

_TUI_PY = Path(__file__).resolve().parents[1] / "tui.py"

# Execute the original tui.py as __main__ so its argparse and sys.exit work correctly.
runpy.run_path(str(_TUI_PY), run_name="__main__")
