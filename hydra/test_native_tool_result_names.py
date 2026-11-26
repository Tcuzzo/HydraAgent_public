"""Seal Ollama's native tool-result names at a real HTTP boundary.

The public native protocol uses ``tool_name`` on tool results. Hydra's loop
uses OpenAI call IDs, which must be resolved against the preceding assistant
batch before those IDs disappear from the native request.

Contract: https://docs.ollama.com/capabilities/tool-calling
"""
from __future__ import annotations

import copy
import http.server
import json
import threading

import pytest

from hydra.llm import OllamaClient


@pytest.fixture()
def wire(monkeypatch):
    # Always reach this fixture directly, including on hosts with proxy envs.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    seen: list[tuple[str, dict]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, body))
            message = {"role": "assistant", "content": "wire accepted"}
            if self.path == "/api/chat":
                reply = {"model": body["model"], "message": message,
                         "done": True, "done_reason": "stop"}
            else:
                reply = {"model": body["model"], "choices": [
                    {"message": message, "finish_reason": "stop"}]}
            data = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def assistant(*calls):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps({"path": f"{name}.txt"})}}
        for call_id, name in calls
    ]}


def result(call_id, content="result", **extra):
    return {"role": "tool", "tool_call_id": call_id, "content": content, **extra}


def native_messages(wire, messages):
    url, seen = wire
    before = copy.deepcopy(messages)
    response = OllamaClient(endpoint=url, num_ctx=8192).chat(
        messages, model="wire-contract", timeout=5)
    assert response.content == "wire accepted"
    assert messages == before, "native translation must not mutate caller history"
    path, body = seen[-1]
    assert path == "/api/chat"
    return body["messages"]


def test_single_result_carries_native_name_and_object_arguments(wire):
    messages = native_messages(wire, [
        assistant(("call_0", "fs_read")), result("call_0", "file bytes")])
    assert messages[0]["tool_calls"] == [{"function": {
        "name": "fs_read", "arguments": {"path": "fs_read.txt"}}}]
    assert messages[1] == {
        "role": "tool", "tool_name": "fs_read", "content": "file bytes"}


def test_parallel_results_use_call_ids_even_when_returned_out_of_order(wire):
    messages = native_messages(wire, [
        assistant(("write", "fs_write"), ("read", "fs_read"), ("list", "list_directory")),
        result("list", "listing"), result("write", "written"), result("read", "read back")])
    assert [(m.get("tool_name"), m["content"]) for m in messages[1:]] == [
        ("list_directory", "listing"), ("fs_write", "written"), ("fs_read", "read back")]
    assert all("tool_call_id" not in m for m in messages[1:])


def test_reused_call_ids_are_resolved_in_each_preceding_assistant_batch(wire):
    messages = native_messages(wire, [
        assistant(("call_0", "fs_write")), result("call_0", "written"),
        assistant(("call_0", "fs_read")), result("call_0", "read back")])
    assert messages[1].get("tool_name") == "fs_write"
    assert messages[3].get("tool_name") == "fs_read"


def test_explicit_native_names_are_preserved_even_when_an_id_disagrees(wire):
    native_call = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "native_read", "arguments": {"path": "sentinel.txt"}}}]}
    messages = native_messages(wire, [
        assistant(("call_0", "fs_read")),
        result("call_0", tool_name="explicit_native_name"),
        native_call, {"role": "tool", "tool_name": "native_read", "content": "native result"}])
    assert messages[1] == {
        "role": "tool", "tool_name": "explicit_native_name", "content": "result"}
    assert messages[3] == {
        "role": "tool", "tool_name": "native_read", "content": "native result"}


def test_unknown_future_and_stale_ids_are_not_guessed(wire):
    messages = native_messages(wire, [
        result("future"),
        assistant(("future", "fs_read"), ("old", "fs_write")),
        result("unknown"), result("old"),
        assistant(("new", "list_directory")), result("old"),
        {"role": "tool", "content": "missing id"}])
    for index in (0, 2, 5, 6):
        assert "tool_name" not in messages[index]
        assert "tool_call_id" not in messages[index]
    assert messages[3].get("tool_name") == "fs_write"


@pytest.mark.parametrize("client_options", [{}, {"api_key": "test-key", "num_ctx": 8192}])
def test_openai_requests_preserve_ids_and_history_unchanged(wire, client_options):
    url, seen = wire
    messages = [assistant(("call_0", "fs_read")), result("call_0", "file bytes")]
    before = copy.deepcopy(messages)
    response = OllamaClient(endpoint=url, **client_options).chat(
        messages, model="wire-contract", timeout=5)
    assert response.content == "wire accepted"
    assert seen[-1][0] == "/v1/chat/completions"
    assert seen[-1][1]["messages"] == before
    assert messages == before
