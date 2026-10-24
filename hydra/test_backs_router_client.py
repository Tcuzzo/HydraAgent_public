"""Hydra talks to the BACKS router (backs.router.v1, docs/BACKS_ROUTER_CONTRACT.md).

Cloud rungs resolve through POST /api/router/resolve, plain turns are answered by
POST /api/router/complete (BACKS walks the chain and records outcomes), tool turns go to
the gateway capability and Hydra reports the outcome. Hydra falls to its own local GPU
rung only when BACKS itself is unreachable or has no rung left. No model is named here:
every rung and model below is a placeholder.
"""
from __future__ import annotations

import pytest

from hydra import model_routing, rung_resolver
from hydra.llm import ChatMessage
from hydra.rung_resolver import GATEWAY, RungGatewayClient, RungResolutionError

BASE = "http://backs.test:8000"


@pytest.fixture(autouse=True)
def _backs_here(monkeypatch):
    monkeypatch.setenv("HYDRA_BACKS_URL", BASE)
    for name in ("_DOWN", "_CHAINS"):
        getattr(rung_resolver, name, []).clear()
    yield
    getattr(rung_resolver, "_DOWN", []).clear()


def _chain(*rungs):
    return {"contract": "backs.router.v1", "chain": [
        {"rung": r, "model": f"model-of-{r}", "tier": "cloud", "usable": u} for r, u in rungs]}


def test_cloud_rung_resolves_through_the_backs_router(capsys):
    calls = []

    def post(url, body, timeout):
        calls.append((url, body))
        return 200, _chain(("rung-a", False), ("rung-b", True))

    assert rung_resolver.resolve("plan", task="plan", post=post) == (GATEWAY, "router/plan/plan")
    assert calls[0][0] == BASE + "/api/router/resolve"
    assert calls[0][1]["task_type"] == "plan"
    err = capsys.readouterr().err
    assert "BACKS router task plan" in err and "rung rung-b" in err and "model model-of-rung-b" in err


def test_router_with_no_usable_rung_is_a_retryable_miss():
    with pytest.raises(RungResolutionError):
        rung_resolver.resolve("plan", task="plan", post=lambda *a: (200, _chain(("rung-a", False))))


def test_backs_unreachable_falls_to_the_local_rung_without_retrying_each_rung():
    def down(url, body, timeout):
        rung_resolver._mark_down("unreachable")
        raise RungResolutionError("unreachable")

    with pytest.raises(RungResolutionError):
        rung_resolver.resolve("plan", task="plan", post=down)
    with pytest.raises(RungResolutionError, match="retried"):
        rung_resolver._check_down()


def test_routing_entries_carry_router_tasks():
    routing = model_routing.load_routing(refresh=True)
    cloud = [e for e in routing.roster if not e.local]
    assert cloud and all(e.task for e in cloud)
    assert routing.role_entry("independent_auditor").task == "grade"


def test_plain_turn_is_answered_by_router_complete(capsys):
    getattr(rung_resolver, "_CHAINS", {})["answer"] = [{"rung": "rung-b", "model": "model-of-rung-b"}]
    sent = []

    def post(url, body, timeout):
        sent.append((url, body, timeout))
        return 200, {"ok": True, "rung": "rung-b", "tier": "cloud", "answer": "pong",
                     "attempts": [{"rung": "rung-a", "ok": False, "error_class": "quota"},
                                  {"rung": "rung-b", "ok": True}]}

    client = RungGatewayClient(endpoint=BASE)
    client.post = post
    resp = client.chat([ChatMessage("system", "be brief"), ChatMessage("user", "ping")],
                       model="router/answer/work")
    assert resp.content == "pong" and resp.model == "rung-b"
    url, body, timeout = sent[0]
    assert url == BASE + "/api/router/complete" and body["task_type"] == "answer"
    assert "ping" in body["task"] and body["tools"] == []
    assert timeout >= rung_resolver.ROUTER_MIN_TIMEOUT
    err = capsys.readouterr().err
    assert "rung work -> BACKS router task answer" in err and "model model-of-rung-b" in err
    assert "fell through rung-a:quota" in err


def test_router_with_nothing_left_raises_so_hydra_takes_the_local_rung():
    client = RungGatewayClient(endpoint=BASE)
    client.post = lambda *a: (503, {"ok": False, "error": "no_rung_reachable", "message": "No rung could answer."})
    with pytest.raises(RungResolutionError, match="No rung could answer"):
        client.chat([ChatMessage("user", "ping")], model="router/answer/work")
    with pytest.raises(RungResolutionError):
        rung_resolver._check_down()


def test_tool_turn_uses_the_gateway_and_reports_the_outcome(monkeypatch):
    from hydra.llm import ChatResponse, OllamaClient

    reported = []
    monkeypatch.setattr(rung_resolver, "_get", lambda url, t: {"data": [{"id": "backs/x-work", "display_name": "Work"}]})

    def fake_chat(self, messages, *, model, **kw):
        assert model == "backs/x-work" and kw["tools"]
        self.last_receipt = {"x-backs-identity": "rung-b"}
        return ChatResponse(content="", model=model, finish_reason="tool_calls",
                            prompt_tokens=3, completion_tokens=4, raw={})

    monkeypatch.setattr(OllamaClient, "chat", fake_chat)
    client = RungGatewayClient(endpoint=BASE)
    client.post = lambda url, body, timeout: (reported.append((url, body)) or (200, {"ok": True}))
    client.chat([ChatMessage("user", "read a file")], model="router/answer/work",
                tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}])
    url, body = reported[0]
    assert url == BASE + "/api/router/outcome"
    assert body["task_type"] == "answer" and body["rung"] == "rung-b" and body["success"] is True
    assert body["source"] == "hydra"


def test_outcome_report_never_raises():
    def boom(*a):
        raise RungResolutionError("down")

    assert rung_resolver.report_outcome(BASE, {"task_type": "answer", "rung": "r", "success": True}, post=boom) is False
