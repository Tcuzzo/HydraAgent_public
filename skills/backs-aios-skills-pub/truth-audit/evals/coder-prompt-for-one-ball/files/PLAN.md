# Notes Sync — Repair Spec (2026-01-15)

**Goal:** the sync worker tells the truth about its state and never retries a rejected sign-in forever.

**Spec:** `ledger.jsonl`, `lanes/*.md`. When a ball says "spec: SVC-01", open that block.

## Repos and roots

| key | path | remote | role |
|---|---|---|---|
| APP | `./app` | `https://example.com/notes-sync.git` | the shipping product |
| AUD | `./audit` | none | ledger, lanes, this plan, tools |

## Global constraints

- No force push. One ball = one branch `ball/<id>` in its own worktree off `main`.
- No model names in code, prompts or config. Sandbox only for anything that writes: `NOTES_HOME=<scratch>`, port 18080, file keyring under `NOTES_HOME`, scheduler off.
- Every loop bounded; failures loud; red tests first.

## Wave 1 — Stop the bleeding

#### Ball 1A · The sync loop stops on a rejected token and tells the human — SVC-01, SVC-02

**Goal:** when the cloud API rejects the token, the worker stops retrying within one
loop, health turns red, and the human sees one plain message telling them what to do.
**Gate:** in the sandbox, an injected 401 produces `health()["ok"] is False` and exactly
one owner event within 90 s; a 500 backs off 60/120/300 s and recovers on the next 200.
**Budget:** 2 h wall clock, 4 build rounds, 0 paid calls.

**Scope:** `runtime/loop_guard.py` (new), `runtime/sync_worker.py` (`run_forever` only),
`api/health.py` (`health`), `tests/runtime/test_loop_guard.py` (new),
`tests/runtime/test_sync_auth_fault.py` (new)

**Interfaces (produces):**
- `class LoopGuard(name: str, ceiling: int = 5, backoff_s: tuple[float, ...] = (60, 120, 300, 600), clock: Clock = SystemClock())`
  - `run(fn: Callable[[], T]) -> T | None` — never raises; records the outcome
  - `state -> Literal["ok", "degraded", "stopped"]`; `last_error -> str | None`
  - emits exactly one owner event per state transition, never one per failure
- `class CredentialFault(Exception)` — the HTTP client converts 401/403 into it;
  `LoopGuard` stops on it with no retry.
- `health()` gains `sync_auth: {"state": "ok" | "rejected" | "unknown", "since": iso8601}`
  and returns `ok=False` while `state == "rejected"`.
Consumed by Ball 1C (the header chip reads `health()["sync_auth"]`).

**Red tests (commit these first; they fail on today's code):**
- `test_auth_fault_stops_and_tells` (fake API returns 401 → `run` called once, guard
  state `stopped`, `health()["ok"] is False`, exactly one owner event whose text
  contains "rejected the sign-in")
- `test_transient_error_backs_off` (500 ×3 → injected clock records waits 60, 120, 300)
- `test_ceiling_degrades_then_recovers` (5 failures → `degraded`; next 200 → `ok`, one
  recovery event)
- `test_sync_loop_runs_the_real_loop` (drives the real `run_forever` with a loopback
  HTTP server serving recorded responses; no monkeypatch of `run_forever` or `LoopGuard`)

**Decisions:** backoff steps and the ceiling of 5 come from the API's published rate
limit (spec SVC-01 evidence). Message wording follows the human-voice skill; the
reviewer may change words, not the one-event-per-transition rule.

**Steps:**
- [ ] Red: write the four tests; run them; record the failing output in the receipt.
- [ ] Green: implement `LoopGuard` and wire `run_forever` through it. Spec: SVC-01, SVC-02.
- [ ] Sweep: `grep -rn "except Exception" runtime/` — every loop that swallows is listed
  in the receipt; loops outside this scope go to `lanes/NEW_1A.md`, not fixed here.
- [ ] `python AUD/tools/ball_scope_check.py 1A --plan AUD/PLAN.md` exits 0.

**Native proof:** with no builder session open, the watchdog row
`SELECT ok, observed_json FROM proof WHERE name = 'sync_auth' AND at > :merge_time`
shows `ok=1` through a full day, and the sandbox run with a revoked token shows
`state=rejected` within one loop and one owner toast.

**Rollback:** `git revert <merge sha>`; the old loop returns, no data migration involved.
