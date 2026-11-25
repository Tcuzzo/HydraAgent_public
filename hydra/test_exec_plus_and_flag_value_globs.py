"""Ball 1A, round 9 — the last four holes: find ``+``, glob flag values, find primary values, awk timing.

* ``find ... -exec cmd {} +`` hands the command EVERY match, so ``{}`` acts as an unbounded glob
  operand: ``uniq {} +`` and ``python3 -m json.tool {} +`` took a second operand, their OUTPUT
  file. The ``+`` form goes through the same rule as a glob word.
* A glob inside a FLAG word (``-F*``, ``-e*``, ``--include=*``, or a separate ``-F *``) was
  never judged, so a file named ``-F`` turned ``awk -F* '{print}' prog`` into awk running ``prog``.
  An unquoted glob in a flag word or a flag value always needs approval; a quoted pattern is fine.
* find primary values (-name, -path, -newer, -maxdepth ...) skipped the glob rule.
* The awk program regex backtracked cubically (3000 spaces took 50 s): the check is a tokenizer now.
* ``awk ''`` raised IndexError out of ``classify_tool_call``.

Real ``ApprovalPolicy("ask")`` and real ``classify_tool_call``. Where the seat executed a form,
this file executes it too in a temp dir (only when the policy lets it run) and asserts that every
file's content hash is unchanged and no file appeared.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from hydra.autonomy import _SAFE_COMMANDS, classify_tool_call
from hydra.operator_auth import AUTH_DIR_ENV_VAR
from hydra.policy import ApprovalDenied, ApprovalPolicy

FIND_PLUS = (
    "find . -type f -exec uniq {} +",
    "find . -execdir uniq {} +",
    "find . -exec python3 -m json.tool {} +",
    "find . -type f -exec python3 -m json.tool {} +",
    "find . -exec uniq {} {} \\;",
    "find . -exec bash script.sh {} +",
    "find . -exec git status {} +",
    "find . -exec python3 script.py {} +",
)

FLAG_VALUE_GLOBS = (
    "awk -F* '{print}' 'BEGIN{system(\"touch PWNED_awk\")}'",
    "awk -F* '{print}' f",
    "awk -F? '{print}' f",
    "awk -F * '{print}' f",
    "awk -F ? '{print}' f",
    "grep -e* x f",
    "grep -e * f",
    "grep --include=* x f",
    "grep --include * x f",
    "grep --exclude-dir=* -r x .",
    "grep -m* x f",
    "head -n* f",
    "head -n * f",
    "tail -c ? f",
    "sort -k* f",
    "sort -t * f",
    "ls --color=* f",
    "ls -*",
    "ls -l*",
    "rg -g * x",
    "rg --glob=* x",
    "du -d * f",
    "cut -d* -f1 f",
    "sed -e* f",
    "sed -n -e * f",
    "sed --expression=* f",
    "pytest -k * x",
    "pytest -k* x",
    "git log -n* ",
    "git log --since=* ",
)

FIND_VALUES = (
    "find . -name *",
    "find . -iname *.py",
    "find . -path * -print",
    "find . -ipath ./a/* -print",
    "find . -newer *",
    "find . -newer /etc/passwd",
    "find . -newer ../x",
    "find . -maxdepth *",
    "find . -mindepth ?",
    "find . -maxdepth x",
    "find . -maxdepth -1",
    "find . -mtime x",
    "find . -size big",
    "find . -perm abc",
    "find . -perm 99999",
    "find . -type zz",
    "find . -type *",
    "find . -user *",
    "find . -regex *",
    "find . -name",
)

EMPTY_PROGRAMS = (
    "awk ''",
    "awk '' x",
    "awk ' '",
    "awk '   ' f",
    "awk \"\"",
    "awk '\t'",
    "awk '\n' f",
)

GATED = list(FIND_PLUS + FLAG_VALUE_GLOBS + FIND_VALUES + EMPTY_PROGRAMS)

# (command, files that exist before it runs). The seat ran these; with the policy in front,
# every file's content hash must be unchanged and no file may appear.
REAL_EXEC = (
    ("find . -type f -exec uniq {} +", {"a": "x\nx\ny\n", "b": "ORIGINAL_B\n"}),
    ("find . -execdir uniq {} +", {"a": "x\nx\ny\n", "b": "ORIGINAL_B\n"}),
    ("find . -type f -exec python3 -m json.tool {} +", {"c.json": '{"k":1}\n', "d.json": '{"z":2}\n'}),
    ("awk -F* '{print}' 'BEGIN{system(\"touch PWNED_awk\")}'", {"-F": ""}),
    ("awk -F* '{print}' 'BEGIN{system(\"touch PWNED_awk2\")}'", {"-F": "", "x": "1\n"}),
    ("grep -e* x f", {"-e": "", "f": "x\n"}),
)

SAFE = (
    "find . -name '*.py'",
    "find . -iname 'x*' -type f",
    "find . -path './src/*' -print",
    "find . -maxdepth 2 -mindepth 1",
    "find . -mtime -3",
    "find . -size +10k",
    "find . -perm 644",
    "find . -newer a.txt",
    "find . -type f -user root",
    "find . -regex '.*x'",
    "find . -exec grep mv {} \\;",
    "find . -exec grep -l foo {} +",
    "find . -exec cat {} +",
    "find . -exec wc -l {} +",
    "find . -exec uniq {} \\;",
    "find . -exec bash script.sh {} \\;",
    "grep --include='*.py' -rn x .",
    "grep --include='*.py' -e 'a*' -rn x ./src",
    "grep -e 'a*' f",
    "head -n 5 f",
    "tail -n 5 f",
    "sort -k 2 f",
    "ls --color=auto",
    "ls -lh *.py",
    "grep -n -i foo *.py",
    "awk -F, '{print $2}' f",
    "awk -F'|' '{print $2}' f",
    "awk '{print $1}' f",
    "awk '{print $1, $3}' f",
    "awk 'NR==5' f",
    "awk 'NR>1{print $2}' f",
    "awk '$1 > 5' f",
    "awk '{ print }' f",
    "awk '{print $NF}' f",
    "sed -n '1,5p' f",
    "sed -e 's/a/b/' f",
    "pytest -k 'not slow' x",
    "git log -n 5 --oneline",
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
def test_known_safe_shapes_stay_free(policy: ApprovalPolicy, command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "ask")["decision"] == "auto_allow", command
    policy.require("bash", {"command": command})


_TIMER = (
    "import sys, time\n"
    "from hydra.autonomy import classify_tool_call\n"
    "command = sys.stdin.read()\n"
    "started = time.perf_counter()\n"
    "classify_tool_call('bash', {'command': command}, 'ask')\n"
    "print(time.perf_counter() - started)\n"
)


def _seconds_to_classify(command: str) -> float:
    """Time classify_tool_call in a child process so a stalled classifier fails the test fast."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", _TIMER], input=command, capture_output=True, text=True, timeout=10, check=True,
        )
    except subprocess.TimeoutExpired:
        return float("inf")
    return float(done.stdout.strip())


@pytest.mark.parametrize("pad", [10_000, 50_000])
@pytest.mark.parametrize(
    "template",
    [
        "awk '{pad}x' f",
        "awk '{pad}{{print}}' f",
        "awk '{{print{pad}}}' f",
        "awk 'NR=={pad}5' f",
        "sed -n '{pad}x' f",
        "sed -n '{pad}p' f",
        "sed -n 'p{pad}' f",
    ],
)
def test_a_long_run_of_spaces_is_classified_fast(template: str, pad: int) -> None:
    command = template.replace("{pad}", " " * pad)
    assert _seconds_to_classify(command) < 0.1


@pytest.mark.parametrize("name", sorted(_SAFE_COMMANDS))
def test_empty_and_blank_words_never_raise(name: str) -> None:
    spec = _SAFE_COMMANDS[name]
    prefixes = [name] + [f"{name} {sub}" for sub in spec.subs]
    blanks = ["''", '""', "' '", "'  '", "'\t'", "-", "'-'", "''  ''", "-x ''", "-- ''", "'' ''"]
    for prefix in prefixes:
        for blank in blanks:
            decision = classify_tool_call("bash", {"command": f"{prefix} {blank}"}, "ask")
            assert decision["decision"] in {"auto_allow", "needs_approval"}, (prefix, blank)


@pytest.mark.parametrize("command", EMPTY_PROGRAMS)
def test_an_empty_awk_program_says_why(command: str) -> None:
    assert "awk program" in classify_tool_call("bash", {"command": command}, "ask")["reason"]


@pytest.mark.parametrize("command", ["find . -exec uniq {} +", "awk -F* '{print}' f", "find . -name *", "awk ''"])
def test_deny_mode_blocks_them(command: str) -> None:
    assert classify_tool_call("bash", {"command": command}, "deny")["decision"] == "blocked"
