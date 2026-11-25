"""Ball 1C fix round 1: shell INDIRECTION must not carry a write past the confinement.

The blind grade found four real escapes through ``skills.bash.run`` (backtick
substitution, ``<( )``, ``>( )`` and a variable used as the command word) and two
false refusals of ordinary work (``$PWD`` and a literal loop variable). The class is
"the static scan does not see the command the shell will really run"; these tests
pin the class, not only the four instances. Real subprocesses, real files on disk.
"""
from __future__ import annotations

import os
import sys

import pytest

from hydra.proc import confinement_violation
from skills import bash

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell syntax")


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "elsewhere"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    return root, outside


# Each template writes {out}/hit from inside some indirection.
ESCAPES = [
    pytest.param("echo `touch {out}/hit`", id="backtick"),
    pytest.param("echo \"x $(touch {out}/hit) y\"", id="dollar_paren_in_dquotes"),
    pytest.param("cat <(touch {out}/hit)", id="process_substitution_in"),
    pytest.param("echo hi > >(tee {out}/hit)", id="process_substitution_out"),
    pytest.param("t=touch; $t {out}/hit", id="variable_as_verb"),
    pytest.param("export t=touch; $t {out}/hit", id="exported_variable_as_verb"),
    pytest.param("`echo touch` {out}/hit", id="substitution_as_verb"),
    pytest.param("if touch {out}/hit; then :; fi", id="inside_if_keyword"),
    pytest.param("for f in hit; do touch {out}/$f; done", id="inside_for_loop"),
    pytest.param("echo a | xargs touch {out}/hit", id="behind_xargs"),
    pytest.param("echo $(echo $(touch {out}/hit))", id="nested_substitution"),
]


@posix_only
@pytest.mark.parametrize("template", ESCAPES)
def test_indirection_cannot_carry_a_write_outside_the_root(world, template):
    root, outside = world
    result = bash.run(template.format(out=outside), root)
    assert result["ok"] is False, result
    assert not os.path.lexists(outside / "hit"), "the write reached the disk"


@posix_only
def test_unresolvable_command_word_is_refused_loudly_and_named(world):
    root, _outside = world
    result = bash.run("$UNSET_COMMAND_WORD_1C sub/f", root)
    assert result["ok"] is False
    assert "$UNSET_COMMAND_WORD_1C" in result["stderr"]


@posix_only
def test_dollar_pwd_is_the_workspace_not_the_agent_process(world):
    root, _outside = world
    result = bash.run("echo a > $PWD/sub/f", root)
    assert result["ok"] is True, result
    assert (root / "sub" / "f").read_text(encoding="utf-8").strip() == "a"


@posix_only
def test_dollar_pwd_follows_cd(world):
    root, outside = world
    assert bash.run("cd sub && echo a > $PWD/g", root)["ok"] is True
    assert (root / "sub" / "g").is_file()
    result = bash.run(f"cd {outside} && echo a > $PWD/h", root)
    assert result["ok"] is False
    assert not os.path.lexists(outside / "h")


@posix_only
def test_literal_loop_variable_resolves_inside_the_workspace(world):
    root, _outside = world
    result = bash.run("for i in 1 2; do echo $i > sub/f$i; done", root)
    assert result["ok"] is True, result
    assert (root / "sub" / "f1").read_text(encoding="utf-8").strip() == "1"
    assert (root / "sub" / "f2").read_text(encoding="utf-8").strip() == "2"


@posix_only
def test_loop_variable_with_one_escaping_value_is_refused(world):
    root, outside = world
    result = bash.run(f"for d in sub {outside}; do touch $d/f; done", root)
    assert result["ok"] is False
    assert not os.path.lexists(outside / "f")


@posix_only
def test_variable_verb_with_literal_value_is_allowed_inside(world):
    root, _outside = world
    result = bash.run("t=touch; $t sub/made", root)
    assert result["ok"] is True, result
    assert (root / "sub" / "made").exists()


@posix_only
def test_variable_built_from_pwd_cannot_walk_out(world):
    root, outside = world
    result = bash.run("d=$PWD/../" + outside.name + "; echo a > $d/x", root)
    assert result["ok"] is False
    assert not os.path.lexists(outside / "x")


@posix_only
def test_quoted_single_quote_text_is_not_executed_so_not_scanned(world):
    root, outside = world
    result = bash.run(f"echo '`touch {outside}/hit` <(touch {outside}/hit)'", root)
    assert result["ok"] is True, result
    assert not os.path.lexists(outside / "hit")


# Pure-function cases for the minor write-shaped verbs the grade named.
@pytest.mark.parametrize(
    "command",
    [
        "rsync -a src/ /elsewhere/",
        "rsync a /elsewhere/f",
        "truncate -s 0 /elsewhere/f",
        "tar -xf a.tar -C /elsewhere",
        "tar --directory=/elsewhere -xf a.tar",
        "curl -o /elsewhere/f http://example.invalid/x",
        "curl --output /elsewhere/f http://example.invalid/x",
        "wget -O /elsewhere/f http://example.invalid/x",
        "wget -P /elsewhere http://example.invalid/x",
    ],
)
def test_other_write_shaped_verbs_and_deep_nesting_are_refused(command):
    assert confinement_violation(command, "/ws/project", windows=False), command


@pytest.mark.parametrize(
    "command",
    [
        "rsync -a /elsewhere/ ./copy/",
        "tar -xf /elsewhere/a.tar -C sub",
        "curl -o sub/f http://example.invalid/x",
        "curl http://example.invalid/x",
        "wget -qO- http://example.invalid/x",
    ],
)
def test_those_verbs_are_free_when_they_stay_inside(command):
    assert confinement_violation(command, "/ws/project", windows=False) is None, command


def test_nesting_too_deep_to_verify_is_refused_not_waved_through():
    blocked = confinement_violation("eval eval eval eval touch f", "/ws/project", windows=False)
    assert blocked and "too deeply" in blocked
