---
name: "gpu-dispatch"
description: "Use when dispatching local models to GPUs — scheduling inference work, picking a card, or managing model residency. One model per GPU, no spill to system RAM, keep warm through the loop, unload at loop end, admit by measured truth. Trigger words: gpu, vram, gpu dispatch, model loading, keep alive, resident model, local inference, spill, warm."
license: "MIT"
---

# GPU Dispatch Law
**Effort:** free — dispatcher-enforced rules read off the node's own live state; cuts cost outright. Removes: VRAM-spilled runs quietly 10x slower, cold-start churn between jobs, and cards idled by assumed fences.

Four rules for running local models on GPUs. They exist because the two common
failure modes are opposite and equally expensive: thrashing cards with loads
and spills, and over-fencing hardware so it sits idle. Both are lost capability.
Enforce these in the dispatcher as code — never as a rule a model must remember.

## When to run

- Before dispatching any inference job to a local GPU.
- When designing or reviewing a dispatcher, scheduler, or model router.
- When a local run is mysteriously slow, or a card is mysteriously "unavailable".

## The four rules

1. **One model resident per card, at a time.** Before any dispatch, read the
   node's live loaded-model state from the runtime's own API. If a different
   model is resident, either use it or unload it first. Never load a second
   model beside it.
2. **No spill to system RAM — abort, not slow-run.** Verify the model fits
   entirely in the card's free VRAM before dispatch, and assert it stays fully
   in VRAM during work. Any spill into system RAM is an ABORT, not a degraded
   run — a spilled model is quietly 10x slower and poisons every job behind it.
   A model that does not fit above the card's reserved floor is not dispatched
   to that card; pick a smaller model or another card.
3. **Keep the card warm for the whole work loop.** Hold the model resident with
   a bounded keep-alive (a floor and ceiling you configure, never unlimited)
   and refresh it while the loop runs. No cold-start churn between jobs in the
   same loop.
4. **Unload only when the loop completes.** Explicit release at loop end — not
   after each job. Per-job unloading is cold-start churn; never unloading is a
   leak. Loop-end release is the seam.

## Admission by measured truth

Whether a card may take work is decided by live measurement, never assumption:

- A **real probe** of the node — not a stale "unreachable" note in a config.
- **Real free VRAM** above the card's reserved floor — the floor is the only
  standing limit; everything above it is free to use.
- A **real running process check** for interactive workloads. A live game,
  stream, or editing session on the card wins instantly — but its presence is
  measured, never assumed from a marker file or a hardcoded "cold" list.

Fail-closed defaults, "unknown purpose" denials, and marker files whose absence
means "fence on" are all the same bug: the runtime refusing hardware the human
owns. Over-gating owned hardware is lost capability, and lost capability is a
defect. Only the human's live word adds or lifts a fence.

## Hard rules (what fails this skill)

- Loading a second model onto a card that already has one resident.
- Continuing a run after detecting VRAM spill instead of aborting.
- An unbounded keep-alive, or unloading between jobs inside one loop.
- Denying a card based on a config note, marker file, or assumption instead of
  a live probe.
- Enforcing any of this by prompt instead of in the dispatcher's code.

## Works well with

- [invariant-floor](../invariant-floor/SKILL.md) — measured truth and loud
  failures are floor laws; this skill applies them to GPUs.
- [fleet-ladder](../fleet-ladder/SKILL.md) — resolve which model to dispatch
  before deciding where it runs.
- [bounded-loops](../bounded-loops/SKILL.md) — the work loop that keep-alive
  and loop-end release are scoped to.
