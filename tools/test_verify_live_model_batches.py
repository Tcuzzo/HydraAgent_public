"""LoopStep.step identifies an assistant iteration, including batched calls."""
import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("trace_contract_fixture", Path(__file__).with_name("test_verify_live_model.py"))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def batched_trace():
    trace = fixture.trace_fixture()
    write, written, read, read_result, final = trace["messages"]
    read["tool_calls"][0]["id"] = "call_1"
    read_result["tool_call_id"] = "call_1"
    write["tool_calls"].extend(read["tool_calls"])
    trace["messages"] = [write, written, read_result, final]
    trace["iterations"] = 2
    trace["tool_steps"][1].update(step=1, tool_call_id="call_1")
    return trace


def test_valid_write_read_batch_shares_assistant_step(tmp_path):
    trace = batched_trace()
    fixture.verifier.verify_trace(trace, tmp_path / "proof.txt")
    assert fixture.verifier.assistant_turns(trace) == 2


def test_batch_cannot_claim_a_nonexistent_assistant_step(tmp_path):
    trace = batched_trace()
    trace["tool_steps"][1]["step"] = 2
    with pytest.raises(RuntimeError):
        fixture.verifier.verify_trace(trace, tmp_path / "proof.txt")
