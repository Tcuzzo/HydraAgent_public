"""Unit tests for hydra.watch — the pure recurring/triggered run engine.

The engine takes injected clock/sleep/snapshot/run_cycle so timing and the
filesystem are fully simulated: no real sleeping, no real files, no real agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from hydra.watch import WatchConfig, WatchError, WatchLoop, parse_duration


class FakeClock:
    """Deterministic monotonic clock; sleep() advances it instead of blocking."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _loop(config, *, snapshot=None, run_cycle=None, stop_check=None):
    clock = FakeClock()
    calls: list[tuple[float, str]] = []

    def _default_cycle(reason: str):
        calls.append((clock.now(), reason))
        return {"ok": True}

    loop = WatchLoop(
        config,
        clock=clock.now,
        sleep=clock.sleep,
        snapshot=snapshot or (lambda paths: {}),
        run_cycle=run_cycle or _default_cycle,
        stop_check=stop_check,
    )
    return loop, clock, calls


# ── parse_duration ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [("30s", 30.0), ("10m", 600.0), ("2h", 7200.0), ("45", 45.0), ("1.5m", 90.0)],
)
def test_parse_duration_units(text, expected):
    assert parse_duration(text) == expected


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("soon")


# ── trigger validation ──────────────────────────────────────────────────────


def test_no_trigger_raises_watch_error():
    cfg = WatchConfig(interval_seconds=None, watch_paths=())
    loop, _clock, _calls = _loop(cfg)
    with pytest.raises(WatchError):
        loop.run()


# ── interval timer ──────────────────────────────────────────────────────────


def test_timer_fires_once_after_interval():
    cfg = WatchConfig(interval_seconds=10, watch_paths=(), poll_seconds=2, max_cycles=1)
    loop, _clock, calls = _loop(cfg)
    n = loop.run()
    assert n == 1
    assert len(calls) == 1
    fired_at, reason = calls[0]
    assert fired_at == pytest.approx(10.0)  # after one full interval, not at t=0
    assert "timer" in reason.lower()


def test_timer_does_not_fire_before_interval():
    cfg = WatchConfig(interval_seconds=10, watch_paths=(), poll_seconds=2)
    loop, clock, calls = _loop(cfg, stop_check=lambda: clock.now() >= 8)
    n = loop.run()
    assert n == 0
    assert calls == []


def test_timer_fires_repeatedly():
    cfg = WatchConfig(interval_seconds=10, watch_paths=(), poll_seconds=2, max_cycles=3)
    loop, _clock, calls = _loop(cfg)
    n = loop.run()
    assert n == 3
    assert [round(t) for t, _ in calls] == [10, 20, 30]


# ── file watch + debounce ───────────────────────────────────────────────────


def test_file_change_fires_after_debounce():
    cfg = WatchConfig(
        interval_seconds=None,
        watch_paths=(Path("a"),),
        poll_seconds=2,
        debounce_seconds=4,
        max_cycles=1,
    )
    clock_box = {}

    def snap(paths):
        # baseline {a:1} until t=4, then changes to {a:2} and stays
        return {"a": 1.0} if clock_box["clock"].now() < 4 else {"a": 2.0}

    loop, clock, calls = _loop(cfg, snapshot=snap)
    clock_box["clock"] = clock
    n = loop.run()
    assert n == 1
    fired_at, reason = calls[0]
    # change seen at t=4, stable for debounce=4 → fires at t=8
    assert fired_at == pytest.approx(8.0)
    assert "chang" in reason.lower() or "file" in reason.lower()


def test_baseline_snapshot_does_not_fire():
    # files exist from the start but never change → no cycle ever runs
    cfg = WatchConfig(interval_seconds=None, watch_paths=(Path("a"),), poll_seconds=2, debounce_seconds=2)
    loop, clock, calls = _loop(
        cfg, snapshot=lambda paths: {"a": 1.0}, stop_check=lambda: clock.now() >= 30
    )
    n = loop.run()
    assert n == 0
    assert calls == []


def test_debounce_collapses_burst_into_one_fire():
    cfg = WatchConfig(
        interval_seconds=None,
        watch_paths=(Path("a"),),
        poll_seconds=2,
        debounce_seconds=4,
        max_cycles=5,
    )
    clock_box = {}

    def snap(paths):
        # changes every tick from t=2..t=8 (burst), then stable from t=10
        t = clock_box["clock"].now()
        if t < 2:
            return {"a": 0.0}
        if t <= 8:
            return {"a": t}  # different every tick = ongoing burst
        return {"a": 8.0}  # settles

    loop, clock, calls = _loop(cfg, snapshot=snap)
    clock_box["clock"] = clock
    # cap runaway: stop once we are well past settle + debounce
    loop_stop = lambda: clock.now() >= 40  # noqa: E731
    loop.stop_check = loop_stop
    n = loop.run()
    assert n == 1  # the whole burst collapses to a single fire


# ── both triggers ───────────────────────────────────────────────────────────


def test_both_triggers_either_fires():
    # timer every 100 (won't hit in window) + a file change at t=4 → file fires
    cfg = WatchConfig(
        interval_seconds=100,
        watch_paths=(Path("a"),),
        poll_seconds=2,
        debounce_seconds=2,
        max_cycles=1,
    )
    clock_box = {}
    snap = lambda paths: {"a": 1.0} if clock_box["clock"].now() < 4 else {"a": 2.0}  # noqa: E731
    loop, clock, calls = _loop(cfg, snapshot=snap)
    clock_box["clock"] = clock
    n = loop.run()
    assert n == 1
    assert calls[0][0] == pytest.approx(6.0)  # change@4, debounce 2 → fire@6


# ── stop conditions ─────────────────────────────────────────────────────────


def test_stop_check_halts_cleanly():
    cfg = WatchConfig(interval_seconds=2, watch_paths=(), poll_seconds=2, max_cycles=10)
    loop, clock, calls = _loop(cfg, stop_check=lambda: clock.now() >= 5)
    n = loop.run()
    # fires at t=2, t=4; stop_check true at t=6 → stops with 2 cycles
    assert n == 2


def test_max_cycles_stops():
    cfg = WatchConfig(interval_seconds=1, watch_paths=(), poll_seconds=1, max_cycles=2)
    loop, _clock, calls = _loop(cfg)
    n = loop.run()
    assert n == 2
    assert len(calls) == 2


def test_keyboard_interrupt_stops_gracefully():
    cfg = WatchConfig(interval_seconds=1, watch_paths=(), poll_seconds=1, max_cycles=10)

    def boom(reason):
        raise KeyboardInterrupt

    loop, _clock, _calls = _loop(cfg, run_cycle=boom)
    # KeyboardInterrupt during a cycle is caught → run() returns, does not propagate
    n = loop.run()
    assert n == 0
