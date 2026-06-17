"""hydra.intent_intake — model-based INTENT routing (not keyword matching).

The operator prefers intent reasoning over keyword matching: a small fast model
judges whether the operator is directing WORK or just talking, and the agent acts
on that judgment. This is the PRIMARY classifier for the live operator path.

The judge model + client are passed in by the live wiring (operator: use the cloud
roster, not local). Note: a reasoning model needs token room before its visible
answer, so the judge call must not starve max_tokens (256 here), or it returns empty.

Guarantees:
  - Peer sources route to collab deterministically (reliable, no model needed).
  - Model unreachable / no client → falls back to the keyword rules (hydra.intake)
    rather than crashing — and even the fallback never gags work.
  - Unrecognized / unsure reply FAILS SAFE to steering (work). A work request is
    never silently dropped into the 1-iteration convo profile.
"""
from __future__ import annotations

from hydra.intake import COLLAB, CONVO, STEERING, Classification
from hydra.intake import classify as keyword_classify

_INTENT_PROMPT = (
    "Route the operator's message by INTENT — reason about what they actually want, "
    "do not pattern-match keywords. Reply with ONLY one word:\n"
    "- work: they want you to DO something (build / fix / run / check / investigate / "
    "continue a task), OR a question that needs you to check the real system or your "
    "own work to answer truthfully (e.g. \"what's stopping you from building X\", "
    "\"is the dashboard done\", \"why isn't it working\").\n"
    "- chat: pure conversation — greeting, small talk, an opinion, or a question you can "
    "answer from general knowledge without touching the system.\n"
    "When unsure, choose work — act, don't gag. Reply with ONLY: work or chat."
)


def _is_peer(source: str) -> bool:
    return source.startswith("peer:")


def classify(
    text: str,
    *,
    source: str = "operator",
    client=None,
    model: str | None = None,
) -> Classification:
    """Judge intent with the model; fail safe to steering; keyword fallback offline."""
    if _is_peer(source):
        return keyword_classify(text, source=source)  # collab routing is deterministic
    if client is None or not model:
        return keyword_classify(text, source=source)  # offline fallback
    try:
        from hydra.llm import ChatMessage

        resp = client.chat(
            [ChatMessage("system", _INTENT_PROMPT), ChatMessage("user", text)],
            model=model,
            max_tokens=256,
            temperature=0.0,
            timeout=20.0,
        )
        word = (getattr(resp, "content", "") or "").strip().lower()
        if word.startswith("chat"):
            return Classification(CONVO, "intent:chat", "model judged this as conversation")
        if word.startswith("work"):
            return Classification(STEERING, "intent:work", "model judged this as a work directive")
        # Empty / unrecognized -> fail safe to work (never gag a possible work request).
        return Classification(
            STEERING, "intent:unrecognized", f"unrecognized intent reply {word!r}; fail-safe to work"
        )
    except Exception as exc:  # model down -> keyword rules (still never gags work)
        out = keyword_classify(text, source=source)
        return Classification(
            out.kind, f"intent-fallback:{out.rule_id}", f"intent model unreachable ({exc}); used keyword rules"
        )
