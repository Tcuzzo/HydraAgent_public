"""Key-only Jev decisions and the three typed tiers.

No network. Transports are injected. Model ids come from the environment
under test, never from a literal the ladder would send on its own.
"""
from __future__ import annotations

import json

import pytest

from hydra.decision_ladder import (
    DecisionError,
    decide,
    jev_key_name,
    tier_rung,
)
from hydra.llm import ChatResponse, LlmError
from hydra.loop import AgentLoop


def _question(**extra):
    payload = {
        "type": "choice",
        "instructions": "Which subsystem should handle this task?",
        "criteria": {"models": "provider configuration", "tools": "file and shell"},
    }
    payload.update(extra)
    return payload


def _isolate(monkeypatch):
    for name in (
        "HYDRA_BACKS_URL",
        "BACKS_ENDPOINT",
        "HYDRA_KEV_MODEL",
        "HYDRA_LAYA_MODEL",
        "HYDRA_LLM_DECISION_MODEL",
        "HYDRA_VISION_MODEL",
        "HYDRA_TEXT_DECISION_MODEL",
        "HYDRA_JEV_MODEL",
        "HYDRA_JEV_BASE_URL",
        jev_key_name(),
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "hydra.decision_ladder.backs_origin",
        lambda: "",
    )


def test_missing_instructions_never_calls_a_transport(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_JEV_MODEL", "from-env")
    monkeypatch.setenv(jev_key_name(), "test-key")
    called = {"n": 0}

    def post(url, body, headers):
        called["n"] += 1
        return 200, {"choices": [{"message": {"content": "nope"}}]}

    with pytest.raises(DecisionError, match="instructions"):
        decide({"type": "choice", "criteria": {"a": "b"}}, post=post)
    assert called["n"] == 0


def test_backs_answers_first_and_the_receipt_names_that_rung(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_BACKS_URL", "http://backs.example")
    monkeypatch.setenv("HYDRA_JEV_MODEL", "from-env")
    monkeypatch.setenv(jev_key_name(), "test-key")
    seen = {}

    def post(url, body, headers):
        seen["url"] = url
        seen["body"] = body
        seen["headers"] = headers
        return 200, {"ok": True, "answer": {"choice": "models"}, "rung": "backs"}

    receipt = decide(_question(), post=post)
    assert receipt["rung"] == "backs"
    assert receipt["tier"] == "llm"
    assert receipt["tier_rung"] == tier_rung("llm")
    assert receipt["answer"]["choice"] == "models"
    assert seen["url"] == "http://backs.example/api/router/complete"
    assert seen["body"]["instructions"] == _question()["instructions"]
    assert "Authorization" not in seen["headers"]
    assert seen["body"].get("model") not in ("", None) or "model" not in seen["body"]


def test_auth_statuses_step_to_the_next_configured_rung(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_BACKS_URL", "http://backs.example")
    monkeypatch.setenv("HYDRA_LAYA_MODEL", "local-checkpoint")
    monkeypatch.setenv("HYDRA_JEV_MODEL", "from-env")
    monkeypatch.setenv(jev_key_name(), "test-key")
    order = []

    def post(url, body, headers):
        order.append(url)
        if url.endswith("/api/router/complete"):
            assert body["instructions"]
            return 401, {"error": "refused"}
        assert body["model"] == "from-env"
        assert body["instructions"] == _question()["instructions"]
        assert body["model"]
        return 200, {"choices": [{"message": {"content": json.dumps({"choice": "tools"})}}]}

    def laya(state, questions, model):
        order.append("laya")
        assert model == "local-checkpoint"
        assert questions["area"]["instructions"]
        raise LlmError("HTTP 402 budget")

    receipt = decide(_question(), post=post, laya=laya)
    assert order[0].endswith("/api/router/complete")
    assert order[1] == "laya"
    assert order[2].endswith("/chat/completions")
    assert receipt["rung"] == "jev"
    assert receipt["answer"]["choice"] == "tools"


def test_empty_jev_model_is_never_called(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv(jev_key_name(), "test-key")
    monkeypatch.setenv("HYDRA_JEV_MODEL", "   ")
    called = {"n": 0}

    def post(url, body, headers):
        called["n"] += 1
        return 200, {}

    with pytest.raises(DecisionError, match="HYDRA_JEV_MODEL"):
        decide(_question(), post=post)
    assert called["n"] == 0


def test_nothing_configured_names_each_way_to_set_a_rung(monkeypatch):
    _isolate(monkeypatch)
    with pytest.raises(DecisionError) as exc:
        decide(_question(), post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")))
    text = str(exc.value)
    assert "HYDRA_BACKS_URL" in text
    assert "BACKS_ENDPOINT" in text
    assert "HYDRA_LAYA_MODEL" in text
    assert "HYDRA_KEV_MODEL" in text
    assert jev_key_name() in text
    assert "HYDRA_JEV_MODEL" in text


def test_vision_tier_sends_the_claim_and_names_its_rung(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_VISION_MODEL", "from-env")
    seen = {}

    def local_chat(model, body):
        seen["model"] = model
        seen["body"] = body
        return {"yes_no": "yes", "score": 0.8}

    receipt = decide(
        {
            "type": "yes_no",
            "instructions": "Does the image show the claimed screen?",
            "claim": "the login form is visible",
        },
        tier="vision",
        image="frame.png",
        claim="the login form is visible",
        local_chat=local_chat,
    )
    assert receipt["rung"] == "vision"
    assert receipt["tier_rung"] == "vision_decision"
    assert seen["model"] == "from-env"
    assert seen["body"]["instructions"]
    assert seen["body"]["claim"] == "the login form is visible"
    assert seen["body"]["image"] == "frame.png"


def test_text_tier_validates_structured_output(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_TEXT_DECISION_MODEL", "from-env")

    def local_chat(model, body):
        assert model == "from-env"
        assert body["instructions"]
        assert body["text"] == "{\"ok\": true}"
        return {"valid": True}

    receipt = decide(
        {
            "type": "validate",
            "instructions": "Is this JSON the required object?",
            "text": "{\"ok\": true}",
        },
        tier="text",
        local_chat=local_chat,
    )
    assert receipt["rung"] == "text"
    assert receipt["tier_rung"] == "text_decision"
    assert receipt["answer"]["valid"] is True


def test_reflex_decider_asks_the_llm_tier_before_the_big_loop(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_REFLEX", "1")
    monkeypatch.setenv("HYDRA_JEV_MODEL", "from-env")
    monkeypatch.setenv(jev_key_name(), "test-key")
    seen = {}

    def fake_decide(question, *, tier="llm", **kwargs):
        seen["tier"] = tier
        seen["instructions"] = question.get("instructions")
        return {
            "ok": True,
            "rung": "jev",
            "tier": tier,
            "tier_rung": "llm_decision",
            "instructions": question.get("instructions"),
            "answer": {
                "schema": "hydra.reflex.decision.v1",
                "next_tool": None,
                "done": True,
                "retry": False,
                "escalate": False,
                "reason": "settled",
            },
        }

    monkeypatch.setattr("hydra.decision_ladder.decide", fake_decide)

    class _Client:
        def chat(self, *_args, **_kwargs):
            raise AssertionError("big model ran")

    result = AgentLoop(_Client(), model="route-token").run("is the direction right")
    assert seen["tier"] == "llm"
    assert seen["instructions"]
    assert result.halted_reason == "reflex_done"
    assert "settled" in result.final_response


def test_second_big_loop_is_gated_by_another_decision(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_REFLEX", "1")
    answers = iter([
        {
            "schema": "hydra.reflex.decision.v1",
            "next_tool": None,
            "done": False,
            "retry": False,
            "escalate": True,
            "reason": "need the model once",
        },
        {
            "schema": "hydra.reflex.decision.v1",
            "next_tool": None,
            "done": True,
            "retry": False,
            "escalate": False,
            "reason": "stop before the second loop",
        },
    ])

    def decider(**_kwargs):
        return next(answers)

    monkeypatch.setattr("hydra.reflex_seam.resolve_route", lambda rung: "router/decide/reflex")
    monkeypatch.setattr("hydra.reflex_seam.default_decider", decider)
    chats = {"n": 0}

    class _Client:
        def chat(self, *_args, **_kwargs):
            chats["n"] += 1
            return ChatResponse(
                content="partial",
                model="route-token",
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[type("T", (), {
                    "id": "1",
                    "name": "missing",
                    "arguments_raw": "{}",
                    "arguments": {},
                })()],
            )

    result = AgentLoop(_Client(), model="route-token").run("keep going", max_iterations=4)
    assert chats["n"] == 1
    assert result.halted_reason == "reflex_done"


def test_403_on_the_last_rung_does_not_invent_a_model(monkeypatch):
    _isolate(monkeypatch)
    monkeypatch.setenv("HYDRA_JEV_MODEL", "from-env")
    monkeypatch.setenv(jev_key_name(), "test-key")

    def post(url, body, headers):
        assert body["model"] == "from-env"
        return 403, {"error": "forbidden"}

    with pytest.raises(DecisionError, match="403") as exc:
        decide(_question(), post=post)
    assert "from-env" not in str(exc.value) or "HYDRA_JEV_MODEL" in str(exc.value)
    assert "None" not in str(exc.value)
