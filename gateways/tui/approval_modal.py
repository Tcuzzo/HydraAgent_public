"""Textual approval card. Keyboard: y / n / a. Mouse: the three buttons."""
from __future__ import annotations

from textual.app import ComposeResult
from textual.markup import escape
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ApprovalModal(ModalScreen[str]):
    DEFAULT_CSS = """
    ApprovalModal { align: center middle; }
    #approval-card {
        width: 80%;
        height: auto;
        padding: 1 2;
        border: solid #ff2d95;
        background: #140818;
    }
    #approval-actions { height: auto; }
    """
    BINDINGS = [
        ("y", "choose('y')", "Yes"),
        ("n", "choose('n')", "No"),
        ("a", "choose('always')", "Always"),
        ("escape", "choose('n')", "No"),
    ]

    def __init__(self, summary: str) -> None:
        super().__init__()
        self.summary = summary

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-card"):
            # Tool text is shown as typed: brackets in a command are not markup.
            yield Static(f"Approve {self.summary}?", markup=False)
            with Horizontal(id="approval-actions"):
                yield Button(escape("Yes [y]"), id="yes")
                yield Button(escape("No [n]"), id="no")
                yield Button(escape("Always [a]"), id="always")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss({"yes": "y", "no": "n", "always": "always"}.get(event.button.id or "", "n"))

    def action_choose(self, value: str) -> None:
        self.dismiss(value)
