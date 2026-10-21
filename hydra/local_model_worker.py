"""Isolated optional model SDK execution, with no tool execution or data uploads."""
from __future__ import annotations

import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from hydra.llm import LlmError
from hydra.proc import kill_tree, popen_portable

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_DIAGNOSTIC_BYTES = 1024 * 1024


def _communicate_bounded(process, serialized, output, diagnostics, timeout):
    deadline = time.monotonic() + timeout
    pending = serialized
    try:
        while True:
            if (os.fstat(output.fileno()).st_size > _MAX_RESPONSE_BYTES
                    or os.fstat(diagnostics.fileno()).st_size > _MAX_DIAGNOSTIC_BYTES):
                raise LlmError("local model output exceeded its byte budget")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LlmError(f"local model exceeded {timeout}s")
            try:
                process.communicate(pending, timeout=min(remaining, 0.1))
                break
            except subprocess.TimeoutExpired:
                pending = None
        if os.fstat(diagnostics.fileno()).st_size > _MAX_DIAGNOSTIC_BYTES:
            raise LlmError("local model output exceeded its byte budget")
        output.seek(0)
        raw = output.read(_MAX_RESPONSE_BYTES + 1)
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise LlmError("local model output exceeded its byte budget")
        return raw.decode("utf-8")
    except BaseException:
        kill_tree(process)
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            for name in ("stdin", "stdout", "stderr"):
                stream = getattr(process, name, None)
                if stream is not None:
                    stream.close()
        raise


def predict_local(request: dict, *, timeout: float) -> dict:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise LlmError("local model timeout must be finite and positive")
    try:
        serialized = json.dumps(request, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise LlmError("local model request must contain valid JSON data") from exc
    if len(serialized.encode("utf-8")) > 32 * 1024 * 1024:
        raise LlmError("local model request exceeded its byte budget")
    env = {**os.environ, "NEEDLE_TELEMETRY": "0", "DO_NOT_TRACK": "1",
           "HF_HUB_DISABLE_TELEMETRY": "1", "HF_HUB_DISABLE_PROGRESS_BARS": "1", "USE_TF": "0"}
    if env.get("HYDRA_MODEL_CACHE"):
        env.setdefault("HF_HOME", str(Path(env["HYDRA_MODEL_CACHE"]).expanduser().resolve() / "huggingface"))
    # Use the package root so callers can run outside a checkout without losing
    # the worker import. No shell, and private state is carried only on stdin.
    try:
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        # File-backed output cannot accumulate unbounded SDK diagnostics in RAM.
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as diagnostics:
            process = popen_portable(
                [sys.executable, "-m", "hydra.local_model_worker"],
                stdin=subprocess.PIPE, stdout=output, stderr=diagnostics,
                text=True, encoding="utf-8", env=env,
                cwd=str(Path(__file__).resolve().parent.parent),
                **kwargs,
            )
            stdout = _communicate_bounded(process, serialized, output, diagnostics, timeout)
    except OSError as exc:
        raise LlmError(f"local model process failed: {exc}") from exc
    except UnicodeError as exc:
        raise LlmError("local model worker returned invalid UTF-8") from exc
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise LlmError(f"local {request['backend']} worker returned invalid JSON (exit {process.returncode})") from exc
    if process.returncode or not isinstance(payload, dict) or payload.get("error"):
        detail = payload.get("error", "worker failed") if isinstance(payload, dict) else "invalid response"
        raise LlmError(f"local {request['backend']}: {detail}")
    return payload


def _needle(request: dict) -> dict:
    import needle

    # The SDK's engine cache is not controlled by HF_HOME/XDG_CACHE_HOME.
    # Scope this override to the isolated worker, without repurposing HOME.
    if os.environ.get("HYDRA_MODEL_CACHE"):
        from needle.agent import fetch
        cache = Path(os.environ["HYDRA_MODEL_CACHE"]).expanduser().resolve()
        fetch.cache_dir = lambda generation=3: str(cache / "needle" / f"v{generation}" / fetch.engine_version(generation))

    model = request["model"]
    schemas = [tool.get("function", tool) for tool in request["tools"]]
    if any(not isinstance(tool, dict) or not tool.get("name") for tool in schemas):
        raise ValueError("Needle requires named function schemas")
    options = {"tools": schemas, "auto_date": False}
    system = "\n".join(m.get("content") or "" for m in request["messages"] if m.get("role") == "system")
    if system:
        options["system"] = system
    if model not in {"needle", "needle3", "Cactus-Compute/needle3"}:
        options["weights"] = model
    # A fresh process prevents native SDK state leaking across conversations or
    # tuned archives. Replay user/tool turns without executing any requested call.
    agent = needle.Needle(**options)
    result = None
    pending_results = []

    def flush_results():
        nonlocal result
        if pending_results:
            result = agent.complete(json.dumps(pending_results), max_new_tokens=request["max_tokens"])
            pending_results.clear()

    for message in request["messages"]:
        if message["role"] != "tool":
            flush_results()
        if message["role"] == "user":
            result = agent.complete(message["content"], max_new_tokens=request["max_tokens"])
        elif message["role"] == "assistant" and message.get("tool_calls"):
            from hydra.llm import _parse_tool_calls

            expected = [{"name": c.name, "arguments": c.arguments} for c in _parse_tool_calls(message["tool_calls"])]
            if not isinstance(result, dict) or result.get("function_calls") != expected:
                raise ValueError("Needle cannot faithfully replay this tool history; start a new turn with a chat model")
        elif message["role"] == "tool":
            content = message["content"]
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                pass
            pending_results.append(content)
    flush_results()
    if not isinstance(result, dict):
        raise ValueError("Needle requires at least one user or tool turn")
    return result


def _laya(request: dict) -> dict:
    import laya

    options = {"device": request["device"]} if request.get("device") else {}
    agent = laya.load(request["model"], **options)
    return agent.predict(request["state"], request["questions"])


def _kev(request: dict) -> dict:
    """Thin adapter over Jared Palmer's Apache-2.0 Kev public inference API.

    Source: https://github.com/jaredpalmer/kev. Use the checkpoint's fitted
    temperature and strict context admission; never execute a predicted action.
    """
    import torch
    from kev.api import SystemOneRequest, to_answers, to_record
    from kev.checkpoint import Checkpoint, LoadOptions
    from kev.device import select
    from kev.model import admit

    request_model = request["model"]
    parsed = SystemOneRequest(state=request["state"], model=request_model, questions=request["questions"])
    device = select(request.get("device") or "cpu")
    checkpoint = Checkpoint(request_model)
    # CPU uses fp32. Explicit CUDA/MPS supports the upstream auto backend;
    # an unavailable requested device raises instead of silently substituting.
    options = LoadOptions(backend="auto", dtype=torch.float32 if device == "cpu" else torch.bfloat16)
    tokenizer, model = checkpoint.load(device, options)
    record, metadata = to_record(parsed)
    encoded = admit(model, tokenizer, record, truncate=False)
    started = time.monotonic()
    probabilities = [row.tolist() for row in model.probs(encoded)]
    return {"backend": "kev", "model": request_model,
            "answers": to_answers(probabilities, metadata),
            "usage": {"input_tokens": len(encoded["ids"]), "output_tokens": 0,
                      "state_tokens": encoded["state_tokens"], "truncated": False},
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "executed_actions": False}


def main() -> int:
    try:
        request = json.load(sys.stdin)
        from hydra.local_resources import cpu_budget
        resource_receipt = cpu_budget()
        # Third-party progress output must not corrupt the JSON protocol.
        with contextlib.redirect_stdout(sys.stderr):
            backend = request["backend"]
            if backend == "needle":
                result = _needle(request)
            elif backend == "laya":
                result = _laya(request)
            elif backend == "kev":
                result = _kev(request)
            elif backend == "embedding":
                from hydra.embeddings import embed
                texts = request['texts']
                if not isinstance(texts, list) or not 1 <= len(texts) <= 64:
                    raise ValueError('embedding worker requires 1..64 texts')
                result = {'vectors': [embed(text, request['config']) for text in texts]}
            else:
                raise ValueError(f"unknown local model backend: {backend}")
        result['resource_budget'] = resource_receipt
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except ImportError as exc:
        print(json.dumps({"error": f"Optional model dependency missing: {exc}. Use Hydra's intelligence installer for Needle and official Kev; hydraagent[laya] supplies Laya."}))
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
