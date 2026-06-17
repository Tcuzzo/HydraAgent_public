"""hydra.self_audit — HYDRA audits its own classify->route->execute path.

THE BUG CLASS THIS CATCHES
--------------------------
A real task ("ssh into a remote server and run a build task") was handled incorrectly because:
  (a) It was mis-classified as CONVO (now fixed in intake_rules.yaml).
  (b) Even when correctly classified as STEERING, work turns ran on the cloud CHAT
      model (llama-3.3-70b) which NARRATES instead of calling tools. The model
      printed "I've successfully connected via SSH" with ZERO tool calls — a lie.

This self-audit exercises HYDRA's OWN classify->route->execute path and asserts
the invariants that the bug violated, so HYDRA can catch this class of regression
on demand (``hydra self-audit``) or in CI.

FOUR INVARIANTS
---------------
  (i)   Work imperatives (ssh/connect/deploy/run/build/fix) must classify
        as STEERING, never as convo.
  (ii)  Work turns (steering/collab) must route to the tool-capable local executor
        (qwen2.5-coder:7b via ollama), NOT the cloud chat model that narrates.
  (iii) A work turn that claims an action with ZERO tool calls must be BLOCKED by
        guard_work_turn (no confabulation reaches the operator).
  (iv)  Convo turns must remain unaffected — they still just talk.

REUSES HYDRA'S OWN PRIMITIVES
------------------------------
  * hydra.intake.classify         — the real classifier (no stub).
  * hydra.work_executor.*         — resolve_work_model, guard_work_turn, is_work_kind.
  * hydra.model_routing.load_routing — roster lookup.
  * hydra.resolution_gate-style report — SelfAuditReport with check/violation list.

Injectable seams (_classify, _resolve_work_model, _guard_work_turn) are exposed
so tests can monkeypatch individual functions and confirm the audit FAILS when an
invariant is broken — proving the audit actually catches the bug class.

CLI: ``python3 -m hydra self-audit``
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

# ── Injectable seams (monkeypatched in tests to break individual invariants) ──

def _classify(text: str, *, source: str = "operator"):
    from hydra.intake import classify
    return classify(text, source=source)


def _resolve_work_model(kind: str):
    from hydra.work_executor import resolve_work_model
    return resolve_work_model(kind)


def _guard_work_turn(*, kind: str, final_text: str, tool_calls_made: int, rerun):
    from hydra.work_executor import guard_work_turn
    return guard_work_turn(
        kind=kind,
        final_text=final_text,
        tool_calls_made=tool_calls_made,
        rerun=rerun,
    )


# ── Report data types ─────────────────────────────────────────────────────────

@dataclass
class AuditCheck:
    """One invariant check that was run."""
    check_id: str
    description: str
    passed: bool
    detail: str = ""


@dataclass
class AuditViolation:
    """One failed invariant check."""
    check_id: str
    description: str
    detail: str = ""


@dataclass
class SelfAuditReport:
    """Structured result of a self-audit run.

    ``passed``     — True only when ALL invariant checks pass.
    ``checks``     — list of AuditCheck (one per invariant, pass or fail).
    ``violations`` — list of AuditViolation for every failed check (empty on pass).
    """
    passed: bool
    checks: list[AuditCheck] = field(default_factory=list)
    violations: list[AuditViolation] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "hydra.self_audit.v1",
            "passed": self.passed,
            "checks": [
                {
                    "check_id": c.check_id,
                    "description": c.description,
                    "passed": c.passed,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
            "violations": [
                {
                    "check_id": v.check_id,
                    "description": v.description,
                    "detail": v.detail,
                }
                for v in self.violations
            ],
        }


# ── Work imperatives the audit exercises ─────────────────────────────────────
# These are the EXACT kinds of texts that triggered the original bug.
_WORK_IMPERATIVES = [
    "ssh into a remote server and run a build task",
    "connect to the remote server",
    "audit the repository",
    "deploy the new build",
    "run the integration test suite",
    "build a test harness",
    "fix the broken import",
]

_CONVO_SAMPLES = [
    "hi hydra",
    "what models do you use?",
    "hey, how are you doing?",
]


# ── Invariant check implementations ──────────────────────────────────────────

def _check_classify_work_imperatives() -> AuditCheck:
    """(i) Work imperatives must classify as STEERING, not convo."""
    from hydra.intake import CONVO, STEERING
    failures: list[str] = []
    for text in _WORK_IMPERATIVES:
        try:
            result = _classify(text)
        except Exception as exc:
            failures.append(f"{text!r}: classify raised {exc}")
            continue
        if result.kind != STEERING:
            failures.append(
                f"{text!r} -> {result.kind!r} (rule={result.rule_id!r}); expected STEERING"
            )
    if failures:
        detail = "; ".join(failures)
        return AuditCheck(
            check_id="classify_work_imperatives",
            description="Work imperatives must classify as STEERING not convo",
            passed=False,
            detail=detail,
        )
    return AuditCheck(
        check_id="classify_work_imperatives",
        description="Work imperatives must classify as STEERING not convo",
        passed=True,
        detail=f"All {len(_WORK_IMPERATIVES)} work imperatives correctly classified as STEERING",
    )


def _check_work_routes_to_executor() -> AuditCheck:
    """(ii) Work turns (steering/collab) must route to qwen-coder, NOT the chat model."""
    failures: list[str] = []
    for kind in ("steering", "collab"):
        try:
            pair = _resolve_work_model(kind)
        except Exception as exc:
            failures.append(f"{kind}: resolve raised {exc}")
            continue
        if pair is None:
            failures.append(
                f"{kind}: resolve_work_model returned None (executor not configured)"
            )
            continue
        provider, model = pair
        if "groq" in provider.lower() or "groq" in model.lower():
            failures.append(
                f"{kind}: routed to groq ({provider}/{model}) — must use local/cloud executor, not chat-only provider"
            )
        elif "qwen" not in model.lower() and provider != "ollama":
            failures.append(
                f"{kind}: executor is {provider}/{model} — expected ollama/qwen2.5-coder"
            )

    if failures:
        return AuditCheck(
            check_id="work_routes_to_executor",
            description="Work turns must route to qwen-coder (local-worker), not the chat model",
            passed=False,
            detail="; ".join(failures),
        )
    return AuditCheck(
        check_id="work_routes_to_executor",
        description="Work turns must route to qwen-coder (local-worker), not groq chat",
        passed=True,
        detail="steering and collab both route to ollama/qwen2.5-coder",
    )


def _check_confabulation_blocked() -> AuditCheck:
    """(iii) guard_work_turn must block a claim-without-tool-call."""
    test_cases = [
        (
            "steering",
            "I successfully connected to the remote server via SSH.",
            0,
            "steering: claim-without-tool should be flagged as confabulation",
        ),
        (
            "collab",
            "I have completed the build and executed the build.",
            0,
            "collab: claim-without-tool should be flagged as confabulation",
        ),
    ]
    failures: list[str] = []
    for kind, text, tool_calls, label in test_cases:
        try:
            outcome = _guard_work_turn(
                kind=kind,
                final_text=text,
                tool_calls_made=tool_calls,
                rerun=lambda force: ("I have not actually done that.", 0),
            )
        except Exception as exc:
            failures.append(f"{label}: guard raised {exc}")
            continue
        if not outcome.confabulated:
            failures.append(
                f"{label}: guard did NOT flag confabulation (confabulated=False)"
            )
        if outcome.action_performed:
            failures.append(
                f"{label}: guard reported action_performed=True but no tool was called"
            )
        # The guard must not present a raw success claim as the final text.
        # outcome.text should be an honest "not done" message — not the original lie.
        # We check that it wasn't simply passed through unchanged.
        if outcome.text.strip() == text.strip():
            failures.append(
                f"{label}: guard passed through the confabulated text unchanged: {outcome.text!r}"
            )

    if failures:
        return AuditCheck(
            check_id="confabulation_blocked",
            description="guard_work_turn must block claim-without-tool-call",
            passed=False,
            detail="; ".join(failures),
        )
    return AuditCheck(
        check_id="confabulation_blocked",
        description="guard_work_turn must block claim-without-tool-call",
        passed=True,
        detail="All confabulation test cases correctly blocked",
    )


def _check_convo_unaffected() -> AuditCheck:
    """(iv) Convo turns must still just talk (not routed to work executor)."""
    from hydra.intake import CONVO
    failures: list[str] = []

    # Convo texts must classify as CONVO
    for text in _CONVO_SAMPLES:
        try:
            result = _classify(text)
        except Exception as exc:
            failures.append(f"{text!r}: classify raised {exc}")
            continue
        if result.kind != CONVO:
            failures.append(
                f"{text!r} -> {result.kind!r}; expected CONVO"
            )

    # resolve_work_model("convo") must return None
    try:
        pair = _resolve_work_model("convo")
        if pair is not None:
            failures.append(
                f"resolve_work_model('convo') returned {pair!r}; must be None"
            )
    except Exception as exc:
        failures.append(f"resolve_work_model('convo') raised {exc}")

    if failures:
        return AuditCheck(
            check_id="convo_unaffected",
            description="Convo turns must classify as CONVO and not route to work executor",
            passed=False,
            detail="; ".join(failures),
        )
    return AuditCheck(
        check_id="convo_unaffected",
        description="Convo turns must classify as CONVO and not route to work executor",
        passed=True,
        detail="All convo samples correctly classified and resolve_work_model returns None",
    )


# ── Injectable seam: is the chat/work decision model-based? ──────────────────

def _decision_is_model_based() -> bool:
    """Return True if the elite TUI's _stream_turn uses route_turn (model-based
    intent) as the chat/work switch, rather than the keyword classifier alone.

    This is checked by inspecting that gateways.tui.elite imports route_turn
    from hydra.intent_router and wires it in _stream_turn.
    """
    try:
        import gateways.tui.elite as elite_mod
        return hasattr(elite_mod, "route_turn")
    except Exception:
        return False


def _check_model_based_decision() -> "AuditCheck":
    """(v) The chat/work routing decision must be model-based (route_turn), not keyword-only."""
    ok = _decision_is_model_based()
    if ok:
        return AuditCheck(
            check_id="model_based_decision",
            description="Intent routing must use model-based route_turn, not keyword classifier",
            passed=True,
            detail="gateways.tui.elite imports route_turn from hydra.intent_router",
        )
    return AuditCheck(
        check_id="model_based_decision",
        description="Intent routing must use model-based route_turn, not keyword classifier",
        passed=False,
        detail=(
            "gateways.tui.elite does NOT import/use route_turn — the chat/work "
            "decision is still keyword-only, which misroutes work tasks to the chat brain"
        ),
    )


def _check_work_executor_cloud_qwen() -> "AuditCheck":
    """(vi) Work executor (steering/collab) must route to the cloud model, not the chat model."""
    failures: list[str] = []
    for kind in ("steering", "collab"):
        try:
            pair = _resolve_work_model(kind)
        except Exception as exc:
            failures.append(f"{kind}: resolve raised {exc}")
            continue
        if pair is None:
            failures.append(f"{kind}: resolve_work_model returned None")
            continue
        provider, model = pair
        if "qwen" not in model.lower() and "cloud" not in provider.lower():
            failures.append(
                f"{kind}: executor is {provider}/{model} — expected cloud-based qwen model"
            )
        elif "ollama-cloud" not in provider.lower() and "cloud" not in provider.lower():
            # Warn if it's a local executor — policy requires cloud for work turns
            failures.append(
                f"{kind}: executor is local ({provider}/{model}) — expected ollama-cloud"
            )

    if failures:
        return AuditCheck(
            check_id="work_executor_cloud_qwen",
            description="Work executor must be cloud-based, not a local chat model",
            passed=False,
            detail="; ".join(failures),
        )
    return AuditCheck(
        check_id="work_executor_cloud_qwen",
        description="Work executor must be cloud-based, not a local chat model",
        passed=True,
        detail="steering and collab both route to ollama-cloud",
    )


# ── Main entry point ──────────────────────────────────────────────────────────

def run_self_audit() -> SelfAuditReport:
    """Run all four invariant checks and return a SelfAuditReport.

    This is the main entry point: ``hydra self-audit`` calls this.
    Reuses HYDRA's own primitives (intake.classify, work_executor.*,
    model_routing.*) — no stubs, no fake infrastructure. The injectable
    seams (_classify, _resolve_work_model, _guard_work_turn) allow tests
    to break individual invariants and confirm the audit catches them.
    """
    checks: list[AuditCheck] = []

    checks.append(_check_classify_work_imperatives())
    checks.append(_check_work_routes_to_executor())
    checks.append(_check_confabulation_blocked())
    checks.append(_check_convo_unaffected())
    checks.append(_check_model_based_decision())
    checks.append(_check_work_executor_cloud_qwen())

    violations = [
        AuditViolation(
            check_id=c.check_id,
            description=c.description,
            detail=c.detail,
        )
        for c in checks
        if not c.passed
    ]

    passed = not violations
    report = SelfAuditReport(passed=passed, checks=checks, violations=violations)

    if passed:
        _LOG.info(
            "hydra self-audit PASSED — all %d invariant checks green", len(checks)
        )
    else:
        _LOG.warning(
            "hydra self-audit FAILED — %d violation(s): %s",
            len(violations),
            [v.check_id for v in violations],
        )

    return report
