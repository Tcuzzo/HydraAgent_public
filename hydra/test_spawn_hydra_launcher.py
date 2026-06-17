from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from importlib.machinery import SourceFileLoader

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_spawn_hydra():
    path = REPO_ROOT / "bin" / "spawn-hydra"
    loader = SourceFileLoader("spawn_hydra_launcher", str(path))
    spec = importlib.util.spec_from_file_location(
        "spawn_hydra_launcher",
        path,
        loader=loader,
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("argv", [["spawn-hydra"], ["spawn-hydra", "--chat"]])
def test_spawn_hydra_interactive_launches_elite_chat(monkeypatch, argv):
    launcher = _load_spawn_hydra()
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(launcher, "_start_telegram_listener", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_launch_elite_chat",
        lambda args: calls.append(("elite", args)) or 0,
    )

    assert launcher.main() == 0
    assert calls and calls[0][0] == "elite"
    assert calls[0][1].root == Path("/")
    assert calls[0][1].request is None


def test_spawn_hydra_with_request_seeds_initial_turn(monkeypatch):
    launcher = _load_spawn_hydra()
    captured: list[object] = []

    monkeypatch.setattr(sys, "argv", ["spawn-hydra", "build a web scraper"])
    monkeypatch.setattr(launcher, "_start_telegram_listener", lambda: None)
    monkeypatch.setattr(
        launcher,
        "_launch_elite_chat",
        lambda args: captured.append(args) or 0,
    )

    assert launcher.main() == 0
    assert captured and captured[0].request == "build a web scraper"
