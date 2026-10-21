"""Public CLI contracts: real processes/files; only the model transport is scripted."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture
def transport():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append(body)
            tool_results = [m for m in body.get("messages", []) if m["role"] == "tool"]
            if body.get("model") == "read-source" and not tool_results:
                message = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "read-evidence", "type": "function", "function": {
                        "name": "fs_read", "arguments": json.dumps({"path": "receipt.txt"})}}]}
            else:
                message = {"role": "assistant", "content": "Answer from the supplied evidence."}
            payload = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 5, "completion_tokens": 5}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", observed
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def cli(tmp_path, *arguments, endpoint=None):
    environment = {**os.environ, "PYTHONPATH": str(REPOSITORY), "NO_PROXY": "127.0.0.1,localhost"}
    if endpoint:
        environment["HYDRAFIXTURE_ENDPOINT"] = endpoint
    return subprocess.run([sys.executable, "-m", "hydra", *arguments], cwd=tmp_path,
                          env=environment, text=True, capture_output=True, timeout=40)


def seed_lesson(tmp_path):
    memory = tmp_path / "memory"
    result = cli(tmp_path, "remember", "The moonflower deployment uses port 7462.",
                 "--source", "operator instruction", "--memory-root", str(memory))
    assert result.returncode == 0, result.stderr
    return memory


def test_ask_recalls_saved_lesson_by_default_in_fresh_process(tmp_path, transport):
    memory = seed_lesson(tmp_path)
    endpoint, observed = transport
    result = cli(tmp_path, "ask", "Which port does moonflower deployment use?",
                 "--provider", "hydrafixture", "--model", "read-only-model",
                 "--root", str(tmp_path), "--memory-root", str(memory),
                 "--approval-policy", "deny", endpoint=endpoint)
    assert result.returncode == 0, result.stderr
    systems = "\n".join(m["content"] for m in observed[0]["messages"] if m["role"] == "system")
    assert "moonflower deployment uses port 7462" in systems
    assert "memory" in result.stderr.lower()


def test_local_memory_query_uses_requested_workspace(tmp_path):
    memory = seed_lesson(tmp_path)
    result = cli(tmp_path, "local-memory", str(memory), "--query", "moonflower deployment",
                 "--workspace-root", str(tmp_path), "--context-only")
    assert result.returncode == 0, result.stderr
    assert "moonflower deployment uses port 7462" in result.stdout


def test_ask_records_real_execution_for_next_session(tmp_path, transport):
    memory = tmp_path / "memory"
    (tmp_path / "receipt.txt").write_text("moonflower calibration passed", encoding="utf-8")
    endpoint, _ = transport
    result = cli(tmp_path, "ask", "Read receipt.txt to inspect moonflower calibration",
                 "--provider", "hydrafixture", "--model", "read-source",
                 "--root", str(tmp_path), "--memory-root", str(memory),
                 "--approval-policy", "deny", endpoint=endpoint)
    assert result.returncode == 0, result.stderr
    assert "tool_calls=1" in result.stderr
    recorded = [p for p in memory.rglob("*") if p.is_file() and p.suffix in {".md", ".json", ".jsonl"}]
    assert recorded, "the actual CLI run must leave durable execution evidence"
    evidence = "\n".join(p.read_text(encoding="utf-8") for p in recorded)
    assert "fs_read" in evidence and "receipt.txt" in evidence


def test_kev_is_in_normal_tool_catalog(tmp_path):
    result = cli(tmp_path, "tools")
    assert result.returncode == 0, result.stderr
    assert "kev_decide" in result.stdout


def test_complete_bundle_pins_the_official_kev_source():
    import tomllib
    project = tomllib.loads((REPOSITORY / "pyproject.toml").read_text())["project"]
    extras = project["optional-dependencies"]
    assert "intelligence" in extras
    requirements = extras["intelligence"]
    assert any("cactus-needle" in item for item in requirements)
    assert any("sqlite-vec" in item for item in requirements)
    assert any("github.com/jaredpalmer/kev.git@" in item for item in requirements)
    assert not any(item.strip().startswith("kev>=") for item in requirements)


def test_initializer_reports_missing_models_without_false_ready(tmp_path):
    result = cli(tmp_path, "init", "--check", "--state-dir", str(tmp_path / "state"))
    assert result.returncode == 1, result.stderr
    report = json.loads(result.stdout)
    assert report["ready"] is False
    assert set(report["models"]) >= {"needle", "kev"}
    assert not (tmp_path / "state" / "ready.json").exists()
