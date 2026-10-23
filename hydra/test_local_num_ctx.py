"""Local GPU rung must get a context window big enough for Hydra's prompt.

Live finding: the local rung ran at the server default of 4096 tokens, so the
system prompt + tool schemas + tool result pushed the operator's question out
of the window and the model summarised the page instead of answering. The
OpenAI-compatible endpoint ignores a context size; the native chat endpoint
takes it. These tests drive a real local socket.
"""
from __future__ import annotations

import http.server
import json
import threading

import pytest

from hydra import providers
from hydra.llm import OllamaClient


@pytest.fixture()
def wire():
    seen: list[tuple[str, dict]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            if self.path == "/api/chat":
                out = {"model": body["model"], "done": True, "done_reason": "stop",
                       "prompt_eval_count": 7, "eval_count": 3,
                       "message": {"role": "assistant", "content": "",
                                   "tool_calls": [{"function": {"name": "http_fetch", "arguments": {"url": "https://x.test/"}}}]}}
            else:
                out = {"model": body["model"], "choices": [{"message": {"role": "assistant", "content": "v1 answer"}, "finish_reason": "stop"}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()
    srv.server_close()


MSGS = [
    {"role": "user", "content": "q"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c0", "type": "function", "function": {"name": "http_fetch", "arguments": "{\"url\": \"https://x.test/\"}"}}]},
    {"role": "tool", "tool_call_id": "c0", "content": "{\"ok\": true}"},
]


def test_local_client_with_num_ctx_uses_native_chat_and_sends_window(wire):
    url, seen = wire
    resp = OllamaClient(endpoint=url, num_ctx=16384).chat(MSGS, model="m", max_tokens=99, tools=[{"type": "function", "function": {"name": "http_fetch"}}])
    path, body = seen[-1]
    assert path == "/api/chat"
    assert body["options"]["num_ctx"] == 16384 and body["options"]["num_predict"] == 99
    assert body["stream"] is False and body["tools"]
    # assistant tool-call arguments go over the native wire as objects
    assert body["messages"][1]["tool_calls"][0]["function"]["arguments"] == {"url": "https://x.test/"}
    assert [(c.name, c.arguments) for c in resp.tool_calls] == [("http_fetch", {"url": "https://x.test/"})]
    assert resp.finish_reason == "tool_calls" and resp.prompt_tokens == 7 and resp.completion_tokens == 3


def test_without_num_ctx_the_openai_wire_is_unchanged(wire, monkeypatch):
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    url, seen = wire
    assert OllamaClient(endpoint=url).chat([{"role": "user", "content": "q"}], model="m").content == "v1 answer"
    assert seen[-1][0] == "/v1/chat/completions"


def test_keyed_clients_never_take_the_native_path(wire):
    url, seen = wire
    OllamaClient(endpoint=url, api_key="k", num_ctx=16384).chat([{"role": "user", "content": "q"}], model="m")
    assert seen[-1][0] == "/v1/chat/completions"


def test_provider_env_file_sets_the_local_window(tmp_path, wire, monkeypatch):
    url, seen = wire
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    (tmp_path / ".env.ollama").write_text(f"OLLAMA_ENDPOINT={url}\nOLLAMA_NUM_CTX=8192\n")
    client, cfg = providers.make_client("ollama", env_dir=tmp_path)
    client.chat([{"role": "user", "content": "q"}], model=cfg.model)
    assert seen[-1][0] == "/api/chat" and seen[-1][1]["options"]["num_ctx"] == 8192
    assert providers.local_num_ctx("ollama", env_dir=tmp_path) == 8192
    (tmp_path / ".env.ollama").write_text(f"OLLAMA_ENDPOINT={url}\n")
    assert providers.local_num_ctx("ollama", env_dir=tmp_path) == 0
