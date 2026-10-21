---
name: "root-cause-first"
description: "Use when facing a hard bug, a silent failure, a regression hunt, or a risky change that could quietly break a downstream consumer. No fixes without investigation — read the error, reproduce it on demand, check recent changes, instrument component boundaries, trace data flow backward to the source. Trigger words: debug, root cause, why is this failing, silent failure, regression, works in tests but fails live, systematic debugging."
license: "MIT"
---

# Root Cause First
**Effort:** free — pure investigation discipline that usually cuts cost outright: one decisive probe replaces firing the whole pipeline to see what happens. Removes: patches to the wrong thing — the symptom fix that hides the real bug and breaks a downstream consumer.

No fixes without investigation. A patch made before you understand the failure
fixes the wrong thing, hides the real bug, and breaks something downstream.
Your product is not a patch — it is a root cause proven by a decisive probe,
and a fix proven not to regress anything.

Two laws govern everything below:

1. **No assumptions — the code, the data, and the live system are truth; notes
   are only hints.** A comment, a memory, a prior conclusion, even your own
   last sentence is a hypothesis until a probe confirms it. The words "all /
   every / none" trigger a three-point check: the environment, a repo-wide
   search, and a scan of every caller.
2. **A verified counter-example kills the prior conclusion immediately.** When
   a probe contradicts what you believed, say plainly "I was wrong — it is
   actually X," then continue from the new fact. Never paper over.

## The loop (run in order; do not skip)

1. **Read the error.** State the symptom in one precise sentence. Read the
   actual message, not what you expect it to say. Name the blast radius: what
   depends on the thing you suspect?
2. **Reproduce.** Make the failure happen on demand — live, or in a failing
   test. **Time it.** A "failure" that returns in milliseconds when real work
   takes seconds is an early swallowed exception, not real work failing. The
   timing gap is itself a clue.
3. **Check recent changes.** Diff what changed since it last worked — code,
   config, environment, dependencies. If the history is long, bisect it.
4. **Map the consumers.** For a bug in a shared surface, list every caller and
   how each one uses it (exact string match? boolean? list?). The real
   regression usually hides in a downstream exact-match comparison, not in the
   knob you are turning.
5. **Instrument the boundaries.** Log or probe at each component seam — what
   goes in, what comes out. Trace the bad data backward, boundary by boundary,
   until you reach the source. Fix the source, never the symptom.
6. **Root-cause by hypothesis.** Form one falsifiable hypothesis. Find the ONE
   decisive probe that separates it from the alternatives, and run only that.
   Do not fire the whole pipeline "to see what happens."
7. **Fix surgically, at the right seam.** Smallest change that resolves the
   root cause. Prefer the single shared source (one normalizer, one runner)
   over editing N call sites. Where possible make the fix inert on the working
   path — it provably changes nothing there and only activates on the broken
   one. No adjacent refactors riding along.
8. **Prove it.** Write the failing test that reproduces the bug; watch it go
   red; fix; watch it go green. Then run the tests for every consumer path you
   mapped in step 4 — green there is your zero-regression floor. A suite that
   mocks the exact seam that failed proves nothing.
9. **Verify live.** Drive the real system — real requests, real database, real
   logs. Never a sidecar script that imports the code into your own process.
   Capture before/after evidence.
10. **Learn.** Write down the symptom, the decisive probe, the root cause, and
    the anti-pattern that hid it, so the next bug of this shape is cheaper.

## Build the reproduction loop BEFORE you theorize

If you catch yourself reading code to build a theory before a red-capable
command exists — stop. No red-capable command, no theory. A tight pass/fail
signal that goes red on THIS bug is the single biggest debugging uplift.
Spend disproportionate effort here.

Ways to build one, roughly in order: a failing test; an HTTP script against a
dev server; a CLI run with a fixture input, diffed against a known-good
snapshot; a headless browser script; a captured real payload replayed through
the code path in isolation; a throwaway harness that calls one function; a
fuzz loop over random inputs; a bisection harness so automated bisect works; a
differential loop (same input through old and new versions, diff the outputs).

Then tighten it: faster (cache setup, narrow the scope), sharper (assert the
specific symptom, not "didn't crash"), deterministic (pin time, seed the RNG,
freeze the network). A two-second deterministic loop is a superpower.

For flaky bugs, chase a higher reproduction rate, not a clean repro: loop the
trigger 100 times, add stress, narrow the timing windows. A 50% flake is
debuggable; a 1% flake is not.

If you still cannot build a loop, stop and say so. List what you tried and ask
your human for access, a captured artifact, or temporary instrumentation. Do
not theorize without a loop. And if no seam exists that can replicate the real
call pattern, that absence IS a finding — flag the architecture gap after the
fix lands.

## Anti-patterns (how hard bugs stay alive)

- Concluding from a note or comment without a probe.
- Fixing before reproducing.
- Trusting a green suite that mocks the exact seam that fails live.
- Sidecar verification — importing the code instead of driving the live system.
- Changing a config knob without mapping the exact-match consumers it feeds.
- Broad refactors riding along with a fix.
- Saying "all / every / none" without the three-point check.

## Works well with

- [red-first](../red-first/SKILL.md) — commit the failing test before the fix.
- [sniper-testing](../sniper-testing/SKILL.md) — scoped tests while iterating.
- [seam-engineering](../seam-engineering/SKILL.md) — fix the class, not the instance.
- [repair-loop](../repair-loop/SKILL.md) — the full fix-and-land cycle.

> Scaffold credit: Matt Pocock, diagnosing-bugs (mattpocock/skills). The composition and hard rules here are BACKS AIOS.
