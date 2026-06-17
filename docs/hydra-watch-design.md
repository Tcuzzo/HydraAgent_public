# Design — `hydra watch` (recurring / triggered runs)

2026-06-17

## Purpose

Run a Hydra agent task automatically and unattended — on a timer, when files
change, or both — without a long-running daemon or external scheduler. Safe by
default: read-only unless the operator explicitly opts in to acting.

## Decisions

- **Triggers:** interval timer **and** file/folder watch. Either can be set; at
  least one is required. If both, either one firing runs a cycle.
- **Task source:** an inline prompt **or** `--task-file PATH` (read fresh each
  cycle so edits take effect on the next run). Exactly one is required.
- **Safety:** default approval policy is **`deny`** → risky tools
  (`bash`/`fs_write`/`fs_edit`) are refused, so the agent can only read/analyze.
  `--yolo` (alias for `--approval-policy allow`) lets it act. Reuses the existing
  `ApprovalPolicy`; adds no new gate.
- **Approach:** single-thread poll loop + a pure engine. Dependency-free,
  cross-platform (Linux/macOS/Windows), no threads/inotify/signals.

## Components

### `hydra/watch.py` — pure engine (the testable core)

- `WatchConfig` (frozen dataclass): `interval_seconds: float|None`,
  `watch_paths: tuple[Path,...]`, `poll_seconds=2.0`, `debounce_seconds=1.0`,
  `max_cycles: int|None=None`, `stop_file: Path|None=None`.
- `WatchError(Exception)` — raised on invalid config (no trigger).
- `WatchLoop(config, *, clock, sleep, snapshot, run_cycle, stop_check=None)`:
  - injected callables: `clock()->float` (monotonic seconds), `sleep(s)`,
    `snapshot(paths)->dict[str,float]` (path→mtime), `run_cycle(reason:str)->Any`,
    `stop_check()->bool` (optional, e.g. stop-file present).
  - `run() -> int`: validates ≥1 trigger; loops; fires `run_cycle` when a trigger
    is due; returns the number of cycles executed. Stops on `max_cycles`,
    `stop_check()`, or `KeyboardInterrupt` (clean).

**Firing logic per tick:** `now = clock()`.
- timer due when `interval_seconds` set and `now - last_run >= interval_seconds`.
- file due: when `snapshot(watch_paths)` differs from the last snapshot, record the
  change time; fire once the snapshot has been **stable for `debounce_seconds`**
  (collapses rapid bursts into one run). The first snapshot establishes a baseline
  and does **not** fire.
- On fire: call `run_cycle(reason)`, set `last_run = now`, clear pending change.
- After each fire: stop if `max_cycles` reached. Each tick: `stop_check()` →
  stop; then `sleep(poll_seconds)`.

### `hydra/cli/cmd_watch.py` — thin CLI shell

- `register_watch_command(sub)` + `cmd_watch(args) -> int`.
- Flags: positional `prompt` (optional), `--task-file`, `--every <dur>`
  (`30s`/`10m`/`2h`), `--watch PATH` (repeatable), `--poll`, `--debounce`,
  `--max-cycles`, `--stop-file`, `--yolo`, `--approval-policy`, `--provider`,
  `--model`, `--root`, `--max-iterations`, `--timeout`.
- Validates: exactly one of {prompt, `--task-file`}; at least one of {`--every`,
  `--watch`}; `--yolo` and `--approval-policy` not both set.
- Builds `run_cycle` = a closure that resolves the task text (re-reading
  `--task-file` each call), runs the existing `AgentLoop` ask path with the
  resolved approval policy (`deny` default, `allow` under `--yolo`), and prints a
  one-line per-cycle summary: ISO time, trigger reason, iterations, tool_calls,
  short result.
- Wires real `clock=time.monotonic`, `sleep=time.sleep`, `snapshot`=mtime walk
  over the watch paths (files + recursive dir `stat`), `stop_check`=stop-file
  exists. Handles `Ctrl-C` → prints a stop summary, exits 0.
- Register in `hydra/__main__.py` dispatch as `"watch"`.

### Duration parsing

`_parse_duration("10m") -> 600.0` supporting `s`/`m`/`h` (and bare seconds).
Pure, unit-tested.

## Testing

Engine (pure, injected fakes — no real time/files/agent):
1. timer fires after the interval elapses; not before.
2. file change fires (after debounce); baseline snapshot does not fire.
3. debounce collapses a burst of changes into one fire.
4. both triggers: either one fires a cycle.
5. `max_cycles` stops after N and `run()` returns N.
6. `stop_check`/stop-file stops cleanly.
7. no trigger configured → `WatchError`.
8. `_parse_duration` table (`30s`, `10m`, `2h`, `45`).

CLI: arg-validation tests (missing task, both task sources, no trigger, both yolo
and policy) return a clear error / nonzero.

E2E smoke (opt-in): `hydra watch --every 1s --max-cycles 1 "say ok"` runs the
agent exactly once and exits 0.

## Out of scope (YAGNI)

Cron syntax, multi-task scheduling, a `--log` JSONL file, quiet hours, native
inotify/watchdog. A user wanting OS-level scheduling still uses cron / systemd
timer / Task Scheduler to invoke `hydra ask` or `hydra watch`.
