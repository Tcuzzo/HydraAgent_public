"""hydra/event_log.py — Typed, APPEND-ONLY event log + deterministic replay.

Every event has:
  seq            — monotonic integer, starts at 1 per log file
  type           — lifecycle event type (see EVENT_TYPES)
  trace_id       — propagated from inter_agent context (or generated)
  correlation_id — propagated from inter_agent context (may be None)
  ts             — wall-clock at EMIT time, stored in the event; replay uses
                   this recorded value and NEVER reads a live clock
  payload        — dict with event-specific data (no model calls, no I/O)

Append-only: events are only ever appended. No mutation, no deletion, no
rewrite is supported. Replay reads the stored events as fixtures — models
are NEVER re-invoked, no network, no live clock.

Event types covering the worker lifecycle:
  job_started          — a worker job has started
  action_applied       — a write_text/replace_text/apply_patch action was applied
  verify_ran           — a verify_command was executed (cmd + returncode stored)
  gate_evaluated       — the resolution gate returned a verdict
  role_verdict         — a role/review verdict was recorded
  promotion_decision   — self-improvement/lesson promotion decision
  job_finished         — a worker job completed (status: passed/failed)

replay(events) -> dict
  Reconstructs the run's OUTCOME (status, timeline) DETERMINISTICALLY from
  the stored events alone. Tool calls / command results / model verdicts are
  replayed as FIXTURES. Same log → same reconstructed outcome, every time.
  No models are invoked, no network, no live clock.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from hydra.inter_agent import current_trace_id, current_correlation_id, new_trace_id


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EVENT_LOG_SCHEMA = "hydra.event_log.v1"

EVENT_TYPES = frozenset(
    {
        "job_started",
        "action_applied",
        "verify_ran",
        "gate_evaluated",
        "role_verdict",
        "promotion_decision",
        "job_finished",
    }
)


# ---------------------------------------------------------------------------
# EventLog — the append-only typed log
# ---------------------------------------------------------------------------


class EventLog:
    """Append-only typed event log for a single worker job run.

    Parameters
    ----------
    path:
        File path for the JSONL log.  The file is created on first emit if
        it does not already exist.  Existing content is preserved (append mode).
    trace_id:
        Trace id to stamp on every event.  If None, falls back to the
        contextvars trace (inter_agent.current_trace_id) or generates a new one.
    correlation_id:
        Correlation id to stamp on every event.  May be None.

    Usage
    -----
    >>> elog = EventLog(run_dir / "typed_events.jsonl", trace_id=tid)
    >>> elog.emit("job_started", {"job_id": "j1", "goal": "..."})
    >>> elog.emit("action_applied", {"kind": "write_text", "path": "foo.py", "ok": True})
    >>> elog.emit("job_finished", {"status": "passed", "job_id": "j1"})
    """

    def __init__(
        self,
        path: Path | str,
        *,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._path = Path(path)
        self._trace_id: str = (
            trace_id
            or current_trace_id()
            or new_trace_id()
        )
        self._correlation_id: str | None = (
            correlation_id
            if correlation_id is not None
            else current_correlation_id()
        )
        self._seq: int = self._count_existing_events()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a typed event to the log and return the event dict.

        The timestamp is captured NOW and stored in the event. Replay reads
        ts from the stored event — it never looks at a live clock.

        Parameters
        ----------
        event_type:
            One of the EVENT_TYPES (or a custom type for forward-compat).
        payload:
            Event-specific data.  Must be JSON-serialisable.

        Returns
        -------
        The event dict that was written.
        """
        self._seq += 1
        event: dict[str, Any] = {
            "seq": self._seq,
            "type": event_type,
            "trace_id": self._trace_id,
            "correlation_id": self._correlation_id,
            "ts": time.time(),  # captured at emit time; stored in event
            "payload": payload,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    # ------------------------------------------------------------------
    # Class method: load events from an existing log file
    # ------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: Path | str) -> list[dict[str, Any]]:
        """Load all events from an existing JSONL log file.

        Returns a list of event dicts in append order.
        """
        p = Path(path)
        if not p.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count_existing_events(self) -> int:
        """Count how many events already exist in the log file (for seq continuity)."""
        if not self._path.exists():
            return 0
        count = 0
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count


# ---------------------------------------------------------------------------
# replay — deterministic reconstruction from the stored log
# ---------------------------------------------------------------------------


def replay(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconstruct the run's OUTCOME from the stored events alone.

    This function is:
    - DETERMINISTIC: same log → same result, every time.
    - MODEL-FREE: no LLM/model is invoked; all verdicts come from stored events.
    - NETWORK-FREE: no I/O except reading the supplied list.
    - CLOCK-FREE: ts is read from the stored event, never from a live clock.

    Parameters
    ----------
    events:
        List of event dicts as returned by EventLog.from_file() or as
        accumulated during a run. Processed in seq order.

    Returns
    -------
    dict with:
        status     — "passed" | "failed" (derived from job_finished event)
        job_id     — the job id (from job_started or job_finished payload)
        timeline   — list of {seq, type, ts, payload} in order
        verify_results — list of {command, returncode} from verify_ran events
        gate_result    — last gate_evaluated payload (or None)
        failure_reason — from job_finished payload (or None)
    """
    # Sort by seq to ensure order even if events were loaded out of order
    ordered = sorted(events, key=lambda e: e.get("seq", 0))

    timeline: list[dict[str, Any]] = []
    job_id: str = ""
    status: str = "unknown"
    verify_results: list[dict[str, Any]] = []
    gate_result: dict[str, Any] | None = None
    failure_reason: str | None = None

    for evt in ordered:
        evt_type = evt.get("type", "")
        payload = evt.get("payload") or {}
        seq = evt.get("seq", 0)
        ts = evt.get("ts", 0.0)

        timeline.append(
            {
                "seq": seq,
                "type": evt_type,
                "ts": ts,  # read from stored event, never from live clock
                "payload": payload,
            }
        )

        if evt_type == "job_started":
            job_id = payload.get("job_id", job_id)

        elif evt_type == "verify_ran":
            verify_results.append(
                {
                    "command": payload.get("command", ""),
                    "returncode": payload.get("returncode", -1),
                }
            )

        elif evt_type == "gate_evaluated":
            gate_result = payload

        elif evt_type == "job_finished":
            job_id = payload.get("job_id", job_id)
            status = payload.get("status", "unknown")
            failure_reason = payload.get("failure_reason") or None

    return {
        "status": status,
        "job_id": job_id,
        "timeline": timeline,
        "verify_results": verify_results,
        "gate_result": gate_result,
        "failure_reason": failure_reason,
    }
