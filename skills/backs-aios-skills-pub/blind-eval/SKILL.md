---
name: "blind-eval"
description: "Use before landing anything where taste or output quality is the question and a test cannot decide it. Judges a change on its merits with authorship hidden, then keeps or reverts — a tie reverts, only proven uplift lands. Trigger words: blind eval, karpathy, keep or revert, quality gate, taste call, blind judge, A/B judge, prove uplift."
license: "MIT"
---

# Blind Eval
**Effort:** light — one blind judge run: several shuffled readings of the frozen pair by a model that authored neither. Removes: self-graded "it's better" landings — taste regressions the author would wave through.

A keep-or-revert quality gate for calls a test cannot decide — prose quality, UI
copy, a refactor's readability, a prompt's output, a design's feel. Judge the
change on its merits with authorship hidden, then KEEP it or REVERT it. A tie
reverts. Only proven uplift lands.

## When to run it

- Before landing any change where "is it better?" is a taste or quality question.
- As the gate inside an improvement loop: propose → try → measure → keep or discard.
- Any time the author is tempted to declare their own work an improvement.

## The method

1. **Write "better" down BEFORE you look.** One goal in plain language. One
   primary measure or rubric axis carrying a hard bar — a level to clear, not a
   number to push past. Secondary axes in priority order (cost, length, latency).
2. **Freeze both versions.** The baseline and the candidate, as real artifacts —
   never a description of them.
3. **Strip authorship.** Label them A and B, shuffle the order, drop every name,
   model id, and the author's reasoning. The judge sees only the artifacts and
   the rubric.
4. **Seat a judge that authored neither** — a model from a different family, or a
   human. The author never grades its own work.
5. **Judge on merits.** Score each rubric axis. Cite evidence from the artifact
   for every score — a verdict without evidence is a guess.
6. **KEEP only if the candidate clears the bar AND strictly beats the baseline.**
   A tie is not uplift — revert.
7. **Revert cleanly.** Restore the tree byte-identical to the pre-change state
   (a scratch branch or stash makes this one command). Log the verdict either way.

## Rules that stop the gaming

- **The bar is checked first, and axes rank in order.** A regression on a
  higher-priority axis is fatal even if every lower axis improves. And clearing
  the bar by extra margin buys nothing — you cannot overshoot the primary to
  "pay for" a cost regression.
- **Never lower the bar after seeing the result.** Fixing the score by weakening
  the eval is forbidden. Keep the rubric and eval outside the files the change
  is allowed to touch.
- **No self-grading.** The judge never sees the author's rationale — a judge that
  reads the sales pitch grades the pitch, not the work.
- **Denoise a stochastic judge.** Blind readings vary run to run, and judges
  prefer the first option they see. Run each comparison several times with the
  order shuffled and take the majority vote — the shuffle kills position bias
  and the repeats kill noise, in one move. If the real improvement is smaller
  than the judge's run-to-run swing, the gate cannot tell signal from luck —
  add readings or pick a steadier measure.
- **Solo rig.** No second model family available? A fresh blind session that
  never saw the author's conversation judges — and the report names the
  weakened gate ("judged same-family-blind, not cross-family").
- **No trusted bar? Use dominance.** When the baseline level is unknown or noisy,
  drop the absolute bar and keep only what strictly beats the current champion.
  A regression can never dominate, so no floor is needed.
- **Never score a cost axis over failures.** "Fewer steps" computed over failed
  attempts rewards giving up fast. Compute cost and effort over successes only.

## De-bias the judge

The judge-mechanics floor. These live here and nowhere else:

- **Held-out suite.** Grade on a suite kept OUTSIDE the builder's write reach —
  the builder never sees the graded tests, so it cannot hardcode to them.
- **Fresh-commit strip.** Strip the workspace to one fresh commit and block
  network egress before a graded run, so a pass is DERIVED — not retrieved from
  git history or someone else's fix.
- **Length-normalize.** Judges strongly prefer the longer answer — correct for
  length before comparing scores.
- **Rotated holdout criteria.** Use a named-axis, yes/no rubric with hidden
  holdout criteria rotated between runs. A visible holistic score gets gamed
  into citation theater.
- **Final-state grading.** Grade multi-step work on the FINAL end-state, not
  each intermediate step.
- **Judge calibration.** Calibrate the judge on a small human-labeled set
  (report its true-positive and true-negative rates) before trusting it on
  your domain.

Order-shuffling is part of the denoise rule above — one law, stated once.

## The loop variant

The same gate powers an autonomous improvement loop: propose a small change →
run a short experiment → blind-measure → keep if better, revert if not → repeat,
on a fixed round budget. Feed the proposer the failure traces from the last
round, not just the goal — a proposer that cannot see why it fails edits blind.
Even a loop that keeps nothing earns its cost: the traces it collects point at
concrete, fixable bugs no aggregate score reveals.

## Works well with

- [blind-tribunal](../blind-tribunal/SKILL.md) — the heavier juror panel when defects, not taste, are the question.
- [red-first](../red-first/SKILL.md) — when a test CAN decide it, write the test instead.
- [clean-code-gauntlet](../clean-code-gauntlet/SKILL.md) — measured code-quality gates to pair with the taste call.

> Namesake credit: Andrej Karpathy. Namesake inspiration; the keep-or-revert
> discipline is independently paralleled in Karpathy's autoresearch (2026,
> github.com/karpathy/autoresearch, MIT). The blind (author-hidden) aspect and
> the composition and hard rules here are BACKS AIOS.
