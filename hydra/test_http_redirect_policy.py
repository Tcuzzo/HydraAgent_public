import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from skills.http_fetch import SkillError, run

pytestmark = pytest.mark.network


@pytest.fixture
def redirect_server():
    visits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            visits.append(self.path)
            if self.path in ("/redirect", "/same-host"):
                self.send_response(302)
                host = "localhost" if self.path == "/redirect" else "127.0.0.1"
                self.send_header("Location", f"http://{host}:{self.server.server_port}/target")
                self.end_headers()
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"target")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", visits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_redirect_cannot_escape_host_allowlist(redirect_server):
    base, visits = redirect_server
    with pytest.raises(SkillError, match="allow-list"):
        run(base + "/redirect", allowed_hosts=["127.0.0.1"])
    assert visits == ["/redirect"]


def test_allowed_redirect_still_works(redirect_server):
    base, visits = redirect_server
    assert run(base + "/same-host", allowed_hosts=["127.0.0.1"])["body"] == "target"
    assert visits == ["/same-host", "/target"]


def test_environment_rechecks_private_destination(redirect_server, tmp_path, monkeypatch):
    from hydra import environment
    base, visits = redirect_server
    # Admit only the initial test server; destination still uses the real IP guard.
    original = environment._is_ip_allowed
    monkeypatch.setattr(environment, "_is_ip_allowed", lambda host: (True, None) if host == "127.0.0.1" else original(host))
    source = tmp_path / "source"
    source.mkdir()
    session = environment.create_session(source_repo=source, env_root=tmp_path / "sessions", title="redirect")
    with pytest.raises(environment.EnvironmentError, match="private/internal"):
        environment.fetch_session_url(tmp_path / "sessions", session["session_id"], base + "/redirect")
    assert visits == ["/redirect"]
