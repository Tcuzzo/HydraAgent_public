---
name: "seam-engineering"
description: "Use when repairing a bug or closing out an audit or bug hunt. Fixes the flaw class once at its shared primitive, sweeps every sibling, lands a guard that catches the next instance, and closes every surfaced finding — no silent deferrals. Trigger words: seam, class fix, whole-seam closure, point patch, structural guard, do it right the first time."
license: "MIT"
---

# Seam Engineering
**Effort:** free — pure repair discipline: one class fix at the shared primitive instead of N point patches. Removes: the same bug repaired again at every sibling site, and the deferred medium finding that becomes the mystery bug nobody can find in six months.

A seam is closed correctly and completely, or it is not closed.
A quick patch today is the bug nobody can find in six months.
This skill turns one bug report into a closed class of bugs.

## When to run it

Any repair: a reported bug, a failed test, a finding list from an audit
or bug hunt. Especially when you feel the pull to "just patch it here."

## Steps

1. **Root cause with evidence.** Fix the cause, not the symptom. Before
   writing the fix, show the proof: a failing repro, a log line, a trace
   that points at the real seam. A fix without evidence is a guess.
2. **Name the flaw CLASS.** Ask: what family of mistake is this, and where
   else could the same mistake live? Write the class down in one sentence.
3. **Fix vertically — once, at the shared primitive.** The shared primitive
   is the one function or module every occurrence flows through. Fix it
   there. Never N point patches. Never mark-the-bad-case-and-compensate.
4. **Sweep horizontally.** Search out every sibling occurrence of the class
   and fix them in the same change, not "later."
5. **Land a structural guard.** A test or automated check that fails on the
   NEXT instance of the class. The class stays closed because something
   watches it, not because everyone remembers.
6. **Close the whole seam.** List every finding the hunt surfaced. Before
   landing, each one is either fixed and green, or carries an explicit,
   recorded "not a bug" verdict with evidence. Never a silent deferral.
   Never "parked in a doc."

## Hard rules

- **A repair that adds a new failure condition is itself a bug.** A rollback
  helper that can crash, a cleanup that strands state, a test edited to
  bless the defect it was meant to catch — all bugs. Redesign the change as
  one atomic unit, or as an explicit crash-safe state machine. Never paper
  past it.
- **"Fixed the high-severity ones; the rest are follow-ups" fails the
  skill.** That is the exact habit this skill exists to kill. A deferred
  medium bug is the future mystery bug. Every finding on the seam counts
  the same.
- **"Good enough to land" is not a status.** If the seam is not right, keep
  iterating (remove the blocker, escalate to a stronger model or reviewer,
  retry) until it is.
- **A point patch beside an existing shared primitive fails the skill.**
  If a primitive already owns the seam, the fix rides it; a bypass fix
  recreates the class.
- **An adjudicated "not a bug" needs evidence,** not a vote. Record what
  was checked and why the finding does not hold.

## Works well with

- [root-cause-first](../root-cause-first/SKILL.md) — the investigation
  discipline behind step 1.
- [red-first](../red-first/SKILL.md) — the failing test that proves the
  fix, and the structural guard pattern for step 5.
- [sniper-testing](../sniper-testing/SKILL.md) — scoped tests while
  iterating; one full pass at landing.
- [repair-loop](../repair-loop/SKILL.md) — the end-to-end loop this
  discipline runs inside.
- [incident-closure](../incident-closure/SKILL.md) — "fix it" means a full close,
  never an option menu.
