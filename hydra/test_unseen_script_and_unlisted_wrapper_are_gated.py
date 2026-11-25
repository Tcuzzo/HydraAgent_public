"""Ball 1A, round 4 — the gate fails closed on what the classifier cannot see.

Two narrowed instances of one class, both found by the round-3 blind seat:

1. A destructive word behind a wrapper nobody listed (flock, strace, ssh host ...)
   ran with no approval, while ``rm`` was gated behind every wrapper.
2. A script the classifier never sees (stdin, a pipe, a here-string, a heredoc,
   process substitution) or a command word that is an expansion (``$x``,
   ``${X:-cp}``, ``cp${IFS}a``, an alias) ran with no approval for every word.

Real ``ApprovalPolicy("ask")`` and real ``classify_tool_call``; nothing is mocked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.operator_auth import AUTH_DIR_ENV_VAR
from hydra.policy import ApprovalDenied, ApprovalPolicy

WORDS = ("cp", "mv", "chmod", "chown")

UNLISTED_WRAPPERS = (
    "flock /tmp/l {w} a b",
    "taskset 1 {w} a b",
    "strace {w} a b",
    "unshare -r {w} a b",
    "chroot / {w} a b",
    "parallel {w} {{}} x ::: a",
    "coproc {w} a b",
    "setpriv --reuid=1 {w} a b",
    "nsenter {w} a b",
    "script -c '{w} a b' /dev/null",
    "sg g -c '{w} a b'",
    "runuser -l u -c '{w} a b'",
    "env -S '{w} a b'",
    "ssh host {w} a b",
    "bash -c -- '{w} a b'",
    "some-future-wrapper --flag {w} a b",
)

UNSEEN_SCRIPTS = (
    "bash <<< '{w} a b'",
    "echo '{w} a b' | sh",
    "printf '{w} a b' | bash",
    "echo '{w} a b' | timeout 5 bash",
    "bash <(echo {w} a b)",
    "source <(echo {w} a b)",
    "at now <<< '{w} a b'",
    "sh <<EOF\n{w} a b\nEOF",
    "bash < script.txt",
    "sh -s",
    "zsh -s",
    "dash",
    "ksh <<< 'x'",
    "${{X:-{w}}} a b",
    "x={w}; $x a b",
    "alias c={w}; c a b",
    "{w}${{IFS}}a${{IFS}}b",
    "$'{w}' a b",
)

RM_UNSEEN = (
    "bash <<< 'rm a'",
    "echo 'rm a' | sh",
    "bash <(echo rm a)",
    "sh -s",
    "x=rm; $x a",
    "${X:-rm} a",
    "rm${IFS}a",
    "flock /tmp/l rm a",
    "ssh host rm a",
)

GATED = (
    [shape.format(w=w) for w in WORDS for shape in UNLISTED_WRAPPERS + UNSEEN_SCRIPTS]
    + list(RM_UNSEEN)
)

SAFE = (
    "ls",
    "ls -la /tmp",
    "cat a",
    "cat -n hydra/autonomy.py",
    "pwd",
    "git status --short",
    "git diff -- cp.py",
    "git log --oneline -5",
    "git commit -m 'fix cp bug'",
    "git diff --check",
    "echo cp",
    "echo mv a b",
    'echo "cp a b"',
    "echo 'chmod 777 x'",
    "grep mv f",
    "grep -r cp .",
    "grep -rn chown src/",
    "grep 'cp a b' notes.txt",
    "pytest -k cp",
    "python3 -m pytest -q -k mv",
    "which cp",
    "man cp",
    "find . -name cp",
    "find . -exec grep mv {} \\;",
    "wc -l f",
    "head -5 f",
    "diff a b",
    "for f in x; do echo cp; done",
    "if true; then echo mv; fi",
    "bash script.sh",
    "bash -c 'echo cp'",
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


def test_there_are_thirty_one_safe_commands() -> None:
    assert len(SAFE) == 31


@pytest.mark.parametrize("command", GATED)
def test_unseen_or_unlisted_form_needs_approval(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "needs_approval", command


@pytest.mark.parametrize("command", GATED)
def test_unseen_or_unlisted_form_is_stopped_by_the_real_policy(
    policy: ApprovalPolicy, command: str
) -> None:
    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": command})


@pytest.mark.parametrize("command", ["flock /tmp/l cp a b", "echo cp | sh", "$x a b"])
def test_deny_mode_blocks_them(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "deny")["decision"] == "blocked"


@pytest.mark.parametrize("command", SAFE)
def test_the_safe_commands_still_run_free(policy: ApprovalPolicy, command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "auto_allow", command
    policy.require("bash", {"command": command})
