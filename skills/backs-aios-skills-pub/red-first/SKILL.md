---
name: "red-first"
description: "Use before dispatching a builder for a behavior change a test can prove, or when a new review finding or mutation gap needs a regression contract. Trigger words: red first, failing test first, contract test, red baseline, tamper-proof test, test before build."
license: "MIT"
---

# Red-First, Tamper-Proof
**Effort:** free — pure ordering discipline: the test you would write anyway is written first, proven red, and sealed with one commit; the tamper check is a single git diff. Removes: tests shaped after the fix to pass, and green verdicts a builder bent by editing the test.

A green result observed only after the fix establishes neither the original
failure nor the test's ability to detect it. Establish and seal the original RED
before the fix; the builder leaves that file unchanged. A separate supplemental
regression may later demonstrate failure against a controlled mutant and pass
against the unmutated candidate, as described below.

Use the current [BACKS Build Flow](../backs-build-flow/SKILL.md) task packet. This
skill owns the RED seal; it does not create another build owner or lifecycle.
Keep its pinned `base_sha`, current `candidate_sha`, and mission identity. The
deadline and remaining attempts from [bounded-loops](../bounded-loops/SKILL.md)
cover reproduction, builders, supplemental tests, and independent reviews alike.
Reuse a valid existing RED receipt instead of manufacturing a new failure.

## When to run it

Before dispatching any build or fix where a test can state the wanted
behavior. This is the default for bug fixes and new capabilities alike.

## Steps

1. **Write the failing contract test.** It states the behavior you want,
   in the smallest form that would catch its absence. It must fail right now.
2. **Prove it red.** Run the test and watch it fail — for the right reason.
   A test that errors on import, or quietly passes, is not red. A red test
   nobody ran is a guess, not a baseline.
3. **Commit the red test BEFORE dispatching the builder.** Record the
   commit id, exact test paths and bytes, failing command/output, and reason.
   That commit is the red baseline — the tamper seal. It does not replace the
   mission's pinned `base_sha` used to scope the aggregate change.
4. **Dispatch the builder with one job: make it green.** The builder is
   forbidden to touch the test file. Say so in the dispatch.
5. **Grade independently.** A grader who did not author the change checks
   two things:
   - the test now passes;
   - the test file is byte-identical to the red baseline —
     `git diff <red-sha> <candidate_sha> -- tests/test_contract.py` prints nothing.
     When checks include pending work, also compare the actual tested file bytes
     to the seal; a clean committed diff cannot excuse a staged or unstaged edit.
   Any diff on the test file fails the grade. No exceptions, not even
   "just fixed a typo."
6. **Prefer one structural guard over scattered point tests.** A structural
   guard is a check (a grep sweep, an AST scan, a lint rule) that fails on
   the NEXT offender, not just this instance. One guard beats ten point
   tests that each pin one case.

## A new finding after the original seal

A mutation survivor or review finding can expose a missing assertion. Keep the
original RED file byte-identical and add a **separate supplemental test file**.
Observe that test failing for the new defect before its fix; for a mutation gap,
demonstrate failure against the relevant controlled mutant in isolation and pass
against the unmutated candidate. Record and seal the supplemental evidence before
the next builder changes that seam. Do not claim this was the original baseline.
The grader checks both seals and the exact aggregate candidate. Adding a test
never authorizes weakening, renaming, or replacing the original contract.

If the shared budget is exhausted or RED cannot be established, checkpoint
**STILL-BUILDING** with the gap and exact next action. Do not fabricate RED or
reset the mission to force a green result.

## Hard rules

- **Red must be proven red.** Run it, watch it fail, before it counts.
- **The builder never edits the test.** The empty test-file diff since the
  red baseline is part of the landing gate, not a courtesy check.
- **Builder is never the grader.** Use a non-builder grader, chosen live by
  capability and availability from the configured rung matrix (the authoring
  execution that built is recorded as provenance and disqualified; review uses
  a fresh author-disjoint session).
- **Green alone is not proof.** Green + untouched test + independent grade
  proves this contract; a live capability claim still needs real-surface and
  delivery evidence from the task's workflow.
- **When a whole class of defect is in play, guard the class.** Point tests
  stop this bug; a structural guard stops the next one.

## Works well with

- [sniper-testing](../sniper-testing/SKILL.md) — run only the tests the
  change touches while iterating; one full pass at landing.
- [seam-engineering](../seam-engineering/SKILL.md) — the class-fix
  discipline the structural guard belongs to.
- [blind-tribunal](../blind-tribunal/SKILL.md) — independent graders who
  never saw the author.
- [repair-loop](../repair-loop/SKILL.md) — the loop that carries red → green
  → proven end to end.
