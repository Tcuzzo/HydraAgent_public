---
name: "clean-code-gauntlet"
description: "Use when hardening or landing executable changes that need measured complexity, coverage, regression strength, and independent design review."
license: "MIT"
---

# Clean Code Gauntlet
**Effort:** heavy — real compute: coverage and complexity runs plus a bounded mutation pass, then an independent design review; spend it on changes that ship. Removes: line-by-line human review of whole diffs, and the fake-green tests a regression hides behind.

## Why this exists

Messy code makes agents thrash, and rules buried in a long prompt fade
mid-context — deterministic checks never fade. So run Clean Code as a
**gauntlet the code must pass**, not prose the model must remember.

**Measure, do not review.** Gate on numbers a tool computes: coverage,
cyclomatic complexity (a count of independent paths through a function), module
size, mutation kills. Humans and models audit samples — never whole diffs.

## The chain (run in order; each stage stops loud on failure)

Bind the existing [Build Flow](../backs-build-flow/SKILL.md) packet's `base_sha`,
`candidate_sha`, integration owner, and remaining
[bounded-loops](../bounded-loops/SKILL.md) mission allowance. Reuse still-valid
evidence; review rounds and mutation campaigns do not reset the budget.

1. **Scoped lint, type, and behavior checks green.** Execute applicable repository
   commands — see [sniper-testing](../sniper-testing/SKILL.md). Record unavailable
   or inapplicable checks explicitly; do not label them passed. A red baseline
   means stop and fix; never mutate or grade on red.
2. **CRAP under threshold** on real coverage data (see the gate below).
   Breach → refactor the function down, or cover it fully. Never lower the bar.
3. **Mutation testing: no unexplained survivors in scope.** A non-equivalent
   survivor is a regression gap. Add a separate supplemental test file; preserve
   the original [red-first](../red-first/SKILL.md) sealed contract byte-for-byte.
   Record equivalent-mutant refutations with evidence. A tool error or timeout
   is inconclusive, never a killed mutant.
4. **Light taste review** — a reviewer judges only what numbers cannot.

## Tools that compute this

| Stack | Tools |
| --- | --- |
| Python | coverage.py + radon + mutmut |
| JS/TS | c8 (or istanbul) + Stryker |
| Go | go test -cover + gocyclo + go-mutesting |
| Rust | cargo-tarpaulin + cargo-mutants |
| Java | JaCoCo + PIT |
| Other | any coverage % + any cyclomatic-complexity counter |

One command shape per stage:
- Coverage: `coverage run -m pytest <sniper files> && coverage report` (JS/TS: `npx c8 vitest run <files>`)
- Complexity: `radon cc -s <changed files>`
- Mutation: `mutmut run --paths-to-mutate <changed files>` (JS/TS: `npx stryker run --mutate "<glob>"`)

These are examples, not install instructions or stable CLI guarantees. Use the
repository's pinned tools and supported flags. For documentation-only changes,
validate links, structure, distribution, and behavior scenarios; code complexity
or mutation may be inapplicable. Record the reason instead of inventing metrics.

## The CRAP gate

```
CRAP(m) = comp(m)^2 * (1 - cov(m)/100)^3 + comp(m)
```

- At 100% coverage the score collapses to the complexity itself.
- 30 is the classic "crappy" line (complexity 5 with zero coverage hits it).
- Humans hold roughly 4–5 complexity per function. An agent may carry 6–8
  ONLY at near-100% coverage — the coverage pays for the slack.
- A high-CRAP function has exactly two exits: refactor it down, or cover it
  fully. **Never lower the threshold to pass.**

## Whose debt is it — AUTHORED / WORSENED / UNCHANGED

An absolute score hides whose debt it is. Split every complexity and CRAP
delta against the pinned pre-task `base_sha`, using comparable actual coverage:

- **AUTHORED** — functions this change created. The full bar applies.
- **WORSENED** — pre-existing functions this change made worse. The delta is
  charged to this change; it must come back to baseline or better.
- **UNCHANGED** — pre-existing debt the change never touched. Report it, file
  it, never charge it to this change — and never use it as cover to skip
  the gauntlet.

## Mutation rules (bounded, never reckless)

- **Never the shared working tree.** Mutate in a scratch checkout cut from the
  exact committed `candidate_sha`. Dirty target or test files = checkpoint and
  finish the candidate first. Preserve unrelated work in the original checkout.
- **Cost is measured, never assumed.** Time the scoped suite once, report
  ETA = baseline x mutant count BEFORE spending anything. Offer a dry run.
- **Bounded and resumable.** Cap mutants and minutes inside the remaining mission
  allowance. A budget stop checkpoints unverified mutants and leaves the gate
  incomplete. Resume only with remaining or explicitly extended allowance.
- **Coverage-first.** Mutate only covered lines; an uncovered line is a
  coverage gap the CRAP gate already caught.
- **Scoped only.** Mutate what the diff touched, never the whole repo.
- A genuinely equivalent mutant may be refuted instead of killed — with the
  refutation written down, never silently skipped.
- **No mutation tool exists for your stack?** Record the limitation and actual
  substitute evidence. Apply the repository's acceptance policy; a required gate
  remains incomplete until its owner accepts a documented alternative.

## The taste review (last, and light)

Deterministic gates go first; use independent judgment for what measurements
cannot establish. The reviewer is a non-builder grader, chosen live by capability and
availability from the configured rung matrix (the authoring execution that
built is recorded as provenance and disqualified; the reviewer uses a fresh
author-disjoint session) — the builder never grades its own work. It judges
only design and taste: naming, mixed concerns, interface width, and the six
smells — rigidity, fragility, immobility, needless complexity, needless
repetition, opacity. The arithmetic was already settled by the gates.

**Model agnostic:** the reviewer may use the same capable model, provider, or
family as the builder. Independence comes from a separate execution with fresh
context that did not inherit the author's conversation. Model diversity is
optional; no model name, provider, or family is a gate.

Craft floor the review holds: functions small, doing one thing, few arguments,
no flag arguments, honest names; deep modules — a small interface hiding real
logic; tests fast, independent, repeatable, one behavior asserted each.

## Hard rules (any one broken fails the skill)

- Never lower a threshold or weaken the mutation set to force a pass.
- Never mutate the shared working tree; never run unbounded.
- Never charge UNCHANGED debt to the current change.
- A test that cannot fail is theater — mutation testing is how you prove
  which tests are real.
- Say the real cost — machine time is cheap, regressions are not. Never
  fake green to save the hour.

## Works well with

- [sniper-testing](../sniper-testing/SKILL.md) — picks the test scope for stage 1
- [red-first](../red-first/SKILL.md) — the failing contract that precedes any build
- [blind-eval](../blind-eval/SKILL.md) — keep-or-revert when taste is the question
- [blind-tribunal](../blind-tribunal/SKILL.md) — a fuller graded verdict before landing

> Scaffold credit: Robert C. Martin, *Clean Code* (2008); Alberto Savoia &
> Bob Evans, the CRAP metric (2007); John Ousterhout, deep modules
> (*A Philosophy of Software Design*, 2018); Pocock, M., & Martin, R. C.
> (2026, Aug 19). LIVE: Uncle Bob on Software Fundamentals in the Age of AI
> [Video]. YouTube. https://www.youtube.com/watch?v=zcLPGC-tvgk — source of
> the agent CRAP band and coverage-first mutation. The composition and hard
> rules here are BACKS AIOS.
