from __future__ import annotations

from pathlib import Path

import pytest

from hydra.telegram_listener_runtime import start_telegram_listener_if_configured


@pytest.fixture(autouse=True)
def _no_systemd_listener(monkeypatch):
    """These tests exercise the SUBPROCESS listener path. Force the systemd-defer
    check off so a real hydra-telegram.service running on this box doesn't short-
    circuit the spawn/reuse logic under test. (The systemd-defer path — skip the
    subprocess when the unit is active — is the operator-intended production default.)"""
    import hydra.telegram_listener_runtime as _tlr

    monkeypatch.setattr(_tlr, "_systemd_listener_active", lambda *a, **k: False)


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid


def test_listener_start_is_skipped_without_hydra_telegram_env(tmp_path: Path, monkeypatch) -> None:
    # Hermetic: don't merge the operator's real ~/.hydraAgent/workspace/.env.telegram
    # (which legitimately now carries the bot's HYDRA_* keys).
    monkeypatch.setenv("HOME", str(tmp_path))
    started: list[object] = []

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={},
        popen=lambda *args, **kwargs: started.append((args, kwargs)),
    )

    assert report == {
        "schema": "hydra.telegram.listener_start.v1",
        "status": "skipped",
        "reason": "HYDRA_TELEGRAM_BOT_TOKEN or HYDRA_TELEGRAM_CHAT_ID is not set",
    }
    assert started == []


def test_listener_start_launches_hydra_telegram_listener_when_configured(tmp_path: Path) -> None:
    launched: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        launched.append({"command": command, **kwargs})
        return _FakeProcess(4321)

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={
            "HYDRA_TELEGRAM_BOT_TOKEN": "token",
            "HYDRA_TELEGRAM_CHAT_ID": "chat",
        },
        popen=fake_popen,
        is_pid_alive=lambda _pid: False,
    )

    command = launched[0]["command"]
    assert report["status"] == "started"
    assert report["pid"] == 4321
    assert report["pid_path"] == str(tmp_path / "evidence" / "telegram" / "listener.pid")
    assert command[1:] == ["-m", "hydra", "telegram", "listen", "--interval", "3.0"]
    assert "HYDRA_TELEGRAM_BOT_TOKEN" in launched[0]["env"]
    assert (tmp_path / "evidence" / "telegram" / "listener.pid").read_text(encoding="utf-8") == "4321\n"


def test_listener_start_reuses_existing_live_pid(tmp_path: Path) -> None:
    pid_path = tmp_path / "evidence" / "telegram" / "listener.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("2468\n", encoding="utf-8")
    started: list[object] = []

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={
            "HYDRA_TELEGRAM_BOT_TOKEN": "token",
            "HYDRA_TELEGRAM_CHAT_ID": "chat",
        },
        popen=lambda *args, **kwargs: started.append((args, kwargs)),
        is_pid_alive=lambda pid: pid == 2468,
        pid_cmdline=lambda pid: "python3 -m hydra telegram listen --interval 3.0" if pid == 2468 else "",
    )

    assert report["status"] == "already_running"
    assert report["pid"] == 2468
    assert started == []


def test_listener_start_reuses_live_listener_when_pidfile_missing(tmp_path: Path) -> None:
    started: list[object] = []

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={
            "HYDRA_TELEGRAM_BOT_TOKEN": "token",
            "HYDRA_TELEGRAM_CHAT_ID": "chat",
        },
        popen=lambda *args, **kwargs: started.append((args, kwargs)),
        is_pid_alive=lambda pid: pid == 9753,
        pid_cmdline=lambda pid: "python3 -m hydra telegram listen --interval 3.0" if pid == 9753 else "",
        listener_pids=lambda: [9753],
    )

    assert report["status"] == "already_running"
    assert report["pid"] == 9753
    assert (tmp_path / "evidence" / "telegram" / "listener.pid").read_text(encoding="utf-8") == "9753\n"
    assert started == []


def test_listener_scan_ignores_shell_commands_that_only_mention_listener(tmp_path: Path) -> None:
    launched: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        launched.append({"command": command, **kwargs})
        return _FakeProcess(4323)

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={
            "HYDRA_TELEGRAM_BOT_TOKEN": "token",
            "HYDRA_TELEGRAM_CHAT_ID": "chat",
        },
        popen=fake_popen,
        is_pid_alive=lambda pid: pid == 2468,
        pid_cmdline=lambda pid: "/bin/bash -c pgrep -f 'python3 -m hydra telegram listen'" if pid == 2468 else "",
        listener_pids=lambda: [2468],
    )

    assert report["status"] == "started"
    assert report["pid"] == 4323
    assert launched


def test_listener_start_replaces_stale_pid_that_is_not_hydra_listener(tmp_path: Path) -> None:
    pid_path = tmp_path / "evidence" / "telegram" / "listener.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text("2468\n", encoding="utf-8")
    launched: list[dict[str, object]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _FakeProcess:
        launched.append({"command": command, **kwargs})
        return _FakeProcess(4322)

    report = start_telegram_listener_if_configured(
        repo_root=tmp_path,
        process_env={
            "HYDRA_TELEGRAM_BOT_TOKEN": "token",
            "HYDRA_TELEGRAM_CHAT_ID": "chat",
        },
        popen=fake_popen,
        is_pid_alive=lambda pid: pid == 2468,
        pid_cmdline=lambda pid: "sleep 1000" if pid == 2468 else "",
    )

    assert report["status"] == "started"
    assert report["pid"] == 4322
    assert len(launched) == 1
