"""Trace acceptance contracts; these never run or replace model inference."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest


SPEC = importlib.util.spec_from_file_location("live_vm_verifier", Path(__file__).with_name("verify_live_model.py"))
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def trace_fixture():
    messages = []
    steps = []
    for number, name in enumerate(("fs_write", "fs_read"), 1):
        arguments = {"path": "proof.txt"}
        result = {"ok": True, "path": "proof.txt"}
        if name == "fs_write":
            arguments["content"] = verifier.EXPECTED.decode()
            result["bytes_written"] = 20
        else:
            result.update(bytes_read=20, content=verifier.EXPECTED.decode(), truncated=False)
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_0", "type": "function",
             "function": {"name": name, "arguments": json.dumps(arguments)}}]},
            {"role": "tool", "tool_call_id": "call_0", "content": json.dumps(result)},
        ])
        steps.append({"step": number, "tool_name": name, "tool_call_id": "call_0", "tool_error": None})
    messages.append({"role": "assistant", "content": "LIVE_PROOF_OK"})
    return {"schema": "hydra.ask_trace.v1", "provider": "ollama", "model": verifier.MODEL,
        "iterations": 3, "tool_calls_made": 2, "halted_reason": "natural", "final_response": "LIVE_PROOF_OK",
        "messages": messages, "tool_steps": steps}


def extra_call(trace, name):
    call = copy.deepcopy(trace["messages"][0])
    call["tool_calls"][0]["function"]["name"] = name
    trace["messages"].insert(0, call)


def test_valid_trace_accepts_reused_ids_in_distinct_turns(tmp_path):
    verifier.verify_trace(trace_fixture(), tmp_path / "proof.txt")


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda t: t["messages"].pop(), id="missing-final-assistant"),
    pytest.param(lambda t: t["messages"][-1].update(content="I could not verify the file."), id="contradictory-final"),
    pytest.param(lambda t: t["tool_steps"][0].update(tool_error="write failed"), id="failed-step"),
    pytest.param(lambda t: t.update(provider="mock"), id="wrong-provider"),
    pytest.param(lambda t: t.update(model="other-model"), id="wrong-model"),
    pytest.param(lambda t: t.update(schema="other-schema"), id="wrong-schema"),
    pytest.param(lambda t: extra_call(t, "bash"), id="unmatched-shell-call"),
    pytest.param(lambda t: extra_call(t, "fs_write"), id="unmatched-permitted-call"),
    pytest.param(lambda t: t["messages"][0]["tool_calls"].append(copy.deepcopy(t["messages"][0]["tool_calls"][0])), id="duplicate-call-in-batch"),
    pytest.param(lambda t: t["messages"][1].update(tool_call_id="unmatched"), id="unmatched-result"),
    pytest.param(lambda t: t["tool_steps"][0].update(tool_call_id="unmatched"), id="unmatched-step"),
    pytest.param(lambda t: t["tool_steps"][0].update(step=2), id="wrong-step-number"),
    pytest.param(lambda t: t.update(halted_reason="max_iterations"), id="forced-halt-control"),
    pytest.param(lambda t: t["messages"][3].update(content='{"ok":false}'), id="failed-read-control"),
])
def test_invalid_trace_is_rejected(tmp_path, mutate):
    trace = trace_fixture()
    mutate(trace)
    with pytest.raises(RuntimeError):
        verifier.verify_trace(trace, tmp_path / "proof.txt")


def test_expired_budget_cannot_launch_process(tmp_path):
    proof = verifier.Proof(tmp_path, {"commands": []})
    proof.deadline = time.monotonic() - 1
    marker = proof.workspace / "late.txt"
    with pytest.raises(RuntimeError, match="deadline"):
        proof.run("too-late", [sys.executable, "-c",
            "from pathlib import Path; Path('late.txt').write_text('executed')"])
    assert not marker.exists()


def test_expired_budget_cannot_produce_success_receipt(tmp_path):
    report = {"task_passed": True, "undo_passed": True, "server_stopped": True,
        "runtime_unchanged": True, "verification_deadline_epoch": time.time() - 1}
    verifier.finish_report(tmp_path, report)
    assert report["passed"] is False
