"""gateways.tui.__main__ — opens the Hydra chat surface.

`python3 -m gateways.tui` historically executed a standalone `gateways/tui.py`;
that module no longer ships (the chat surface lives behind `hydra chat`).
This shim delegates there so the module path keeps working instead of
crashing on a missing file.
"""
from __future__ import annotations

import sys

from hydra.__main__ import main

if __name__ == "__main__":
    sys.exit(main(["chat", *sys.argv[1:]]))
