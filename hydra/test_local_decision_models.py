"""Optional SDK contract tests, using fakes at the SDK and subprocess boundary."""
import json
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from hydra.decision_models import LayaDecisionClient
from hydra.llm import ChatMessage, LlmError
from hydra.local_model_worker import _laya, _needle, predict_local
from hydra.needle_client import NeedleClient


@pytest.fixture(autouse=True)
def local_tempfiles(monkeypatch, tmp_path):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def test_needle_only_completes_and_never_executes_functions(monkeypatch):
    calls = []
    class Needle:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def complete(self, text, **kwargs):
            calls.append(text)
            return {"success": True, "error": None, "function_calls": []}
        def run(self, *a, **kw):
            raise AssertionError("Hydra must own tool execution")
    monkeypatch.setitem(sys.modules, "needle", SimpleNamespace(Needle=Needle))
    result = _needle({"model": "tuned.cact", "tools": [{"type": "function", "function": {"name": "read"}}], "max_tokens": 40,
                      "messages": [{"role": "user", "content": "read a.txt"},
                                   {"role": "assistant", "content": None},
                                   {"role": "tool", "content": "file content"}]})
    assert calls[0]["weights"] == "tuned.cact"
    assert calls[0]["tools"] == [{"name": "read"}]
    assert calls[1] == "read a.txt"
    assert json.loads(calls[2]) == ["file content"]
    assert result["success"] is True


def test_needle_history_mismatch_is_not_replayed_as_real_tool_result(monkeypatch):
    class Needle:
        def __init__(self, **kw):
            pass
        def complete(self, *a, **kw):
            return {"function_calls": [{"name": "wrong", "arguments": {}}]}
    monkeypatch.setitem(sys.modules, "needle", SimpleNamespace(Needle=Needle))
    with pytest.raises(ValueError, match="faithfully replay"):
        _needle({"model": "needle3", "tools": [{"name": "read"}], "max_tokens": 40,
                 "messages": [{"role": "user", "content": "read"}, {"role": "assistant", "tool_calls": [
                     {"id": "1", "function": {"name": "read", "arguments": "{}"}}]}]})


def test_needle_client_preserves_calls_but_not_reasoning_as_answer(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setattr("hydra.needle_client.predict_local", lambda *a, **kw: {
        "success": True, "function_calls": [{"name": "read", "arguments": {"path": "a.txt"}}],
        "reasoning": "model metadata", "confidence": None})
    client = NeedleClient()
    result = client.chat([ChatMessage("user", "read a.txt")], model="needle3",
                         tools=[{"type": "function", "function": {"name": "read"}}])
    assert result.content == ""
    assert result.tool_calls[0].arguments == {"path": "a.txt"}
    assert result.tool_calls[0].id
    with pytest.raises(LlmError, match="supply tool schemas"):
        client.chat([ChatMessage("user", "hi")], model="needle3")


def test_needle_rejects_ungrounded_arguments(monkeypatch):
    monkeypatch.setattr("hydra.needle_client.find_spec", lambda name: object())
    monkeypatch.setattr("hydra.needle_client.predict_local", lambda *a, **kw: {
        "function_calls": [{"name": "delete", "arguments": {"path": "guessed"}}],
        "validation": {"ungrounded": ["delete.path"]}})
    with pytest.raises(LlmError, match="ungrounded"):
        NeedleClient().chat([ChatMessage("user", "delete")], model="needle3", tools=[{"name": "delete"}])


def test_local_worker_enforces_timeout_and_disables_telemetry(monkeypatch):
    killed = []
    class Process:
        def communicate(self, *a, **kw):
            raise subprocess.TimeoutExpired("worker", kw["timeout"])
    def run(argv, **kw):
        assert kw["env"]["NEEDLE_TELEMETRY"] == "0"
        assert kw["env"]["DO_NOT_TRACK"] == "1"
        assert "private" not in " ".join(argv)
        return Process()
    monkeypatch.setattr("hydra.local_model_worker.popen_portable", run)
    monkeypatch.setattr("hydra.local_model_worker.kill_tree", lambda process: killed.append(process))
    with pytest.raises(LlmError, match="exceeded"):
        predict_local({"backend": "needle", "state": "private"}, timeout=0.1)
    assert len(killed) == 1


def test_local_worker_accepts_sdk_null_error_on_success(monkeypatch):
    def launch(*args, **kwargs):
        kwargs["stdout"].write(b'{"success":true,"error":null,"function_calls":[]}')
        kwargs["stdout"].flush()
        return SimpleNamespace(returncode=0, communicate=lambda *a, **kw: (None, None))
    monkeypatch.setattr("hydra.local_model_worker.popen_portable", launch)
    assert predict_local({"backend": "needle"}, timeout=1)["success"]


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, -1, True])
def test_invalid_timeouts_never_start_a_model(monkeypatch, timeout):
    monkeypatch.setattr("hydra.local_model_worker.popen_portable", lambda *a, **kw: pytest.fail("worker started"))
    with pytest.raises(LlmError, match="finite and positive"):
        predict_local({"backend": "needle"}, timeout=timeout)


@pytest.mark.parametrize("state", [{1, 2}, float("nan")])
def test_unserializable_input_never_starts_a_model(monkeypatch, state):
    monkeypatch.setattr("hydra.local_model_worker.popen_portable", lambda *a, **kw: pytest.fail("worker started"))
    with pytest.raises(LlmError, match="valid JSON"):
        predict_local({"backend": "laya", "state": state}, timeout=1)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_noisy_real_worker_is_stopped_at_output_budget(monkeypatch, stream):
    from hydra.proc import popen_portable
    processes = []
    monkeypatch.setattr("hydra.local_model_worker._MAX_RESPONSE_BYTES", 1024)
    monkeypatch.setattr("hydra.local_model_worker._MAX_DIAGNOSTIC_BYTES", 1024)
    def launch(argv, **kwargs):
        process = popen_portable([sys.executable, "-c",
            f"import sys,time; sys.{stream}.write('x'*65536); sys.{stream}.flush(); time.sleep(60)"], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr("hydra.local_model_worker.popen_portable", launch)
    try:
        with pytest.raises(LlmError, match="byte budget"):
            predict_local({"backend": "needle"}, timeout=3)
        assert processes[0].poll() is not None
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_laya_actual_sdk_shape_and_local_checkpoint(monkeypatch):
    loaded = []
    class Agent:
        def predict(self, state, questions):
            assert state == "public/private Hydra context"
            return {"answers": {"route": {"choice": "read"}}}
    def load(model, **kwargs):
        loaded.append((model, kwargs))
        return Agent()
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=load))
    questions = {"route": {"type": "choice", "criteria": {"read": "Inspect code", "edit": "Change code"}}}
    monkeypatch.setattr("hydra.decision_models.predict_local", lambda req, **kw: _laya(req))
    response = LayaDecisionClient("local-trained-checkpoint").predict("public/private Hydra context", questions)
    assert response["answers"]["route"]["choice"] == "read"
    assert loaded == [("local-trained-checkpoint", {"device": "cpu"})]


def test_laya_rejects_missing_answer(monkeypatch):
    monkeypatch.setattr("hydra.decision_models.predict_local", lambda *a, **kw: {"answers": {}})
    with pytest.raises(LlmError, match="missing"):
        LayaDecisionClient().predict("a", {"route": {"type": "choice"}})
