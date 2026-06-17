"""Regression test: gateways.tui.hydra_app must import cleanly on textual 8.2.7+.

RED before fix (ImportError: cannot import name 'MouseWheel' from 'textual.events').
GREEN after the textual-8.2.7-compatible rewrite.
"""


def test_hydra_app_module_imports():
    """The TUI module must load without error on any supported textual version."""
    import gateways.tui.hydra_app  # noqa: F401
