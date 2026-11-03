---
name: "repair-loop"
description: "Use when fixing a bug, closing a reported issue, uplifting a shared seam, or resuming an unfinished bounded repair. Trigger words: repair loop, dev mode, fix this, uplift, close the seam, dev build."
license: "MIT"
---

# Repair Loop
**Effort:** light — the loop itself is discipline plus one independent grading pass; the heavier steps it chains (gauntlet, tribunal) carry their own stamps and fire only on changes that ship. Removes: green-but-broken landings, and the reopened-bug rework they cost.

The default loop for any fix, bug close, or uplift. It is a behavior, not approval
machinery: it adds zero gates and zero friction for the human. It binds the agent to a
discipline that makes "green but broken" structurally hard to ship.

## Join the current task

Use [BACKS Build Flow](../backs-build-flow/SKILL.md) for the task packet, operator
essence, and one integration owner. Reuse applicable rules, context, loaded skill
bodies, and evidence already established by the caller; refresh only what changed.
Direct invocation establishes missing context once. Load the invariant floor and
required understanding gates when not already available; use wayfinder only for
an unresolved route. Do not restart the outer build play for a repair finding.

[bounded-loops](../bounded-loops/SKILL.md) owns the shared deadline and remaining
attempt/round allowances. Preserve `mission_id`, `task_mode`, pinned `base_sha`,
and current `candidate_sha` through reproduction, edits, tools, models, reviews,
and handoffs. A nested call never resets the budget. Review and diagnosis modes
return evidence without edits or promotion; build actions require that task scope.

## The loop

1. **Ground the affected path.** Check the rules and the project's current source,
   caller contracts, and existing checks. Reuse current, relevant evidence instead
   of repeating a whole-repository audit.
2. **Reproduce on live truth.** See the failure yourself, on the real path the human
   uses — not a proxy probe, not the bug report's word for it. No reproduction, no fix.
3. **Red contract test.** Write a failing test that captures the defect, and commit it
   before the fix. Prove it is actually red. The fix makes it green; the fix never edits
   the test. See [red-first](../red-first/SKILL.md).
4. **Fix the CLASS at the seam** — not a point patch per symptom. The full formula
   lives in [seam-engineering](../seam-engineering/SKILL.md).
5. **Run scoped checks.** Use [sniper-testing](../sniper-testing/SKILL.md) to derive
   scope from the aggregate pinned `base_sha` to `candidate_sha` diff and owned
   pending paths. Run the repository's actual lint, type-check, and test commands
   for that scope. Record the command, result, and revision. Mark an inapplicable
   check **N/A** with its reason; a missing required tool is a gap. Do not install
   arbitrary tools or invent passing checks. A sealed test stays byte-identical.
6. **Measure inline.** Run [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md)
   for the candidate's quality and bounded mutation evidence. Investigate a
   surviving mutant; if it exposes a test gap, add a separate supplemental test
   while preserving the original RED file. Fix the owning seam and re-run the
   affected checks within the same remaining budget; never weaken a gate for GREEN.
7. **Independent grade.** A grader that did not author the change — chosen live
   by capability and availability from the configured rung matrix; the
   authoring execution that built is recorded as provenance and disqualified;
   the reviewer must pass it in a fresh author-disjoint session.
   The builder never grades its own work. See
   [blind-tribunal](../blind-tribunal/SKILL.md).
8. **Check concurrent work.** Before altering shared state, verify any other session's
   in-flight work is preserved (on a branch or commit). Never commit or clean up work
   that is not yours.
9. **Verify and land within scope.** One full pass over the touched modules' suites
   at landing, plus any required project checks, then follow the authorized
   delivery procedure. Check behavior on the human's actual surface (the UI they
   use or command they run) and record the exact candidate/installed revision.
   Local tests and upstream static evidence do not establish live delivery.
   Close every finding the loop surfaced on this seam — or record an explicit,
   evidenced "not a bug" verdict per finding. "Fixed the big one, deferred the rest"
   never lands.

## Iterate within the mission budget

A failed check supplies the next bounded hypothesis. Retry or choose a capable
configured route only within the remaining allowance; switching models does not
create a fresh mission. Apply bounded-loops' stagnation and exhaustion rules.
When a limit is reached, checkpoint **STILL-BUILDING** with the current candidate,
sealed tests, failure evidence, unmet gates, and exact next action. Complete useful
authorized work that does not consume an exhausted allowance. An open finding
blocks landing but does not erase progress or become an invented **PROVEN** verdict.

## Hard rules — any one of these fails the skill

- Fix shipped without a reproduction on live truth.
- Test written after the fix, or edited by the fix.
- Symptom patched while the class stays open at the seam.
- Capability claimed green off a proxy while the human's own path is broken.
- Builder graded its own change.
- A surfaced finding silently deferred at landing.
- Loop reset, silently abandoned, or reported green with an unmet gate.

## Report

Two words, **PROVEN** or **STILL-BUILDING**, plus the intent in plain language and
the single decision in front of the human, if there is one. Questions go to the human
only for taste, vision, or destructive risk; see [decision-bar](../decision-bar/SKILL.md).

## Works well with

- [incident-closure](../incident-closure/SKILL.md) — when the human reports breakage, this loop runs inside a full close.
- [red-first](../red-first/SKILL.md) · [seam-engineering](../seam-engineering/SKILL.md) · [sniper-testing](../sniper-testing/SKILL.md)
- [blind-tribunal](../blind-tribunal/SKILL.md) · [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md)
