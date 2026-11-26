"""Public distribution boundaries through real CLI and HTTP consumer paths.

The HTTP leaf is a recording protocol fixture, not a model. These tests prove
provider isolation, actual tool dispatch, and distribution contents; the separate
fresh-VM acceptance owns real inference. No production resolver is patched.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODEL = "standalone-boundary-protocol-fixture"


class RecordingLeaf(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.receipts.append(("GET", self.path))
        if self.path.startswith("/forbidden"):
            return self.reply({"error": "private integration trap"}, 503)
        if self.path == "/api/tags":
            return self.reply({"models": [{"name": MODEL}]})
        return self.reply({"data": [{"id": MODEL}]})

    def do_POST(self):
        self.server.receipts.append(("POST", self.path))
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path.startswith("/forbidden"):
            return self.reply({"error": "private integration trap"}, 503)
        messages = payload.get("messages", [])
        if any(message.get("role") == "tool" for message in messages):
            message = {"role": "assistant", "content": "BOUNDARY_OK"}
            finish = "stop"
        else:
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "boundary-read", "type": "function", "function": {
                    "name": "fs_read", "arguments": json.dumps({"path": "evidence.txt"})}}]}
            finish = "tool_calls"
        self.reply({"model": MODEL, "choices": [{"message": message,
                    "finish_reason": finish}], "usage": {"prompt_tokens": 1,
                    "completion_tokens": 1}})


@contextmanager
def recording_leaf():
    server = ThreadingHTTPServer(("localhost", 0), RecordingLeaf)
    server.receipts = []
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://localhost:{server.server_port}", server.receipts
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def isolated_environment(tmp_path, endpoint, backs_source="environment"):
    home, workspace, env_dir = (tmp_path / name for name in ("home", "workspace", "providers"))
    for directory in (home, workspace, env_dir):
        directory.mkdir()
    app_data = home / "AppData" / "Roaming"
    app_data.mkdir(parents=True)
    (workspace / "evidence.txt").write_text("actual standalone tool evidence\n")
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "SYSTEMDRIVE"}}
    env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(app_data),
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT),
                "HYDRA_ENV_DIR": str(env_dir), "HYDRA_HOME": str(home / "state"),
                "HYDRA_REFLEX": "0", "HYDRA_DECISION_TIMEOUT": "1",
                "HYDRA_MODEL_HEALTH_PATH": str(home / "health.json"),
                "HYDRA_CHAT_BUDGET_PATH": str(home / "budget.json"),
                "NO_PROXY": "localhost", "no_proxy": "localhost"})
    # No local model is pinned: normal model discovery must still stay local.
    (env_dir / ".env.ollama").write_text(f"OLLAMA_ENDPOINT={endpoint}\n")
    trap = endpoint + "/forbidden"
    if backs_source == "environment":
        env.update({"HYDRA_BACKS_URL": trap, "BACKS_ENDPOINT": trap,
                    "BACKS_URL": trap, "BACKS_MODEL": MODEL})
    else:
        (env_dir / ".env.backs").write_text(f"BACKS_ENDPOINT={trap}\nBACKS_MODEL={MODEL}\n")
    return env, workspace, env_dir


def cli(env, workspace, *args):
    return subprocess.run([sys.executable, "-B", "-m", "hydra", *args],
                          cwd=workspace, env=env, capture_output=True,
                          text=True, timeout=25)


def assert_no_private_requests(receipts):
    assert not [row for row in receipts if row[1].startswith("/forbidden")], (
        "public Hydra contacted the private-integration recording trap")


@pytest.mark.parametrize("backs_source", ["environment", "env_file"])
def test_local_model_discovery_ignores_private_integration_config(tmp_path, backs_source):
    with recording_leaf() as (endpoint, receipts):
        env, workspace, env_dir = isolated_environment(tmp_path, endpoint, backs_source)
        result = cli(env, workspace, "models", "--provider", "local", "--env-dir", str(env_dir))
        assert result.returncode == 0, result.stdout + result.stderr
        assert MODEL in result.stdout
        assert any(path == "/api/tags" for _method, path in receipts)
        assert_no_private_requests(receipts)


@pytest.mark.parametrize("provider", ["local", "custom"])
def test_real_cli_tool_path_uses_only_selected_direct_provider(tmp_path, provider):
    with recording_leaf() as (endpoint, receipts):
        env, workspace, env_dir = isolated_environment(tmp_path, endpoint)
        if provider == "custom":
            (env_dir / ".env.custom").write_text(
                f"CUSTOM_ENDPOINT={endpoint}/direct/v1\nCUSTOM_MODEL={MODEL}\n")
        trace_path = workspace / "trace.json"
        result = cli(env, workspace, "ask", "Read evidence.txt, then say BOUNDARY_OK.",
                     "--provider", provider, "--profile", "local", "--root", str(workspace),
                     "--env-dir", str(env_dir), "--approval-policy", "allow",
                     "--max-iterations", "2", "--timeout", "2", "--trace-out", str(trace_path))
        assert result.returncode == 0, result.stdout + result.stderr
        trace = json.loads(trace_path.read_text())
        assert trace["halted_reason"] == "natural"
        assert trace["final_response"] == "BOUNDARY_OK"
        assert trace["tool_calls_made"] == 1
        assert trace["tool_steps"][0]["tool_name"] == "fs_read"
        assert trace["tool_steps"][0]["tool_error"] is None
        assert "actual standalone tool evidence" in json.dumps(trace["messages"])
        assert any(method == "POST" and path.endswith("/chat/completions")
                   for method, path in receipts)
        assert_no_private_requests(receipts)


@pytest.mark.parametrize("name", ["backs", "BACKS", "BaCkS"])
def test_private_provider_name_cannot_reenter_through_generic_fallback(tmp_path, name):
    with recording_leaf() as (endpoint, receipts):
        env, workspace, env_dir = isolated_environment(tmp_path, endpoint, "env_file")
        env.update({"BACKS_ENDPOINT": endpoint + "/forbidden", "BACKS_MODEL": MODEL})
        result = cli(env, workspace, "models", "--provider", name, "--env-dir", str(env_dir))
        assert result.returncode != 0, "private provider was accepted"
        assert "backs" in (result.stdout + result.stderr).lower()
        assert_no_private_requests(receipts)


def test_default_roles_and_tool_inventory_stay_standalone(tmp_path):
    with recording_leaf() as (endpoint, receipts):
        env, workspace, env_dir = isolated_environment(tmp_path, endpoint, "env_file")
        roles = cli(env, workspace, "roles", "--env-dir", str(env_dir))
        assert roles.returncode == 0, roles.stdout + roles.stderr
        assert "provider=backs" not in roles.stdout.lower()
        assert all(role in roles.stdout for role in ("planner:", "doer:", "auditor:"))
        tools = cli(env, workspace, "tools")
        assert tools.returncode == 0, tools.stdout + tools.stderr
        assert "fs_read" in tools.stdout
        assert_no_private_requests(receipts)


def test_built_wheel_excludes_private_gateway_and_operational_payload(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist", "*.egg-info"))
    wheels = tmp_path / "wheels"
    builder = os.environ.get("HYDRA_TEST_BUILD_PYTHON", sys.executable)
    build = subprocess.run([builder, "-m", "pip", "wheel", "--no-deps",
                            "--no-build-isolation", "--wheel-dir", str(wheels), str(source)],
                           capture_output=True, text=True, timeout=90)
    assert build.returncode == 0, build.stdout + build.stderr
    artifacts = list(wheels.glob("*.whl"))
    assert len(artifacts) == 1
    forbidden = ("RungGatewayClient", "HYDRA_BACKS_URL", "BACKS_ENDPOINT",
                 "/api/router/resolve", "/api/router/complete", "/api/router/outcome",
                 "/api/system/needle_engineer_rungs", "/api/kev/decision")
    found = []
    with zipfile.ZipFile(artifacts[0]) as wheel:
        names = wheel.namelist()
        assert "hydra/__main__.py" in names
        for name in names:
            if "/test_" in name or not name.endswith((".py", ".yaml", ".yml", ".json", ".md")):
                continue
            text = wheel.read(name).decode("utf-8", errors="replace")
            if any(marker in text for marker in forbidden):
                found.append(name)
    assert not found, f"private operational payload shipped: {found}"
