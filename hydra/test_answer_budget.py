"""Bound checks for the answer budget: one per requirement. Fake clients only stand in for the provider wire;
the live proof is a real `hydra ask` from the serving tree."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from hydra.answer_budget import (AnswerBudgetClient, Rung, RungLedger, is_long, keeps_cutting_off, start_budget)
from hydra.llm import LlmError


class Fake:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, *, model=None, max_tokens=1024, **kw):
        self.calls.append({"model": model, "max_tokens": max_tokens})
        step = self.script.pop(0) if self.script else "ok"
        if isinstance(step, Exception):
            raise step
        finish = "length" if step == "cut" else "stop"
        return SimpleNamespace(content=f"{step}:{model}:{max_tokens}", finish_reason=finish, tool_calls=[],
                               completion_tokens=max_tokens if step == "cut" else 10, model=model)


MSG = [{"role": "user", "content": "hi"}]
LONG_Q = ("Explain in detail how a market maker hedges an options book, list each Greek and compare delta hedging "
          "versus gamma scalping with a worked example")


def _client(tmp_path, *fakes, **kw):
    rungs = [Rung("p", f"m{i}", client=f) for i, f in enumerate(fakes)]
    return AnswerBudgetClient(rungs, ledger=RungLedger(tmp_path), log=lambda m: None, **kw)


def test_cut_off_reply_moves_to_next_rung(tmp_path):
    a, b = Fake(["cut"]), Fake(["ok"])
    resp = _client(tmp_path, a, b).chat(MSG, model="m0", max_tokens=512)
    assert resp.finish_reason == "stop" and resp.model == "m1"
    assert len(a.calls) == 1, "a cut-off rung is not retried bigger while another rung waits"


def test_last_rung_retries_with_bigger_budget(tmp_path):
    a = Fake(["cut", "ok"])
    resp = _client(tmp_path, a, cap=4096).chat(MSG, max_tokens=512)
    assert [c["max_tokens"] for c in a.calls] == [512, 1024]
    assert resp.finish_reason == "stop"


def test_learned_allowance_starts_past_last_cut(tmp_path):
    a = Fake(["cut", "ok", "ok"])
    c = _client(tmp_path, a, cap=4096)
    c.chat(MSG, max_tokens=512)
    c2 = _client(tmp_path, Fake(["ok"]), cap=4096)
    c2.chat(MSG, max_tokens=512)
    assert c2._rungs[0]._client.calls[0]["max_tokens"] >= 1024
    assert start_budget({"cut_at": 512}, 512, 4096) == 1024
    assert start_budget({"cut_at": 512, "fit": 2048}, 512, 4096) == 2048


def test_long_question_routing(tmp_path):
    assert is_long(LONG_Q) and not is_long("what time is it")
    led = RungLedger(tmp_path)
    for _ in range(2):
        led.record("p/m0", {"max_tokens": 2048, "truncated": True}, long=True)
    assert keeps_cutting_off(led.lane("p/m0", long=True))
    a, b = Fake(["ok"]), Fake(["ok"])
    resp = _client(tmp_path, a, b).chat([{"role": "user", "content": LONG_Q}], max_tokens=512)
    assert resp.model == "m1" and not a.calls, "long questions skip a rung that keeps cutting off long answers"
    assert b.calls[0]["max_tokens"] == 1024, "long questions start with a bigger allowance"


def test_retired_model_walks_and_is_remembered(tmp_path):
    gone = LlmError('HTTP 410 from https://x/v1/chat/completions: {"error":"model was retired"}')
    a, b = Fake([gone]), Fake(["ok", "ok"])
    resp = _client(tmp_path, a, b).chat(MSG, max_tokens=512)
    assert resp.model == "m1"
    a2, b2 = Fake(["ok"]), Fake(["ok"])
    _client(tmp_path, a2, b2).chat(MSG, max_tokens=512)
    assert not a2.calls, "a retired rung is skipped on the next ask instead of failing again"


def test_auth_error_still_surfaces(tmp_path):
    a, b = Fake([LlmError("HTTP 401 from x: unauthorized")]), Fake(["ok"])
    with pytest.raises(LlmError):
        _client(tmp_path, a, b).chat(MSG)
    assert not b.calls


def test_sticky_rung_and_model_names_from_rungs(tmp_path):
    a, b = Fake(["cut"]), Fake(["ok", "ok"])
    c = _client(tmp_path, a, b)
    c.chat(MSG, model="m0", max_tokens=512)
    c.chat(MSG, model="m0", max_tokens=512)
    assert len(a.calls) == 1 and [x["model"] for x in b.calls] == ["m1", "m1"]


def test_lazy_rung_factory_not_built_unless_reached(tmp_path):
    built = []
    rungs = [Rung("p", "m0", client=Fake(["ok"])), Rung("p", "m1", factory=lambda: built.append(1) or Fake([]))]
    AnswerBudgetClient(rungs, ledger=RungLedger(tmp_path), log=lambda m: None).chat(MSG)
    assert not built


def test_module_names_no_model_or_provider():
    import hydra.answer_budget as mod
    src = open(mod.__file__, encoding="utf-8").read().lower()
    for word in ("qwen", "gpt", "ollama", "nemotron", "gemma", "kimi", "claude", "openai", "anthropic", "api_key"):
        assert word not in src, word


def test_unbuildable_rung_is_skipped(tmp_path):
    def boom():
        raise RuntimeError("no credentials for this rung")
    b = Fake(["ok"])
    rungs = [Rung("p", "m0", factory=boom), Rung("p", "m1", client=b)]
    resp = AnswerBudgetClient(rungs, ledger=RungLedger(tmp_path), log=lambda m: None).chat(MSG)
    assert resp.model == "m1"
