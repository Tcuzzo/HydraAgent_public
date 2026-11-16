"""Every subprocess in Hydra's CLI and workers carries an explicit timeout.

A child process that hangs (a stalled compiler, a git command waiting on a lock,
a pip install waiting on the network) must never wedge the parent forever. The
timeout is what turns "hangs forever" into "fails cleanly with a named reason".

The test spies on the real ``subprocess.run`` / ``subprocess.call``: it records the
keyword arguments and then calls the real function, so the command genuinely runs
and the captured timeout is the one the production code passed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hydra.cli import cmd_code, cmd_doctor, cmd_update
from hydra import worker_aci, worker_sessions

#: The timeout each call site must use, in seconds.
EXPECTED_TIMEOUTS = {
    ("cmd_code", "compile"): 120.0,
    ("cmd_code", "run"): 300.0,
    ("cmd_code", "direct"): 300.0,
    ("cmd_doctor", "upgrade"): 120.0,
    ("cmd_update", "update"): 600.0,
    ("worker_aci", "ls_files"): 120.0,
    ("worker_aci", "rev_parse"): 60.0,
    ("worker_aci", "git"): 120.0,
    ("worker_sessions", "ls_files"): 60.0,
    ("worker_sessions", "session_git"): 120.0,
}


@pytest.fixture
def spy(monkeypatch):
    """Record every subprocess timeout the code under test passes, then run it for real."""
    calls: list[tuple[str, object, dict]] = []

    def _wrap(name, real):
        def wrapper(cmd, *args, **kwargs):
            calls.append((name, cmd, kwargs))
            return real(cmd, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(subprocess, "run", _wrap("run", subprocess.run))
    monkeypatch.setattr(subprocess, "call", _wrap("call", subprocess.call))
    return calls


def _timeouts(calls, name):
    return [kwargs.get("timeout") for called, _cmd, kwargs in calls if called == name]


def test_cmd_code_compile_then_run_has_timeouts(spy, tmp_path):
    """Compiling and running a snippet must both be bounded."""
    source = tmp_path / "snippet.py"
    source.write_text("print('hello')\n", encoding="utf-8")

    rc = cmd_code.cmd_code(_args(tmp_path, source, "python"))

    assert rc == 0
    assert all(timeout is not None for timeout in _timeouts(spy, "run"))


def test_cmd_code_compile_timeout_is_120(spy, tmp_path):
    """The compile step is bounded at 120s."""
    source = tmp_path / "snippet.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    cmd_code.cmd_code(_args(tmp_path, source, "c"))

    compile_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[0] == "gcc"]
    assert compile_calls, "the compile step must actually run"
    assert compile_calls[0].get("timeout") == 120.0


def test_cmd_code_direct_run_timeout_is_300(spy, tmp_path):
    """A directly executed snippet is bounded at 300s."""
    source = tmp_path / "snippet.py"
    source.write_text("print('hello')\n", encoding="utf-8")

    cmd_code.cmd_code(_args(tmp_path, source, "python"))

    run_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[0] == sys.executable]
    assert run_calls, "the snippet must actually run"
    assert run_calls[0].get("timeout") == 300.0


def test_cmd_code_timeout_fails_cleanly(spy, tmp_path, monkeypatch):
    """A timed-out compile must fail cleanly -- no raw traceback."""

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    monkeypatch.setattr(subprocess, "run", _boom)

    source = tmp_path / "snippet.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")

    rc = cmd_code.cmd_code(_args(tmp_path, source, "c"))

    assert rc != 0


def _stub_doctor(monkeypatch, upgrade_cmd):
    """Make the doctor's network lookups deterministic and offline."""
    monkeypatch.setattr(cmd_doctor.doctor, "hydra_dependency_names", lambda: ["somepkg"])
    monkeypatch.setattr(cmd_doctor.doctor, "installed_version", lambda name: "1.0.0")
    monkeypatch.setattr(cmd_doctor.doctor, "diagnose", lambda names, **kw: [])
    monkeypatch.setattr(cmd_doctor.doctor, "summarize", lambda reports: {
        "total": 1, "outdated": 1, "vulnerable": 1, "missing": 0,
    })
    monkeypatch.setattr(cmd_doctor.doctor, "decision_configuration", lambda: {
        "tiers": {"llm": "local", "vision": "local", "text": "local"},
        "backs": "unset", "kev_model": "unset", "laya_model": "unset",
        "llm_decision_model": "unset", "jev_key": "unset", "jev_model": "unset",
        "vision_model": "unset", "text_decision_model": "unset",
        "live_provider": "duckduckgo", "live_key": "unset",
        "paid_calls": False,
    })
    monkeypatch.setattr(cmd_doctor.doctor, "packages_to_fix", lambda reports: ["somepkg"])
    monkeypatch.setattr(cmd_doctor.doctor, "upgrade_command", lambda fixes: upgrade_cmd)


def test_cmd_doctor_upgrade_has_timeout(spy, monkeypatch):
    """The dependency upgrade is bounded at 120s."""
    _stub_doctor(monkeypatch, [sys.executable, "-m", "pip", "install", "-U", "somepkg"])

    cmd_doctor.cmd_doctor(_doctor_args(fix=True))

    assert _timeouts(spy, "call"), "the upgrade must actually run"
    assert _timeouts(spy, "call")[0] == 120.0


def test_cmd_doctor_timeout_fails_cleanly(monkeypatch):
    """A timed-out upgrade must fail cleanly."""

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    _stub_doctor(monkeypatch, ["pip", "install"])
    monkeypatch.setattr(subprocess, "call", _boom)

    rc = cmd_doctor.cmd_doctor(_doctor_args(fix=True))

    assert rc != 0


def test_cmd_update_has_timeout(spy, monkeypatch, tmp_path):
    """The update command is bounded at 600s."""
    monkeypatch.setattr(cmd_update, "build_update_command", lambda repo: [sys.executable, "-c", "pass"])
    monkeypatch.setattr(cmd_update, "_installed_version", lambda: "1.0.0")

    rc = cmd_update.cmd_update(_update_args(tmp_path))

    assert rc == 0
    assert _timeouts(spy, "call")[0] == 600.0


def test_cmd_update_timeout_fails_cleanly(monkeypatch, tmp_path):
    """A timed-out update must fail cleanly."""

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    monkeypatch.setattr(subprocess, "call", _boom)
    monkeypatch.setattr(cmd_update, "build_update_command", lambda repo: ["pip", "install"])
    monkeypatch.setattr(cmd_update, "_installed_version", lambda: "1.0.0")

    rc = cmd_update.cmd_update(_update_args(tmp_path))

    assert rc != 0


def test_worker_aci_ls_files_has_timeout(spy, tmp_path):
    """Listing tracked files is bounded at 120s."""
    _git_init(tmp_path)

    worker_aci._tracked_files(tmp_path)

    ls_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[1:3] == ["ls-files", "-z"]]
    assert ls_calls, "git ls-files must actually run"
    assert ls_calls[0].get("timeout") == 120.0


def test_worker_aci_rev_parse_has_timeout(spy, tmp_path):
    """The git-repo probe is bounded at 60s."""
    _git_init(tmp_path)

    worker_aci._is_git_repo(tmp_path)

    probe_calls = [kwargs for _name, cmd, kwargs in spy if cmd and "rev-parse" in cmd]
    assert probe_calls, "git rev-parse must actually run"
    assert probe_calls[0].get("timeout") == 60.0


def test_worker_aci_git_has_timeout(spy, tmp_path):
    """A git command is bounded at 120s."""
    _git_init(tmp_path)

    worker_aci._git(tmp_path, ["status", "--porcelain"])

    git_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[:2] == ["git", "status"]]
    assert git_calls, "the git command must actually run"
    assert git_calls[0].get("timeout") == 120.0


def test_worker_sessions_ls_files_has_timeout(spy, tmp_path):
    """Listing tracked files in a session repo is bounded at 60s."""
    _git_init(tmp_path)

    worker_sessions._tracked_files(tmp_path)

    ls_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[1:3] == ["ls-files", "-z"]]
    assert ls_calls, "git ls-files must actually run"
    assert ls_calls[0].get("timeout") == 60.0


def test_worker_sessions_session_git_has_timeout(spy, tmp_path):
    """The session baseline git commands are bounded at 120s."""
    (tmp_path / "tracked.txt").write_text("content\n", encoding="utf-8")

    worker_sessions._init_session_git(tmp_path)

    init_calls = [kwargs for _name, cmd, kwargs in spy if cmd and cmd[:2] == ["git", "init"]]
    assert init_calls, "git init must actually run"
    assert init_calls[0].get("timeout") == 120.0


def test_worker_aci_timeout_raises_a_domain_error(spy, monkeypatch, tmp_path):
    """A hung git command surfaces as a worker error, not a raw traceback."""

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(worker_aci.WorkerAciError):
        worker_aci._tracked_files(tmp_path)


def test_worker_aci_hung_repo_probe_is_not_reported_as_not_a_repo(monkeypatch, tmp_path):
    """A hung git probe must fail loudly, not look like an empty repo.

    Treating a timeout as "not a git repo" would make every diff come back empty
    and the job would report success while doing nothing.
    """

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(worker_aci.WorkerAciError):
        worker_aci._is_git_repo(tmp_path)

    with pytest.raises(worker_aci.WorkerAciError):
        worker_aci._git(tmp_path, ["status", "--short"])


def test_worker_sessions_timeout_raises_a_domain_error(spy, monkeypatch, tmp_path):
    """A hung git command surfaces as a worker error, not a raw traceback."""

    def _boom(cmd, *args, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0) or 0)

    monkeypatch.setattr(subprocess, "run", _boom)

    with pytest.raises(worker_sessions.WorkerSessionError):
        worker_sessions._tracked_files(tmp_path)


# --- helpers -----------------------------------------------------------------


def _args(tmp_path: Path, source: Path, lang: str):
    import argparse

    return argparse.Namespace(file=source, lang=lang, env_dir=str(tmp_path), highlight=False)


def _doctor_args(fix: bool):
    import argparse

    return argparse.Namespace(
        fix=fix,
        format="text",
        offline=True,
        no_pip=False,
        timeout=10.0,
        rungs=False,
    )


def _update_args(tmp_path: Path):
    import argparse

    return argparse.Namespace(repo=str(tmp_path), check=False)


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "t@example.test"], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
