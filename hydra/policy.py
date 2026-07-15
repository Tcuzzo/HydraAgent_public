"""hydra.policy — operator approval policy for destructive tool calls.

The low-level skills enforce scope and bounds. This module adds the
operator-facing decision layer for commands that can delete, overwrite,
escalate privilege, restart services, force-push, or mutate external systems.
"""
from __future__ import annotations

import sys
import time
import uuid
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from hydra.autonomy import classify_tool_call
from hydra.workbench_approvals import DEFAULT_APPROVAL_QUEUE_PATH
from hydra.workbench_approvals import append_record as append_approval_record
from hydra.workbench_approvals import create_request
from hydra.workbench_approvals import load_records as load_approval_records
from hydra.workbench_runs import DEFAULT_RUNS_PATH
from hydra.workbench_runs import append_record as append_run_record
from hydra.workbench_runs import create_run


RISKY_TOOLS = frozenset({"bash", "fs_write", "fs_edit", "memory_remember", "worker_job"})

# Tools that mutate state — blocked in plan mode.  This is a broader list than
# RISKY_TOOLS: plan mode enforces read-only discipline so even low-risk mutating
# tools (apply_patch, etc.) are denied.
MUTATING_TOOLS = frozenset({
    "bash", "fs_write", "fs_edit", "apply_patch",
    "memory_remember", "worker_job",
})
POLICY_CHOICES = ("allow", "ask", "deny")


class ApprovalDenied(Exception):
    """Raised when a risky tool call is refused by policy."""


class ApprovalTimeout(ApprovalDenied):
    """Raised when the operator never decided a queued approval before the wait
    timeout. A subclass of ApprovalDenied so existing callers/handlers that catch
    ApprovalDenied (the agent loop, worker gates) treat a timeout as a clean
    refusal — the gated action does NOT run."""


def is_risky_tool(tool_name: str) -> bool:
    return tool_name in RISKY_TOOLS


@dataclass
class ApprovalPolicy:
    """Decide whether a tool call may run.

    Modes:
      - allow: run without prompting.
      - ask: prompt or queue only destructive commands; auto-allow normal tools.
      - deny: refuse destructive commands.
    """

    mode: str = "allow"
    input_fn: Callable[[str], str] = input
    output_fn: Callable[[str], None] = print
    stdin_is_tty: Callable[[], bool] = field(default=lambda: sys.stdin.isatty())
    approval_path: Path = DEFAULT_APPROVAL_QUEUE_PATH
    run_path: Path = DEFAULT_RUNS_PATH
    notify_telegram: bool = False
    authority_checker: Callable[[], bool] | None = None
    # Rule 1 (operator, 2026-06-02): is the input surface the operator's own trusted
    # Telegram bot session? Default True keeps every existing call site on the LAW
    # (only destructive gates). When False (public/untrusted surface), every ACTION
    # tool is queued for the operator's approval — see hydra.channel_trust.
    surface_trusted: bool = True

    # Slice 4 — PLAN/ACT discipline.
    # When plan_mode is True the agent is in the read-only PLAN phase: every
    # mutating/RISKY tool call is blocked outright (ApprovalDenied). Read-only
    # tools pass through normally. Flipping plan_mode back to False (ACT phase)
    # re-enables mutations subject to the normal approval gate.
    # Default is False so all existing call sites are unaffected (back-compat).
    plan_mode: bool = False

    # ── Re-execution seam (operator's #1 bug: "tap Approve does nothing") ──────
    # When a tool needs approval, require() queues the request (sends the Telegram
    # buttons) and then — if wait_for_approval is True — BLOCKS until the operator
    # decides: APPROVE -> require() returns (the caller re-runs the gated tool),
    # DENY -> ApprovalDenied, no decision before the timeout -> ApprovalTimeout.
    # The decision is read from the SAME approval queue the Telegram listener
    # writes to (decide_request), so a real button press resolves the wait.
    #
    # Default is False so every existing call site keeps the old immediate-raise
    # contract (and existing tests don't block). guarded() opts INTO the wait.
    wait_for_approval: bool = False
    approval_poll_interval: float = 1.0
    approval_wait_timeout: float = 1800.0  # 30 min: a sane bound, not forever.
    # Injectable for offline/deterministic tests: given a request_id, return the
    # current ApprovalRecord (or None). Defaults to reading the queue file.
    approval_decision_reader: Callable[[str], object | None] | None = None
    # Optional hook fired with the resolved decision ("approved"/"denied"/
    # "timeout") and request_id — lets the run record / surface react.
    on_decision: Callable[[str, str], None] | None = None

    def __post_init__(self) -> None:
        if self.mode not in POLICY_CHOICES:
            raise ValueError(
                f"approval policy must be one of {POLICY_CHOICES}, got {self.mode!r}"
            )

    def set_plan_mode(self, enabled: bool) -> None:
        """Toggle PLAN (read-only) vs ACT mode.  Thread-safe assignment."""
        self.plan_mode = bool(enabled)

    def require(
        self,
        tool_name: str,
        arguments: dict,
        *,
        mission_level: str | None = None,
        is_self_heal: bool = False,
        wait: bool | None = None,
        non_destructive_auto_allow: bool = True,
    ) -> None:
        # `non_destructive_auto_allow` is threaded from the calling tool contract
        # (e.g. .hydraAgent/tools/shell.yaml). Default True preserves EXACTLY the
        # historical behavior for every existing call site. When a contract sets it
        # False (the public-edition safe default), a command the classifier would
        # normally auto-allow — a non-destructive shell command — is instead routed
        # through the approval gate below. Destructive commands are unaffected: they
        # already classify as needs_approval regardless of this flag.
        # Re-execution seam: `wait` overrides self.wait_for_approval for this one
        # call so guarded() can opt a single tool call INTO the blocking wait
        # WITHOUT cloning the policy (the policy is mutated live — e.g. plan_mode
        # toggled per turn — so a snapshot clone would go stale).  Reset the
        # per-call resolved-request marker guarded() reads after we return.
        self._wait_this_call = self.wait_for_approval if wait is None else bool(wait)
        self._last_approved_request_id = None
        # Slice 4 — PLAN/ACT gate (checked before everything else).
        # In plan_mode, only reads are allowed.  Any mutating tool is denied
        # immediately so the agent cannot write files, run bash, or apply patches
        # while it is still in the planning phase.  Self-heal bypasses plan_mode
        # (same exemption pattern as the untrusted-surface gate below).
        if self.plan_mode and not is_self_heal:
            if tool_name in MUTATING_TOOLS:
                raise ApprovalDenied(
                    f"plan mode is active — mutating tool {tool_name!r} is blocked; "
                    "exit plan mode (set plan_mode=False) before executing actions"
                )

        # Untrusted-surface gate: input from a public surface (Discord, social media,
        # any messenger outside the operator's Telegram session) cannot call an ACTION
        # tool without the operator's approval. Research/reads run free; self-heal is
        # exempt. This takes priority over yolo authority — a stranger's tool call must
        # never be auto-approved by the operator's own yolo flag.
        from hydra.channel_trust import requires_operator_approval

        if requires_operator_approval(
            tool_name, surface_trusted=self.surface_trusted, is_self_heal=is_self_heal
        ):
            summary = _summarize(tool_name, arguments)
            if self.mode == "deny":
                raise ApprovalDenied(f"approval denied for {summary}")
            request_id = self._queue_approval(
                tool_name, arguments, summary, mission_level=mission_level, untrusted=True
            )
            # The untrusted-surface gate is owned by Alpha and resolved ASYNC — we
            # never block a public/non-operator tool call inline (that would tie up a
            # worker for the whole wait). Queue + raise; the action re-runs on a later
            # attempt once Alpha relays the operator's approval into the queue.
            raise ApprovalDenied(
                f"approval queued (untrusted surface) for {summary}: {request_id}"
            )

        if is_self_heal:
            return
        if not is_risky_tool(tool_name):
            return
        if self._operator_has_yolo_authority():
            return
        # 'allow' means ALLOW. On the operator's own trusted surface, a tool launched
        # in allow mode executes in real time — no hidden destructive sub-gate, no
        # "blocked" friction. The untrusted-surface gate above still protects
        # public/non-operator input, and 'deny'/'ask' modes still classify below.
        if self.mode == "allow":
            return
        if self.mode == "deny":
            # Deny mode refuses all risky tools, not just destructive ones
            summary = _summarize(tool_name, arguments)
            raise ApprovalDenied(f"approval denied for {summary}")
        autonomy = classify_tool_call(tool_name, arguments, self.mode, mission_level=mission_level)
        if autonomy["decision"] == "auto_allow" and non_destructive_auto_allow:
            return
        # Contract opted out of non-destructive auto-allow: fall through so even a
        # benign command is queued/prompted like any other gated action.
        if autonomy["decision"] == "blocked":
            raise ApprovalDenied(f"approval denied for {_summarize(tool_name, arguments)}")
        summary = _summarize(tool_name, arguments)
        if not self.stdin_is_tty():
            request_id = self._queue_approval(tool_name, arguments, summary, mission_level=mission_level)
            # Re-execution seam: block for the operator's decision and resolve it.
            self._resolve_or_raise(
                request_id,
                summary,
                queued_message=f"approval queued for {summary}: {request_id}",
            )
            return
        answer = self.input_fn(f"Approve {summary}? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            raise ApprovalDenied(f"approval denied for {summary}")
        self.output_fn(f"approved: {summary}")

    def _operator_has_yolo_authority(self) -> bool:
        if self.authority_checker is not None:
            return bool(self.authority_checker())
        try:
            from hydra.operator_auth import is_yolo_active
        except Exception:
            return False
        return is_yolo_active()

    def _resolve_or_raise(self, request_id: str, summary: str, *, queued_message: str) -> None:
        """The re-execution seam's decision point.

        When waiting is OFF for this call (the default), preserve the legacy
        contract: the approval is queued and ApprovalDenied is raised immediately
        (the agent loop / worker gate handles it). When ON, BLOCK until the
        operator decides via Telegram (which writes the same approval queue):
          - APPROVE  -> return (the caller re-runs the gated tool);
          - DENY     -> ApprovalDenied (clean refusal, tool does NOT run);
          - TIMEOUT  -> ApprovalTimeout (clean abort, tool does NOT run).
        """
        if not getattr(self, "_wait_this_call", self.wait_for_approval):
            raise ApprovalDenied(queued_message)
        # Visible "waiting for approval" indication (operator caveat). The Telegram
        # buttons were already sent by _queue_approval; this surfaces the wait on
        # the local console/TUI too. Best-effort — never let it break the gate.
        try:
            self.output_fn(
                f"Waiting for your approval in Telegram: {summary} "
                f"(tap Approve to run it, Deny to skip)"
            )
        except Exception:  # noqa: BLE001
            pass
        decision = self._wait_for_decision(request_id)
        if self.on_decision is not None:
            try:
                self.on_decision(decision, request_id)
            except Exception:  # noqa: BLE001 — a surface hook must never break the gate
                pass
        if decision == "approved":
            # Record so guarded() can finalize the run record after the tool runs.
            self._last_approved_request_id = request_id
            return
        if decision == "denied":
            raise ApprovalDenied(f"approval denied for {summary} ({request_id})")
        raise ApprovalTimeout(
            f"approval timed out for {summary} after "
            f"{self.approval_wait_timeout:g}s — no operator decision ({request_id})"
        )

    def _read_decision(self, request_id: str) -> str:
        """Return the current status of a queued approval request: 'pending',
        'approved', 'denied', or 'expired'. Reads the durable queue the Telegram
        listener updates, unless a test injects approval_decision_reader."""
        if self.approval_decision_reader is not None:
            record = self.approval_decision_reader(request_id)
            return str(getattr(record, "status", "pending")) if record is not None else "pending"
        try:
            records = load_approval_records(Path(self.approval_path))
        except Exception:  # noqa: BLE001 — a transient read error -> keep waiting
            return "pending"
        for record in records:
            if record.request_id == request_id:
                return str(record.status)
        return "pending"

    def _wait_for_decision(self, request_id: str) -> str:
        """Poll the approval queue until the request is decided or the wait
        times out. Returns 'approved', 'denied', or 'timeout'."""
        interval = max(0.01, float(self.approval_poll_interval))
        deadline = time.monotonic() + max(0.0, float(self.approval_wait_timeout))
        while True:
            status = self._read_decision(request_id)
            if status == "approved":
                return "approved"
            if status in {"denied", "expired"}:
                return "denied"
            if time.monotonic() >= deadline:
                return "timeout"
            time.sleep(interval)

    def _queue_approval(
        self,
        tool_name: str,
        arguments: dict,
        summary: str,
        *,
        mission_level: str | None = None,
        untrusted: bool = False,
    ) -> str:
        arguments_preview = _preview_arguments(tool_name, arguments)
        duplicate = self._find_pending_duplicate(tool_name, summary, arguments_preview)
        if duplicate is not None:
            return duplicate
        stamp = uuid.uuid4().hex[:8]
        run_id = f"approval-{stamp}"
        request_id = f"{run_id}-request"
        # D4: a mission-level gate carries a namespaced risk_tier; tool-level
        # gates keep the T2 default. An untrusted-surface gate is its own tier so the
        # operator sees WHY it's asking. create_request accepts risk_tier — pass-through.
        if untrusted:
            risk_tier = "untrusted_surface"
        elif mission_level:
            risk_tier = f"mission:{mission_level}"
        else:
            risk_tier = "T2"
        approval = create_request(
            request_id=request_id,
            run_id=run_id,
            tool_name=tool_name,
            risk_tier=risk_tier,
            summary=f"Approve {summary}",
            arguments_preview=arguments_preview,
        )
        run = create_run(
            run_id=run_id,
            title="Approval-gated action",
            lane="chat",
            status="waiting_approval",
            goal=f"Wait for Telegram approval before running {summary}",
            approval_request_ids=[request_id],
        )
        append_run_record(Path(self.run_path), run)
        append_approval_record(Path(self.approval_path), approval)
        if self.notify_telegram:
            # Rule 1: Alpha owns the gate ask. For an untrusted-surface approval, hand it
            # Route untrusted-surface approvals to the fabric coordinator so the
            # operator's approve button arrives over the right channel. Best-effort
            # — falls back to the local Telegram notify so the ask is never silently lost.
            if untrusted:
                _route_approval_to_alpha(approval)
            try:
                from datetime import datetime

                from gateways.telegram.live import notify_approval

                # Pass the clock so quiet hours (1am-6am) holds routine pings.
                notify_approval(approval, now=datetime.now())
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "notify_approval failed (approval %s will not be announced): %s",
                    request_id,
                    exc,
                )
        return request_id

    def _find_pending_duplicate(self, tool_name: str, summary: str, arguments_preview: dict) -> str | None:
        try:
            records = load_approval_records(Path(self.approval_path))
        except Exception:
            return None
        expected_summary = f"Approve {summary}"
        for record in records:
            if (
                record.status == "pending"
                and record.tool_name == tool_name
                and record.summary == expected_summary
                and record.arguments_preview == arguments_preview
            ):
                return record.request_id
        return None


def _route_approval_to_alpha(approval) -> None:
    """Best-effort: hand an untrusted-surface approval to the fabric coordinator so the
    operator's approve-button ask is correctly routed. Silent on any failure — the local
    Telegram notify still runs as a fallback so the request is never lost."""
    try:
        import json
        import os
        import urllib.request

        fabric_base_env = os.environ.get("HYDRA_FABRIC_BASE")
        if not fabric_base_env:
            return  # fabric not configured; no-op
        base = fabric_base_env.rstrip("/")
        body = {
            "from": "hydra",
            "to": "coordinator",
            "message": (
                f"Operator approval needed (untrusted surface): {getattr(approval, 'summary', '')}"
            ),
            "mission_id": "approval-gate",
            "data": {
                "kind": "approval_request",
                "request_id": getattr(approval, "request_id", None),
                "run_id": getattr(approval, "run_id", None),
                "tool_name": getattr(approval, "tool_name", None),
                "risk_tier": getattr(approval, "risk_tier", None),
            },
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            base + "/ask", data=data, headers={"content-type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 — LAN fabric
    except Exception:  # noqa: BLE001 — routing is best-effort, never breaks the gate
        return


def _summarize(tool_name: str, arguments: dict) -> str:
    if tool_name == "bash":
        command = str(arguments.get("command", ""))
        return f"bash command {command[:120]!r}"
    if tool_name in {"fs_write", "fs_edit"}:
        path = str(arguments.get("path", ""))
        return f"{tool_name} on {path!r}"
    if tool_name == "memory_remember":
        return "store durable Hydra memory"
    if tool_name == "worker_job":
        job_id = str(arguments.get("job_id", ""))
        return f"worker job {job_id!r}"
    return tool_name


_SENSITIVE_KEY_FRAGMENTS = frozenset(
    {"token", "key", "secret", "password", "passwd", "apikey", "api_key", "auth", "credential", "bearer"}
)


def _is_sensitive_key(key: str) -> bool:
    lower = str(key).lower()
    return any(fragment in lower for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _preview_arguments(tool_name: str, arguments: dict) -> dict:
    if tool_name == "bash":
        return {"command": str(arguments.get("command", ""))[:500]}
    if tool_name in {"fs_write", "fs_edit"}:
        return {"path": str(arguments.get("path", ""))[:500]}
    return {
        str(k): "[redacted]" if _is_sensitive_key(k) or not isinstance(v, (str, int, float, bool)) else str(v)[:500]
        for k, v in arguments.items()
    }
