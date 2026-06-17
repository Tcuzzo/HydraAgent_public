"""Mission-level approval classifier (S1).

Classifies a mission into the THREE operator-gated classes (or 'normal').
This module is the pure decision core; the side effect (queueing a Telegram
approval) reuses the SINGLE existing approval path in ``hydra.policy`` /
``gateways.telegram`` — there is no second gate.

Operator doctrine — ping Telegram only for:
  1. dangerous missions (real)
  2. destructive OR outside-the-LAN missions
  3. huge multiturn collaborative runtime build / builds (software/security)
Everything else is 'normal' and proceeds without asking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Mission-class vocabulary (S0 decision #4: namespaced, distinct from tool T0..T3).
DANGEROUS = "dangerous"
DESTRUCTIVE_OR_OFF_LAN = "destructive_or_off_lan"
HUGE_COLLAB_BUILD = "huge_batch"
NORMAL = "normal"

# Severity order — first match wins.
_ORDER = (DANGEROUS, DESTRUCTIVE_OR_OFF_LAN, HUGE_COLLAB_BUILD, NORMAL)

_DANGEROUS_KEYWORDS = (
    "rm -rf", "wipe", "exfiltrat", "ddos", "exploit", "malware",
    "destroy everything", "format disk", "kill -9 -1", "drop all databases",
)
_DESTRUCTIVE_KEYWORDS = (
    "delete", "drop table", "drop all", "overwrite", "force push", "force-push",
    "truncate", "rm ", "reset --hard", "format",
)
# Policy: ssh/remote-host heuristics ('ssh to', 'remote host',
# 'send to remote') were an undeclared gate that force-classified an ssh
# mission as DESTRUCTIVE_OR_OFF_LAN in ALL modes — even on the operator's own
# LAN. They are removed (bug #4). What remains are genuine off-LAN/public
# deployment signals (prod/public-internet/external-host/deploy/push-to-github),
# plus the explicit off_lan caller flag below, which is a separate legitimate
# control and is unaffected.
_OFF_LAN_KEYWORDS = (
    "production", "prod", "public internet", "external host", "outside the lan",
    "push to github", "deploy to",
)
_COLLAB_KEYWORDS = (
    "swarm", "multi-agent", "multi agent", "collaborative", "all three",
    "fabric", "cross-machine", "every worktree", "multi-agent", "peer agents",
)
_BUILD_KEYWORDS = (
    "build", "runtime build", "security protocol", "build the runtime",
    "huge build", "multiturn build", "runtime build", "build the security",
)


@dataclass(frozen=True)
class MissionGate:
    mission_class: str
    gated: bool
    reason: str


def _any(text: str, needles: tuple[str, ...]) -> str | None:
    """Word-boundary match so 'swarm' never trips 'rm ' and 'information'
    never trips 'format'. Letters on either side of a needle reject the match."""
    for n in needles:
        if re.search(r"(?<![a-z])" + re.escape(n) + r"(?![a-z])", text):
            return n
    return None


def classify_mission(
    text: str,
    *,
    dangerous: bool = False,
    destructive: bool = False,
    off_lan: bool = False,
    collaborative_build: bool = False,
) -> MissionGate:
    """Return the mission's gate class. Explicit flags OR text heuristics trip
    a class; severity order is dangerous > destructive/off-LAN > collab-build."""
    lower = (text or "").lower()
    reasons: dict[str, str] = {}

    dk = _any(lower, _DANGEROUS_KEYWORDS)
    if dangerous or dk:
        reasons[DANGEROUS] = "explicit dangerous flag" if dangerous else f"dangerous signal: {dk!r}"

    desk = _any(lower, _DESTRUCTIVE_KEYWORDS)
    offk = _any(lower, _OFF_LAN_KEYWORDS)
    if destructive or off_lan or desk or offk:
        if destructive:
            reasons[DESTRUCTIVE_OR_OFF_LAN] = "explicit destructive flag"
        elif off_lan:
            reasons[DESTRUCTIVE_OR_OFF_LAN] = "explicit off-LAN flag"
        elif desk:
            reasons[DESTRUCTIVE_OR_OFF_LAN] = f"destructive signal: {desk!r}"
        else:
            reasons[DESTRUCTIVE_OR_OFF_LAN] = f"off-LAN signal: {offk!r}"

    has_collab = collaborative_build or _any(lower, _COLLAB_KEYWORDS)
    has_build = collaborative_build or _any(lower, _BUILD_KEYWORDS)
    if collaborative_build or (has_collab and has_build):
        reasons[HUGE_COLLAB_BUILD] = (
            "explicit collaborative-build flag" if collaborative_build
            else "collaborative + build/build signals"
        )

    for klass in _ORDER:
        if klass == NORMAL:
            return MissionGate(NORMAL, False, "no gated signal detected")
        if klass in reasons:
            return MissionGate(klass, True, reasons[klass])
    return MissionGate(NORMAL, False, "no gated signal detected")
