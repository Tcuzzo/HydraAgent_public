"""gateways.telegram.fakes — deterministic transports for tests.

Scripted FakeTransport returns the next Decision from a list; perfect
for proving the gate's decision logic without touching the wire.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from gateways.telegram.gate import ActionRequest, Decision


@dataclass
class FakeTransport:
    decisions: list[Decision]
    raise_on_call: Exception | None = None
    prompted: list[ActionRequest] = field(default_factory=list)
    _idx: int = 0

    def prompt(self, request: ActionRequest) -> Decision:
        self.prompted.append(request)
        if self.raise_on_call is not None:
            raise self.raise_on_call
        if self._idx >= len(self.decisions):
            return Decision.TIMEOUT
        d = self.decisions[self._idx]
        self._idx += 1
        return d
