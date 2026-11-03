---
name: fix-shit
description: Use when the operator reports broken behavior, says fix shit or stop swallowing errors, or asks to finish a stubborn bug.
license: MIT
---

# Fix Shit

Use this as the fast entry point into `root-cause-first` and `repair-loop` within
the current [BACKS Build Flow](../backs-build-flow/SKILL.md) task. Keep its one
integration owner, operator essence, `task_mode`, candidate identity, and evidence;
do not reload a valid context packet or start another lifecycle. Direct invocation
establishes the packet once. The caller's remaining budget and deadline from
[bounded-loops](../bounded-loops/SKILL.md) apply to every reproduction, fix, model
fallback, and review. This skill never resets them or hides a failing signal.

1. Name the user's broken behavior and the exact surface where it appears.
2. Build a repeatable red signal from the real path. Trace the input and each
   boundary to find where actual behavior diverges from expected behavior. Seal
   the original failing contract with [red-first](../red-first/SKILL.md) before
   a builder changes the owning seam. A diagnostic task returns this evidence.
3. Identify the root cause and all callers of the broken seam. Preserve the
   original error in logs or the operator-facing state; a fallback may protect
   the app, but it must not turn failure into false success.
4. For an authorized fix, use [repair-loop](../repair-loop/SKILL.md) inline: the
   smallest class-level repair, scoped real lint/type/test commands from the
   pinned aggregate candidate diff, then clean-code-gauntlet quality and mutation
   checks. Preserve the sealed original; a newly exposed gap gets a separate
   supplemental test. Reuse already verified stages instead of repeating them.
5. Complete the required author-disjoint review, affected-suite landing gate,
   and actual app-path checks for the requested delivery. Verify both the desired
   result and the explicit failure state. Missing reviewer capability is a runtime
   gap, never a self-issued independent pass.
6. Return the candidate and evidence to the integration owner. Report PROVEN only
   with the required review and real delivery evidence; otherwise checkpoint
   STILL-BUILDING with the exact remaining red bar and next action. Budget
   exhaustion preserves the work and limits; continue useful unblocked work only
   within its remaining allowance.

If the error is intermittent, capture timing and a bounded reproduction rate;
do not infer a cause from one green rerun. For code-location ambiguity, start
with `building-with-backs` and its target check.

## Evidence and independent review

For shipped executables, packages, or requested runtime behavior, invoke
[rea-investigation](../rea-investigation/SKILL.md) and the separately installed `reverse-engineer-anything` skill when its
analysis is useful; use an existing Ghidra provider for supported native targets.
Do not treat static evidence as live behavior or install a missing provider
silently. For adopting an open-source capability, invoke [absorb](../absorb/SKILL.md).
Use [fleet-ladder](../fleet-ladder/SKILL.md) to route by role and capability, never
hardcoded model names. When a tribunal is required, follow
[blind-tribunal](../blind-tribunal/SKILL.md): a fresh author-disjoint execution
reviews an author-redacted frozen packet.
Report any missing resolver, reviewer, or evidence as a runtime gap.
