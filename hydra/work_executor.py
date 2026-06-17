"""hydra.work_executor — route WORK turns to a real tool-calling executor,
block confabulated "I did the work" claims, and cap runaway generation.

THE BUG THIS FIXES
------------------
A real task ("ssh into a remote server and run an end-to-end build") arrived as a STEERING
(work) turn but was run on the cloud CHAT model (llama-3.3-70b). The chat model
NARRATED — it printed "I've successfully connected to the remote server via SSH" —
with ZERO tool calls (no bash, no ssh), then spewed runaway blank lines until the
process died. It LIED about doing the work.

THREE FIXES (all WORK-only; the convo/chat path is never touched):

1. ``resolve_work_model(kind)`` — a work turn (a profile with an ``executor`` set)
   routes to the most-capable executor from ``model_routing``
   (cloud-planner), NOT the chat narrator. Declarative: the roster id comes
   from ``turn_profiles.yaml``'s ``executor`` field; the provider/model comes from
   ``model_routing.yaml``. Convo returns ``None`` (stays on the chat model).

2. ``guard_work_turn(...)`` — the no-confabulation guard. On a WORK turn, if the
   model's final text CLAIMS it performed an action (``is_action_claim``) but made
   ZERO tool calls, that's a confabulation. The guard re-prompts ONCE, forcing the
   model to either actually CALL the tool or state honestly it has not done it. If
   it still claims-without-acting, the guard returns an HONEST "not done" message —
   never a fake "done". Convo turns are exempt.

3. ``detect_runaway(text, max_tokens)`` — detects degenerate output (a long run of
   blank lines, many repeated identical lines, or output far past the profile's
   ``max_tokens`` budget) and returns a clean, short capped message so a turn never
   spews until the process dies.

This module is pure/declarative and import-light so it is fully unit-testable
without a live model. ``_stream_turn`` in ``gateways/tui/elite.py`` wires it in.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from hydra.turn_profiles import load_turn_profile

_LOG = logging.getLogger(__name__)

# ── Self-audit confabulation log ──────────────────────────────────────────────
# Every time guard_work_turn catches a confabulation (claim without tool call),
# a JSON record is appended here so the operator can see it and the self-audit
# can read it. Path is module-level so tests can monkeypatch it.
SELF_AUDIT_LOG_PATH: str = str(
    Path(__file__).resolve().parent.parent / ".hydra_self_audit.jsonl"
)

# ── Ollama base URL (module-level so tests can monkeypatch) ───────────────────
_ollama_base_url: str = "http://127.0.0.1:11434"


# ── Qwen tool-capability probe ───────────────────────────────────────────────

@dataclass
class QwenProbeResult:
    """Result of probing whether the local qwen2.5-coder is reachable + tool-capable."""
    reachable: bool
    tool_capable: bool
    model: str = "qwen2.5-coder:7b"
    note: str = ""


def probe_qwen_tool_support(
    model: str = "qwen2.5-coder:7b",
    base_url: str | None = None,
) -> QwenProbeResult:
    """Probe whether the local qwen2.5-coder:7b is reachable and tool-capable.

    Uses the ollama /api/show endpoint (offline-safe: just checks capabilities,
    no actual inference call needed). Degrades gracefully on any error.
    """
    import urllib.request
    import urllib.error

    url = (base_url or _ollama_base_url).rstrip("/") + "/api/show"
    payload = json.dumps({"name": model}).encode()
    try:
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        capabilities = data.get("capabilities") or []
        tool_capable = "tools" in capabilities
        if not tool_capable:
            _LOG.warning(
                "qwen probe: %r does not report 'tools' capability (got %r). "
                "Work turns may fall back to chat loop if tool calls fail.",
                model,
                capabilities,
            )
        return QwenProbeResult(
            reachable=True,
            tool_capable=tool_capable,
            model=model,
            note=f"capabilities={capabilities}",
        )
    except Exception as exc:
        _LOG.warning(
            "qwen probe: could not reach ollama at %s for model %r (%s). "
            "Work turns will fall back to the chat loop (guarded against confabulation).",
            url,
            model,
            exc,
        )
        return QwenProbeResult(
            reachable=False,
            tool_capable=False,
            model=model,
            note=str(exc),
        )


# ── Work-turn classification (declarative: a profile with an executor is work) ──

def is_work_kind(kind: str) -> bool:
    """True when this intake kind is a WORK turn (has an executor in its profile).

    Convo has no executor → False (it stays on the chat model and just talks).
    Steering/collab declare ``executor: codex-build`` → True.
    """
    try:
        prof = load_turn_profile(kind)
    except Exception:  # pragma: no cover — load_turn_profile is itself fail-safe
        return False
    return bool(getattr(prof, "executor", "") or "")


# ── Work-model routing ───────────────────────────────────────────────────────

def resolve_work_model(kind: str) -> tuple[str, str] | None:
    """Resolve the (provider, model) a WORK turn must run on.

    The roster id is read from the turn profile's ``executor`` field
    (``turn_profiles.yaml``); the concrete provider/model is read from
    ``model_routing.yaml``. Returns ``None`` for a non-work turn (convo), which
    must stay on the chat model. Fail-safe: any lookup error returns ``None`` so
    the caller cleanly keeps the current (chat) client rather than crashing.
    """
    try:
        prof = load_turn_profile(kind)
    except Exception:  # pragma: no cover
        return None
    roster_id = (getattr(prof, "executor", "") or "").strip()
    if not roster_id:
        return None
    try:
        from hydra.model_routing import load_routing

        routing = load_routing()
        entry = routing.entry(roster_id)
        return entry.as_pair()
    except Exception as exc:  # unknown id, bad YAML — never crash the turn
        _LOG.warning("work executor roster id %r unresolved (%s)", roster_id, exc)
        return None


# ── Action-claim detection (the core no-confabulation regex) ────────────────
#
# Matches a model CLAIMING (past/present-perfect, first person OR a bare
# "successfully connected" / "connection established") that it performed a real
# action. Deliberately does NOT match future/hypothetical/conditional mentions
# ("I can connect", "we should connect", "to connect you would run …", "first we
# will connect") so a planning sentence is never mistaken for a false claim.

_ACTION_VERBS = (
    r"connected|ran|executed|created|deployed|installed|"
    r"ssh|sshed|completed|finished|connect"  # 'connect' only via the I-(successfully) path
)

# Variant A: first-person past/perfect action claim.
#   "I successfully connected", "I have connected", "I've connected", "I ran",
#   "I sshed", "I have completed", "I finished"
_CLAIM_FIRST_PERSON = re.compile(
    r"\bi\s+(?:have\s+|'?ve\s+|had\s+)?"
    r"(?:successfully\s+)?"
    r"(?:connected|ran|executed|created|deployed|installed|sshed|ssh|completed|finished)\b",
    re.IGNORECASE,
)

# Variant B: bare state assertions that imply the action already happened.
_CLAIM_STATE = re.compile(
    r"\b(?:connection established|successfully connected)\b",
    re.IGNORECASE,
)

# Guards: phrases that flip a would-be claim into a non-claim (future/hypothetical).
_NON_CLAIM_GUARD = re.compile(
    r"\b(?:will|would|can|could|should|going to|gonna|let me|i'?ll|plan to|"
    r"if you want|to connect)\b",
    re.IGNORECASE,
)


def is_action_claim(text: str) -> bool:
    """True when ``text`` claims a real action was ALREADY performed.

    Used only on WORK turns. Convo turns never call this.
    """
    if not text:
        return False
    s = text.strip()
    matched = bool(_CLAIM_FIRST_PERSON.search(s) or _CLAIM_STATE.search(s))
    if not matched:
        return False
    # A first-person claim ("I have connected") is a hard claim regardless of other
    # words. But for the looser cases, a future/hypothetical guard word demotes it.
    if _CLAIM_FIRST_PERSON.search(s):
        return True
    # State-only assertion ("successfully connected to remote server") — still a claim
    # unless it's clearly hypothetical.
    if _NON_CLAIM_GUARD.search(s):
        return False
    return True


# ── No-confabulation guard ───────────────────────────────────────────────────

@dataclass
class WorkGuardOutcome:
    """Result of running the no-confabulation guard over a work turn.

    * ``text`` — the operator-facing text to actually present (the real result,
      or an honest "not done" — NEVER a fake success).
    * ``confabulated`` — True when the FIRST response was a claim-without-action.
    * ``action_performed`` — True when a real tool call backed the claim (either
      on the first response, or on the forced re-prompt). False = honest not-done.
    * ``reprompted`` — True when the guard forced the one re-prompt.
    """

    text: str
    confabulated: bool
    action_performed: bool
    reprompted: bool = False


# A re-run callable takes ``force_tool_instruction`` (True) and returns
# ``(final_text, tool_calls_made)``. The caller (elite._stream_turn) closes over
# the loop so the guard stays model-agnostic and unit-testable.
RerunFn = Callable[[bool], "tuple[str, int]"]


_HONEST_NOT_DONE = (
    "I did NOT perform that action. I claimed it but never actually ran a tool to "
    "do it, so I won't report it as done. Tell me to retry and I'll run the real "
    "command this time."
)


def _record_confabulation(*, kind: str, claimed_text: str) -> None:
    """Append a confabulation-caught record to the self-audit log.

    Called every time guard_work_turn catches a claim-without-tool-call. The
    record is JSON-lines format so it can be read by self_audit.py and by the
    operator. Fail-soft: a write error must never crash the agent turn.
    """
    record = {
        "event": "confabulation_caught",
        "ts": time.time(),
        "kind": kind,
        "claimed_text_snippet": (claimed_text or "")[:120].replace("\n", " "),
    }
    try:
        log_path = Path(SELF_AUDIT_LOG_PATH)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception as exc:  # pragma: no cover — I/O errors must not break turns
        _LOG.warning("self-audit log write failed: %s", exc)


def guard_work_turn(
    *,
    kind: str,
    final_text: str,
    tool_calls_made: int,
    rerun: RerunFn,
) -> WorkGuardOutcome:
    """Block confabulated success on a WORK turn.

    Decision table (work turns only — convo is exempt and passes straight through):

      claim? | tools? | action
      -------|--------|----------------------------------------------------------
        no   |  any   | pass through (no claim → nothing to verify)
       yes   | >= 1   | pass through (the claim is backed by a real tool call)
       yes   |   0    | CONFAB → force one re-prompt:
                          re-prompt calls a tool  → accept the new real result
                          re-prompt still 0 tools → honest "not done" (never fake)
    """
    # Convo (and any non-work kind) is exempt — never re-prompt, never alter text.
    if not is_work_kind(kind):
        return WorkGuardOutcome(
            text=final_text, confabulated=False, action_performed=True
        )

    claim = is_action_claim(final_text)

    # Real tool activity supports any claim → accept as-is.
    if tool_calls_made >= 1:
        return WorkGuardOutcome(
            text=final_text, confabulated=False, action_performed=True
        )

    # No tools this turn. If there's no action claim, there's nothing to confabulate
    # (e.g. the model asked a clarifying question) — pass through untouched.
    if not claim:
        return WorkGuardOutcome(
            text=final_text, confabulated=False, action_performed=True
        )

    # CONFABULATION: claimed an action but made ZERO tool calls. Record it to the
    # self-audit log so the operator can see the catch and the self-audit can verify.
    _record_confabulation(kind=kind, claimed_text=final_text)

    # Force ONE re-prompt that demands the model either actually CALL the tool
    # or admit it has not done it.
    new_text, new_tools = rerun(True)

    if new_tools >= 1:
        # The re-prompt actually acted — accept the real result.
        return WorkGuardOutcome(
            text=new_text,
            confabulated=True,
            action_performed=True,
            reprompted=True,
        )

    # Still claiming without acting (or now honestly admitting it). Either way we
    # must NOT present a fake success. If the model now states honestly that it has
    # not done the action, keep its honest words; otherwise replace the lie.
    if is_action_claim(new_text):
        honest = _HONEST_NOT_DONE
    else:
        honest = (new_text or "").strip() or _HONEST_NOT_DONE
    return WorkGuardOutcome(
        text=honest,
        confabulated=True,
        action_performed=False,
        reprompted=True,
    )


# ── Runaway-generation cap ───────────────────────────────────────────────────

@dataclass
class RunawayHit:
    """A detected runaway/degenerate-output condition + a clean capped message."""

    reason: str  # "blank_lines" | "repeated_lines" | "length"
    capped_text: str


# Thresholds — generous enough to never trip on normal multi-line work output,
# tight enough to catch a degenerate spew long before a process dies.
_MAX_BLANK_RUN = 40          # >40 consecutive blank lines = degenerate
_MAX_REPEAT_RUN = 30         # >30 identical non-blank lines in a row = degenerate
_LENGTH_MULTIPLIER = 6       # output > 6x the max_tokens char budget = runaway
_CHARS_PER_TOKEN = 4         # rough OpenAI-ish chars/token


def _max_consecutive_blank_run(lines: list[str]) -> int:
    best = run = 0
    for ln in lines:
        if ln.strip() == "":
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _max_consecutive_repeat_run(lines: list[str]) -> int:
    best = run = 0
    prev = object()
    for ln in lines:
        stripped = ln.strip()
        if stripped == "":
            run = 0
            prev = object()
            continue
        if stripped == prev:
            run += 1
        else:
            run = 1
            prev = stripped
        best = max(best, run)
    return best


def detect_runaway(text: str, *, max_tokens: int) -> RunawayHit | None:
    """Detect degenerate/runaway output. Returns a ``RunawayHit`` or ``None``.

    Three signals, checked in priority order:
      * length      — text far exceeds the profile's max_tokens char budget
      * blank_lines — a long run of consecutive blank lines (the observed bug)
      * repeated_lines — the same non-blank line repeated many times in a row
    """
    if not text:
        return None

    char_budget = max(1, int(max_tokens)) * _CHARS_PER_TOKEN
    length_cap = char_budget * _LENGTH_MULTIPLIER

    lines = text.split("\n")
    blank_run = _max_consecutive_blank_run(lines)
    repeat_run = _max_consecutive_repeat_run(lines)
    over_length = len(text) > length_cap

    if not (over_length or blank_run > _MAX_BLANK_RUN or repeat_run > _MAX_REPEAT_RUN):
        return None

    # Pick the dominant reason. Length overflow wins (it's the broadest signal),
    # then blank-line spew, then repeated lines.
    if over_length:
        reason = "length"
    elif blank_run > _MAX_BLANK_RUN:
        reason = "blank_lines"
    else:
        reason = "repeated_lines"

    # Build a clean, short capped message: keep the meaningful leading content,
    # drop the degenerate tail, append a clear stop notice.
    meaningful: list[str] = []
    blanks = 0
    for ln in lines:
        if ln.strip() == "":
            blanks += 1
            if blanks > 2:  # collapse long blank runs
                continue
        else:
            blanks = 0
        meaningful.append(ln)
        if len("\n".join(meaningful)) >= char_budget:
            break

    head = "\n".join(meaningful).rstrip()
    notice = (
        "\n\n[stopped: runaway output detected — the model began repeating/blank-"
        "spewing, so the turn was cut off cleanly. Nothing above was reported as "
        "done unless a tool actually ran.]"
    )
    capped = (head + notice) if head else notice.strip()
    return RunawayHit(reason=reason, capped_text=capped)
