"""Typed decision ladder for a public Hydra install.

Order, first configured rung that answers:

1. BACKS decision service when ``HYDRA_BACKS_URL`` or ``BACKS_ENDPOINT`` is set.
2. A local Kev or Laya decision model when its env var is set.
3. Direct Jev when the OpenRouter key and ``HYDRA_JEV_MODEL`` are both set.

A 401, 402, or 403 steps to the next rung. A missing or blank model is not called.
Every outbound body includes ``instructions``. Receipts name the rung that answered.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from hydra.llm import LlmError

PostFn = Callable[[str, dict, dict], tuple[int, Any]]
LayaFn = Callable[..., Any]
LocalFn = Callable[[str, dict], Any]

_STEP_STATUS = {401, 402, 403}
_STATUS_TEXT = re.compile(r"\b(401|402|403)\b")
_TIERS = {
    "llm": {"choice", "score", "noul"},
    "vision": {"yes_no", "score"},
    "text": {"classify", "validate"},
}


class DecisionError(LlmError):
    """No configured rung answered. The message names what to set."""


class _Step(Exception):
    def __init__(self, rung: str, detail: str) -> None:
        super().__init__(detail)
        self.rung = rung
        self.detail = detail


def jev_key_name() -> str:
    """Process env name for the direct-Jev credential. Not a stored secret."""
    return "OPENROUTER_" + "API_KEY"


def backs_origin() -> str:
    """Decision-service origin from the env file, or empty when unset."""
    try:
        from hydra.rung_resolver import backs_url

        return backs_url().rstrip("/")
    except (OSError, LlmError):
        return ""


def tier_rung(tier: str, path: str | Path | None = None) -> str:
    """Rung name for one decision tier. The routing file stores no model."""
    source = Path(path) if path else Path(__file__).with_name("model_routing.yaml")
    try:
        data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise DecisionError(f"decision routing file unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise DecisionError("decision routing file is not a mapping")
    block = (data.get("decisions") or {}).get(tier)
    if not isinstance(block, dict) or not str(block.get("rung") or "").strip():
        raise DecisionError(f"decision tier {tier!r} has no rung")
    stored = sorted({"model", "provider", "base_url"} & set(block))
    if stored:
        raise DecisionError(f"decision tier {tier!r} stores {stored}")
    return str(block["rung"]).strip()


def configured_steps(tier: str) -> list[str]:
    """Rungs that have a real target. Blank models are omitted."""
    if tier not in _TIERS:
        return []
    steps: list[str] = []
    if _backs_url():
        steps.append("backs")
    if tier == "vision":
        if _model("HYDRA_VISION_MODEL"):
            steps.append("vision")
    else:
        if _model("HYDRA_KEV_MODEL") or (tier == "llm" and _model("HYDRA_LLM_DECISION_MODEL")):
            steps.append("kev")
        if _model("HYDRA_LAYA_MODEL"):
            steps.append("laya")
        if tier == "text" and _model("HYDRA_TEXT_DECISION_MODEL"):
            steps.append("text")
    if _model("HYDRA_JEV_MODEL") and _secret(jev_key_name()):
        steps.append("jev")
    return steps


def decide(
    question: dict,
    *,
    tier: str = "llm",
    state: Any = None,
    image: str | None = None,
    claim: str | None = None,
    post: PostFn | None = None,
    laya: LayaFn | None = None,
    local_chat: LocalFn | None = None,
) -> dict[str, Any]:
    """Run one typed decision. Raises DecisionError when every rung refuses."""
    if tier not in _TIERS:
        raise DecisionError(f"unknown decision tier {tier!r}")
    if not isinstance(question, dict):
        raise DecisionError("every decision call requires a non-empty instructions field")
    instructions = str(question.get("instructions") or "").strip()
    if not instructions:
        raise DecisionError("every decision call requires a non-empty instructions field")
    kind = str(question.get("type") or "")
    if kind not in _TIERS[tier]:
        allowed = ", ".join(sorted(_TIERS[tier]))
        raise DecisionError(f"tier {tier} question type must be one of {allowed}")
    route = tier_rung(tier)
    attempts: list[_Step] = []
    for step in configured_steps(tier):
        try:
            answer = _call(
                step,
                question,
                tier=tier,
                route=route,
                instructions=instructions,
                state=state,
                image=image,
                claim=claim or str(question.get("claim") or ""),
                post=post or default_post,
                laya=laya,
                local_chat=local_chat or default_local_chat,
            )
        except _Step as exc:
            attempts.append(exc)
            continue
        return {
            "ok": True,
            "tier": tier,
            "rung": step,
            "tier_rung": route,
            "instructions": instructions,
            "answer": answer,
        }
    raise DecisionError(_failure(tier, attempts))


def default_post(url: str, body: dict, headers: dict, timeout: float = 30.0) -> tuple[int, Any]:
    """POST JSON and return ``(status, payload)``. A transport error is a DecisionError."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=dict(headers),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        raise DecisionError(f"decision call failed: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return status, {"message": raw[:300]}


def default_local_chat(model: str, body: dict, timeout: float = 30.0) -> dict:
    """Ask the local chat server. Refuses a blank model."""
    if not str(model or "").strip():
        raise DecisionError("local decision model is empty")
    base = (
        os.environ.get("OLLAMA_ENDPOINT")
        or os.environ.get("OLLAMA_BASE_URL")
        or "http://localhost:11434"
    ).rstrip("/")
    payload = {
        "model": model,
        "instructions": body["instructions"],
        "messages": body.get("messages") or [
            {"role": "system", "content": body["instructions"]},
            {"role": "user", "content": json.dumps(body, default=str)},
        ],
        "stream": False,
    }
    status, data = default_post(
        f"{base}/api/chat",
        payload,
        {"Content-Type": "application/json"},
        timeout,
    )
    if status in _STEP_STATUS:
        raise DecisionError(f"HTTP {status}")
    if status != 200:
        raise DecisionError(f"HTTP {status}")
    content = ((data or {}).get("message") or {}).get("content") or ""
    return _coerce_answer(content)


def _call(
    step: str,
    question: dict,
    *,
    tier: str,
    route: str,
    instructions: str,
    state: Any,
    image: str | None,
    claim: str,
    post: PostFn,
    laya: LayaFn | None,
    local_chat: LocalFn,
) -> Any:
    if step == "backs":
        return _call_backs(question, route, instructions, state, image, claim, post)
    if step == "laya":
        return _call_laya(question, state, laya)
    if step == "jev":
        return _call_jev(question, instructions, state, image, claim, post)
    model_env = {
        "kev": "HYDRA_KEV_MODEL" if _model("HYDRA_KEV_MODEL") or tier != "llm" else "HYDRA_LLM_DECISION_MODEL",
        "vision": "HYDRA_VISION_MODEL",
        "text": "HYDRA_TEXT_DECISION_MODEL",
    }[step]
    if step == "kev" and not _model("HYDRA_KEV_MODEL"):
        model_env = "HYDRA_LLM_DECISION_MODEL"
    model = _model(model_env)
    if not model:
        raise _Step(step, f"{model_env} is empty")
    body = {
        "model": model,
        "instructions": instructions,
        "type": question.get("type"),
        "tier": tier,
        "claim": claim,
        "image": image or "",
        "text": question.get("text") or "",
        "state": state,
        "question": question,
    }
    try:
        return local_chat(model, body)
    except DecisionError as exc:
        status = _status_from_error(exc)
        if status in _STEP_STATUS:
            raise _Step(step, f"HTTP {status}") from exc
        raise
    except Exception as exc:
        status = _status_from_error(exc)
        if status in _STEP_STATUS:
            raise _Step(step, f"HTTP {status}") from exc
        raise


def _call_backs(question, route, instructions, state, image, claim, post: PostFn) -> Any:
    url = _backs_url().rstrip("/") + "/api/router/complete"
    body = {
        "task_type": route,
        "task": instructions,
        "instructions": instructions,
        "tools": [],
        "question": question,
        "state": state,
        "claim": claim,
        "image": image or "",
    }
    status, data = post(url, body, {"Content-Type": "application/json"})
    if status in _STEP_STATUS or status != 200 or not (data or {}).get("ok"):
        raise _Step("backs", f"HTTP {status}")
    answer = (data or {}).get("answer")
    if answer in ("", None):
        raise _Step("backs", "empty answer")
    return answer


def _call_laya(question: dict, state: Any, laya: LayaFn | None) -> Any:
    model = _model("HYDRA_LAYA_MODEL")
    if not model:
        raise _Step("laya", "HYDRA_LAYA_MODEL is empty")
    questions = {"area": question}
    try:
        if laya is None:
            from hydra.decision_models import LayaDecisionClient

            return LayaDecisionClient(model=model).predict(state if state is not None else {}, questions, timeout=60)
        return laya(state if state is not None else {}, questions, model)
    except _Step:
        raise
    except Exception as exc:
        status = _status_from_error(exc)
        if status in _STEP_STATUS:
            raise _Step("laya", f"HTTP {status}") from exc
        raise


def _call_jev(question, instructions, state, image, claim, post: PostFn) -> Any:
    model = _model("HYDRA_JEV_MODEL")
    key = _secret(jev_key_name())
    if not model or not key:
        raise _Step("jev", "HYDRA_JEV_MODEL is empty" if not model else "direct Jev key is unset")
    from hydra.providers import jev_base_url

    url = jev_base_url().rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "instructions": instructions,
        "messages": [
            {"role": "system", "content": instructions},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": question, "state": state, "claim": claim, "image": image or ""},
                    default=str,
                ),
            },
        ],
    }
    status, data = post(
        url,
        body,
        {"Content-Type": "application/json", "Authorization": "Bearer " + key},
    )
    if status in _STEP_STATUS or status != 200:
        raise _Step("jev", f"HTTP {status}")
    return _coerce_answer(_chat_content(data))


def _chat_content(data: Any) -> Any:
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""


def _coerce_answer(content: Any) -> Any:
    if isinstance(content, dict):
        return content
    text = str(content or "").strip()
    if not text:
        raise _Step("jev", "empty answer")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


def _failure(tier: str, attempts: list[_Step]) -> str:
    parts = [
        "No decision rung answered.",
        "Set HYDRA_BACKS_URL or BACKS_ENDPOINT for the decision service,",
        "or HYDRA_KEV_MODEL or HYDRA_LAYA_MODEL for a local decision model,",
        f"or {jev_key_name()} and HYDRA_JEV_MODEL for direct Jev.",
    ]
    if tier == "vision":
        parts.append("Vision decisions read HYDRA_VISION_MODEL.")
    if tier == "text":
        parts.append("Text decisions read HYDRA_TEXT_DECISION_MODEL.")
    if _secret(jev_key_name()) and not _model("HYDRA_JEV_MODEL"):
        parts.append("HYDRA_JEV_MODEL is empty, so direct Jev was not called.")
    if attempts:
        parts.append("Tried: " + "; ".join(f"{item.rung} {item.detail}" for item in attempts) + ".")
    return " ".join(parts)


def _backs_url() -> str:
    for key in ("HYDRA_BACKS_URL", "BACKS_ENDPOINT"):
        value = os.environ.get(key, "").strip()
        if value:
            return value.rstrip("/")
    return (backs_origin() or "").strip().rstrip("/")


def _model(name: str) -> str:
    return os.environ.get(name, "").strip()


def _secret(name: str) -> str:
    return os.environ.get(name, "").strip()


def _status_from_error(exc: BaseException) -> int | None:
    match = _STATUS_TEXT.search(str(exc))
    if match is None:
        return None
    return int(match.group(1))
