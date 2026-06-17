"""S6 — Life-support fallback tests.

Proves: on a provider failure during an autonomous/local mission the agent
does NOT crash. It classifies the error (auth vs timeout), switches to the
local never-expires life-support model (ollama/qwen2.5-coder:7b),
checkpoints to evidence/{mission_id}/s6_pause_checkpoint.json, drives the
ledger running->blocked with a provider reason, returns
halted_reason='provider_fallback', resumes from the checkpoint once the
provider is healthy again, and surfaces the silent substitution to the
operator (it is never silent).

All tests inject a failing client — no real network, fully deterministic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydra.emergency_fallback import (
    LIFE_SUPPORT_MODEL,
    LIFE_SUPPORT_PROVIDER,
    classify_provider_error,
    engage_life_support_fallback,
)
from hydra.llm import ChatResponse, LlmError
from hydra.loop import AgentLoop
from hydra import workbench_ledger as wl
from hydra.continuation import resume_blocked_mission, S6_CHECKPOINT_NAME


# --- injected clients ----------------------------------------------------


class _AuthFailClient:
    """Raises an auth-class LlmError on every chat (e.g. 401/invalid key)."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, _messages, *, model, max_tokens=0, temperature=0.0, timeout=0.0, tools=None):
        self.calls += 1
        raise LlmError("HTTP 401 from https://api.example-cloud.com/...: API key invalid")


class _TimeoutFailClient:
    """Raises a timeout/connection-class LlmError on every chat."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, _messages, *, model, max_tokens=0, temperature=0.0, timeout=0.0, tools=None):
        self.calls += 1
        raise LlmError("timed out talking to https://api.ollama.cloud/... after 60.0s")


class _LocalClient:
    """Stand-in for the local life-support client. Always answers."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, _messages, *, model, max_tokens=0, temperature=0.0, timeout=0.0, tools=None):
        self.calls += 1
        return ChatResponse(
            content="local life-support reply",
            model=model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
            raw={},
        )


# --- classification (the bare-except fix) --------------------------------


def test_classify_auth_signatures():
    assert classify_provider_error(LlmError("HTTP 401 from x: Unauthorized")) == "auth"
    assert classify_provider_error(LlmError("HTTP 403 from x: forbidden")) == "auth"
    assert classify_provider_error(LlmError("x: API key invalid")) == "auth"
    assert classify_provider_error(LlmError("Unauthorized")) == "auth"


def test_classify_timeout_and_connection_signatures():
    assert classify_provider_error(LlmError("timed out talking to x after 60.0s")) == "timeout"
    assert classify_provider_error(LlmError("could not reach x: Connection refused")) == "connection"


def test_classify_other_is_not_swallowed_as_auth_or_timeout():
    # A 500 / malformed response is neither auth nor timeout — must be distinguishable.
    assert classify_provider_error(LlmError("HTTP 500 from x: internal error")) == "other"
    assert classify_provider_error(LlmError("non-JSON response from x")) == "other"


# --- engage helper: switch client + checkpoint + surface -----------------


def test_engage_writes_checkpoint_and_surfaces_substitution(tmp_path):
    local = _LocalClient()
    fallback = engage_life_support_fallback(
        error=LlmError("HTTP 401: API key invalid"),
        requested_provider="ollama-cloud",
        mission_id="mission-x",
        repo_root=tmp_path,
        checkpoint_state={"messages": [{"role": "user", "content": "hi"}], "iteration": 3},
        local_client_factory=lambda: (local, LIFE_SUPPORT_MODEL),
    )
    # classified auth
    assert fallback["error_class"] == "auth"
    # switched to the local never-expires model
    assert fallback["client"] is local
    assert fallback["substitution"]["downgraded_to_local"] is True
    assert fallback["substitution"]["used"] == LIFE_SUPPORT_MODEL
    assert fallback["substitution"]["requested"] == "ollama-cloud"
    # checkpoint written to the spec path
    ckpt = tmp_path / "evidence" / "mission-x" / S6_CHECKPOINT_NAME
    assert ckpt.exists()
    data = json.loads(ckpt.read_text())
    assert data["iteration"] == 3
    assert data["mission_id"] == "mission-x"
    assert data["error_class"] == "auth"


def test_engage_timeout_uses_timeout_reason(tmp_path):
    local = _LocalClient()
    fallback = engage_life_support_fallback(
        error=LlmError("timed out talking to x after 60.0s"),
        requested_provider="ollama-cloud",
        mission_id="mission-t",
        repo_root=tmp_path,
        checkpoint_state={"messages": [], "iteration": 1},
        local_client_factory=lambda: (local, LIFE_SUPPORT_MODEL),
    )
    assert fallback["error_class"] == "timeout"
    assert fallback["ledger_reason"] == "provider_timeout_fallback_engaged"


def test_engage_auth_uses_auth_reason(tmp_path):
    fallback = engage_life_support_fallback(
        error=LlmError("HTTP 403: forbidden"),
        requested_provider="ollama-cloud",
        mission_id="mission-a",
        repo_root=tmp_path,
        checkpoint_state={"messages": [], "iteration": 1},
        local_client_factory=lambda: (_LocalClient(), LIFE_SUPPORT_MODEL),
    )
    assert fallback["ledger_reason"] == "provider_auth_fallback_engaged"


# --- AgentLoop autonomous path: no crash, halts with provider_fallback ---


def test_agentloop_auth_failure_engages_fallback_no_crash(tmp_path):
    local = _LocalClient()
    loop = AgentLoop(_AuthFailClient(), model="cloud-model")
    result = loop.run(
        "do the mission",
        autonomous=True,
        mission_id="m-auth",
        repo_root=tmp_path,
        requested_provider="ollama-cloud",
        local_client_factory=lambda: (local, LIFE_SUPPORT_MODEL),
    )
    assert result.halted_reason == "provider_fallback"
    # switched to local life-support
    assert result.fallback_engaged is True
    assert result.fallback_error_class == "auth"
    assert result.substitution["downgraded_to_local"] is True
    assert result.substitution["used"] == LIFE_SUPPORT_MODEL
    # checkpoint written
    ckpt = tmp_path / "evidence" / "m-auth" / S6_CHECKPOINT_NAME
    assert ckpt.exists()


def test_agentloop_timeout_failure_engages_fallback(tmp_path):
    local = _LocalClient()
    loop = AgentLoop(_TimeoutFailClient(), model="cloud-model")
    result = loop.run(
        "do the mission",
        autonomous=True,
        mission_id="m-timeout",
        repo_root=tmp_path,
        requested_provider="ollama-cloud",
        local_client_factory=lambda: (local, LIFE_SUPPORT_MODEL),
    )
    assert result.halted_reason == "provider_fallback"
    assert result.fallback_error_class == "timeout"
    assert result.fallback_engaged is True


def test_interactive_path_still_raises(tmp_path):
    # Non-autonomous interactive chat must NOT silently swallow errors.
    loop = AgentLoop(_AuthFailClient(), model="cloud-model")
    with pytest.raises(LlmError):
        loop.run("hello")


# --- ledger: running -> blocked with provider reason + checkpoint field --


def _planned_record(slice_id="m-led"):
    return wl.create_record(
        slice_id=slice_id,
        title="autonomous mission",
        owner_lane="cloud_model",
        goal="ship the slice",
        scope=["one"],
        non_goals=["none"],
    )


def test_ledger_running_to_blocked_with_provider_reason_and_checkpoint(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    rec = _planned_record()
    wl.append_record(ledger, rec)
    running = wl.update_record(ledger, "m-led", status="running")
    assert running.status == "running"
    ckpt_rel = "evidence/m-led/s6_pause_checkpoint.json"
    blocked = wl.update_record(
        ledger,
        "m-led",
        status="blocked",
        failure_reason="provider_timeout_fallback_engaged",
        s6_fallback_checkpoint=ckpt_rel,
    )
    assert blocked.status == "blocked"
    assert blocked.failure_reason == "provider_timeout_fallback_engaged"
    assert blocked.s6_fallback_checkpoint == ckpt_rel
    # round-trips through disk
    reloaded = wl.load_records(ledger)[0]
    assert reloaded.s6_fallback_checkpoint == ckpt_rel


# --- resume: blocked + checkpoint + healthy provider ---------------------


def test_resume_blocked_mission_from_checkpoint_when_healthy(tmp_path):
    # Arrange: a blocked mission with an s6 checkpoint on disk.
    mission_dir = tmp_path / "evidence" / "m-resume"
    mission_dir.mkdir(parents=True)
    ckpt = mission_dir / S6_CHECKPOINT_NAME
    ckpt.write_text(json.dumps({
        "mission_id": "m-resume",
        "iteration": 2,
        "messages": [{"role": "user", "content": "continue"}],
        "error_class": "timeout",
    }))
    ledger = tmp_path / "ledger.jsonl"
    wl.append_record(ledger, _planned_record("m-resume"))
    wl.update_record(ledger, "m-resume", status="running")
    wl.update_record(
        ledger, "m-resume", status="blocked",
        failure_reason="provider_timeout_fallback_engaged",
        s6_fallback_checkpoint="evidence/m-resume/" + S6_CHECKPOINT_NAME,
    )

    # Act: provider is healthy again (probe returns True).
    outcome = resume_blocked_mission(
        repo_root=tmp_path,
        ledger_path=ledger,
        slice_id="m-resume",
        probe=lambda: True,
    )

    assert outcome["resumed"] is True
    assert outcome["checkpoint"]["iteration"] == 2
    # ledger walked blocked -> running on resume
    rec = wl.load_records(ledger)[0]
    assert rec.status == "running"


def test_resume_refuses_when_provider_unhealthy(tmp_path):
    mission_dir = tmp_path / "evidence" / "m-down"
    mission_dir.mkdir(parents=True)
    (mission_dir / S6_CHECKPOINT_NAME).write_text(json.dumps({"mission_id": "m-down", "iteration": 1, "messages": []}))
    ledger = tmp_path / "ledger.jsonl"
    wl.append_record(ledger, _planned_record("m-down"))
    wl.update_record(ledger, "m-down", status="running")
    wl.update_record(
        ledger, "m-down", status="blocked",
        failure_reason="provider_auth_fallback_engaged",
        s6_fallback_checkpoint="evidence/m-down/" + S6_CHECKPOINT_NAME,
    )
    outcome = resume_blocked_mission(
        repo_root=tmp_path,
        ledger_path=ledger,
        slice_id="m-down",
        probe=lambda: False,
    )
    assert outcome["resumed"] is False
    # stays blocked
    assert wl.load_records(ledger)[0].status == "blocked"
