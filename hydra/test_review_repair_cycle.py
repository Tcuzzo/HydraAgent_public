"""A real red -> edit -> green regression cycle can advance to independent review."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import sys
import threading


def test_recovered_same_regression_passes_team_with_original_failure_preserved(tmp_path, monkeypatch):
    from hydra.teams import run_team
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "source.py").write_text("def answer():\n    return 41\n")
    (root / "check.py").write_text("from source import answer\nassert answer() == 42, answer()\n")
    requests = []
    command = f'"{sys.executable}" -B check.py'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            results = [m for m in body["messages"] if m["role"] == "tool"]
            n = len(results)
            reviewer = "specialist code_reviewer" in body["messages"][0]["content"]
            if reviewer:
                tool, args = ("fs_read", {"path": "source.py"}) if n == 0 else (None, None)
            elif n in (0, 2):
                tool, args = "bash", {"command": command, "timeout": 5}
            elif n == 1:
                tool, args = "fs_edit", {"path": "source.py", "old_string": "return 41", "new_string": "return 42"}
            else:
                tool, args = None, None
            message = {"role": "assistant", "content": "Observed the repair and actual regression results."}
            if tool:
                message = {"role": "assistant", "content": "", "tool_calls": [{"id": f"operation-{n}",
                    "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}
            payload = json.dumps({"model": body["model"], "choices": [{"message": message,
                "finish_reason": "tool_calls" if tool else "stop"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("REPAIRCYCLE_ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1")
    monkeypatch.setenv("HYDRA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    expected = b"def answer():\n    return 42\n"
    plan = {"goal": "Repair the regression and review the result", "provider": "repaircycle", "model": "scripted",
        "max_iterations": 5, "timeout_seconds": 25, "tasks": [
            {"id": "repair", "specialist": "seam_bot", "acceptance": {"artifacts": [
                {"path": "source.py", "sha256": hashlib.sha256(expected).hexdigest(), "must_change": True}]}},
            {"id": "review", "specialist": "code_reviewer", "depends_on": ["repair"]}]}
    try:
        report = run_team(plan, root=root, output_root=tmp_path / "runs", memory_root=tmp_path / "memory",
                          approval_policy="allow")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
    assert (root / "source.py").read_bytes() == expected
    assert report["outcomes"][0]["acceptance"]["status"] == "passed"
    assert report["verdict"] == "GREEN", report
    assert [row["error"] for row in report["outcomes"][0]["evidence"]] == [True, False, False]
    assert any(row["id"] == "review" and row["ok"] for row in report["outcomes"])
