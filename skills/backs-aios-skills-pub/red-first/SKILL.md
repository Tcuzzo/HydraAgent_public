---
name: "red-first"
description: "Use when dispatching any builder (an agent, a model, or yourself) to make a change a test should prove. Commits a proven-failing contract test before the build starts, forbids the builder from touching it, and has an independent grader verify the test was never edited. Trigger words: red first, failing test first, contract test, red baseline, tamper-proof test, test before build."
license: "MIT"
---

# Red-First, Tamper-Proof
**Effort:** free — pure ordering discipline: the test you would write anyway is written first, proven red, and sealed with one commit; the tamper check is a single git diff. Removes: tests shaped after the fix to pass, and green verdicts a builder bent by editing the test.

A test written after the fix proves nothing — it was shaped to pass.
A test the builder can edit proves less — it can be bent to pass.
So the test comes first, gets locked, and is graded untouched.

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
   commit id. That commit is the red baseline — the tamper seal.
4. **Dispatch the builder with one job: make it green.** The builder is
   forbidden to touch the test file. Say so in the dispatch.
5. **Grade independently.** A grader who did not author the change checks
   two things:
   - the test now passes;
   - the test file is byte-identical to the red baseline —
     `git diff <red-sha> HEAD -- tests/test_contract.py` prints nothing.
   Any diff on the test file fails the grade. No exceptions, not even
   "just fixed a typo."
6. **Prefer one structural guard over scattered point tests.** A structural
   guard is a check (a grep sweep, an AST scan, a lint rule) that fails on
   the NEXT offender, not just this instance. One guard beats ten point
   tests that each pin one case.

## Hard rules

- **Red must be proven red.** Run it, watch it fail, before it counts.
- **The builder never edits the test.** The empty test-file diff since the
  red baseline is part of the landing gate, not a courtesy check.
- **Builder is never the grader.** Use a different person, agent, or a
  model from a different family than the builder.
- **Green alone is not proof.** Green + untouched test + independent grade
  is proof.
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
