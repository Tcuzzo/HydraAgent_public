"""Ball 1C: both shell grammars of the write-target rule, checked on every host OS.

``hydra.proc.confinement_violation`` is a pure function of (command, root, grammar), so
the cmd.exe grammar is exercised on Linux/macOS CI and the bash grammar on Windows CI,
in addition to the real-subprocess contract in ``test_shell_workspace_confinement``.
"""
from __future__ import annotations

import pytest

from hydra.proc import confinement_violation

POSIX_ROOT = "/ws/project"
WINDOWS_ROOT = "C:\\ws\\project"

POSIX_ESCAPES = [
    "echo x > /etc/escape",
    "echo x >> ../sibling/f",
    "echo x &> /elsewhere/f",
    "cp a.txt /elsewhere/a.txt",
    "cp -t /elsewhere a.txt",
    "cp --target-directory=/elsewhere a.txt",
    "mv a.txt /elsewhere/a.txt",
    "mv /elsewhere/a.txt .",  # mv removes its source: a write outside the root
    "install -m 644 a.txt /elsewhere/a.txt",
    "ln -s a.txt /elsewhere/link",
    "touch /elsewhere/f",
    "mkdir -p /elsewhere/d",
    "echo x | tee -a /elsewhere/f",
    "dd if=a of=/elsewhere/f",
    "cd /elsewhere && touch f",
    "ls; echo x > /elsewhere/f",
    "FOO=1 sudo cp a /elsewhere/f",
    "bash -lc 'echo x > /elsewhere/f'",
    "sh -c \"bash -c 'touch /elsewhere/f'\"",
    "eval 'touch /elsewhere/f'",
    "echo x > $UNSET_BY_TEST_1C/f",  # cannot be resolved: refused, not guessed
    "cd - && touch f",
    "echo x\ntouch /elsewhere/f",
]
POSIX_ALLOWED = [
    "echo x > out.txt",
    "echo x >> sub/dir/out.txt",
    "echo x > /ws/project/abs.txt",
    "echo x > /dev/null 2>&1",
    "echo x 2>/dev/null",
    "cat /etc/hostname",
    "cp /etc/hostname ./hostname.copy",
    "cp -t sub a.txt",
    "ls /elsewhere > listing.txt",
    "cd sub && touch f",
    "git status && git log -1 > /dev/null",
    "python -c 'print(1)'",
    "echo 'a > /elsewhere/f'",  # quoted text, not a redirection
    "dd if=/dev/zero of=blob bs=1 count=1",
    "bash -c 'echo x > inside.txt'",
]
WINDOWS_ESCAPES = [
    "echo x > C:\\elsewhere\\f",
    "echo x >> ..\\sibling\\f",
    "copy a.txt D:\\elsewhere\\a.txt",
    "copy a.txt \"C:\\other dir\\a.txt\"",
    "move a.txt C:\\elsewhere\\a.txt",
    "md C:\\elsewhere\\d",
    "mkdir ..\\sibling",
    "xcopy /E /Y a C:\\elsewhere\\",
    "robocopy a C:\\elsewhere /MIR",
    "cd /d C:\\elsewhere && md d",
    "cmd /c \"echo x > C:\\elsewhere\\f\"",
    "echo x > %UNDEFINED_BY_TEST_1C%\\f",
]
WINDOWS_ALLOWED = [
    "echo x > out.txt",
    "echo x >> sub\\out.txt",
    "echo x > C:\\ws\\project\\abs.txt",
    "echo x > NUL",
    "type C:\\elsewhere\\f > nul",
    "copy C:\\elsewhere\\a.txt .\\a.txt",
    "copy C:\\elsewhere\\a.txt c:\\WS\\Project\\sub\\a.txt",  # drive/dir case-insensitive
    "dir C:\\elsewhere",
    "cd sub && md d",
]


@pytest.mark.parametrize("command", POSIX_ESCAPES)
def test_posix_grammar_refuses_writes_outside_root(command):
    assert confinement_violation(command, POSIX_ROOT, windows=False), command


@pytest.mark.parametrize("command", POSIX_ALLOWED)
def test_posix_grammar_allows_writes_inside_root_and_all_reads(command):
    assert confinement_violation(command, POSIX_ROOT, windows=False) is None, command


@pytest.mark.parametrize("command", WINDOWS_ESCAPES)
def test_windows_grammar_refuses_writes_outside_root(command):
    assert confinement_violation(command, WINDOWS_ROOT, windows=True), command


@pytest.mark.parametrize("command", WINDOWS_ALLOWED)
def test_windows_grammar_allows_writes_inside_root_and_all_reads(command):
    assert confinement_violation(command, WINDOWS_ROOT, windows=True) is None, command


def test_the_blocked_path_is_named():
    blocked = confinement_violation("echo x > ../sibling/f", POSIX_ROOT, windows=False)
    assert blocked == "/ws/sibling/f"
    blocked = confinement_violation("echo x > ..\\sibling\\f", WINDOWS_ROOT, windows=True)
    assert blocked.lower() == "c:\\ws\\sibling\\f"


def test_unbalanced_quote_is_scanned_not_crashed():
    assert confinement_violation("echo 'oops > /elsewhere/f", POSIX_ROOT, windows=False)
    assert confinement_violation("echo 'oops > inside", POSIX_ROOT, windows=False) is None
