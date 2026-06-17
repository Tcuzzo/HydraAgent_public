"""gateways.telegram.gate — the one authority gate.

The doctrine (PRINCIPLES.md §3): authority-class actions (service
restart, credential use, destructive ops, external posting) require a
human button press; everything else is "act, don't ask."

The gate's *decision logic* lives here. The wire-level Telegram client
is abstracted behind the `Transport` protocol. In production: a real
bot using callback queries. In tests: `FakeTransport`. The codebase
contains zero live credentials.

Outcomes:
  Decision.APPROVED  — operator pressed APPROVE
  Decision.REJECTED  — operator pressed REJECT
  Decision.TIMEOUT   — no response within budget
  Decision.ERROR     — transport failure

Fail-closed: only APPROVED permits the action. TIMEOUT and ERROR are
treated as a refusal — this matches the rule of thumb in operations
work: "no answer is a NO."
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Decision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ActionRequest:
    action: str            # one-line plain-English description
    detail: str = ""       # optional multi-line context (operator sees it)
    timeout_s: float = 60  # how long to wait for the button press


class Transport(Protocol):
    """Wire-level callback. `prompt` sends the operator a message with
    APPROVE / REJECT inline buttons and blocks until either a button is
    pressed or `timeout_s` elapses. Returns a Decision."""

    def prompt(self, request: ActionRequest) -> Decision: ...


@dataclass
class Gate:
    transport: Transport

    def authorize(self, request: ActionRequest) -> tuple[bool, Decision, str]:
        """Run a request through the transport. Returns
        (permitted, decision, reason)."""
        try:
            decision = self.transport.prompt(request)
        except Exception as e:  # noqa: BLE001
            return (False, Decision.ERROR, f"transport error: {e}")
        if decision == Decision.APPROVED:
            return (True, decision, "operator approved")
        if decision == Decision.REJECTED:
            return (False, decision, "operator rejected")
        if decision == Decision.TIMEOUT:
            return (False, decision, f"no button within {request.timeout_s}s")
        return (False, Decision.ERROR, f"unknown decision {decision!r}")
