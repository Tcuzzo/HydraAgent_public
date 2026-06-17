"""hydra.turn_shape — the small loop the operator asked for.

Shape:
    operator input
        -> measure INTENT (chat vs work) while a speculative chat draft runs IN
           PARALLEL  (reuse intent_router.route_turn — chat and the reasoning/doing
           side run together; zero added latency on chat)
        -> chat  : return the ready draft
        -> work  : hand the mission to the existing ORCHESTRATION SEAM (parallel
                   Builder -> Critic -> Verifier -> Auditor with auto-repair, runs
                   to accepted, no gates). The seam's plan file is the working-
                   memory LEDGER it reads/updates each cycle.

This REUSES the robust pieces that already exist (intent_router.route_turn +
autonomous_mission_parallel.run_autonomous_mission_v3 / LolOrchestrator). It does
not reinvent intent classification or orchestration. All heavy deps are injected
so the routing logic is testable and the live wiring lives at the call site
(see build_live_handlers).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hydra.intent_router import IntentDecision, route_turn


@dataclass
class TurnOutcome:
    kind: str                      # "chat" | "work"
    chat_reply: str | None = None  # set when kind == "chat"
    mission: dict | None = None    # the orchestration result when kind == "work"
    decision: IntentDecision | None = None


def run_turn(
    text: str,
    *,
    intent_fn: Callable[[str], IntentDecision],
    chat_fn: Callable[[str], str],
    orchestrate_fn: Callable[[str], dict],
) -> TurnOutcome:
    """Measure intent (chat ‖ work draft in parallel), then route.

    chat -> the ready draft. work -> the orchestration seam (orchestrate_fn).
    Fails safe to work because intent_fn/route_turn fail safe to 'task' — a work
    request is never gagged.
    """
    route = route_turn(text, intent_fn=intent_fn, chat_draft_fn=chat_fn)
    if route.intent == "chat":
        return TurnOutcome("chat", chat_reply=route.chat_draft, decision=route.decision)
    mission = orchestrate_fn(text)
    return TurnOutcome("work", mission=mission, decision=route.decision)


def orchestrate_mission(
    text: str,
    *,
    repo_root,
    brainstorm_fn: Callable[[str], Any],
    build_fn: Callable[[str, Any], dict],
    land_fn: Callable[[str, dict], dict],
) -> dict:
    """The seam (operator's choice: v3 BUILDS, LoL LANDS).

      brainstorm_fn(text)            -> ledger path  (working memory; the plan the
                                        parallel seam reads/updates each cycle)
      build_fn(text, ledger)         -> build result (run_autonomous_mission_v3:
                                        parallel workers + auto-repair, to accepted)
      land_fn(text, build_result)    -> promotion verdict (LoL fresh-context audit)

    Steps are injected so the sequence is unit-tested without the heavy externals;
    build_live_handlers() supplies the real cloud-backed implementations.
    """
    ledger = brainstorm_fn(text)
    build = build_fn(text, ledger)
    verdict = land_fn(text, build)
    return {"ledger": str(ledger), "build": build, "verdict": verdict}
