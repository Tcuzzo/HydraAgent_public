# Repair / uplift spec template

The deliverable of a truth audit. A coder or agent with no memory of the audit must
be able to execute any ball from this file plus the lane blocks it cites. Copy the
skeleton, fill it, delete the guidance lines in italics.

The worked ball at the end (Ball 1A) shows the level of detail every ball needs.

---

```markdown
# <Product> — Repair and Uplift Spec (<date>)

> For agentic workers: one fresh builder per ball, one fresh reader per ball, the
> tribunal before anything lands. Every ball is also a valid `/karpathy` experiment:
> its Goal, Gate (metric), Scope (fair-game files) and Budget are stated.

**Goal:** <the end state in two sentences, in the human's words where possible>.

**Architecture:** <one paragraph: which piece is the product, which pieces become
helpers, what leaves the product path>.

**Spec (travels with this plan):** `LEDGER.md` / `ledger.jsonl` (findings + rules),
`lanes/*.md` (every finding's evidence, root cause, fix seam, proof). When a ball says
"spec: SVC-03", open that block; it is binding detail. Live status: `STATUS.md`.

## Repos and roots (exact; nothing else is in scope)

| key | path | remote | role |
|---|---|---|---|
| APP | `<path>` | `<remote url or "none">` | the shipping product |
| OPS | `<path>` | `<remote>` | deploy units and host config |
| AUD | `<audit dir>` | none (ignored by git) | ledger, lanes, this plan, tools |

<Name stale forks and live data dirs that no builder or test may write.>

## Global constraints (every ball inherits these)

- No force push, ever: no `--force`, `--force-with-lease`, `+refspec`, remote branch
  deletion, or repo delete/recreate. A push that cannot fast-forward stops and escalates.
- One ball = one branch `ball/<id>` in its own worktree off the same base commit. A ball
  edits only its Scope; `python AUD/tools/ball_scope_check.py <id> --plan AUD/PLAN.md`
  passes before review.
- No drive-by edits outside the finding being fixed.
- No model names at call sites, in prompts or in configs. Code asks for a role and a
  capability; the configured fleet ladder seats the model.
- Sandbox only for anything that writes: <the sandbox recipe: home dir, port, keyring,
  schedulers off>. Test accounts only for anything that spends or sends.
- Every loop is bounded: ceiling, backoff, then a loud human-visible state.
- Failures are loud. No swallowed exceptions, no "complete" with zero work, no green
  health while a dependency fails.
- Red first: failing tests are committed before the fix; the reviewer checks they were
  not edited after the red commit.

## Roles while executing

- **Coordinator (single write spine).** Owns the main branches. Cuts worktrees, throws
  balls, merges passed balls in wave order, moves ledger entries to PLANNED / IN-BUILD,
  sends envelopes to the tribunal. Never writes product code.
- **Ledger keeper.** The only writer of `ledger.jsonl`. After each receipt:
  `ledger.py import-lane lanes/NEW_<ball>.md`, `ledger.py check`, `ledger.py render`.
  A failing `check` halts the wave.
- **Builder (one per ball, fresh session).** Reads this plan, the cited lane blocks and
  its scope files. Returns -1 / 0 / 1 (see `coder_prompt.md`). New defects found while
  building go into `lanes/NEW_<ball>.md` as blocks; never fixed outside scope.
- **Reader/tester (fresh session, read-only).** Re-runs the ball's native proof on the
  merged spine; writes the PROVEN evidence.
- **Tribunal.** Convened through the configured fleet ladder only, with the builder's
  execution recorded and excluded. Every finding becomes a failing test in the same ball.

Ledger flow: PLANNED (coordinator, plan accepted) → IN-BUILD (coordinator, ball thrown,
recording who builds it: `ledger.py status <ID> IN-BUILD --by coordinator --builder <builder id>`)
→ PROVEN (reader, native evidence) → CLOSED (ledger keeper, citing the tribunal run).
The tool refuses an IN-BUILD move without `--builder`, and refuses the recorded builder,
the IN-BUILD actor, or the prover when they try to prove, close, adjudicate, or amend
the same entry.

## Root-cause classes (fix the class, sweep siblings)

| class | what it is here | ledger entries |
|---|---|---|
| RC1 many sources of truth | <...> | <ids> |
| RC2 many authorities, one resource | <...> | <ids> |
| RC3 silent failure dressed as success | <...> | <ids> |
| RC4 inputs the engine never reads | <...> | <ids> |
| RC5 undefined trust boundaries | <...> | <ids> |
| RC6 tests that prove mocks | <...> | <ids> |
| RC7 harness lets agents pick models or loop blind | <...> | <ids> |

<Every OPEN P0/P1 id appears in exactly one class row and in at least one ball.>

## Review focus (inputs no single ball names, most likely to bite first)

1. <failure mode> — expected behavior. Pinned by Ball <id> `<test name>`.
2. ...
<Five rows. Each names the test that pins it. If no test pins it, add one to a ball.>

---

## Wave 0 — Truth, safety, harness (sequential; coordinator)

#### Ball 0.1 · Freeze what runs (coordinator) — <ids>
**Goal:** every byte that runs today is in a commit. **Gate:** `git status --porcelain`
empty in every product checkout. **Budget:** 15 min.
- [ ] <exact commands>

#### Ball 0.N · Grade this plan (coordinator)
- [ ] Envelope = PLAN.md, LEDGER.md, lanes/*.md, STATUS.md, author fields redacted.
  Convene the tribunal through the configured ladder. Every refusal becomes a plan
  edit; reconvene until unanimous. Only then move entries to PLANNED with `--task`.

## Wave 1 — <name> (parallel; disjoint scopes)

#### Ball 1A · ...
<every ball in this shape; see the worked example below>

## Wave N — Final proof and release
- [ ] Aggregate tribunal over the merged diff (whole files).
- [ ] Native day: one full working day with no builder session open; proof = watchdog rows.
- [ ] Owner walk (15 min) on the real surfaces. Any miss is a new ledger entry.
- [ ] Close-out: `ledger.py list --open` prints nothing (every entry is CLOSED or ADJUDICATED-NOT-A-BUG);
  review the adjudicated ones with `ledger.py list`, which shows every entry; `ledger.py check` passes.

## Ledger mechanism (how anyone adds to it)
<the ledger.py commands with this plan's paths: import-lane, status (IN-BUILD with
`--builder <builder id>`), amend, check, render>

## Open items only the owner can answer (everything else was deduced)
1. <decision> — deduced default: <...>. One word overrides: "<word>".
```

---

## Ball checklist

Every ball carries all of these. A missing one is a plan defect the tribunal will flag.

| field | rule |
|---|---|
| title line | `#### Ball <id> · <outcome> — <ledger ids>` |
| Goal | the outcome in one sentence a human can check |
| Gate | a metric or observable that is true only when the goal is met |
| Scope | exact files as backticked relative paths. A wrapped line must start (after any indent) with a backticked path or a comma; anything else ends the field, and a line that ends it while still naming a path is refused. Symbols go in parentheses after their file (`api/health.py` (`health`)); a path-like token in parentheses still counts. Routes, absolute paths and `..` are refused. Optionally pin each existing file to its content at the base, the LEAP hash: `api/health.py@sha256:3f9a1c0d` (8+ hex digits of `git show <base>:<path> \| sha256sum`); the scope check reports drift. Tag a field with its repo key, **Scope (OPS):**; an untagged field belongs to every repo. Balls in one wave never share a file in the same repo; `ball_scope_check.py --disjoint` proves it |
| Interfaces | exact signatures other balls rely on, produced or consumed |
| Red tests | by name, each with its assertion in a parenthesis |
| Decisions | only where the tests leave a real choice; say what was deduced and from what |
| Native proof | a watchdog row or a live observation, not a green test |
| Rollback | the one command or revert that undoes it |
| Budget | time, rounds (default 4), paid calls |

The `/karpathy` mapping is direct: Goal → goal, Gate → metric gate, Scope → fair game
files, Budget → budget.

---

## Worked example ball

The product in this example is a desktop app with a background worker that syncs a
user's notes to a cloud API. The audit found the worker retries forever when the API
rejects the token (SVC-01, P0), the health page stays green meanwhile (SVC-02, P2),
and the only test of the sync loop replaces the loop itself (TST-04, P1).

```markdown
#### Ball 1A · The sync loop stops on a rejected token and tells the human — SVC-01, SVC-02, TST-04

**Goal:** when the cloud API rejects the token, the worker stops retrying within one
loop, health turns red, and the human sees one plain message telling them what to do.
**Gate:** in the sandbox, an injected 401 produces `health()["ok"] is False` and exactly
one owner event within 90 s; a 500 backs off 60/120/300 s and recovers on the next 200.
**Budget:** 2 h wall clock, 4 build rounds, 0 paid calls.

**Scope:** `runtime/loop_guard.py` (new), `runtime/sync_worker.py@sha256:9c1e4b7a20d3` (`run_forever` only),
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
```

Why this ball is good enough to hand off: a builder can start without asking anything.
The tests say what done means, the interfaces say what other balls depend on, the
scope says what may change, and the proof says who confirms it after the builder is gone.
