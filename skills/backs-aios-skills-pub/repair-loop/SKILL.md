---
name: "repair-loop"
description: "Use when fixing a bug, closing a reported issue, or uplifting a seam end to end. Runs the full repair loop (ground in the floor, reproduce on live truth, red contract test, fix the class at the seam, verify on the real path, independent grade, land) and iterates until it is true. Trigger words: repair loop, dev mode, fix this, uplift, close the seam, dev build."
license: "MIT"
---

# Repair Loop
**Effort:** light — the loop itself is discipline plus one independent grading pass; the heavier steps it chains (gauntlet, tribunal) carry their own stamps and fire only on changes that ship. Removes: green-but-broken landings, and the reopened-bug rework they cost.

The default loop for any fix, bug close, or uplift. It is a behavior, not approval
machinery: it adds zero gates and zero friction for the human. It binds the agent to a
discipline that makes "green but broken" structurally hard to ship.

## Load first, before any design or edit

1. [invariant-floor](../invariant-floor/SKILL.md) — read your ruleset before you work.
2. [human-calibration](../human-calibration/SKILL.md) — apply the human's profile; never re-interrogate them.
3. [understanding-gates](../understanding-gates/SKILL.md) — the diagnostic planner: Design → Plan → Build → Test → Ship.
4. [wayfinder](../wayfinder/SKILL.md) — when lost, chart the route; never park a question on the human.
5. If the ask arrives as prose or metaphor, run [intent-compiler](../intent-compiler/SKILL.md) first and loop on the deduced directive.

## The loop

1. **Ground in the floor.** Load the rules and the project's own truth (docs, source,
   tracker) before touching code. Work done from memory of the rules does not count.
2. **Reproduce on live truth.** See the failure yourself, on the real path the human
   uses — not a proxy probe, not the bug report's word for it. No reproduction, no fix.
3. **Red contract test.** Write a failing test that captures the defect, and commit it
   before the fix. Prove it is actually red. The fix makes it green; the fix never edits
   the test. See [red-first](../red-first/SKILL.md).
4. **Fix the CLASS at the seam** — not a point patch per symptom. The full formula
   lives in [seam-engineering](../seam-engineering/SKILL.md).
5. **Verify on the real path.** Trust but verify. Capability is proven on the human's
   own surface (the UI they type into, the command they run), never on a green test
   over a mocked seam. Check every claim ("the other branch landed it", "that service
   is down") against live truth before acting on it.
6. **Measure the fix.** Mid-loop, run only the tests that cover the seam you touched —
   see [sniper-testing](../sniper-testing/SKILL.md). Then run the
   [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) on the changed code:
   scoped tests, complexity-vs-coverage score, bounded mutation testing. A mutant that
   survives your fix means the test never reached the branch you changed — fake green;
   keep iterating.
7. **Independent grade.** A grader that did not author the change (ideally a model
   from a different family than the builder) must pass it. The builder never grades
   its own work. See [blind-tribunal](../blind-tribunal/SKILL.md).
8. **Check concurrent work.** Before altering shared state, verify any other session's
   in-flight work is preserved (on a branch or commit). Never commit or clean up work
   that is not yours.
9. **Land.** One full pass over the touched modules' suites at landing, then commit.
   Close every finding the loop surfaced on this seam — or record an explicit,
   evidenced "not a bug" verdict per finding. "Fixed the big one, deferred the rest"
   never lands.

## Iterate until true

A rule that is not yet met does not stop the loop — it drives it. Escalate the model
or tier, fix the blocker, retry, until every step above is true and the change lands.
"Good enough" is not a status. If genuinely stuck twice on the same seam, log exact
blocker evidence and move to the next unblocked piece — never grind silently.

## Hard rules — any one of these fails the skill

- Fix shipped without a reproduction on live truth.
- Test written after the fix, or edited by the fix.
- Symptom patched while the class stays open at the seam.
- Capability claimed green off a proxy while the human's own path is broken.
- Builder graded its own change.
- A surfaced finding silently deferred at landing.
- Loop abandoned at "good enough" instead of escalated.

## Report

Two words, **PROVEN** or **STILL-BUILDING**, plus the intent in plain language and
the single decision in front of the human, if there is one. Questions go to the human
only for taste, vision, or destructive risk; see [decision-bar](../decision-bar/SKILL.md).

## Works well with

- [incident-closure](../incident-closure/SKILL.md) — when the human reports breakage, this loop runs inside a full close.
- [red-first](../red-first/SKILL.md) · [seam-engineering](../seam-engineering/SKILL.md) · [sniper-testing](../sniper-testing/SKILL.md)
- [blind-tribunal](../blind-tribunal/SKILL.md) · [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md)
