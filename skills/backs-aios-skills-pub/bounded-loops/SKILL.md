---
name: "bounded-loops"
description: "Use before starting any loop that can retry, poll, iterate, or call an external API — agent loops, repair loops, schedulers, watchers. Declares budget ceilings, checkpoints on exhaustion, and makes hammering structurally impossible. Trigger words: bounded loop, budget, ceiling, retry, backoff, rate limit, throttle, kill-switch, checkpoint, runaway, infinite loop, spin, budget exhaustion."
license: "MIT"
---

# Bounded Loops
**Effort:** free — ceilings and checkpoints declared before the loop starts; cuts cost outright. Removes: runaway loop spend — burned quota, 429-blocked routes, and progress a crash erases.

An unbounded loop is the most expensive bug an agent can ship. It burns budget, hammers
providers until they block you, and hides its own failure inside the spin. Every loop
gets a ceiling, a checkpoint, and a loud way to die — before it starts.

## When to run

Before starting any loop: a repair loop, a retry wrapper, a poller, a scheduler, a
multi-step autonomous run, anything that can re-issue a call or re-attempt a step.

## The steps

1. **Declare the budget first.** Tokens, cost, wall time, and max attempts — written
   down before the first iteration. A loop with no declared budget is unbounded by
   definition and does not start.
2. **Cap the inner rounds.** One inner episode (one LLM/tool cycle on one problem) gets
   a small fixed round ceiling (about 4). The ceiling bounds the episode, not the
   mission — unfinished work moves up, it does not grind.
3. **Checkpoint every iteration.** Durable state on disk — run manifest, evidence log,
   current step — never chat memory. Anyone (including a fresh session) can resume from
   the last checkpoint.
4. **On exhaustion: checkpoint, then escalate.** Hand the checkpoint to the outer loop
   or to your human with what was done, what is left, and the blocker. Never silently
   continue past a budget. Never silently stop, either — exhaustion is loud.
5. **Respect every external API.** Before the first call, learn the provider's rate
   limit and quota; when unknown, treat it as strict (one call, wide spacing) until
   measured. Throttle every call, cache and reuse responses, and hold a hard
   per-window ceiling.
6. **Back off exponentially on pushback.** A 429 or 503 means wait, then wait longer.
   Zero instant same-endpoint retries. A tight retry against one endpoint is how a
   working route dies: it burns quota and can get your whole egress address blocked.
7. **Carry a loud, bounded kill-switch.** Any loop that can re-issue a call has a max
   attempt count; when it hits, the loop stops LOUD with the evidence — never an
   infinite or silent spin.
8. **Stop and queue at safe points only.** Stop means checkpoint-then-cancel. New work
   queues for the next safe point (a state boundary between steps) — never injected
   mid-step. One loop instance, one writer, atomic state writes.

## Hard rules (what fails this skill)

- A loop that starts with no declared token / cost / time / attempt budget.
- Continuing past an exhausted budget, silently or otherwise, without escalating.
- An instant retry against the same endpoint, or any retry path without backoff.
- A retry loop with no attempt cap, or a cap that fails quietly when hit.
- Progress state kept only in conversation memory — a crash erases the run.
- Two loop instances writing the same state, or non-atomic state writes.
- Escaping the loop by weakening its own exit checks — a green produced by lowering
  the bar, deleting data, or swallowing errors is a fake green, not an exit.

## Works well with

- [optimus](../optimus/SKILL.md) — load the floor before any loop starts.
- [repair-loop](../repair-loop/SKILL.md) — the main consumer of these ceilings.
- [fleet-ladder](../fleet-ladder/SKILL.md) — bounded fallback across models, not hammering one.
- [session-handoff](../session-handoff/SKILL.md) — what a checkpoint escalates into.
