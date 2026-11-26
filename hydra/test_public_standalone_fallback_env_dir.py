"""Provider-directory ownership on a real 503 fallback and subsequent CLI invocations."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread

import pytest

from hydra.test_public_standalone_boundary import cli
from hydra.test_public_standalone_profile_env_dir import profile_directories


@contextmanager
def unavailable_primary():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body["model"]))
            self.send_error(503, "test primary unavailable")

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://localhost:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def test_real_503_fallback_uses_selected_model_and_never_queries_ambient(tmp_path):
    with profile_directories(tmp_path) as (env, workspace, providers, selected, ambient):
        with unavailable_primary() as (endpoint, primary):
            with (providers / ".env.ollama").open("a", encoding="utf-8") as stream:
                stream.write("OLLAMA_MODEL=selected-local-fallback\n")
            (providers / ".env.custom").write_text(
                f"CUSTOM_ENDPOINT={endpoint}/direct/v1\nCUSTOM_MODEL=primary-cloud\n", encoding="utf-8"
            )
            result = cli(env, workspace, "ask", "hello", "--profile", "auto", "--provider", "custom",
                         "--env-dir", str(providers), "--root", str(workspace), "--approval-policy", "deny",
                         "--max-iterations", "2", "--timeout", "2")

        assert result.returncode == 0, result.stdout + result.stderr
        assert primary == [("/direct/v1/chat/completions", "primary-cloud")]
        observed = ([p for p, _ in ambient], [(p, b["model"]) for p, b in selected if b is not None])
        assert observed == ([], [("/v1/chat/completions", "selected-local-fallback")])


@pytest.mark.parametrize("result_code", [0, 2])
def test_cli_directory_scope_ends_after_dispatch(tmp_path, monkeypatch, result_code):
    from hydra.__main__ import main
    from hydra.providers import default_env_dir

    ambient, selected = (tmp_path / name for name in ("ambient", "selected"))
    ambient.mkdir()
    selected.mkdir()
    monkeypatch.setenv("HYDRA_ENV_DIR", str(ambient))
    (selected / ".env.ollama").write_text("OLLAMA_MODEL=selected\n", encoding="utf-8")
    args = ["ask", "hello", "--profile", "local", "--env-dir", str(selected), "--root", str(tmp_path)]
    if result_code == 0:
        args.append("--runtime-only")
    else:
        args.extend(["--provider", "unknown-provider"])

    assert main(args) == result_code
    assert default_env_dir() == ambient
    assert os.environ["HYDRA_ENV_DIR"] == str(ambient)
