"""Ball 1A, round 3 — a destructive command word is gated wherever it can run.

The class: ``cp``/``mv``/``chmod``/``chown`` were gated only as the first word of
a pipeline segment, so a wrapper, a nested shell, a subshell, a loop body or a
``find -exec`` ran them with no approval. These tests use the real classifier and
a real ``ApprovalPolicy("ask")``; nothing is mocked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.operator_auth import AUTH_DIR_ENV_VAR
from hydra.policy import ApprovalDenied, ApprovalPolicy

WORDS = ("cp", "mv", "chmod", "chown")

# Every shape that can put a command word somewhere other than first.
SHAPES = (
    "{w} a b",
    "nice {w} a b",
    "timeout 5 {w} a b",
    "nohup {w} a b",
    "env FOO=1 {w} a b",
    "busybox {w} a b",
    "command {w} a b",
    "exec {w} a b",
    "xargs {w} a b",
    "xargs -I {{}} {w} {{}} /x",
    "/bin/{w} a b",
    "echo hi && {w} a b",
    "echo hi ; {w} a b",
    "echo hi | {w} a b",
    "false || {w} a b",
    "sleep 1 & {w} a b",
    "({w} a b)",
    "{{ {w} a b; }}",
    "if true; then {w} a b; fi",
    "while true; do {w} a b; done",
    "for f in x; do {w} $f y; done",
    "bash -c '{w} a b'",
    "sh -c '{w} a b'",
    "bash -lc '{w} a b'",
    "env bash -c '{w} a b'",
    "timeout 5 sh -c '{w} a b'",
    "eval '{w} a b'",
    "find . -exec {w} {{}} /x \\;",
    "find . -execdir {w} {{}} /x \\;",
    "find . -type f -exec {w} {{}} /x +",
    "bash -c \"if true; then {w} a b; fi\"",
)

CASES = [pytest.param(shape.format(w=word), id=f"{word}:{shape}") for word in WORDS for shape in SHAPES]

SAFE = (
    "ls",
    "ls -la /tmp",
    "cat a",
    "echo cp",
    "echo mv a b",
    'echo "cp a b"',
    "echo 'chmod 777 x'",
    "grep mv file",
    "grep -rn chown src/",
    "grep 'cp a b' notes.txt",
    "bash script.sh",
    "bash -c 'echo cp'",
    "bash -c 'grep mv file'",
    "git status --short",
    "python3 -m pytest -q",
    "find . -name cp",
    "find . -exec grep mv {} \\;",
)


@pytest.fixture()
def policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApprovalPolicy:
    monkeypatch.setenv(AUTH_DIR_ENV_VAR, str(tmp_path / "auth"))
    monkeypatch.chdir(tmp_path)
    return ApprovalPolicy(
        "ask",
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )


@pytest.mark.parametrize("command", CASES)
def test_destructive_word_needs_approval(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "needs_approval", command


@pytest.mark.parametrize("command", CASES)
def test_destructive_word_is_stopped_by_the_real_policy(
    policy: ApprovalPolicy, command: str
) -> None:
    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": command})


@pytest.mark.parametrize("command", ["bash -c 'cp a b'", "timeout 5 mv a b", "(chmod 777 x)"])
def test_deny_mode_blocks_the_wrapped_word(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "deny")
    assert decision["decision"] == "blocked", command


@pytest.mark.parametrize("command", ["bash -c 'cp a", "sh -c", "if true; then cp a b"])
def test_a_command_that_cannot_be_parsed_is_gated(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "needs_approval", command


@pytest.mark.parametrize("command", SAFE)
def test_plain_safe_commands_still_run_free(policy: ApprovalPolicy, command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "auto_allow", command
    policy.require("bash", {"command": command})
