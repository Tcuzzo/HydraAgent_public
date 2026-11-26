"""Real CLI successor to private-default role-model expectations.

These protocol tests exercise model selection outside a checkout. The HTTP
leaf records the actual requested model; it does not prove model inference.
The old installed-role test and its observed failure remain in Git history.
"""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hydra.test_public_standalone_boundary import cli, isolated_environment


@contextmanager
def model_recorder():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            calls.append((self.path, None))
            if self.path.startswith("/forbidden"):
                return self.reply({"error": "private service must not be contacted"}, 503)
            self.reply({"models": [{"name": "catalog-default"}],
                        "data": [{"id": "catalog-default"}]})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, payload))
            if self.path.startswith("/forbidden"):
                return self.reply({"error": "private service must not be contacted"}, 503)
            message = {"role": "assistant", "content": "ROLE_MODEL_OK"}
            self.reply({"model": payload["model"], "message": message, "done": True,
                        "choices": [{"message": message, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://localhost:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()


@pytest.mark.parametrize("config_kind", ["absent", "route_only", "explicit_model"])
@pytest.mark.parametrize("private_source", [None, "environment", "env_file"])
def test_auto_route_uses_only_explicit_public_models(tmp_path, config_kind, private_source):
    with model_recorder() as (endpoint, calls):
        env, workspace, env_dir = isolated_environment(tmp_path, endpoint, private_source or "environment")
        if private_source is None:
            for key in ("HYDRA_BACKS_URL", "BACKS_ENDPOINT", "BACKS_URL", "BACKS_MODEL"):
                env.pop(key, None)
        expected_model = "explicit-cli-model"
        if config_kind != "absent":
            folder = workspace / ".hydraAgent"
            folder.mkdir()
            if config_kind == "route_only":
                role = "      route: doer\n"
            else:
                expected_model = "explicit-role-model"
                role = "      provider: local\n      model: explicit-role-model\n      family: fixture\n"
            (folder / "hydra.yaml").write_text("agentic:\n  roles:\n    planner:\n" + role, encoding="utf-8")
        trace_path = workspace / "role-trace.json"
        result = cli(env, workspace, "ask", "Plan this task", "--auto-route",
                     "--provider", "local", "--profile", "local", "--model", "explicit-cli-model",
                     "--env-dir", str(env_dir), "--root", str(workspace),
                     "--approval-policy", "deny", "--max-iterations", "2", "--timeout", "2",
                     "--trace-out", str(trace_path))
        assert result.returncode == 0, result.stdout + result.stderr
        assert "auto-route: role=planner" in result.stderr
        requests = [body for path, body in calls if body is not None and not path.startswith("/forbidden")]
        assert requests and [body["model"] for body in requests] == [expected_model]
        assert not [path for path, _ in calls if path.startswith("/forbidden")]
        trace = json.loads(trace_path.read_text())
        assert trace["final_response"] == "ROLE_MODEL_OK"
        assert trace["halted_reason"] == "natural"
        assert trace["iterations"] == 1
