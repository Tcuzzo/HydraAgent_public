"""Ball 1C fix round 2: on Linux the bash tool runs inside the kernel sandbox BY DEFAULT.

Operator ruling: "Yes, sandbox default". A text scanner cannot see every way a shell
can run a write (stdin scripts, wrappers, brace lists, traps, symlink-then-write), so
the boundary is the kernel: the workspace root is mounted read-write, everything else
read-only, and a write outside the root fails whatever the command looks like.

Real ``skills.bash.run`` calls, real bubblewrap, real files read back from disk. The
escape cases are a sample of what the regrade landed outside the root. When the
sandbox binary is absent the escape tests are skipped with the stated reason; nothing
is faked. ``confinement_violation`` stays as the cheap first layer.

Per-OS contract (also stated in hydra/proc.py):
  * Linux with bwrap  -> kernel sandbox ON by default, plus the static scan.
  * Linux without it  -> host execution + static scan + one loud line naming the
                         missing binary and how to install it (never a lock-out).
  * macOS / Windows   -> host execution + static scan, exactly as before.
  * Kill switch HYDRA_SHELL_SANDBOX=0 (any OS): host execution + a WARNING line on
    every call while it is off. No prompt, no approval step.
"""
from __future__ import annotations

import os
import sys
from argparse import Namespace

import pytest

from hydra import exec_backend
from skills import bash

SANDBOX_REASON = "needs Linux with a working bwrap (bubblewrap)"
HAS_SANDBOX = sys.platform.startswith("linux") and exec_backend._bwrap_available()
needs_sandbox = pytest.mark.skipif(not HAS_SANDBOX, reason=SANDBOX_REASON)
linux_only = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux sandbox contract"
)


@pytest.fixture(autouse=True)
def _default_config(monkeypatch):
    """The default configuration: no kill switch, no container opt-in."""
    monkeypatch.delenv("HYDRA_SHELL_SANDBOX", raising=False)
    monkeypatch.delenv("HYDRA_EXEC_SANDBOX", raising=False)


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "workspace"
    out = tmp_path / "out"
    (root / "sub").mkdir(parents=True)
    out.mkdir()
    (out / "readme.txt").write_text("outside-content\n", encoding="utf-8")
    return root, out


# {out} = absolute outside dir; ../out is the same dir, relative to the root.
ESCAPES = [
    pytest.param("bash <<< 'touch {out}/x'", id="stdin_script_here_string"),
    pytest.param("echo 'touch {out}/x' | sh", id="piped_script"),
    pytest.param("timeout 5 touch {out}/x", id="wrapper_timeout"),
    pytest.param("t='touch ../out/x'; $t", id="variable_holds_whole_command"),
    pytest.param("echo {out}/x | xargs touch", id="xargs_operand_from_stdin"),
    pytest.param("touch {{../out/x,sub/y}}", id="brace_expansion"),
    pytest.param("ln -s ../out l; echo a > l/x", id="symlink_then_write"),
    pytest.param("trap 'touch {out}/x' EXIT", id="trap_on_exit"),
]


@needs_sandbox
@pytest.mark.parametrize("template", ESCAPES)
def test_no_trick_lands_a_write_outside_the_root_by_default(world, template):
    root, out = world
    result = bash.run(template.format(out=out), root)
    assert not (out / "x").exists(), f"the write reached the disk: {result}"
    assert result["sandbox"] == "on", result


@needs_sandbox
def test_work_inside_the_root_and_reads_outside_it_still_work(world):
    root, out = world
    result = bash.run(f"echo made > sub/f && cat {out}/readme.txt && ls {out}", root)
    assert result["ok"] is True, result
    assert result["sandbox"] == "on"
    assert "outside-content" in result["stdout"]
    assert (root / "sub" / "f").read_text(encoding="utf-8").strip() == "made"


@needs_sandbox
def test_the_kernel_not_the_scanner_stops_a_write_outside(world):
    """A command the scanner cannot see still fails at the kernel with a named path."""
    root, out = world
    result = bash.run(f"timeout 5 touch {out}/x", root)
    assert result["ok"] is False
    assert "Read-only file system" in result["stderr"]
    assert str(out) in result["stderr"]
    assert not (out / "x").exists()


@needs_sandbox
def test_kill_switch_restores_host_execution_and_warns_every_call(world, monkeypatch):
    root, out = world
    monkeypatch.setenv("HYDRA_SHELL_SANDBOX", "0")
    for n in (1, 2):
        result = bash.run(f"timeout 5 touch {out}/x{n}", root)
        assert result["ok"] is True, result
        assert (out / f"x{n}").exists(), "host execution: the write lands"
        assert result["sandbox"] == "off(kill switch)"
        assert "WARNING" in result["stderr"] and "HYDRA_SHELL_SANDBOX" in result["stderr"]


@linux_only
def test_missing_binary_never_locks_out_and_names_the_binary(world, monkeypatch, tmp_path):
    """No bwrap on PATH: run on the host with the static scan, one loud install line."""
    root, out = world
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "bash").symlink_to(os.path.realpath("/bin/bash"))
    monkeypatch.setenv("PATH", str(bin_dir))
    exec_backend._reset_capability_cache()
    try:
        result = bash.run("echo hello > sub/f", root)
        assert result["ok"] is True, result
        assert result["sandbox"] == "unavailable"
        assert "bwrap" in result["stderr"] and "install" in result["stderr"].lower()
        assert (root / "sub" / "f").read_text(encoding="utf-8").strip() == "hello"
        refused = bash.run(f"echo hello > {out}/x", root)  # the static scan still guards
        assert refused["ok"] is False
        assert refused["reason"] == "write-outside-workspace-root"
        assert not (out / "x").exists()
    finally:
        exec_backend._reset_capability_cache()


def test_doctor_reports_the_three_sandbox_states(monkeypatch, capsys):
    from hydra.cli.cmd_doctor import cmd_doctor

    monkeypatch.setattr("hydra.doctor.diagnose", lambda *a, **k: [])
    monkeypatch.setattr(
        "hydra.doctor.summarize",
        lambda reports: {"total": 0, "outdated": 0, "vulnerable": 0},
    )
    monkeypatch.setattr("hydra.doctor.packages_to_fix", lambda reports: [])
    args = Namespace(fix=False, no_pip=True, timeout=1, format="text")

    monkeypatch.setenv("HYDRA_SHELL_SANDBOX", "off")
    assert cmd_doctor(args) == 0
    assert "sandbox: off(kill switch)" in capsys.readouterr().out

    monkeypatch.delenv("HYDRA_SHELL_SANDBOX")
    assert cmd_doctor(args) == 0
    expected = "on" if HAS_SANDBOX else "unavailable"
    assert f"sandbox: {expected}" in capsys.readouterr().out


@linux_only
def test_doctor_says_unavailable_when_the_binary_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    exec_backend._reset_capability_cache()
    try:
        assert exec_backend.shell_sandbox_state() == "unavailable"
    finally:
        exec_backend._reset_capability_cache()
