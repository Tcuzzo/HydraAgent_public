"""Ball 1A, round 8 — shrink each table entry to a shape that cannot do more than it says.

Round 7's allowlist held; every landed form went through a tool's own power:

* awk reads ``1system(`` as a number and a call, so a banned-word regex missed it:
  the awk entry now takes only a tiny print-of-fields grammar;
* find -exec handed ``{}`` to bash/python/sh as the script, and its start path was never
  judged: ``{}`` is never a script word or a ``-C`` path, and start paths use the workspace rule;
* the shell expands a glob AFTER the classifier judged it, so ``uniq *`` made uniq's second
  operand its output file and a file named ``-o`` turned ``sort *`` into ``sort -o``: a glob
  that could expand to a flag or an extra operand needs approval;
* ``>`` + a Unicode space + ``/dev/null`` named another file;
* ``date MMDDhhmm`` sets the clock; ``py_compile -`` and absolute paths wrote bytecode anywhere.

Real ``ApprovalPolicy("ask")`` and real ``classify_tool_call``. Where the seat executed a
form, this file executes it too in a temp dir, but only when the policy lets it run, and
asserts that no file was created or changed (content hashes before and after).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from hydra.autonomy import classify_tool_call
from hydra.operator_auth import AUTH_DIR_ENV_VAR
from hydra.policy import ApprovalDenied, ApprovalPolicy

AWK = (
    "awk 'BEGIN{print 1system(\"touch PWNED_awk\")}'",
    "awk 'BEGIN{x=1system(\"cp a b\")}'",
    "awk 'BEGIN{print 1system(\"rm -rf x\")}'",
    "awk 'BEGIN{print 1.5system(\"cp a b\")}'",
    "awk 'BEGIN{print 0x1system(\"cp a b\")}'",
    "awk 'BEGIN{print 2system(\"cp a b\")}'",
    "awk 'BEGIN{print 9system(\"cp a b\")}'",
    "awk 'BEGIN{print 1e1system(\"cp a b\")}'",
    "awk 'BEGIN{print 1system (\"cp a b\")}'",
    "awk 'BEGIN{x = 7system(\"cp a b\")}'",
    "awk 'BEGIN{x=1}' f",
    "awk '{print $1 > \"out\"}' f",
    "awk '{print \"a\"}' f",
    "awk '{print length($1)}' f",
    "awk '{print $(1)}' f",
    "awk '{print | \"sh\"}' f",
    "awk 'BEGIN{getline}' f",
    "awk '{print $1; print $2}' f",
    "awk -v x=1 '{print}' f",
    "awk -f prog.awk f",
    "awk '@include \"x\"'",
    "awk '{print $1}' *",
    "awk '{print}' *",
    "awk",
)

FIND = (
    "echo 'touch PWNED_find' | find /dev -maxdepth 1 -name stdin -exec bash {} \\;",
    "echo 'touch PWNED' | find /dev -name stdin -exec python3 {} \\;",
    "printf 'x' | find /dev -name stdin -exec sh {} \\;",
    "find /dev -name stdin -exec bash {} \\;",
    "find /proc/self/fd -name 0 -exec bash {} \\;",
    "find /tmp -name '*.sh' -exec bash {} \\;",
    "find / -name .git -exec git -C {} commit -m x \\;",
    "find . -exec bash {} \\;",
    "find . -exec python3 {} \\;",
    "find . -exec sh {} \\;",
    "find . -exec git -C {} commit -m x \\;",
    "find .. -name x",
    "find /etc -name x",
    "find dev -name x",
    "find ./dev -name x",
    "find a/../.. -name x",
    "find . -execdir bash {} \\;",
    "find . -exec cp {} x \\;",
)

GLOBS = (
    "uniq *",
    "uniq ?",
    "uniq a*",
    "uniq -c *",
    "uniq -d a?",
    "uniq -i *",
    "uniq -- *",
    "sort *",
    "sort ?",
    "tree *",
    "sed -n p *",
    "rg foo *",
    "ag foo *",
    "file *",
    "sort -n *.txt",
)

REDIRECTS = (
    "ls > /dev/null",
    "ls >\x0b/dev/null",
    "ls >\x0c/dev/null",
    "ls >\r/dev/null",
    "ls >\n/dev/null",
    "ls >\x1f/dev/null",
    "ls > /dev/null",
    "ls >\x85/dev/null",
    "ls 2> /dev/null",
)

CLOCK_AND_BYTECODE = (
    "date 010101012030",
    "date -u 0101",
    "date foo",
    "python3 -m py_compile -",
    "python3 -m py_compile /abs/x.py",
    "python3 -m py_compile ../x.py",
    "python3 -m py_compile dev/x.py",
    "python3 -m py_compile a.py -",
    "python3 -m py_compile",
)

GATED = list(AWK + FIND + GLOBS + REDIRECTS + CLOCK_AND_BYTECODE)

# (command, files that exist before it runs). The seat ran each of these and saw a file
# created or rewritten; with the policy in front, nothing may change.
REAL_EXEC = (
    ("awk 'BEGIN{print 1system(\"touch PWNED_awk\")}'", {}),
    ("echo 'touch PWNED_find' | find /dev -maxdepth 1 -name stdin -exec bash {} \\;", {}),
    ("echo 'touch PWNED_find2' | find /dev -name stdin -exec python3 {} \\;", {}),
    ("uniq *", {"a": "x\nx\ny\n", "b": "ORIGINAL_B\n"}),
    ("uniq -c a*", {"a": "x\nx\ny\n", "ab": "ORIGINAL_B\n"}),
    ("sort *", {"t": "b\na\n", "-o": "o\n"}),
    ("echo a.py | python3 -m py_compile -", {"a.py": "x = 1\n"}),
    ("ls > /dev/null", {" /dev/null": "ORIGINAL\n"}),
)

SAFE = (
    "awk '{print $1}' f",
    "awk '{print}' f",
    "awk '{print $1, $3}' f",
    "awk '{print $NF}' f",
    "awk 'NR==5' f",
    "awk 'NR>1{print $2}' f",
    "awk '$1 > 5' f",
    "awk -F, '{print $2}' f",
    "awk -F'|' '{print $2}' f",
    "awk '{print $1}' ./*",
    "find . -name cp",
    "find src -name '*.py' -print",
    "find . -maxdepth 2 -type f",
    "find . -exec grep mv {} \\;",
    "find . -exec cat {} \\;",
    "find . -exec bash script.sh {} \\;",
    "find a/b -type f -name '*.md'",
    "ls *",
    "ls -lh *.py",
    "cat *.py",
    "grep -n -i foo *.py",
    "grep -rn x *",
    "wc -l *.py",
    "diff *",
    "sort ./*",
    "sort a*",
    "sort -- *",
    "tree ./*",
    "sed -n p ./*",
    "uniq -c f",
    "uniq f",
    "sort -n f",
    "ls >/dev/null",
    "ls \t>/dev/null",
    "ls 2>/dev/null",
    "ls 2>&1",
    "date",
    "date -u",
    "date -I",
    "python3 -m py_compile x.py",
    "python3 -m py_compile a/b.py c.py",
)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"approvals.jsonl", "runs.jsonl"} and "auth" not in path.parts
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


@pytest.mark.parametrize("command", GATED)
def test_form_needs_approval(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "needs_approval", command


@pytest.mark.parametrize("command", GATED)
def test_form_is_stopped_by_the_real_policy(policy: ApprovalPolicy, command: str) -> None:
    with pytest.raises(ApprovalDenied):
        policy.require("bash", {"command": command})


@pytest.mark.parametrize(("command", "files"), REAL_EXEC)
def test_no_file_is_created_or_changed(
    policy: ApprovalPolicy, tmp_path: Path, command: str, files: dict[str, str]
) -> None:
    """Run the command the way the agent would, but only if the real policy lets it run."""
    work = tmp_path / "work"
    for name, content in files.items():
        target = work / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
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
def test_known_safe_shapes_stay_free(policy: ApprovalPolicy, command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "auto_allow", command
    policy.require("bash", {"command": command})


@pytest.mark.parametrize("command", ["awk '{print 1system(\"x\")}'", "uniq *", "find /dev -name stdin", "date 0101"])
def test_deny_mode_blocks_them(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "deny")["decision"] == "blocked"


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        ("awk '{print 1system(\"x\")}'", "awk program"),
        ("uniq *", "glob"),
        ("find /etc -name x", "must be a relative path inside the workspace"),
        ("date 0101", "does not take the argument"),
    ],
)
def test_the_reason_names_why(command: str, reason: str) -> None:
    assert reason in classify_tool_call("bash", {"command": command}, "ask")["reason"]
