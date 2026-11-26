"""Public decisions stay standalone despite private-service configuration.

Only the remote HTTP protocol is simulated. Hydra's decision, provider, rung,
and reflex implementations run unchanged, with the suite's offline resolver
explicitly disabled. These are transport contracts, not model-inference proof.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hydra import decision_ladder, decision_tools, model_routing, reflex_seam
from hydra.llm import LlmError


@pytest.fixture
def http_routes(monkeypatch, tmp_path):
    """Serve distinguishable private, native local, and direct cloud leaves."""
    calls = []
    answer = {"classification": "standalone", "yes_no": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.respond({})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append({"path": self.path, "body": body,
                          "authorization": self.headers.get("Authorization")})
            if self.path.startswith("/private/"):
                self.respond({"ok": True, "answer": {"classification": "private"},
                              "chain": [{"rung": "private", "model": "private", "usable": True}]})
            elif self.path == "/local/api/chat":
                self.respond({"message": {"role": "assistant", "content": json.dumps(answer)}})
            else:
                self.respond({"choices": [{"message": {"content": json.dumps(answer)}}]})

        def respond(self, payload):
            if self.command == "GET":
                calls.append({"path": self.path, "body": None})
            raw = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("HYDRA_ENV_DIR", str(tmp_path))
    monkeypatch.setenv("HYDRA_DECISION_TIMEOUT", "0.5")
    model_routing.set_resolver(None)
    reflex_seam.set_decision_client(None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base, calls, answer
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        model_routing.set_resolver(None)
        reflex_seam.set_decision_client(None)


def private_configuration(monkeypatch, tmp_path, source, base):
    if source is None:
        return
    if source == "file":
        (tmp_path / ".env.backs").write_text(f"BACKS_ENDPOINT={base}/private\n", encoding="utf-8")
    else:
        monkeypatch.setenv(source, base + "/private")


def public_decision(tier):
    if tier == "vision":
        return decision_tools.vision_decide("a test image", "test.png", "Return the image decision")
    return decision_tools.text_decide("a test text", "Return the text decision", labels=["standalone"])


@pytest.mark.parametrize("source", [None, "HYDRA_BACKS_URL", "BACKS_ENDPOINT", "file"])
@pytest.mark.parametrize("tier,model_env,rung", [
    ("text", "HYDRA_KEV_MODEL", "kev"),
    ("text", "HYDRA_TEXT_DECISION_MODEL", "text"),
    ("vision", "HYDRA_VISION_MODEL", "vision"),
])
def test_public_local_decisions_ignore_private_config(monkeypatch, tmp_path, http_routes, source, tier, model_env, rung):
    base, calls, answer = http_routes
    private_configuration(monkeypatch, tmp_path, source, base)
    monkeypatch.setenv(model_env, "configured-test-model")
    monkeypatch.setenv("OLLAMA_ENDPOINT", base + "/local")
    result = public_decision(tier)
    assert [call["path"] for call in calls] == ["/local/api/chat"]
    assert result["rung"] == rung
    assert result["answer"] == answer
    assert calls[0]["body"]["model"] == "configured-test-model"
    assert calls[0]["body"]["instructions"]


@pytest.mark.parametrize("source", [None, "HYDRA_BACKS_URL", "BACKS_ENDPOINT", "file"])
@pytest.mark.parametrize("tier", ["text", "vision"])
def test_public_direct_jev_remains_available(monkeypatch, tmp_path, http_routes, source, tier):
    base, calls, answer = http_routes
    private_configuration(monkeypatch, tmp_path, source, base)
    monkeypatch.setenv("HYDRA_JEV_MODEL", "configured-cloud-model")
    monkeypatch.setenv("HYDRA_JEV_BASE_URL", base + "/cloud/v1")
    monkeypatch.setenv(decision_ladder.jev_key_name(), "test-only-key")
    result = public_decision(tier)
    assert [call["path"] for call in calls] == ["/cloud/v1/chat/completions"]
    assert result["rung"] == "jev"
    assert result["answer"] == answer
    assert calls[0]["body"]["model"] == "configured-cloud-model"
    assert calls[0]["authorization"] == "Bearer test-only-key"


@pytest.mark.parametrize("source", ["HYDRA_BACKS_URL", "BACKS_ENDPOINT", "file"])
def test_unconfigured_public_decisions_refuse_without_private_call(monkeypatch, tmp_path, http_routes, source):
    base, calls, _ = http_routes
    private_configuration(monkeypatch, tmp_path, source, base)
    with pytest.raises(decision_ladder.DecisionError) as caught:
        public_decision("text")
    assert calls == []
    assert "BACKS" not in str(caught.value)


def test_default_rung_resolution_never_discovers_private_service(monkeypatch, tmp_path, http_routes):
    base, calls, _ = http_routes
    private_configuration(monkeypatch, tmp_path, "file", base)
    try:
        result = model_routing.resolve_rung("work", task="code")
    except LlmError:
        pass  # No configured standalone provider may legitimately be unavailable.
    else:
        assert result[0] != "backs"
    assert calls == []


@pytest.mark.parametrize("source", [None, "HYDRA_BACKS_URL"])
def test_reflex_default_path_uses_configured_local_decision(monkeypatch, tmp_path, http_routes, source):
    base, calls, answer = http_routes
    private_configuration(monkeypatch, tmp_path, source, base)
    answer.clear()
    answer.update(schema="hydra.reflex.decision.v1", next_tool=None, done=True,
                  retry=False, escalate=False, reason="local decision")
    monkeypatch.setenv("HYDRA_REFLEX", "1")
    monkeypatch.setenv("HYDRA_KEV_MODEL", "configured-reflex-model")
    monkeypatch.setenv("OLLAMA_ENDPOINT", base + "/local")
    result = reflex_seam.maybe_handle("Finish this test decision")
    assert [call["path"] for call in calls] == ["/local/api/chat"]
    assert result is not None and result["handled"] is True
    assert result["decision"] == answer


@pytest.mark.parametrize("source", [None, "BACKS_ENDPOINT"])
def test_laya_configuration_and_local_schema_boundary_remain(monkeypatch, tmp_path, http_routes, source):
    base, calls, _ = http_routes
    private_configuration(monkeypatch, tmp_path, source, base)
    monkeypatch.setenv("HYDRA_LAYA_MODEL", str(tmp_path / "explicit-checkpoint"))
    assert decision_ladder.configured_steps("llm") == ["laya"]
    with pytest.raises(LlmError, match="choice, score, or noul"):
        decision_tools.laya_decide({}, {"area": {"type": "invalid", "instructions": "validate locally"}})
    assert calls == []
