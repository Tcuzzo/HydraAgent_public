"""Exercise real CLI output through strict, non-TTY pipes; HTTP fixtures perform no inference."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
from threading import Thread

import pytest

from hydra.test_public_standalone_profile_env_dir import profile_directories


TEXT = "Café € 東京 complete"


def run_redirected(env, workspace, encoding, args, validated_input=""):
    return subprocess.run(
        [sys.executable, "-B", "-m", "hydra", *args],
        cwd=workspace, env={**env, "PYTHONIOENCODING": f"{encoding}:strict"},
        input=validated_input, capture_output=True, encoding=encoding, errors="strict", timeout=20,
    )


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_redirected_chat_welcomes_lists_unicode_skill_and_exits(tmp_path, encoding):
    with profile_directories(tmp_path) as (env, workspace, providers, selected, ambient):
        skill = tmp_path / "public-skills" / "pipe-check"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: pipe-check\ndescription: {TEXT}\n---\n# Pipe check\n", encoding="utf-8"
        )
        env["HYDRA_SKILLS_ROOT"] = str(skill.parent)
        args = ["chat", "--profile", "local", "--env-dir", str(providers),
                "--model", "explicit-cli", "--no-local-memory", "--root", str(workspace),
                "--approval-policy", "deny"]
        result = run_redirected(env, workspace, encoding, args, "/skills pipe-check\n/exit\n")

        assert result.returncode == 0, result.stdout + result.stderr
        assert f"root={workspace}" in result.stdout
        assert "Type a task" in result.stdout
        assert "pipe-check" in result.stdout
        assert "Café €" in result.stdout and "complete" in result.stdout
        assert "Hydra signing off." in result.stdout
        assert not [path for path, body in selected if body is not None]
        assert [path for path, _ in ambient] == []
        if encoding == "utf-8":
            assert TEXT in result.stdout


@contextmanager
def ordinary_reply():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, request["model"]))
            message = {"role": "assistant", "content": TEXT}
            body = json.dumps({"choices": [{"message": message, "finish_reason": "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("encoding", ["cp1252", "utf-8"])
def test_redirected_ask_prints_unicode_reply_in_selected_encoding(tmp_path, encoding):
    with profile_directories(tmp_path) as (env, workspace, providers, _selected, ambient):
        with ordinary_reply() as (endpoint, requests):
            (providers / ".env.custom").write_text(f"CUSTOM_ENDPOINT={endpoint}/v1\n", encoding="utf-8")
            args = ["ask", "hello", "--provider", "custom", "--profile", "local", "--env-dir",
                    str(providers), "--model", "explicit-cli", "--root", str(workspace),
                    "--approval-policy", "deny", "--max-iterations", "1", "--timeout", "2"]
            result = run_redirected(env, workspace, encoding, args)

        assert requests == [("/v1/chat/completions", "explicit-cli")]
        assert [path for path, _ in ambient] == []
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Café €" in result.stdout and "complete" in result.stdout
        if encoding == "utf-8":
            assert TEXT in result.stdout
