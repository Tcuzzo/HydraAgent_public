#!/usr/bin/env python3
"""One bounded installed-wheel write/read/undo proof with a real local model.

This downloads the pinned official Ollama build and model. It never substitutes
model responses, retries inference, or treats a model's final text as file proof.
Run with the fresh wheel environment's Python on a disposable Linux VM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile


OLLAMA_VERSION = "0.40.18"
ARCHIVE_URL = "https://github.com/ollama/ollama/releases/download/v0.40.18/ollama-linux-amd64.tar.zst"
ARCHIVE_SHA256 = "b82649006cb5b54ed1237fa2d1a26a7f939a4fd03150033759acbff7ff315adf"
MODEL = "qwen3:4b-instruct-2507-q4_K_M"
MODEL_DIGEST = "0edcdef34593eac1aa2be9c7d06c432dcf81945adca5eca2f27662c18f168ba0"
ENDPOINT = "http://127.0.0.1:45867"
EXPECTED = b"hydra-live-model-ok\n"
PROMPT = (
    "Use fs_write to write proof.txt in the current workspace with the single line "
    "hydra-live-model-ok followed by one newline. Then use fs_read to read proof.txt "
    "and verify its contents. Do not run shell commands. When both tools succeed, "
    "say LIVE_PROOF_OK. /no_think"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def stop(process: subprocess.Popen) -> None:
    send_group_signal(process, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    send_group_signal(process, signal.SIGKILL)
    process.wait(timeout=5)


def send_group_signal(process: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass


def cleanup(process: subprocess.Popen, report: dict) -> None:
    try:
        stop(process)
    except Exception as exc:
        report.setdefault("cleanup_errors", []).append(f"{type(exc).__name__}: {exc}")


def package_hashes(runtime: Path) -> dict[str, str]:
    return {str(path.relative_to(runtime)): digest(path)
            for path in package_files(runtime)}


def package_files(runtime: Path):
    for top in ("hydra", "skills", "core", "gateways"):
        for path in (runtime / top).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path


def wheel_hashes(wheel: Path) -> dict[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        return {name: hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist()
                if name.split("/", 1)[0] in {"hydra", "skills", "core", "gateways"}
                and not name.endswith("/")}


def environment(root: Path) -> dict[str, str]:
    allowed = ("PATH", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "https_proxy")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update({
        "HOME": str(root / "home"), "PYTHONNOUSERSITE": "1", "PYTHONUNBUFFERED": "1",
        "OLLAMA_HOST": ENDPOINT.removeprefix("http://"),
        "OLLAMA_MODELS": str(root / "models"), "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_CONTEXT_LENGTH": "12288", "OLLAMA_NUM_CTX": "12288",
        "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
        "HYDRA_ENV_DIR": str(root / "providers"), "HYDRA_HOME": str(root / "state"),
        "HYDRA_REFLEX": "0", "HYDRA_MODEL_HEALTH_PATH": str(root / "state/health.json"),
        "HYDRA_CHECKPOINTS_DIR": str(root / "state/checkpoints"),
    })
    return env


def api(path: str) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(ENDPOINT + path, timeout=5) as response:
        return json.load(response)


def wait_ready(server: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        require(server.poll() is None, "Ollama exited before readiness")
        try:
            return api("/api/version")
        except (OSError, ValueError):
            time.sleep(0.2)
    raise RuntimeError("Ollama did not become ready within 20 seconds")


class Proof:
    def __init__(self, root: Path, report: dict):
        self.root = root
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (root / "home").mkdir()
        self.env = environment(root)
        self.report = report
        self.deadline = time.monotonic() + 1200
        self.report["verification_deadline_epoch"] = time.time() + 1200

    def run(self, name: str, argv: list[str], timeout: float = 30) -> str:
        row = {"phase": name, "argv": argv, "exit_code": None}
        self.report["commands"].append(row)
        started = time.monotonic()
        remaining = self.deadline - started
        require(remaining > 0, f"{name}: verification deadline expired before launch")
        print(f"phase={name} started", flush=True)
        with (self.root / f"{name}.stdout.log").open("w") as out, \
             (self.root / f"{name}.stderr.log").open("w") as err:
            process = subprocess.Popen(argv, cwd=self.workspace, env=self.env,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            try:
                row["exit_code"] = process.wait(timeout=min(timeout, remaining))
            finally:
                cleanup(process, self.report)
                row["seconds"] = round(time.monotonic() - started, 3)
        require(row["exit_code"] == 0, f"{name} exited {row['exit_code']}; see phase logs")
        return (self.root / f"{name}.stdout.log").read_text()

    def cli(self, name: str, *args: str, timeout: float = 30) -> str:
        return self.run(name, [sys.executable, "-I", "-m", "hydra", *args], timeout)

    def check_install(self) -> None:
        output = self.run("installed-origin", [sys.executable, "-I", "-c",
            "import hydra,json,sys; from pathlib import Path; "
            "from importlib.metadata import version; p=Path(hydra.__file__).resolve(); "
            "assert p.is_relative_to(Path(sys.prefix).resolve()), 'non-editable wheel required'; "
            "print(json.dumps({'module':str(p),'version':version('hydraagent'),'python':sys.version}))"])
        self.report["installed_package"] = json.loads(output)
        runtime = Path(self.report["installed_package"]["module"]).parent.parent
        self.report["runtime_root"] = str(runtime)
        self.report["package_hashes_before"] = package_hashes(runtime)
        require(self.report["package_hashes_before"] == self.report["wheel_package_hashes"],
                "installed runtime package bytes differ from the fresh wheel")

    def download_ollama(self) -> Path:
        archive = self.root / "ollama.tar.zst"
        self.run("download", ["curl", "--fail", "--location", "--connect-timeout", "15",
            "--max-time", "180", "--retry", "0", "--output", str(archive), ARCHIVE_URL], 185)
        actual = digest(archive)
        self.report["ollama_archive_sha256"] = actual
        require(actual == ARCHIVE_SHA256, "official Ollama archive digest mismatch")
        destination = self.root / "ollama"
        destination.mkdir()
        self.run("extract", ["tar", "--zstd", "-xf", str(archive), "-C", str(destination)], 90)
        archive.unlink()
        return destination / "bin/ollama"

    def pull_model(self, binary: Path) -> None:
        self.run("pull", [str(binary), "pull", MODEL], 240)
        models = api("/api/tags")["models"]
        matching = [model for model in models if model.get("name") == MODEL]
        require(len(matching) == 1, "pinned model absent or ambiguous in Ollama catalog")
        self.report["model_catalog_entry"] = matching[0]
        require(matching[0]["digest"] == MODEL_DIGEST, "pinned model digest mismatch")

    def task(self) -> None:
        self.deadline = min(self.deadline, time.monotonic() + 900)
        self.report["model_task_deadline_epoch"] = time.time() + max(0, self.deadline - time.monotonic())
        self.cli("setup", "setup", "--mode", "local", "--model", MODEL,
                 "--endpoint", ENDPOINT, "--non-interactive")
        models = self.cli("models", "models", "--provider", "ollama")
        require(MODEL in models, "saved provider configuration did not discover pinned model")
        trace_path = self.root / "trace.json"
        self.report["inference_attempts"] = 1
        self.cli("ask", "ask", PROMPT, "--provider", "ollama", "--model", MODEL,
            "--root", str(self.workspace), "--memory-root", str(self.root / "memory"),
            "--approval-policy", "allow", "--max-iterations", "4", "--timeout", "420",
            "--trace-out", str(trace_path), timeout=max(0.1, self.deadline - time.monotonic() - 60))
        artifact = self.workspace / "proof.txt"
        require(artifact.read_bytes() == EXPECTED, "independent artifact bytes differ from the 20-byte proof")
        require(sorted(p.name for p in self.workspace.iterdir()) == ["proof.txt"], "unexpected workspace files")
        self.report["artifact_sha256_before_undo"] = digest(artifact)
        self.report["artifact_bytes_before_undo"] = len(EXPECTED)
        self.report["artifact_content_before_undo"] = EXPECTED.decode()
        trace = json.loads(trace_path.read_text())
        verify_trace(trace, artifact)
        self.report["trace_summary"] = {key: trace[key] for key in
            ("iterations", "tool_calls_made", "halted_reason", "final_response", "tool_steps")}
        self.report["trace_summary"]["assistant_turns"] = assistant_turns(trace)
        self.report["task_passed"] = True
        self.undo()

    def undo(self) -> None:
        args = ("--root", str(self.workspace))
        before = self.cli("undo-list-before", "undo", "--list", *args)
        count = len(re.findall(r"^\s*seq=", before, flags=re.MULTILINE))
        require(count == 1, f"expected one durable checkpoint, found {count}")
        self.cli("undo", "undo", "-n", "1", *args)
        after = self.cli("undo-list-after", "undo", "--list", *args)
        require(after.strip() == "Checkpoint stack is empty.", "checkpoint list is not empty after undo")
        require(not list(self.workspace.iterdir()), "separate CLI undo did not remove the created file")
        self.report.update(undo_passed=True, undo_snapshots_restored=1, workspace_empty=True)


def tool_events(trace: dict):
    pending = []
    for message in trace.get("messages", []):
        if message.get("role") == "assistant":
            require(not pending, "assistant continued before pending tool results")
            pending = list(message.get("tool_calls", []))
        elif message.get("role") == "tool":
            function = consume_result(pending, message)
            yield (function.get("name"), json.loads(function.get("arguments", "{}")),
                   json.loads(message.get("content", "{}")))
    require(not pending, "assistant tool call has no result")


def consume_result(pending: list, message: dict) -> dict:
    require(bool(pending), "tool result has no pending assistant call")
    call = pending.pop(0)
    require(message.get("tool_call_id") == call.get("id"), "tool result does not match pending call")
    return call.get("function", {})


def verify_identity_and_final(trace: dict) -> None:
    for key, expected in (("schema", "hydra.ask_trace.v1"), ("provider", "ollama"), ("model", MODEL)):
        require(trace.get(key) == expected, f"unexpected trace {key}")
    messages = trace.get("messages", [])
    require(bool(messages), "trace has no actual messages")
    final = messages[-1]
    require(final.get("role") == "assistant", "actual final assistant message absent")
    require(not final.get("tool_calls"), "final assistant still requests tools")
    require(final.get("content", "").strip() == "LIVE_PROOF_OK", "actual final assistant contradicts success")


def assistant_call_turns(trace: dict):
    turn = 0
    for message in trace.get("messages", []):
        if message.get("role") != "assistant":
            continue
        turn += 1
        for call in message.get("tool_calls", []):
            yield call, turn


def assistant_turns(trace: dict) -> int:
    return sum(1 for message in trace.get("messages", []) if message.get("role") == "assistant")


def verify_call_inventory(trace: dict) -> None:
    call_turns = list(assistant_call_turns(trace))
    calls = [call for call, _turn in call_turns]
    names = [call.get("function", {}).get("name") for call in calls]
    require(names == ["fs_write", "fs_read"], "actual assistant call inventory differs from proof")
    steps = trace.get("tool_steps", [])
    require(len(steps) == len(calls), "tool step count differs from actual calls")
    for step, (call, turn) in zip(steps, call_turns):
        verify_step(step, call, turn)


def verify_step(step: dict, call: dict, number: int) -> None:
    require(step.get("step") == number, "tool step order differs from actual calls")
    require(step.get("tool_name") == call["function"]["name"], "tool step name differs from actual call")
    require(step.get("tool_call_id") == call.get("id"), "tool step ID differs from actual call")
    require("tool_error" in step, "tool step has no success/error receipt")
    require(step["tool_error"] is None, "tool step reports a failure")


def verify_event(event: tuple, artifact: Path, operation: str) -> None:
    name, arguments, result = event
    require(name == operation, f"expected {operation}, observed {name}")
    require((artifact.parent / arguments.get("path", "")).resolve() == artifact.resolve(), "tool used wrong path")
    require(result.get("ok") is True, f"{operation} did not report success")
    if operation == "fs_write":
        require(arguments.get("content") == EXPECTED.decode(), "tool wrote incorrect content")
        require(result.get("bytes_written") == 20, "tool did not write 20 bytes")
    else:
        require(result.get("content") == EXPECTED.decode(), "tool read incorrect content")
        require(result.get("bytes_read") == 20, "tool did not read 20 bytes")
        require(result.get("truncated") is False, "tool read was truncated")


def verify_trace(trace: dict, artifact: Path) -> None:
    verify_identity_and_final(trace)
    verify_call_inventory(trace)
    require(trace.get("tool_calls_made") == 2, "expected exactly two real tool calls")
    require(1 <= trace.get("iterations", 0) <= 4, "iteration budget exceeded")
    require(trace.get("halted_reason") == "natural", "model task did not finish naturally")
    require(trace.get("final_response", "").strip() == "LIVE_PROOF_OK", "exact completion marker absent")
    events = list(tool_events(trace))
    require(len(events) == 2, "expected exactly two tool result messages")
    verify_event(events[0], artifact, "fs_write")
    verify_event(events[1], artifact, "fs_read")
    require([step.get("tool_name") for step in trace.get("tool_steps", [])] == ["fs_write", "fs_read"],
            "unexpected tool steps (shell execution is forbidden in this proof)")


def execute(proof: Proof) -> None:
    proof.check_install()
    binary = proof.download_ollama()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 45867))
    with (proof.root / "server.log").open("w") as log:
        server = subprocess.Popen([str(binary), "serve"], cwd=proof.workspace, env=proof.env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            version = wait_ready(server)
            require(server.poll() is None, "owned Ollama process exited during readiness")
            proof.report["ollama_version"] = version
            require(version.get("version") == OLLAMA_VERSION, "Ollama version mismatch")
            proof.pull_model(binary)
            proof.task()
        finally:
            cleanup(server, proof.report)
            proof.report["server_stopped"] = server.poll() is not None
            proof.report["provider_chat_receipts"] = [line for line in
                (proof.root / "server.log").read_text().splitlines() if '"/api/chat"' in line]


def record_runtime_after(report: dict) -> None:
    try:
        after = package_hashes(Path(report["runtime_root"]))
        report["package_hashes_after"] = after
        report["runtime_unchanged"] = after == report.get("package_hashes_before")
        if not report["runtime_unchanged"]:
            report.setdefault("error", "installed runtime package bytes changed during proof")
    except Exception as exc:
        report.setdefault("error", f"runtime verification failed: {type(exc).__name__}: {exc}")


def successful_chat_receipt(line: str) -> bool:
    return re.fullmatch(r'\[GIN\][^|]+\|\s*200\s*\|[^|]+\|[^|]+\|\s*POST\s+"/api/chat"\s*', line) is not None


def chat_receipts_verified(report: dict) -> bool:
    summary = report.get("trace_summary", {})
    receipts = report.get("provider_chat_receipts", [])
    iterations = summary.get("iterations", 0)
    return all((0 < iterations <= 4, summary.get("assistant_turns") == iterations,
        len(receipts) == iterations, all(successful_chat_receipt(line) for line in receipts)))


def finish_report(root: Path, report: dict) -> None:
    if "runtime_root" in report:
        record_runtime_after(report)
    deadlines = [report[key] for key in ("verification_deadline_epoch", "model_task_deadline_epoch")
                 if key in report]
    report["within_deadline"] = all(time.time() <= deadline for deadline in deadlines)
    report["provider_chat_verified"] = chat_receipts_verified(report)
    report["passed"] = all((report.get("task_passed"), report.get("undo_passed"),
        report.get("server_stopped"), report.get("runtime_unchanged"),
        report["within_deadline"], report["provider_chat_verified"],
        not report.get("error"), not report.get("cleanup_errors")))
    report["completed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"passed": report["passed"], "report": str(root / "report.json")}), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {"schema": "hydra.live_vm_acceptance.v1", "passed": False,
        "source_commit": args.source_commit,
        "model": MODEL, "model_digest": MODEL_DIGEST, "ollama_archive_url": ARCHIVE_URL,
        "context_tokens": 12288, "per_call_timeout_seconds": 420, "max_iterations": 4,
        "model_task_timeout_seconds": 900, "verification_timeout_seconds": 1200,
        "inference_attempts": 0, "inference_retries": 0, "commands": [],
        "task_passed": False, "undo_passed": False,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        report["wheel_sha256"] = digest(args.wheel)
        report["wheel_package_hashes"] = wheel_hashes(args.wheel)
        execute(Proof(root, report))
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        finish_report(root, report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
