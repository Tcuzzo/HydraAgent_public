"""Reflex seam: deterministic R1, schema-checked rung decisions, edit gauntlet.

The decision service resolves the ``reflex`` rung. These tests use a stub
decider and never name a model or a provider.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hydra.llm import ChatResponse, ToolCall
from hydra.loop import AgentLoop, Tool
from hydra.reflex_bench import run_bench
from hydra.reflex_gauntlet import GauntletConfig, after_file_edit
from hydra.reflex_seam import (
    DECISION_SCHEMA_ID,
    ReflexSchemaError,
    ReflexUnavailable,
    reflex_rung_name,
    rule_reflex,
    run_turn,
    validate_decision,
)
from hydra.skill_spine import find_skill, route_skill_records


REPO = Path(__file__).resolve().parents[1]


def _decision(**overrides):
    payload = {
        "schema": DECISION_SCHEMA_ID,
        "next_tool": None,
        "done": False,
        "retry": False,
        "escalate": False,
        "reason": "",
    }
    payload.update(overrides)
    return payload


def test_rule_reflex_maps_a_read_intent_to_a_tool_hint():
    hint = rule_reflex("read README.md")
    assert hint == {"tool": "fs_read", "arguments": {"path": "README.md"}}


def test_rule_reflex_leaves_open_design_work_without_a_tool_hint():
    assert rule_reflex("design a migration plan for the storage layer") is None


def test_reflex_decisions_are_schema_validated():
    done = validate_decision(_decision(done=True, reason="finished"))
    assert done["done"] is True and done["schema"] == DECISION_SCHEMA_ID

    tool = validate_decision(
        _decision(next_tool={"name": "fs_read", "arguments": {"path": "README.md"}})
    )
    assert tool["next_tool"]["name"] == "fs_read"

    retry = validate_decision(_decision(retry=True, reason="once more"))
    assert retry["retry"] is True

    stop = validate_decision(_decision(reason="stop"))
    assert stop["done"] is False and stop["retry"] is False and stop["next_tool"] is None

    escalate = validate_decision(_decision(escalate=True, reason="full loop"))
    assert escalate["escalate"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": DECISION_SCHEMA_ID, "done": True},
        _decision(done=True, escalate=True),
        _decision(done=True, next_tool={"name": "fs_read", "arguments": {}}),
        _decision(done="yes"),
        _decision(next_tool={"arguments": {}}),
        {"schema": "other", "next_tool": None, "done": True, "retry": False, "escalate": False},
    ],
)
def test_reflex_decision_schema_rejects_malformed_payloads(payload):
    with pytest.raises(ReflexSchemaError):
        validate_decision(payload)


def test_missing_reflex_rung_falls_through_without_a_default_model(tmp_path):
    routing = tmp_path / "model_routing.yaml"
    routing.write_text("schema: hydra.model_routing.v2\nroster: []\n", encoding="utf-8")
    seen = {"decider": 0, "big": 0}

    def resolver(rung):
        raise AssertionError(f"resolver asked for {rung}")

    def decider(**_kwargs):
        seen["decider"] += 1
        raise AssertionError("decider ran")

    def big_loop(_prompt):
        seen["big"] += 1
        return "today"

    outcome = run_turn(
        "read README.md",
        resolver=resolver,
        decider=decider,
        big_loop=big_loop,
        routing_path=routing,
    )

    assert seen == {"decider": 0, "big": 1}
    assert outcome["tier"] == "fallthrough"
    assert outcome["big_model_loops"] == 1
    assert outcome["handled"] is True
    assert "model" not in outcome
    assert "provider" not in outcome
    blob = json.dumps(outcome)
    assert "router/" not in blob or outcome.get("route") in (None, "")


def test_unresolved_reflex_rung_falls_through_and_does_not_call_the_decider():
    seen = {"decider": 0, "big": 0}

    def resolver(rung):
        assert rung == "reflex"
        raise ReflexUnavailable("decision service has no reflex rung")

    def decider(**_kwargs):
        seen["decider"] += 1
        return _decision(done=True)

    def big_loop(_prompt):
        seen["big"] += 1
        return "today"

    outcome = run_turn("read README.md", resolver=resolver, decider=decider, big_loop=big_loop)

    assert seen == {"decider": 0, "big": 1}
    assert outcome["tier"] == "fallthrough"
    assert outcome["big_model_loops"] == 1
    assert "model" not in outcome and "provider" not in outcome


def test_routing_file_names_the_reflex_rung_only():
    rung = reflex_rung_name()
    assert rung == "reflex"
    text = (REPO / "hydra" / "model_routing.yaml").read_text(encoding="utf-8")
    block = text.split("reflex:", 1)[1].split("\nroster:", 1)[0]
    assert "rung: reflex" in block
    for forbidden in ("model:", "provider:", "base_url:"):
        assert forbidden not in block


def test_escalate_is_the_only_path_that_spends_a_big_loop_when_the_rung_is_up():
    def resolver(rung):
        assert rung == "reflex"
        return "router/decide/reflex"

    def big_loop(_prompt):
        return "full"

    settled = run_turn(
        "read README.md",
        resolver=resolver,
        decider=lambda **_kwargs: _decision(
            next_tool={"name": "fs_read", "arguments": {"path": "README.md"}}
        ),
        big_loop=big_loop,
        tools={"fs_read": lambda **_kwargs: {"ok": True}},
    )
    assert settled["tier"] == "reflex"
    assert settled["big_model_loops"] == 0
    assert settled["hint"]["tool"] == "fs_read"

    escalated = run_turn(
        "design a migration plan for the storage layer",
        resolver=resolver,
        decider=lambda **_kwargs: _decision(escalate=True, reason="needs the full loop"),
        big_loop=big_loop,
    )
    assert escalated["tier"] == "escalate"
    assert escalated["big_model_loops"] == 1


def _write_calc(root: Path, body: str) -> None:
    (root / "calc.py").write_text(body, encoding="utf-8")
    (root / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )


def test_gauntlet_rejects_a_broken_edit_and_restores_it(tmp_path):
    original = "def add(a, b):\n    return a + b\n"
    _write_calc(tmp_path, original)
    target = tmp_path / "calc.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    config = GauntletConfig(enabled=True, sniper=True, lint=False, format=False, mutation=False)

    verdict = after_file_edit(
        tmp_path,
        ["calc.py"],
        snapshots={str(target): original.encode("utf-8")},
        config=config,
    )

    sniper = next(gate for gate in verdict["gates"] if gate["name"] == "sniper")
    assert verdict["schema"] == "hydra.reflex.gauntlet.v1"
    assert verdict["reject"] is True
    assert sniper["status"] == "fail"
    assert target.read_text(encoding="utf-8") == original
    assert all(not str(gate["status"]).startswith("pass") or gate["name"] == "sniper" for gate in verdict["gates"])


def test_mutation_check_flags_a_surviving_mutant(tmp_path):
    source = "def magnitude(n):\n    return n if n >= 0 else -n\n"
    (tmp_path / "magnitude.py").write_text(source, encoding="utf-8")
    (tmp_path / "test_magnitude.py").write_text(
        "from magnitude import magnitude\n\ndef test_magnitude():\n    assert magnitude(4) == 4\n",
        encoding="utf-8",
    )
    config = GauntletConfig(enabled=True, sniper=True, lint=False, format=False, mutation=True)

    verdict = after_file_edit(tmp_path, ["magnitude.py"], config=config)

    mutation = next(gate for gate in verdict["gates"] if gate["name"] == "mutation")
    assert mutation["status"] == "fail"
    assert "surviv" in mutation["detail"].lower()
    assert verdict["reject"] is True
    assert (tmp_path / "magnitude.py").read_text(encoding="utf-8") == source


def test_missing_lint_tool_is_skipped_and_not_a_pass(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.ruff.format]\nline-length = 88\n", encoding="utf-8")
    (tmp_path / "sample.py").write_text("def ready():\n    return True\n", encoding="utf-8")
    config = GauntletConfig(enabled=True, sniper=False, lint=True, format=True, mutation=False)

    verdict = after_file_edit(
        tmp_path,
        ["sample.py"],
        config=config,
        which=lambda _name: None,
    )

    by_name = {gate["name"]: gate for gate in verdict["gates"]}
    assert by_name["lint"]["status"] == "skipped (tool missing)"
    assert by_name["format"]["status"] == "skipped (tool missing)"
    assert verdict["reject"] is False
    assert all("pass" not in gate["status"] for gate in verdict["gates"])


def test_gauntlet_is_off_unless_enabled_and_does_not_fake_a_pass(tmp_path, monkeypatch):
    monkeypatch.delenv("HYDRA_REFLEX_GAUNTLET", raising=False)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    verdict = after_file_edit(tmp_path, ["calc.py"])

    assert verdict["reject"] is False
    assert verdict["gates"]
    assert all(gate["status"].startswith("skipped") for gate in verdict["gates"])
    assert all("pass" not in gate["status"] for gate in verdict["gates"])


def test_reflex_skill_has_a_use_when_line_and_routes():
    record = find_skill("reflex-seam", REPO / "skills")
    assert "Use when" in record.description
    assert record.path.name == "SKILL.md"
    routed = route_skill_records("explain the reflex seam and the gauntlet hook", REPO / "skills")
    assert "reflex-seam" in [item.name for item in routed]


def test_reflex_skill_is_in_the_catalog_without_validation_errors():
    from hydra.declarative_runtime import doctor_runtime_catalog, load_runtime_catalog

    catalog = load_runtime_catalog(REPO)
    ids = {item.get("skill_id") for item in catalog.skills.get("skills", [])}
    assert "reflex-seam" in ids
    report = doctor_runtime_catalog(catalog)
    errors = [
        finding
        for finding in report.get("findings", [])
        if finding.get("level") == "error" and finding.get("target") == "reflex-seam"
    ]
    assert errors == []


@pytest.mark.reflex_bench
def test_reflex_bench_counts_stub_loops_and_names_the_live_follow_up(tmp_path):
    out = tmp_path / "receipt.json"
    receipt = run_bench(out=out)

    assert receipt["schema"] == "hydra.reflex.bench.v1"
    assert receipt["measurement"] == "stub"
    assert receipt["claim"] == "stub_only"
    assert receipt["without_reflex"]["big_model_loops"] == 5
    assert receipt["with_reflex"]["big_model_loops"] == 1
    assert receipt["loops_saved"] == 4
    assert "real reflex rung" in receipt["follow_up"]
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["loops_saved"] == receipt["loops_saved"]
    blob = json.dumps(receipt).lower()
    assert "real-model savings" not in blob
    assert "provider" not in blob


def test_reflex_bench_cli_writes_the_same_receipt(tmp_path):
    out = tmp_path / "receipt.json"
    proc = subprocess.run(
        [sys.executable, "-m", "hydra", "reflex-bench", "--out", str(out)],
        cwd=REPO,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["without_reflex"]["big_model_loops"] == 5
    assert payload["with_reflex"]["big_model_loops"] == 1


def test_loop_uses_reflex_done_without_calling_the_model(monkeypatch):
    monkeypatch.setenv("HYDRA_REFLEX", "1")

    def resolver(rung):
        assert rung == "reflex"
        return "router/decide/reflex"

    def decider(**_kwargs):
        return _decision(done=True, reason="already done")

    monkeypatch.setattr("hydra.reflex_seam.resolve_route", resolver)
    monkeypatch.setattr("hydra.reflex_seam.default_decider", decider)

    class _Client:
        def chat(self, *_args, **_kwargs):
            raise AssertionError("big-model turn")

    result = AgentLoop(_Client(), model="route-token").run("is the inventory done")
    assert result.halted_reason == "reflex_done"
    assert "already done" in result.final_response
    assert result.iterations == 0


def test_loop_falls_through_when_the_reflex_rung_is_missing(monkeypatch):
    monkeypatch.setenv("HYDRA_REFLEX", "1")

    def resolver(_rung):
        raise ReflexUnavailable("missing")

    monkeypatch.setattr("hydra.reflex_seam.resolve_route", resolver)
    calls = {"n": 0}

    class _Client:
        def chat(self, *_args, **_kwargs):
            calls["n"] += 1
            return ChatResponse(
                content="today",
                model="route-token",
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[],
            )

    result = AgentLoop(_Client(), model="route-token").run("hello")
    assert calls["n"] == 1
    assert result.halted_reason == "natural"
    assert result.final_response == "today"


def test_loop_gauntlet_rejects_a_broken_edit_without_another_model_turn(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRA_REFLEX_GAUNTLET", "1")
    original = "def add(a, b):\n    return a + b\n"
    _write_calc(tmp_path, original)
    calls = {"n": 0}

    class _Client:
        def chat(self, *_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] > 1:
                raise AssertionError("big-model turn spent judging the edit")
            raw = json.dumps({"path": "calc.py", "old_string": "return a + b", "new_string": "return a - b"})
            return ChatResponse(
                content="",
                model="route-token",
                finish_reason="tool_calls",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[
                    ToolCall(
                        id="edit-1",
                        name="fs_edit",
                        arguments_raw=raw,
                        arguments={"path": "calc.py", "old_string": "return a + b", "new_string": "return a - b"},
                    )
                ],
            )

    def edit(path, old_string, new_string):
        target = tmp_path / path
        text = target.read_text(encoding="utf-8")
        target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
        return {"ok": True, "path": path}

    tool = Tool(
        name="fs_edit",
        description="edit a file",
        parameters={"type": "object"},
        invoke=edit,
    )
    result = AgentLoop(_Client(), model="route-token").run(
        "break the adder",
        tools=[tool],
        repo_root=tmp_path,
        max_iterations=4,
    )

    assert calls["n"] == 1
    assert result.halted_reason == "gauntlet_reject"
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == original
    verdict = json.loads(result.final_response)
    assert verdict["reject"] is True


def test_loop_leaves_edits_alone_when_the_gauntlet_is_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HYDRA_REFLEX_GAUNTLET", raising=False)
    (tmp_path / "note.txt").write_text("before\n", encoding="utf-8")
    calls = {"n": 0}

    class _Client:
        def chat(self, *_args, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raw = json.dumps({"path": "note.txt", "content": "after\n"})
                return ChatResponse(
                    content="",
                    model="route-token",
                    finish_reason="tool_calls",
                    prompt_tokens=0,
                    completion_tokens=0,
                    raw={},
                    tool_calls=[
                        ToolCall(
                            id="write-1",
                            name="fs_write",
                            arguments_raw=raw,
                            arguments={"path": "note.txt", "content": "after\n"},
                        )
                    ],
                )
            return ChatResponse(
                content="wrote it",
                model="route-token",
                finish_reason="stop",
                prompt_tokens=0,
                completion_tokens=0,
                raw={},
                tool_calls=[],
            )

    def write(path, content):
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    tool = Tool(name="fs_write", description="write", parameters={"type": "object"}, invoke=write)
    result = AgentLoop(_Client(), model="route-token").run(
        "write the note",
        tools=[tool],
        repo_root=tmp_path,
        max_iterations=3,
    )
    assert result.halted_reason == "natural"
    assert result.final_response == "wrote it"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "after\n"
