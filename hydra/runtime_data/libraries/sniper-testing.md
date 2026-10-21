---
name: "sniper-testing"
description: "Use during any fix or build loop, and before trusting any green test. Runs only the tests that cover what you touched, and kills mock theater — tests that pass while the capability is broken. Trigger words: sniper testing, scoped tests, test scope, mock theater, fake green, full suite, test bloat."
license: "MIT"
---

# Sniper Testing
**Effort:** free — pure discipline, no extra runs; it cuts net cost outright by deleting full-suite reruns during iteration. Removes: test bloat (whole-suite runs for a tiny diff) and the mock-theater greens you would otherwise build on.

## Why this exists

Two failure modes burn most test time. Test bloat: running the whole suite for
a tiny change. Mock theater: tests that pass while the real capability is
physically broken. This skill kills both.

## Rule 1 — the diff defines scope, not optimism

During the fix/build iteration loop, you are forbidden from running the entire
test suite.

1. Run `git diff --name-only HEAD` to see exactly which files you touched.
2. Map each touched file to the test files that directly cover it
   (e.g. `src/payments/refund.py` → `tests/test_refund.py`).
3. State your specific test target, then run ONLY those files
   (Python: `pytest tests/test_refund.py`;
   JS: `npx vitest run tests/refund.test.js`;
   Go: `go test ./payments/ -run TestRefund`).
4. A test that already passed is not re-run unless your next change touches
   code it exercises. The diff defines the scope — not optimism, not fear.
5. At landing time (the commit gate), run ONE full pass over every touched
   module's suite. That single pass catches indirect couplings exactly once.
   Iteration speed and a sound landing are both part of the job.

## Rule 2 — kill mock theater

A capability test must assert a real, physical side-effect:

- "produces a video" → a real file exists on disk with size > 0 bytes.
- "stores memory" → the row reads back from a real local database.
- "renders the widget" → a real DOM element exists on the page.

Do not mock the database. Do not mock the file system. Do not mock local
network sockets.

The one legal mock is the paid external transport leaf — the HTTP call to a
metered third-party API. Even then, the test must traverse all the real logic
around it: building the request, routing, parsing the response. Mock the wire,
never the brain.

## Audit before you trust

Before relying on any test, read it. If it is mock theater (green because of
mocks, with no physical assertion), delete the mock and rewrite the test to
assert a real side-effect. A test that cannot fail is worse than no test: it
certifies a lie, and you will build on that lie.

## Hard rules (any one broken fails the skill)

- No full-suite run during iteration.
- No green claim without a real side-effect assertion.
- No mock beyond the paid external transport leaf in a capability test.
- No landing without the single full pass over touched modules.

## Works well with

- [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) — sniper scope feeds its first gate
- [red-first](../red-first/SKILL.md) — write the failing test before the fix
- [seam-engineering](../seam-engineering/SKILL.md) — fix the class, then sweep with scoped tests
