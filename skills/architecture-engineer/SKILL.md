---
name: architecture-engineer
description: >-
  Evaluate structure against the project that actually exists, not a remembered
  diagram or a generic architecture preference. Detect structural drift, review
  boundary-changing proposals, and write architecture decision records when an
  architecture review or ADR is requested. Cites local rules, never borrowed
  numeric targets from other repos.
trigger_summary: architecture, ADR, drift, boundary, design review, structure, dependency direction
license: MIT
---

# Architecture Engineer

Evaluate structure against the project that actually exists, not a remembered
diagram or a generic architecture preference.

## Establish the fixed point

- Identify the request, the revision or diff under review, and the
  repository's current architecture and contribution rules.
- Trace the affected entrypoints, dependencies, data flow, configuration,
  persistence, and callers before judging the change.
- Treat declared architecture and runtime wiring as separate evidence. Report
  when they disagree.

## Review the structure

Check the proposed or current design for:

- responsibility and dependency direction across boundaries;
- duplicated orchestration, import cycles, and new abstractions that have no
  real caller;
- configuration embedded in code when the project already owns a
  configuration seam;
- registrations, routes, or adapters without a reachable runtime
  implementation;
- a change that merges concerns the project deliberately keeps separate;
- drift from explicit project rules, including permission or authority
  boundaries.

Do not enforce stale numeric targets or conventions from another repository.
Cite the local rule or concrete failure mode behind every violation. Label
unproven design concerns as risks, not defects.

## Structural change review

For a diff or plan:

1. List the affected files and runtime seams.
2. Explain how control and data move before and after the change.
3. Check whether the change adds a new boundary, bypasses an existing one, or
   leaves a sibling path inconsistent.
4. Separate required fixes from optional simplifications.
5. Give each finding a severity, exact evidence location, consequence, and
   smallest architecture-level remedy.

## Architecture decision records

Write an ADR only when a durable decision needs to be recorded. Use the
project's existing ADR location and format when present. Capture status,
context, decision, considered alternatives, consequences, and superseded
decisions. Do not manufacture an ADR for a reversible implementation detail.

## Report

State the review type, fixed point, rules and seams checked, violations,
drift findings, open evidence gaps, and ADR path if one was authored. A clean
review says what was inspected; absence of findings is not proof that
uninspected areas are sound.

## Works well with

- `systematic-debugging` — root-cause discipline for structural failures.
- `elite-build-understanding` — Design → Plan → Build → Test → Ship.
- `repo-map` — bounded source discovery before judging.
- `red-first` — failing contract before structural changes.

## Provenance

Adapted from the BACKS AIOS package (skill `architecture-engineer`, upstream
version recorded in the original frontmatter). The full operative method
above is the substantive content; BACKS-specific runtime bindings were
removed for this public edition.