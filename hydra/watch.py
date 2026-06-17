"""hydra.watch — recurring / triggered run engine for `hydra watch`.

Pure, dependency-free, cross-platform. The loop decides WHEN to fire a cycle
(an interval timer and/or a file-change watch with debounce) and calls an
injected ``run_cycle`` callback. Time, the filesystem, and the agent are all
injected, so the engine is fully unit-testable and the same code runs on
Linux/macOS/Windows (polling only — no inotify, threads, or signals).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class WatchError(Exception):
    """Raised when the watch configuration is invalid (e.g. no trigger)."""


_DURATION_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([smh]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(text: str) -> float:
    """Parse '30s' / '10m' / '2h' / '45' (bare number = seconds) into seconds."""
    match = _DURATION_RE.match(text or "")
    if not match:
        raise ValueError(f"invalid duration: {text!r} (use e.g. 30s, 10m, 2h)")
    value, unit = match.group(1), match.group(2).lower()
    return float(value) * _UNIT_SECONDS[unit]


@dataclass(frozen=True)
class WatchConfig:
    """How `hydra watch` decides when to run. At least one trigger is required."""

    interval_seconds: float | None = None   # timer; None disables the timer
    watch_paths: tuple[Path, ...] = ()       # file/dir paths; empty disables file-watch
    poll_seconds: float = 2.0                # how often the loop ticks
    debounce_seconds: float = 1.0            # quiet period after a change before firing
    max_cycles: int | None = None            # stop after N cycles; None = unbounded
    stop_file: Path | None = None            # advisory; the CLI maps this to stop_check


class WatchLoop:
    """Single-thread poll loop. All side effects are injected for testability.

    clock()        -> float monotonic seconds.
    sleep(seconds) -> advance time / block.
    snapshot(paths)-> dict mapping path -> mtime (compared for change detection).
    run_cycle(reason) -> run one agent cycle; return value is ignored here.
    stop_check()   -> optional; truthy means stop at the next safe point.
    """

    def __init__(self, config, *, clock, sleep, snapshot, run_cycle, stop_check=None):
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._snapshot = snapshot
        self._run_cycle = run_cycle
        self.stop_check = stop_check

    def run(self) -> int:
        """Run until a stop condition; return the number of cycles executed."""
        cfg = self.config
        if cfg.interval_seconds is None and not cfg.watch_paths:
            raise WatchError(
                "watch needs at least one trigger: set interval_seconds or watch_paths"
            )

        cycles = 0
        last_run = self._clock()
        baseline: dict | None = None
        pending_change: float | None = None

        try:
            while True:
                if self.stop_check and self.stop_check():
                    break
                now = self._clock()

                file_due = False
                if cfg.watch_paths:
                    cur = self._snapshot(cfg.watch_paths)
                    if baseline is None:
                        baseline = cur  # establish baseline; never fire on first look
                    elif cur != baseline:
                        baseline = cur
                        pending_change = now  # (re)start the debounce timer on each change
                    elif (
                        pending_change is not None
                        and (now - pending_change) >= cfg.debounce_seconds
                    ):
                        file_due = True

                timer_due = (
                    cfg.interval_seconds is not None
                    and (now - last_run) >= cfg.interval_seconds
                )

                if timer_due or file_due:
                    reason = "timer" if timer_due else self._change_reason(cfg.watch_paths)
                    self._run_cycle(reason)
                    cycles += 1
                    last_run = now
                    pending_change = None
                    if cfg.max_cycles is not None and cycles >= cfg.max_cycles:
                        break

                self._sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            pass

        return cycles

    @staticmethod
    def _change_reason(paths) -> str:
        names = ", ".join(str(p) for p in paths)
        return f"change in {names}" if names else "change"
