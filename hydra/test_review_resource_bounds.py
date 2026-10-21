"""Runtime work budgets and an HTTP deadline against a real trickling server."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace

import pytest


def test_legacy_work_obeys_requested_iteration_limit(tmp_path, monkeypatch):
    from gateways.tui.elite import EliteTUI
    from hydra.llm import ChatResponse, ToolCall
    from hydra.loop import Tool
    from hydra.work_executor import resolve_work_model
    monkeypatch.setattr("hydra.work_executor.SELF_AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    actions = []
    class Transport:
        requests = 0
        def chat(self, messages, *, model, **kwargs):
            self.requests += 1
            if self.requests <= 3:
                args = {"index": self.requests}
                return ChatResponse("", model, "tool_calls", 0, 0, {}, [
                    ToolCall(str(self.requests), "observe", json.dumps(args), args)])
            return ChatResponse("Observations returned.", model, "stop", 0, 0, {}, [])
    def observe(index):
        actions.append(index)
        return {"ok": True, "index": index}
    client = Transport()
    provider, model = resolve_work_model("steering")
    tool = Tool("observe", "Return one observation.", {"type": "object", "properties": {
        "index": {"type": "integer"}}, "required": ["index"]}, observe)
    tui = EliteTUI(client, model, SimpleNamespace(name=provider), tmp_path, "Observe data.",
        initial_messages=[], tools=[tool], max_iterations=1, timeout=1, recall_builder=None)
    tui._stream_turn("run three observations", source="peer:test")
    assert len(actions) <= 1 and tui._last_turn_result.iterations <= 1


def test_http_timeout_is_a_wall_clock_deadline_for_trickling_response(tmp_path):
    from hydra.cli.tool_binding import _fetch_same_host
    from skills.http_fetch import SkillError
    stopped = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "50")
            self.end_headers()
            try:
                for _ in range(50):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    if stopped.wait(0.04):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(SkillError, match="[Tt]imeout|deadline|timed out"):
            _fetch_same_host(f"http://127.0.0.1:{server.server_port}/", timeout=0.15, max_bytes=1024)
        assert time.monotonic() - started < 1.2
    finally:
        stopped.set()
        server.shutdown()
        server.server_close()
        thread.join(2)
