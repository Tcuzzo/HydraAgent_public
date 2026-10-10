"""Behavioral regressions found at the loop / mission runtime boundaries."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydra import autonomous_mission as serial
from hydra import autonomous_mission_parallel as parallel
from hydra.llm import ChatResponse, LlmError, ToolCall
from hydra.loop import AgentLoop, Tool


def response(content="", tool_calls=None):
    return ChatResponse(content=content, model="test", finish_reason="stop",
                        prompt_tokens=0, completion_tokens=0, raw={},
                        tool_calls=tool_calls or [])


class ScriptedClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def chat(self, messages, **kwargs):
        self.requests.append([dict(message) for message in messages])
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.mark.parametrize("result", [{"path": Path("README.md")}, [{1, 2}]])
def test_unserializable_tool_results_become_recoverable_errors(result):
    client = ScriptedClient([
        response(tool_calls=[ToolCall(id="a", name="read", arguments_raw="{}", arguments={})]),
        response("Recovered from the tool error"),
    ])
    loop = AgentLoop(client, model="test")
    outcome = loop.run("read", tools=[Tool("read", "read", {}, lambda: result)])
    assert outcome.final_response == "Recovered from the tool error"
    tool_message = next(m for m in client.requests[1] if m["role"] == "tool")
    assert "error" in json.loads(tool_message["content"])
    assert not outcome.has_real_tool_activity
    assert outcome.steps[1].tool_error


def test_plain_text_recovery_never_dispatches_a_bridged_tool():
    invoked = []
    client = ScriptedClient([
        LlmError("HTTP 400 tool_use_failed"),
        response('function: read path="README.md"'),
        response("done"),
    ])
    result = AgentLoop(client, model="test").run(
        "read", tools=[Tool("read", "read", {}, lambda **args: invoked.append(args))],
    )
    assert invoked == []
    assert result.phantom_tool_recovery
    assert result.tool_calls_made == 0


def test_prior_iteration_limit_instruction_does_not_leak_into_new_turn():
    client = ScriptedClient([response("new answer")])
    AgentLoop(client, model="test").run("new task", initial_messages=[
        {"role": "system", "content": "SYNTHESIS REQUIRED: STOP collecting more information."},
        {"role": "system", "content": "HYDRA FINAL SYNTHESIS REQUIRED: You are out of iterations."},
        {"role": "assistant", "content": "old answer"},
    ])
    assert not any("SYNTHESIS REQUIRED" in (m.get("content") or "") for m in client.requests[0])


@pytest.mark.parametrize("module,runner", [
    (serial, serial.run_autonomous_mission_v2),
    (parallel, parallel.run_autonomous_mission_v3),
])
@pytest.mark.parametrize("max_cycles", [0, -1])
def test_mission_rejects_invalid_cycle_budget_before_writing(tmp_path, module, runner, max_cycles):
    with pytest.raises(module.AutonomousMissionError, match="max_cycles"):
        runner(mission_id="test", prompt="task", plan_path=tmp_path / "unused.json",
               repo_root=tmp_path, max_cycles=max_cycles)
    assert list(tmp_path.iterdir()) == []


def test_parallel_mission_path_cannot_escape_evidence_root(tmp_path):
    evidence = tmp_path / "evidence"
    path = parallel._mission_dir(tmp_path, evidence, "../../outside")
    assert path.resolve().is_relative_to(evidence.resolve())


def test_real_review_findings_are_carried_to_next_mission_cycle(tmp_path, monkeypatch):
    goals = []

    def build(plan, *, batch_id, goal):
        goals.append(goal)
        return {"batch_id": batch_id, "goal": goal, "jobs": []}

    monkeypatch.setattr(serial, "build_worker_batch_from_plan", build)
    monkeypatch.setattr(serial, "run_worker_batch", lambda *a, **kw: {
        "status": "passed", "run_dir": str(tmp_path),
    })
    # Actual worker_review returns checks and child_reviews, not an issues list.
    monkeypatch.setattr(serial, "review_worker_run", lambda *a: {
        "verdict": "rejected", "checks": [], "child_reviews": [{
            "checks": [{"name": "commands_green", "passed": False, "detail": "one failed command"}],
        }],
    })
    serial.run_autonomous_mission_v2(mission_id="review", prompt="repair", repo_root=tmp_path,
                                     plan_path=tmp_path / "unused.json", max_cycles=2)
    assert "commands_green" in goals[1]
    assert "one failed command" in goals[1]
