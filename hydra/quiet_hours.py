"""hydra.quiet_hours — respect the operator's rest.

Operator rule (2026-06-02): 1:00am to 6:00am is quiet time. The agent does NOT
ping, chime, or chatter at the operator during quiet hours unless it is a real
emergency. A non-emergency that wants attention is HELD until quiet hours end (or
the agent asks first). A genuine emergency DOES break through and keeps chiming
until the operator answers.

This is a notification-layer policy: it decides whether an outbound, operator-
facing notification may go out right now. It never blocks the operator, and it
never blocks an emergency.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

QUIET_START = time(1, 0)   # 01:00 inclusive
QUIET_END = time(6, 0)     # 06:00 exclusive

# Actions a notification can take.
SEND = "send"      # go now, normal
HOLD = "hold"      # quiet hours, non-emergency: defer until QUIET_END (or ask first)
CHIME = "chime"    # quiet hours, emergency: send AND keep chiming until acknowledged


def is_quiet_hours(now: datetime | None = None) -> bool:
    """True if `now` (default: current local time) is within 1am-6am quiet time."""
    t = (now or datetime.now()).time()
    return QUIET_START <= t < QUIET_END


@dataclass
class NotificationDecision:
    action: str            # SEND | HOLD | CHIME
    quiet: bool
    rechime: bool          # keep chiming until acknowledged (emergencies only)
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"action": self.action, "quiet": self.quiet, "rechime": self.rechime, "reason": self.reason}


def classify_notification(*, is_emergency: bool, now: datetime | None = None) -> NotificationDecision:
    """Decide what an operator-facing notification may do right now.

    - Not quiet hours -> SEND.
    - Quiet hours + emergency -> CHIME (send and re-chime until the operator answers).
    - Quiet hours + non-emergency -> HOLD (wait for 6am, or the agent asks first).
    """
    quiet = is_quiet_hours(now)
    if not quiet:
        return NotificationDecision(SEND, quiet=False, rechime=False, reason="not quiet hours")
    if is_emergency:
        return NotificationDecision(
            CHIME, quiet=True, rechime=True,
            reason="emergency during quiet hours: breaks through and chimes until answered",
        )
    return NotificationDecision(
        HOLD, quiet=True, rechime=False,
        reason="quiet hours (1am-6am): held until 6am, no chatter; ask the operator if it can't wait",
    )
