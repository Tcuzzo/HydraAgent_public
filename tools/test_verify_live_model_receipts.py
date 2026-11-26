"""An acceptance receipt must include matching successful owned-server calls."""
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("live_vm_receipts", Path(__file__).with_name("verify_live_model.py"))
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
CHAT_200 = '[GIN] 2026/11/25 - 20:47:46 | 200 |    3m10s |       127.0.0.1 | POST     "/api/chat"'


def report_fixture():
    return {"task_passed": True, "undo_passed": True, "server_stopped": True,
        "runtime_unchanged": True, "trace_summary": {"iterations": 3, "assistant_turns": 3},
        "provider_chat_receipts": [CHAT_200] * 3}


def test_successful_owned_chat_calls_allow_acceptance(tmp_path):
    report = report_fixture()
    verifier.finish_report(tmp_path, report)
    assert report["passed"] is True
    assert json.loads((tmp_path / "report.json").read_text())["passed"] is True


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda r: r.pop("provider_chat_receipts"), id="missing-calls"),
    pytest.param(lambda r: r.update(provider_chat_receipts=[]), id="empty-calls"),
    pytest.param(lambda r: r["provider_chat_receipts"].pop(), id="too-few-calls"),
    pytest.param(lambda r: r["provider_chat_receipts"].append(CHAT_200), id="extra-call"),
    pytest.param(lambda r: r["provider_chat_receipts"].__setitem__(0, CHAT_200.replace("200", "500")), id="failed-call"),
    pytest.param(lambda r: r["provider_chat_receipts"].__setitem__(0, CHAT_200.replace("/api/chat", "/api/tags")), id="wrong-endpoint"),
    pytest.param(lambda r: r["provider_chat_receipts"].__setitem__(0, CHAT_200.replace("POST", "GET")), id="wrong-method"),
    pytest.param(lambda r: r["trace_summary"].update(assistant_turns=2), id="turn-count-mismatch"),
    pytest.param(lambda r: r["trace_summary"].update(iterations=2), id="iteration-count-mismatch"),
])
def test_unproven_model_activity_cannot_pass(tmp_path, mutate):
    report = report_fixture()
    mutate(report)
    verifier.finish_report(tmp_path, report)
    assert report["passed"] is False
