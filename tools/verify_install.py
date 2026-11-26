#!/usr/bin/env python3
"""Exercise an installed wheel through real CLI processes and local HTTP.

The model response is a deterministic fixture. Hydra's setup, transport, loop,
tools, files, memory, undo, and sandbox are real. This does not test model quality
or authentication to an external provider. Run with the installed venv's Python.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse


MODEL = "hydra-install-fixture"
LESSON = "Install verification: addition uses plus and verify.py checks the result."
ORIGINAL = "def add(a, b):\n    return a - b\n"
FIXED = ORIGINAL.replace("a - b", "a + b")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def shell_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def fixture_steps(scenario: str) -> list[tuple[str, dict]]:
    if scenario == "edit":
        return [
            ("fs_read", {"path": "calculation.py"}),
            ("fs_edit", {"path": "calculation.py", "old_string": "a - b", "new_string": "a + b"}),
            ("bash", {"command": shell_command([sys.executable, "verify.py"])}),
            ("memory_remember", {"lesson": LESSON}),
        ]
    return {
        "restart": [("memory_recall", {}), ("fs_read", {"path": "calculation.py"})],
        "deny": [("fs_write", {"path": "denied.txt", "content": "must not exist"})],
        "escape": [("fs_write", {"path": "../escape.txt", "content": "must not escape"})],
        "budget": [("fs_read", {"path": "calculation.py"})],
    }[scenario]


class ModelFixture(BaseHTTPRequestHandler):
    requests_seen: list[dict] = []

    def log_message(self, *_args) -> None:
        pass

    def reply(self, data: dict, status: int = 200) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path == "/api/tags":
            self.reply({"models": [{"name": MODEL}]})
        else:
            self.reply({"error": "unexpected fixture URL"}, 404)

    def do_POST(self) -> None:
        if self.path not in {"/api/chat", "/v1/chat/completions"}:
            self.reply({"error": "unexpected local-model transport"}, 404)
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        messages = body["messages"]
        prompt = next(m["content"] for m in messages if m["role"] == "user")
        scenario = prompt.split("INSTALL_SCENARIO=", 1)[1].split()[0]
        self.requests_seen.append({"scenario": scenario, "model": body.get("model"), "path": self.path})
        if scenario == "error":
            self.reply({"error": "install fixture: provider rejected the request"}, 503)
            return
        step = sum(m["role"] == "tool" for m in messages)
        steps = fixture_steps(scenario)
        message = {"role": "assistant", "content": "install fixture complete"}
        if step < len(steps):
            name, arguments = steps[step]
            message = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": name, "arguments": arguments}},
            ]}
        if self.path == "/api/chat":
            self.reply({"model": MODEL, "message": message, "done": True, "done_reason": "stop"})
        else:
            for index, call in enumerate(message.get("tool_calls", [])):
                call.update(id=f"fixture_{step}_{index}", type="function")
                call["function"]["arguments"] = json.dumps(call["function"]["arguments"])
            self.reply({"model": MODEL, "choices": [{"message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}]})


@contextmanager
def model_server():
    ModelFixture.requests_seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ModelFixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def isolated_environment(state: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("HYDRA_", "OLLAMA_", "CODEX_"))
           and not key.endswith(("_API_KEY", "_TOKEN"))}
    env.update({
        "HYDRA_HOME": str(state / "home"),
        "HYDRA_ENV_DIR": str(state / "providers"),
        "HYDRA_CHECKPOINTS_DIR": str(state / "checkpoints"),
        "HYDRA_MODEL_HEALTH_PATH": str(state / "health.json"),
        "HYDRA_CHAT_BUDGET_PATH": str(state / "budget.json"),
        "HYDRA_OPERATOR_AUTH_DIR": str(state / "auth"),
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    })
    return env


class InstallationCheck:
    def __init__(self, directory: Path, report: dict):
        self.workspace = directory / "workspace with spaces"
        self.workspace.mkdir()
        self.state = directory / "state"
        self.env = isolated_environment(self.state)
        self.report = report

    def process(self, label: str, arguments: list[str], expected: int = 0) -> subprocess.CompletedProcess:
        started = time.monotonic()
        result = subprocess.run(
            arguments, cwd=self.workspace, env=self.env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90,
        )
        self.report["checks"].append({
            "check": label, "exit_code": result.returncode,
            "expected_exit_code": expected, "seconds": round(time.monotonic() - started, 3),
            "stdout": result.stdout[-6000:], "stderr": result.stderr[-6000:],
        })
        require(result.returncode == expected,
                f"{label}: exit {result.returncode}, expected {expected}\n{result.stderr[-2000:]}")
        return result

    def cli(self, label: str, *args: str, expected: int = 0) -> subprocess.CompletedProcess:
        return self.process(label, [sys.executable, "-I", "-m", "hydra", *args], expected)

    def ask(self, scenario: str, policy: str = "allow", *, expected: int = 0, iterations: int = 8) -> dict:
        trace = self.state / f"{scenario}-{policy}.json"
        self.cli(f"task {scenario} ({policy})", "ask", f"INSTALL_SCENARIO={scenario}",
                 "--root", str(self.workspace), "--provider", "ollama",
                 "--memory-root", str(self.state / "memory"),
                 "--approval-policy", policy, "--max-iterations", str(iterations),
                 "--timeout", "10", "--trace-out", str(trace), expected=expected)
        return json.loads(trace.read_text()) if trace.exists() else {}

    def check_package(self) -> None:
        result = self.process("installed package identity", [sys.executable, "-I", "-c",
            "import hydra, json, sys; from pathlib import Path; "
            "from importlib.metadata import version; "
            "p=Path(hydra.__file__).resolve(); "
            "print(json.dumps({'module':str(p),'version':version('hydraagent'),'python':sys.version})); "
            "assert p.is_relative_to(Path(sys.prefix).resolve()), 'use a non-editable wheel install'",
        ])
        self.report["package"] = json.loads(result.stdout)
        console = Path(sys.executable).parent / ("hydra.exe" if os.name == "nt" else "hydra")
        self.process("installed console script", [str(console), "--version"])
        self.cli("entry point", "--help")
        self.cli("tool inventory", "tools")
        self.cli("skill inventory", "skills", "doctor")
        self.cli("role config", "roles")
        catalog = self.cli("runtime catalog", "declarative", "doctor", "--root", str(self.workspace))
        require(json.loads(catalog.stdout)["status"] == "OK", "installed runtime catalog is invalid")

    def check_task(self, endpoint: str) -> None:
        self.cli("first-run setup", "setup", "--mode", "local", "--endpoint", endpoint,
                 "--model", MODEL, "--non-interactive")
        models = self.cli("model discovery", "models", "--provider", "ollama")
        require(MODEL in models.stdout, "saved provider settings did not reach model discovery")
        (self.workspace / "calculation.py").write_text(ORIGINAL)
        (self.workspace / "verify.py").write_text(
            "from calculation import add\nfrom pathlib import Path\n"
            "assert add(2, 3) == 5\nPath('verified.txt').write_text('5')\nprint('verified: 5')\n")
        trace = self.ask("edit")
        require((self.workspace / "calculation.py").read_text() == FIXED, "file edit failed")
        require((self.workspace / "verified.txt").read_text() == "5", "verification command failed")
        require(trace["tool_calls_made"] == 4 and trace["halted_reason"] == "natural", "task did not finish")
        provider_file = Path(self.env["HYDRA_ENV_DIR"]) / ".env.ollama"
        with provider_file.open("a", encoding="utf-8") as stream:
            stream.write("\nOLLAMA_NUM_CTX=4096\n")
        restart = self.ask("restart", "deny")
        require(LESSON in json.dumps(restart), "durable memory did not survive a new process")
        require("a + b" in json.dumps(restart), "restarted process did not read the edited file")
        self.cli("undo after restart", "undo", "--root", str(self.workspace))
        require((self.workspace / "calculation.py").read_text() == ORIGINAL, "undo did not restore original bytes")

    def check_boundaries(self) -> None:
        for policy in ("deny", "ask"):
            trace = self.ask("deny", policy)
            require(trace["tool_calls_made"] == 1, "refusal check did not attempt its tool")
            require(not (self.workspace / "denied.txt").exists(), f"{policy} allowed an unapproved write")
            error = str(trace["tool_steps"][0].get("tool_error", "")).lower()
            require("denied" in error or "approval" in error,
                    "refusal trace did not explain the denial")
        trace = self.ask("escape")
        require(trace["tool_calls_made"] == 1, "scope check did not attempt its tool")
        require(not (self.workspace.parent / "escape.txt").exists(), "write escaped the workspace")
        require("escapes root" in str(trace["tool_steps"][0].get("tool_error", "")),
                "scope refusal was not reported")
        limited = self.ask("budget", expected=1, iterations=1)
        require(limited["halted_reason"] != "natural", "iteration exhaustion reported success")
        self.ask("error", expected=1)

    def check_sandbox(self, required: bool, endpoint: str) -> None:
        # sys.prefix is mounted read-only by the worker. A random temporary
        # directory here proves host writability without touching package files.
        with tempfile.TemporaryDirectory(prefix="hydra-sandbox-probe-", dir=sys.prefix) as directory:
            outside = Path(directory) / "outside.txt"
            outside.write_text("protected", encoding="utf-8")
            self._probe_sandbox(required, endpoint, outside)

    def _probe_sandbox(self, required: bool, endpoint: str, outside: Path) -> None:
        address = ("127.0.0.1", urlparse(endpoint).port)
        payload = f"""
from pathlib import Path
import socket
Path('sandbox-inside').write_text('ok')
try:
    with socket.create_connection({address!r}, timeout=2):
        print('HYDRA_NETWORK_REACHABLE', flush=True)
except OSError:
    print('HYDRA_NETWORK_BLOCKED', flush=True)
Path({str(outside)!r}).write_text('escape')
"""
        code = f"""
import json, socket, sys
from pathlib import Path
from hydra import exec_backend as b
root = Path.cwd()
outside = Path({str(outside)!r})
with socket.create_connection({address!r}, timeout=2):
    host_connected = True
ready = b._bwrap_available() and b._bwrap_network_available()
r = b.run_sandboxed([sys.executable, '-c', {payload!r}], workspace=root)
network_isolated = 'HYDRA_NETWORK_BLOCKED' in r.stdout and 'HYDRA_NETWORK_REACHABLE' not in r.stdout
inside = root / 'sandbox-inside'
ok = (ready and r.sandboxed and r.returncode != 0 and inside.exists()
      and inside.read_text() == 'ok' and outside.read_text() == 'protected' and network_isolated)
print(json.dumps({{'available': ready, 'verified': ok, 'host_network_control': host_connected,
                  'host_write_control': True, 'network_isolated': network_isolated,
                  'exit_code': r.returncode, 'detail': r.stdout}}))
assert outside.read_text() == 'protected', 'sandbox changed the host control file'
assert ok if ready else (r.returncode == 126 and not r.sandboxed)
assert ok or {not required!r}, 'this VM must provide working Bubblewrap network isolation'
"""
        result = self.process("sandbox isolation", [sys.executable, "-I", "-c", code])
        self.report["sandbox"] = json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-sandbox", action="store_true", help="fail unless real Linux isolation works")
    parser.add_argument("--output", type=Path, help="write a JSON receipt including subprocess output")
    args = parser.parse_args()
    report = {"schema": "hydra.install_verification.v1", "model": "deterministic HTTP fixture", "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="hydra-install-") as directory, model_server() as endpoint:
            check = InstallationCheck(Path(directory), report)
            check.check_package()
            check.check_task(endpoint)
            check.check_boundaries()
            check.check_sandbox(args.require_sandbox, endpoint)
            report["model_requests"] = ModelFixture.requests_seen
        report["status"] = "PASS"
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        report.update(status="FAIL", error=str(exc))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
