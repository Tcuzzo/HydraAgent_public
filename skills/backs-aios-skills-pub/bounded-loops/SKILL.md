---
name: bounded-loops
description: "Use before work that can retry, poll, iterate, dispatch workers, or repeat external calls, including autonomous build, repair, review, and adoption loops."
license: MIT
---

# Bounded Loops

One mission has one remaining allowance. A retry, worker, model fallback, review
round, context reset, or nested skill spends that allowance; it never replenishes it.
[Build Flow](../backs-build-flow/SKILL.md) owns the task transitions. This method
owns budget accounting, retries, cancellation, and checkpoints.

## Declare and carry the contract

Before the first repeating action, record in the existing task packet:

- `mission_id`, integration owner, objective, and measurable acceptance condition.
- Deadline or maximum elapsed time, maximum attempts/rounds, and resource/call caps.
- Consumed and remaining allowance, the current episode's smaller round limit,
  failure signature, and next action. Use a small explicit inner cap, often three
  attempts; choose it for this task rather than treating an example as universal.
- Measured cost/token/call data where available. Mark unavailable counters unknown;
  do not invent a zero, an unlimited allocation, or a provider budget. Set enforceable
  time and attempt bounds using the counters this host actually exposes.

Inherit stricter user/project limits. If a routine authorized task has no supplied
limits, choose and state proportionate bounds, then act without a new permission
round. Ask only when the actual resource commitment needs authorization. A proposed
budget extension requires the mission owner's explicit decision and any necessary
operator authorization; changing workers or tactics is not an extension.

## The iteration

1. Check remaining allowance before issuing work. Allocate child ceilings within
   it, including parallel children: reserve their maximum allocation centrally so
   combined spend cannot exceed the parent. A child returns consumed/unused allowance.
2. Execute one hypothesis-driven action. In build/fix work, preserve real RED,
   apply the smallest complete fix, run scoped lint/type/behavior checks, and return
   quality evidence through [sniper-testing](../sniper-testing/SKILL.md) and
   [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md).
3. Checkpoint results and remaining allowance at safe boundaries. One writer updates
   the authorized task state atomically; workers return evidence to that writer.
   For a strict read-only review, use an already-authorized external task record or
   the response. Read-only scope does not become permission to write a repo ledger.
4. An identical failure without new evidence calls for a changed hypothesis or
   tactic, not another blind run. Close an exhausted inner episode. A different
   episode may use the remaining parent allowance, explicitly accounted for.
5. On mission exhaustion, cancellation, or a blocked required gate: checkpoint,
   explain the blocker, and stop repeating. Continue independent useful work only
   if its scope and remaining allocation permit it. Report unfinished capability
   **STILL-BUILDING**; a timeout or skipped gate is never GREEN or PROVEN.

## External calls and cancellation

Honor published provider limits and Retry-After signals. Reuse cached results;
when limits are unknown start conservatively and measure. Use bounded exponential
backoff on transient pushback, with a maximum elapsed time and attempt count.
Do not retry permanent authorization/input failures as transient failures. Waiting,
backoff, and fallback all count against the same mission deadline.

Stop at a safe state boundary: record work in flight, checkpoint, and cancel.
Do not start a second writer or leave a background loop running after cancellation.
New requests steer the current mission at that boundary; account for changed scope
explicitly rather than silently replacing the original objective.

## Handoff and exit

A receiver gets the same mission identity, base/candidate revisions, sealed test
identity, consumed and remaining allowance, evidence, and next action. It verifies
that state before resuming. A fresh context is not a fresh budget.

Success requires the original acceptance checks and requested delivery. Budget
exhaustion requires an honest checkpoint, not weaker tests, swallowed errors, data
deletion, an invented pass, or an automatic outer loop with a replenished counter.

See [session-handoff](../session-handoff/SKILL.md) for continuation and
[fleet-ladder](../fleet-ladder/SKILL.md) before actual configured model dispatch.
