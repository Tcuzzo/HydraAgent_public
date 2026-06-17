"""hydra.intent_router — model-based per-turn intent (task vs chat), fail-safe to task.

Fans out two threads per turn:
  1. intent check  — cloud qwen3.5 classifies the message as 'task' or 'chat'
  2. chat draft    — speculative chat reply (side-effect-free, tools=[])

chat  → emit the ready draft (zero added latency)
task  → discard the draft, run the cloud-qwen executor + guard_work_turn

The classifier ALWAYS fails safe to 'task' on model error/timeout/unrecognized
response — it never silently routes a work request to the chat path.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

_PROMPT = (
    "Classify the user's message as exactly one word: 'task' if it asks you to DO "
    "something (run/build/fix/ssh/deploy/connect/build/edit/check a system), or "
    "'chat' if it is conversation/greeting/question-about-you. Reply with ONLY the "
    "one word."
)


@dataclass(frozen=True)
class IntentDecision:
    intent: str          # 'task' | 'chat'
    reason: str
    fallback: bool = False


def classify_intent(message: str, *, client) -> IntentDecision:
    """Classify a message as 'task' or 'chat' using the given client.

    Always fails safe to 'task' on any error or unrecognized response.
    The client must implement .chat(messages, max_tokens, temperature).
    """
    try:
        resp = client.chat(
            messages=[{"role": "system", "content": _PROMPT},
                      {"role": "user", "content": message}],
            max_tokens=4, temperature=0.0,
        )
        word = (getattr(resp, "content", "") or "").strip().lower()
        if word.startswith("task"):
            return IntentDecision("task", f"model:{word!r}")
        if word.startswith("chat"):
            return IntentDecision("chat", f"model:{word!r}")
        # Unrecognized -> fail safe to task (never silently chat).
        return IntentDecision("task", f"model-unrecognized:{word!r}", fallback=True)
    except Exception as exc:  # offline / error -> fail safe to TASK
        return IntentDecision("task", f"intent-model-unreachable:{exc}", fallback=True)


@dataclass(frozen=True)
class TurnRoute:
    intent: str               # 'task' | 'chat'
    chat_draft: str | None    # the ready draft when intent=='chat', else None
    decision: IntentDecision


def route_turn(message: str, *, intent_fn, chat_draft_fn) -> TurnRoute:
    """Run the intent check and a speculative chat draft IN PARALLEL.

    chat -> return the ready draft (zero added latency).
    task -> drop the draft.

    The chat draft must be side-effect-free (tools=[]).
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_intent = pool.submit(intent_fn, message)
        f_chat = pool.submit(chat_draft_fn, message)
        decision = f_intent.result()
        if decision.intent == "chat":
            return TurnRoute("chat", f_chat.result(), decision)
        # task: we still wait for the (cheap) draft thread to finish, then drop it
        try:
            f_chat.result(timeout=0)
        except Exception:
            pass
        return TurnRoute("task", None, decision)
