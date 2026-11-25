"""Ball 1A, round 10 — a glob may only stand where a FILE operand stands; nesting is bounded.

The shell expands an unquoted glob AFTER the classifier judged it. So a glob in a word the
program reads as CODE or a PATTERN (the sed script, the awk program, the grep/rg/ag pattern)
lets a file name become the program: ``sed s*x*y* in`` ran the script ``s,a,b,w xy`` that a
file in the working directory was named. The rule is one: an unquoted glob is allowed only
in a file-operand position, for every table entry.

Also: ``ssh h ssh h ...`` (400 deep) raised RecursionError out of the classifier. Re-judging
is bounded; past the bound a command needs approval and nothing ever raises.

Real ``ApprovalPolicy("ask")`` and real ``classify_tool_call``. Each landed form is executed
for real in a temp dir (only when the policy lets it run) and every file is hashed before and
after.
"""
from __future__ import annotations

import hashlib
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.operator_auth import AUTH_DIR_ENV_VAR
from hydra.policy import ApprovalDenied, ApprovalPolicy

AWK_NAME = 'BEGIN{system("touch PWNED_awk")}'
SED_WRITE = "s,a,b,w xy"
SED_EXEC = "1,3s,touch,touch,e"

CODE_POSITION_GLOBS = (
    "sed s*x*y* in",
    "sed s?x?y? in",
    "sed 1*3*e in",
    "sed 1*3*s* in",
    "sed p* in",
    "sed -n p* in",
    "sed -n s*x*y*p in",
    "awk B* in",
    "awk B*",
    "awk -F, B* in",
    "awk N* in",
    "grep a* in",
    "grep a?c in",
    "grep -n a* in",
    "grep -rn x* .",
    "rg a* in",
    "rg a?c ./src",
    "ag a* in",
    "ag a?c ./src",
)

DEEP = (
    "ssh h " * 400 + "ls",
    "ssh h " * 20 + "ls",
    "find . -exec " * 400 + "ls {} \\;",
)

KEYWORD_FLOOD = "if " * 40000 + "ls"

# (command, files before). Each form expanded a file name into code and ran it.
REAL_EXEC = (
    ("sed s*x*y* in", {SED_WRITE: "", "in": "a\n"}),
    ("sed 1*3*e in", {SED_EXEC: "", "in": "touch PWNED_sed\n"}),
    ("awk B* in", {AWK_NAME: "", "in": "1\n"}),
)

SAFE = (
    "sed 's/a/b/' in",
    "sed -n p ./*",
    "sed -n p in ./*",
    "sed -e 's/a/b/' ./*.txt",
    "sed -e 's/a/b/' -e 's/c/d/' ./*",
    "sed --expression='s/a/b/' ./*",
    "awk '{print}' ./*.txt",
    "awk '{print $1}' in",
    "grep foo *.py",
    "grep -n -i foo *.py",
    "grep -e foo *.py",
    "grep -rn x ./*",
    "grep 'a*' in",
    "rg foo ./src",
    "rg -e foo ./*",
    "rg 'a*' ./src",
    "ag foo ./*",
    "ls *",
    "cat *.py",
    "wc -l *.py",
    "head -n 3 *.py",
    "ssh host ls",
    "ssh host bash -c 'ls'",
    "bash -c \"bash -c 'ls'\"",
)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture()
def policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApprovalPolicy:
    monkeypatch.setenv(AUTH_DIR_ENV_VAR, str(tmp_path / "auth"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return ApprovalPolicy(
        "ask",
        approval_path=tmp_path / "approvals.jsonl",
        run_path=tmp_path / "runs.jsonl",
    )


@pytest.mark.parametrize("command", CODE_POSITION_GLOBS)
def test_a_glob_in_a_code_position_needs_approval(command: str) -> None:
    decision = classify_tool_call("bash", {"command": command}, "ask")
    assert decision["decision"] == "needs_approval", command


@pytest.mark.parametrize("command", CODE_POSITION_GLOBS)
def test_a_glob_in_a_code_position_is_stopped_by_the_real_policy(policy: ApprovalPolicy, command: str) -> None:
    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": command})


@pytest.mark.parametrize(("command", "files"), REAL_EXEC)
def test_no_file_is_created_or_changed(
    policy: ApprovalPolicy, tmp_path: Path, command: str, files: dict[str, str]
) -> None:
    """Run the command the way the agent would, but only if the real policy lets it run."""
    work = tmp_path / "work"
    for name, content in files.items():
        (work / name).write_text(content, encoding="utf-8")
    before = _snapshot(work)
    try:
        policy.require("bash", {"command": command})
    except ApprovalDenied:
        pass
    else:
        subprocess.run(
            ["bash", "-c", command], cwd=work, stdin=subprocess.DEVNULL,
            capture_output=True, timeout=30, check=False,
        )
    assert _snapshot(work) == before, f"{command!r} changed the working directory"


@pytest.mark.parametrize("command", SAFE)
def test_a_glob_in_a_file_position_stays_free(policy: ApprovalPolicy, command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "auto_allow", command
    policy.require("bash", {"command": command})


_CHILD = (
    "import sys, time\n"
    "from hydra.autonomy import classify_tool_call\n"
    "command = sys.stdin.read()\n"
    "started = time.perf_counter()\n"
    "decision = classify_tool_call('bash', {'command': command}, 'ask')\n"
    "print(decision['decision'], time.perf_counter() - started)\n"
)


def _classify_in_child(command: str) -> tuple[str, float]:
    """Classify in a child process: a raise or a stall fails the test instead of the suite."""
    done = subprocess.run(
        [sys.executable, "-c", _CHILD], input=command, capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    decision, seconds = done.stdout.split()
    return decision, float(seconds)


@pytest.mark.parametrize("command", DEEP)
def test_deep_nesting_needs_approval_and_never_raises(command: str) -> None:
    decision, seconds = _classify_in_child(command)
    assert decision == "needs_approval"
    assert seconds < 5.0


def test_shell_c_nesting_is_bounded_too() -> None:
    script = "ls"
    for _ in range(12):
        script = f"bash -c {shlex.quote(script)}"
    decision, seconds = _classify_in_child(script)
    assert decision == "needs_approval"
    assert seconds < 5.0


def test_the_reason_for_deep_nesting_is_plain() -> None:
    decision = classify_tool_call("bash", {"command": "ssh h " * 40 + "ls"}, "ask")
    assert "nested" in decision["reason"]


def test_a_flood_of_keywords_is_classified_fast() -> None:
    decision, seconds = _classify_in_child(KEYWORD_FLOOD)
    assert decision in {"auto_allow", "needs_approval"}
    assert seconds < 1.5


def test_the_reason_for_a_code_position_glob_names_why() -> None:
    reason = classify_tool_call("bash", {"command": "sed s*x*y* in"}, "ask")["reason"]
    assert "glob" in reason and "script" in reason
    started = time.perf_counter()
    classify_tool_call("bash", {"command": "ls"}, "ask")
    assert time.perf_counter() - started < 0.1
