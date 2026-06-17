"""Tests for hydra.cli.cmd_watch helpers (pure parts: snapshot, task, validation)."""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.cli.cmd_watch import (
    WatchArgsError,
    file_snapshot,
    resolve_task,
    resolve_watch_policy,
    validate_task,
    validate_triggers,
)


# ── file_snapshot ───────────────────────────────────────────────────────────


def test_file_snapshot_maps_files_to_mtimes(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    snap = file_snapshot((tmp_path,))
    assert str(f) in snap
    assert isinstance(snap[str(f)], float)


def test_file_snapshot_detects_change(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    before = file_snapshot((tmp_path,))
    import os

    os.utime(f, (before[str(f)] + 100, before[str(f)] + 100))  # bump mtime
    after = file_snapshot((tmp_path,))
    assert before != after


def test_file_snapshot_recurses_into_dirs(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("y", encoding="utf-8")
    snap = file_snapshot((tmp_path,))
    assert any("deep.py" in k for k in snap)


def test_file_snapshot_missing_path_is_empty():
    assert file_snapshot((Path("/no/such/path/xyz"),)) == {}


# ── resolve_task ────────────────────────────────────────────────────────────


def test_resolve_task_returns_inline_prompt():
    assert resolve_task("do the thing", None) == "do the thing"


def test_resolve_task_reads_file_fresh_each_call(tmp_path):
    tf = tmp_path / "task.md"
    tf.write_text("first task", encoding="utf-8")
    assert resolve_task(None, tf) == "first task"
    tf.write_text("second task", encoding="utf-8")  # edited while "running"
    assert resolve_task(None, tf) == "second task"


# ── resolve_watch_policy (read-only unless --yolo) ──────────────────────────


def test_policy_defaults_to_deny_read_only():
    assert resolve_watch_policy(yolo=False, approval_policy=None) == "deny"


def test_policy_yolo_means_allow():
    assert resolve_watch_policy(yolo=True, approval_policy=None) == "allow"


def test_policy_explicit_passthrough():
    assert resolve_watch_policy(yolo=False, approval_policy="ask") == "ask"


def test_policy_yolo_and_explicit_conflict():
    with pytest.raises(WatchArgsError):
        resolve_watch_policy(yolo=True, approval_policy="allow")


# ── validation ──────────────────────────────────────────────────────────────


def test_validate_task_requires_exactly_one():
    validate_task("a prompt", None)          # ok
    validate_task(None, Path("t.md"))        # ok
    with pytest.raises(WatchArgsError):
        validate_task(None, None)            # neither
    with pytest.raises(WatchArgsError):
        validate_task("p", Path("t.md"))     # both


def test_validate_triggers_requires_at_least_one():
    validate_triggers("10m", [])             # ok (timer)
    validate_triggers(None, ["./src"])       # ok (watch)
    validate_triggers("10m", ["./src"])      # ok (both)
    with pytest.raises(WatchArgsError):
        validate_triggers(None, [])          # neither


# ── ask namespace (cmd_ask reuse) ───────────────────────────────────────────


def test_ask_namespace_fills_max_iterations_when_unset():
    """cmd_ask compares max_iterations as an int; watch defaults it to None, so the
    namespace builder must substitute a real positive int (else cmd_ask crashes)."""
    import argparse

    from hydra.cli.cmd_watch import _ask_namespace

    args = argparse.Namespace(provider=None, model=None, root=None, max_iterations=None, timeout=120.0)
    ns = _ask_namespace("task", "deny", args)
    assert isinstance(ns.max_iterations, int) and ns.max_iterations >= 1
    assert ns.prompt == "task" and ns.approval_policy == "deny"


def test_ask_namespace_preserves_explicit_max_iterations():
    import argparse

    from hydra.cli.cmd_watch import _ask_namespace

    args = argparse.Namespace(provider=None, model=None, root=None, max_iterations=7, timeout=120.0)
    assert _ask_namespace("t", "allow", args).max_iterations == 7
