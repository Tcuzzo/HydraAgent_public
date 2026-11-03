---
name: debugging-engineer
description: Investigate hard regressions, silent failures, and concurrent races with a falsifiable hypothesis and decisive probes before changing code. Use for debugging engineer or systematic debugging requests.
license: MIT
---

# Debugging Engineer

Load `root-cause-first` for the full investigation method. This skill adds an
engineering record that makes the diagnosis reviewable:

1. Pin the environment, version, failing input, observed output, and time.
2. Establish a red reproducer. For a race, control the interleaving and prove
   the old result; for an external API, retain a redacted real response shape.
3. Map all consumers and the state transition that crosses the failed seam.
   State competing hypotheses and run the smallest probe that separates them.
4. State the root cause as a mechanism with exact evidence, not as a symptom.
5. Use `repair-loop` to fix and verify the whole class. Include a negative
   control showing the working path still works.

Do not catch-and-return success on failure, suppress the original exception,
or weaken a test to make it green. If the decisive probe is unavailable, report
the missing evidence and continue only on independent, safe work.
