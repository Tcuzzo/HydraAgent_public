"""skills.browse_agent — in-process agentic browsing on HydraAgent's own browser.

Give it a natural-language goal; it loops **snapshot -> decide -> act**, driving
`skills.browser` directly, steered by a HydraAgent model (local Ollama by default,
cloud for harder tasks). Bounded by `max_steps` (operator's risky-not-destructive law:
tighter scope + capped iterations, NOT a gate). No browser-use, no second browser stack.

The decision step is injectable (`decide`) so the loop is unit-tested with a real
browser but scripted decisions.

Maturity: SCAFFOLDED (2026-06-10).
"""
from __future__ import annotations

import json
from typing import Any, Callable

from skills import browser

SYSTEM = (
    "You drive a web browser to accomplish a goal. Each turn respond with ONE JSON "
    "object and NOTHING else: {\"action\": <navigate|snapshot|get_text|click|type|finish>, ...}. "
    "navigate needs \"url\"; click needs \"target\" (visible text or a CSS selector); "
    "type needs \"selector\" and \"text\"; finish needs \"answer\". "
    "Call snapshot to see the page before you act. Finish as soon as you can answer the goal."
)


def _execute(action: dict) -> str:
    """Run one browser action; return a short text observation."""
    act = action.get("action")
    if act == "navigate":
        r = browser.navigate(action.get("url", ""))
        return f"navigated ok={r.get('ok')} title={r.get('title')!r} {r.get('error','')}".strip()
    if act == "snapshot":
        r = browser.snapshot()
        return r["snapshot"] if r.get("ok") else f"snapshot error: {r.get('error')}"
    if act == "get_text":
        r = browser.get_text()
        return r["text"] if r.get("ok") else f"get_text error: {r.get('error')}"
    if act == "click":
        r = browser.click(action.get("target", ""))
        return f"click ok={r.get('ok')} {r.get('error','')}".strip()
    if act == "type":
        r = browser.type_text(action.get("selector", ""), action.get("text", ""))
        return f"type ok={r.get('ok')} {r.get('error','')}".strip()
    return f"unknown action: {act!r}"


def _parse_action(text: str) -> dict:
    """Extract the first JSON object from model output; fall back to finishing."""
    text = (text or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict) and obj.get("action"):
                return obj
        except Exception:
            pass
    return {"action": "finish", "answer": text[:500]}


def _resolve_best_cloud():
    """The strongest reasoning model + an authed cloud client, from the SSOT
    (hydra/model_routing.yaml planner role; provider auth from env)."""
    from hydra import model_routing, providers

    model = model_routing.load_routing().role_entry("planner").model
    client, _cfg = providers.make_client("ollama-cloud")
    return client, model


def _default_decide(client, model: str) -> Callable[[str, list], dict]:
    from hydra.llm import ChatMessage

    def decide(task: str, history: list) -> dict:
        convo = [ChatMessage("system", SYSTEM), ChatMessage("user", f"GOAL: {task}")]
        for h in history[-6:]:
            convo.append(ChatMessage("assistant", json.dumps(h["action"])))
            convo.append(ChatMessage("user", f"OBSERVATION:\n{h['observation'][:1500]}"))
        resp = client.chat(convo, model=model)
        return _parse_action(resp.content)

    return decide


def run(
    task: str,
    *,
    decide: Callable[[str, list], dict] | None = None,
    model: str | None = None,
    client=None,
    max_steps: int = 12,
) -> dict:
    """Pursue `task` in the browser, bounded by `max_steps`.

    Steered by the strongest cloud model from the routing roster by default. Pass
    `model`/`client` to override, or `decide` to inject the decision step (tests).
    Returns {ok, stopped, steps, answer, transcript}. `stopped` is
    "finish" | "max_steps" | "error".
    """
    if decide is None:
        if client is None or model is None:
            best_client, best_model = _resolve_best_cloud()
            client = client or best_client
            model = model or best_model
        decide = _default_decide(client, model)
    history: list[dict[str, Any]] = []
    transcript: list[str] = []
    try:
        for step in range(1, max_steps + 1):
            action = decide(task, history) or {}
            if action.get("action") == "finish":
                browser.close()
                return {
                    "ok": True,
                    "stopped": "finish",
                    "steps": step,
                    "answer": action.get("answer", ""),
                    "transcript": "\n".join(transcript),
                }
            obs = _execute(action)
            history.append({"action": action, "observation": obs})
            transcript.append(f"[{action.get('action')}] {obs}")
        browser.close()
        return {
            "ok": False,
            "stopped": "max_steps",
            "steps": max_steps,
            "answer": "",
            "transcript": "\n".join(transcript),
        }
    except Exception as e:
        browser.close()
        return {
            "ok": False,
            "stopped": "error",
            "error": f"{type(e).__name__}: {str(e)[:300]}",
            "steps": len(history),
            "transcript": "\n".join(transcript),
        }
