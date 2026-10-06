---
name: "architecture-engineer"
description: "Analyze BACKS architecture, detect structural drift, evaluate proposed changes against the rules file, and produce architecture decision records. Use before approving any boundary-changing change — new modules, dependency direction shifts, or moves that merge concerns the project keeps separate. Trigger words: architecture, structural drift, dependency direction, ADR, boundary change, responsibility leak, architecture review, design drift, rules compliance."
license: "MIT"
---

# Architecture Engineer — drift detection + ADR authorship

The BACKS AIOS mirror of the proven BACKS skill at
`skills/architecture_engineer/SKILL.md`. Use before approving any change
that touches a module boundary, dependency direction, or shared seam.

## When to run

- A new module is proposed — does it deserve to exist, or does the work
  belong in an existing primitive?
- An existing primitive is being moved, renamed, or absorbed — what breaks?
- A new dependency direction is being added — does it cross a known
  forbidden boundary?
- An ADR is needed — a durable decision warrants a record.

## The review recipe (six steps)

1. **Fixed point.** Identify the request, the diff or revision under review,
   and the repository's current architecture + contribution rules. Trace the
   affected entrypoints, dependencies, data flow, configuration, persistence,
   and callers before judging the change.
2. **Declared vs runtime.** Treat declared architecture (docs, ADRs) and
   runtime wiring (the actual seam) as separate evidence. Report when they
   disagree — that's the drift.
3. **Local rule check.** Run the project rule check first
   (`backend/services/authority_surface_classifier.py` for BACKS; equivalent
   for other repos). A change that registers as authority_surface +
   requires_approval=true is a gate — surface the approval row to the
   operator, never bypass it.
4. **Structural review.** Check the change for:
   - responsibility and dependency direction across boundaries
   - duplicated orchestration, import cycles, new abstractions with no caller
   - configuration embedded in code when the project owns a config seam
   - registrations/routes/adapters without a reachable runtime implementation
   - merging concerns the project deliberately keeps separate
   - drift from explicit project rules (permission/authority boundaries)
5. **Severity + smallest fix.** For each finding: severity, exact evidence
   location, consequence, smallest architecture-level remedy. Do not invent
   numeric targets from another repo.
6. **ADR when durable.** Write an ADR only when a durable decision needs
   recording. Use the project's existing ADR location and format. Capture:
   status, context, decision, considered alternatives, consequences,
   superseded decisions. Never manufacture an ADR for a reversible
   implementation detail.

## What never to do

- **Do not enforce stale numeric targets or conventions from another repo.**
  Cite the local rule or concrete failure mode behind every violation. Label
  unproven design concerns as risks, not defects.
- **Do not bypass the authority surface classifier.** If it returns
  requires_approval=true, the change belongs in a proposal row, not a direct
  commit. The classifier's fail-closed default is the law (inv 2).
- **Do not refactor adjacent code during a review.** Stay on the diff under
  review; surface adjacent issues but do not touch them.

## Cross-references

- The BACKS source of truth: `skills/architecture_engineer/SKILL.md` and
  `skills/architecture-engineer/SKILL.md` (hyphenated) in the BACKS repo.
- The authority classifier: `backend/services/authority_surface_classifier.py`.
- The fix-shit companion: this skill + `skills/fix-shit` for understanding-
  first repair.
- Companion skills: `wayfinder`, `intent-compiler`, `seam-engineering`,
  `turn-and-burn` — they are the other half of the operator's named stack.
