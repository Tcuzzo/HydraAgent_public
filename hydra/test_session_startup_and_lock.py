"""Regression tests for operator bugs #5 (startup history bloat) and #6
(cross-process write race + unbounded session backups).

Bug #5: chat startup loaded the last 40 (and the telegram path 48) prior
messages into the LLM context before the operator typed anything. The sane
default must be small (a few exchanges) while staying configurable.

Bug #6: session + workbench JSONL writers did read-modify-write with an atomic
``os.replace`` but NO lock spanning the read+write, so two concurrent OS
processes (the chat process and the always-on telegram listener) clobbered each
other's updates. And ~/.hydra-sessions accumulated one compaction backup per run
forever, with nothing reaping the stale duplicates.
"""
from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

import pytest

import hydra.session_memory as session_memory


# ---------------------------------------------------------------------------
# Bug #5 — sane startup history default
# ---------------------------------------------------------------------------


def test_chat_startup_history_default_is_sane_not_40() -> None:
    """The argparse default for --session-history-limit must be a small,
    sane number of messages, NOT 40."""
    from hydra import __main__ as hydra_main

    parser = hydra_main._build_parser()
    args = parser.parse_args(["chat"])
    limit = getattr(args, "session_history_limit")
    assert limit == session_memory.DEFAULT_STARTUP_HISTORY_LIMIT
    assert 0 < limit <= 12, f"startup history default should be small, got {limit}"


def test_cmd_chat_getattr_fallback_matches_sane_default() -> None:
    """The defensive getattr fallback in cmd_chat must not silently re-introduce
    40 if the attribute is missing."""
    import inspect

    from hydra.cli import cmd_chat

    src = inspect.getsource(cmd_chat.cmd_chat)
    # The old bug was a hard-coded 40 fallback. It must be gone.
    assert 'getattr(args, "session_history_limit", 40)' not in src
    assert "DEFAULT_STARTUP_HISTORY_LIMIT" in src


def test_telegram_startup_history_default_is_sane_not_48() -> None:
    """The telegram listener path must not reload 48 messages either; it uses the
    shared sane default."""
    import inspect

    from gateways.telegram import operator

    src = inspect.getsource(operator)
    assert "limit=48" not in src, "telegram path still reloads 48 messages"


# ---------------------------------------------------------------------------
# Bug #6a — cross-process lock around read-modify-write
# ---------------------------------------------------------------------------


def _writer_proc(session_dir: str, session_id: str, marker: str, barrier_path: str) -> None:
    """Run in a SEPARATE OS process: read the session, sleep to widen the race
    window, then append a marker line via the locked read-modify-write path."""
    import hydra.session_memory as sm

    sm.SESSION_MEMORY_DIR = Path(session_dir)
    # Spin until the barrier file exists so both processes start together.
    bp = Path(barrier_path)
    for _ in range(2000):
        if bp.exists():
            break
        time.sleep(0.001)
    sm.append_message_locked(session_id, "user", marker, {"src": marker})


def test_two_processes_appending_lose_no_update(tmp_path: Path, monkeypatch) -> None:
    """Two real OS processes appending concurrently under the lock must BOTH
    land — no lost update."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    session_id = "race_session"
    session_memory.create_session(session_id, "seed")

    barrier = tmp_path / "go"
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(
            target=_writer_proc,
            args=(str(tmp_path), session_id, f"marker-{i}", str(barrier)),
        )
        for i in range(8)
    ]
    for p in procs:
        p.start()
    barrier.write_text("go")
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    messages = session_memory.get_session_messages(session_id)
    contents = {m["content"] for m in messages}
    for i in range(8):
        assert f"marker-{i}" in contents, f"lost update: marker-{i} missing"


def test_locked_rmw_serializes_full_rewrite(tmp_path: Path, monkeypatch) -> None:
    """A locked full read-modify-rewrite (the compaction/rotate pattern) run by
    two processes must not lose either's appended entry."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    session_id = "rmw_session"
    session_memory.create_session(session_id, "seed")

    barrier = tmp_path / "go2"
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(
            target=_writer_proc,
            args=(str(tmp_path), session_id, f"rmw-{i}", str(barrier)),
        )
        for i in range(6)
    ]
    for p in procs:
        p.start()
    barrier.write_text("go")
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    contents = {m["content"] for m in session_memory.get_session_messages(session_id)}
    for i in range(6):
        assert f"rmw-{i}" in contents


# ---------------------------------------------------------------------------
# Bug #6b — backup reaper
# ---------------------------------------------------------------------------


def test_reaper_keeps_recent_backups_deletes_stale(tmp_path: Path, monkeypatch) -> None:
    """The reaper keeps the most recent N backups and deletes older duplicates,
    NEVER the live session file."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    session_id = "default_chat_session"
    live = tmp_path / f"{session_id}.jsonl"
    live.write_text('{"schema":"x"}\n')

    # Fabricate 10 compaction backups with increasing timestamps.
    backups = []
    for i in range(10):
        stamp = f"2026010{i % 10}T0000{i:02d}Z"
        bp = tmp_path / f"{session_id}.compact-{stamp}.jsonl"
        bp.write_text("{}\n")
        backups.append(bp)

    result = session_memory.reap_session_backups(session_id, keep=3)

    survivors = sorted(p.name for p in tmp_path.glob(f"{session_id}.*-*.jsonl"))
    assert len(survivors) == 3, f"expected 3 survivors, got {survivors}"
    assert live.exists(), "reaper must NEVER delete the live session file"
    assert result["deleted"] == 7
    assert result["kept"] == 3
    # The 3 kept must be the newest by stamp (lexicographic on the ISO stamp).
    expected_keep = sorted(p.name for p in backups)[-3:]
    assert survivors == sorted(expected_keep)


def test_reaper_recency_judged_by_stamp_not_reason_prefix(tmp_path: Path, monkeypatch) -> None:
    """A recent backup with a late-sorting reason prefix (e.g. 'loop-backup')
    must outrank an OLD 'compact' backup. Recency is the embedded ISO stamp, not
    the alphabetical filename."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    session_id = "default_chat_session"
    (tmp_path / f"{session_id}.jsonl").write_text("{}\n")

    old_compact = tmp_path / f"{session_id}.compact-20260101T000000Z.jsonl"
    old_compact.write_text("{}\n")
    recent_loop = tmp_path / f"{session_id}.loop-backup-20260601T000000Z.jsonl"
    recent_loop.write_text("{}\n")

    session_memory.reap_session_backups(session_id, keep=1)

    assert recent_loop.exists(), "recent loop-backup must be kept over old compact"
    assert not old_compact.exists(), "old compact backup must be reaped"


def test_reaper_keep_zero_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """keep must be >= 1 so we can never wipe all history."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    with pytest.raises(session_memory.SessionMemoryError):
        session_memory.reap_session_backups("default_chat_session", keep=0)


def test_reaper_ignores_other_sessions(tmp_path: Path, monkeypatch) -> None:
    """Reaping one session must not touch another session's backups or live file."""
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    (tmp_path / "default_chat_session.jsonl").write_text("{}\n")
    other_live = tmp_path / "other_session.jsonl"
    other_live.write_text("{}\n")
    other_backup = tmp_path / "other_session.compact-20260101T000000Z.jsonl"
    other_backup.write_text("{}\n")
    for i in range(5):
        (tmp_path / f"default_chat_session.compact-2026010{i}T000000Z.jsonl").write_text("{}\n")

    session_memory.reap_session_backups("default_chat_session", keep=2)

    assert other_live.exists()
    assert other_backup.exists()


# ---------------------------------------------------------------------------
# Bug #6a — workbench approval queue (the operator's exact "race-time" surface)
# ---------------------------------------------------------------------------


def _approval_writer(queue_path: str, idx: int, barrier_path: str) -> None:
    """Separate OS process: append one approval record under the lock, after a
    sleep that widens the race window."""
    import time as _t
    from pathlib import Path as _P

    from hydra import workbench_approvals as wa

    bp = _P(barrier_path)
    while not bp.exists():
        _t.sleep(0.001)
    rec = wa.create_request(
        request_id=f"req-{idx}",
        run_id=f"run-{idx}",
        tool_name="bash",
        risk_tier="risky",
        summary=f"req {idx}",
        arguments_preview={"i": idx},
    )
    # Re-read after the barrier and sleep to maximize overlap, then append.
    _t.sleep(0.02)
    wa.append_record(_P(queue_path), rec)


def test_approval_queue_concurrent_appends_lose_no_update(tmp_path: Path) -> None:
    """Concurrent approval appends from real OS processes must all land under the
    lock — this is the operator's reported approvals 'race-time' clobber."""
    from hydra import workbench_approvals as wa

    queue = tmp_path / "approvals.jsonl"
    barrier = tmp_path / "approve-go"
    ctx = mp.get_context("spawn")
    procs = [
        ctx.Process(target=_approval_writer, args=(str(queue), i, str(barrier)))
        for i in range(8)
    ]
    for p in procs:
        p.start()
    barrier.write_text("go")
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    records = wa.load_records(queue)
    ids = {r.request_id for r in records}
    assert ids == {f"req-{i}" for i in range(8)}, f"lost an approval: {sorted(ids)}"
