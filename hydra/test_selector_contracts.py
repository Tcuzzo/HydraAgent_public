"""Boundary regressions: native predictors propose data, never executable defaults."""
import copy

import pytest

from hydra.decision_models import LayaDecisionClient
from hydra.llm import ChatMessage, LlmError
from hydra.needle_client import NeedleClient


QUESTIONS = {
    "route": {"type": "choice", "instructions": "Choose a route", "criteria": {"read": "Read source", "edit": "Edit source"}},
    "urgency": {"type": "score", "criteria": ["low", "medium", "high"]},
    "retry": {"type": "noul", "instructions": "Should this be retried?"},
}
ANSWERS = {
    "route": {"type": "choice", "choice": "read", "confidence": 0.8, "probabilities": {"read": 0.9, "edit": 0.1}},
    "urgency": {"type": "score", "score": 1.2, "confidence": 0.5, "probabilities": {"0": 0.1, "1": 0.6, "2": 0.3}},
    "retry": {"type": "noul", "noul": 0.8},
}


@pytest.mark.parametrize("answer,field,value", [
    ("route", "type", "noul"), ("route", "choice", "delete"),
    ("route", "confidence", 2), ("route", "probabilities", {"read": 0.8, "delete": 0.2}),
    ("route", "probabilities", {"read": 0.9, "edit": 0.9}),
    ("urgency", "score", 3), ("urgency", "score", float("nan")),
    ("retry", "noul", -1), ("retry", "noul", True),
])
def test_laya_rejects_malformed_typed_answers(monkeypatch, answer, field, value):
    answers = copy.deepcopy(ANSWERS)
    answers[answer][field] = value
    monkeypatch.setattr("hydra.decision_models.predict_local", lambda *a, **kw: {"answers": answers})
    with pytest.raises(LlmError):
        LayaDecisionClient().predict("Source state", QUESTIONS)


@pytest.mark.parametrize("question", [
    {"type": "choice"}, {"type": "choice", "criteria": {}},
    {"type": "choice", "criteria": ["read"]},
    {"type": "score", "criteria": []}, {"type": "score", "criteria": {"0": "low"}},
])
def test_bad_question_rejected_before_starting_model(monkeypatch, question):
    def unexpected(*args, **kwargs):
        pytest.fail("invalid question reached model process")
    monkeypatch.setattr("hydra.decision_models.predict_local", unexpected)
    with pytest.raises(LlmError):
        LayaDecisionClient().predict("Source state", {"route": question})


def test_truncated_decisions_are_not_reported_complete(monkeypatch):
    monkeypatch.setattr("hydra.decision_models.predict_local", lambda *a, **kw: {
        "answers": copy.deepcopy(ANSWERS), "usage": {"truncated": True, "state_tokens_dropped": 100},
    })
    with pytest.raises(LlmError, match="truncat"):
        LayaDecisionClient().predict("Source state", QUESTIONS)


def test_kev_uses_distinct_backend_and_pinned_small_checkpoint(monkeypatch):
    from hydra.decision_models import KevDecisionClient
    requests = []
    def predict(request, **kwargs):
        requests.append(request)
        return {"answers": copy.deepcopy(ANSWERS)}
    monkeypatch.setattr("hydra.decision_models.predict_local", predict)
    monkeypatch.delenv("HYDRA_KEV_MODEL", raising=False)
    client = KevDecisionClient()
    assert client.predict("Source state", QUESTIONS)["answers"] == ANSWERS
    assert requests[0]["backend"] == "kev"
    assert requests[0]["model"] == "jaredpalmer/kev-0.8b@v1.0"
    assert requests[0]["device"] == "cpu"


def test_needle_respects_official_negation_failure(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setattr("hydra.needle_client.predict_local", lambda *a, **kw: {
        "success": True, "function_calls": [{"name": "delete", "arguments": {"path": "a.txt"}}],
        "validation": {"negation": True},
    })
    with pytest.raises(LlmError, match="negat"):
        NeedleClient().chat([ChatMessage("user", "Do not delete a.txt")], model="needle3", tools=[{
            "name": "delete", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }])


def test_needle_validates_tool_arguments_against_supplied_schema(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setattr("hydra.needle_client.predict_local", lambda *a, **kw: {
        "success": True, "function_calls": [{"name": "read", "arguments": {}}],
    })
    with pytest.raises(LlmError, match="arguments"):
        NeedleClient().chat([ChatMessage("user", "Read source")], model="needle3", tools=[{
            "name": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }])


def test_needle_shared_checkpoint_environment(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setenv("NEEDLE_MODEL", "/configured/shared.cact")
    monkeypatch.delenv("HYDRA_NEEDLE_MODEL", raising=False)
    assert NeedleClient().model == "/configured/shared.cact"


def test_needle_rejects_non_object_schema_before_worker(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setattr("hydra.needle_client.predict_local", lambda *a, **kw: pytest.fail("invalid schema reached worker"))
    with pytest.raises(LlmError):
        NeedleClient().chat([ChatMessage("user", "read")], model="needle3", tools=[{"function": "read"}])
